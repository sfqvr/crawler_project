import asyncio
import json
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from crawl4ai import AsyncWebCrawler, CacheMode
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig


INPUT_FILE = Path("parsed_danluu/danluu_postmortems.jsonl")
OUTPUT_FILE = Path("parsed_danluu/danluu_postmortems_with_html.jsonl")

LIMIT_ROWS: Optional[int] = None
RESUME_FROM_OUTPUT = False
DEBUG = True
HEADLESS = True

SEMAPHORE_COUNT = 3
BATCH_SIZE = 30


def debug_print(msg: str) -> None:
    if DEBUG:
        print(msg)


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_input_df(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {path}")
    return pd.read_json(path, lines=True)


def append_jsonl_row(output_path: Path, row: dict) -> None:
    with output_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def is_wayback_url(url: str) -> bool:
    return "web.archive.org" in url


def build_browser_config() -> BrowserConfig:
    return BrowserConfig(
        browser_type="chromium",
        headless=HEADLESS,
        verbose=DEBUG,
        viewport_width=1400,
        viewport_height=900,
    )


def build_config(fallback: bool = False) -> CrawlerRunConfig:
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        wait_until="load" if not fallback else "domcontentloaded",
        page_timeout=90000 if not fallback else 120000,
        wait_for_timeout=45000 if not fallback else 60000,
        delay_before_return_html=2.0 if not fallback else 4.0,
        process_iframes=False,
        remove_overlay_elements=True,
        wait_for_images=False,
        scan_full_page=False,
        wait_for="""
        js:() => {
            const text = document.body?.innerText || "";
            return text.trim().length > 1200;
        }
        """,
        js_code="""
        (() => {
            const toolbar = document.querySelector('#wm-ipp');
            if (toolbar) toolbar.remove();
        })();
        """,
        stream=False,
    )


def build_single_run_config(url: str, fallback: bool = False) -> CrawlerRunConfig:
    if is_wayback_url(url):
        return CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            wait_until="domcontentloaded",
            page_timeout=120000 if not fallback else 150000,
            wait_for_timeout=60000 if not fallback else 75000,
            delay_before_return_html=3.0 if not fallback else 6.0,
            process_iframes=False,
            remove_overlay_elements=True,
            wait_for_images=False,
            scan_full_page=False,
            js_code="""
            (() => {
                const toolbar = document.querySelector('#wm-ipp');
                if (toolbar) toolbar.remove();
            })();
            """,
            wait_for="""
            js:() => {
                const text = document.body?.innerText || "";
                return text.trim().length > 1200;
            }
            """,
            stream=False,
        )
    return build_config(fallback=fallback)


def load_processed_urls(output_path: Path) -> set[str]:
    if not output_path.exists():
        return set()

    processed_urls: set[str] = set()
    with output_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                url = obj.get("url")
                if isinstance(url, str) and url:
                    processed_urls.add(url)
            except json.JSONDecodeError:
                continue

    return processed_urls


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def print_output_schema() -> None:
    schema = {
        "name": "str",
        "url": "str",
        "description": "str",
        "error": "bool",
        "cleaned_html": "str",
        "crawl_success": "bool",
        "crawl_error_message": "str",
        "crawl_error_primary": "str",
        "crawl_error_fallback": "str",
        "crawl_mode": "str",
        "crawl_url_type": "str",
        "cleaned_html_length": "int",
        "crawl_suspect": "bool",
        "crawl_attempt_count": "int",
        "crawl_primary_success": "bool",
        "crawl_fallback_used": "bool",
        "crawl_started_at_utc": "str",
        "crawl_finished_at_utc": "str",
        "crawl_elapsed_seconds": "float",
        "crawler_version_hint": "str",
        "debug_requested_url": "str",
        "debug_result_url": "str",
        "debug_status_code": "int | null",
        "debug_match_method": "str",
        "debug_batch_id": "int | null",
    }

    print("=" * 80)
    print("=== OUTPUT JSONL SCHEMA ===")
    for key, value in schema.items():
        print(f"{key}: {value}")
    print("=" * 80)


def validate_success(result) -> tuple[bool, str]:
    if not getattr(result, "success", False):
        return False, ""

    cleaned_html = getattr(result, "cleaned_html", "") or ""
    if not cleaned_html.strip():
        return False, ""

    if len(cleaned_html) < 500:
        return False, cleaned_html

    return True, cleaned_html


async def run_batch(
    crawler: AsyncWebCrawler,
    urls: list[str],
    fallback: bool = False,
):
    if not urls:
        return []

    sem = asyncio.Semaphore(SEMAPHORE_COUNT)

    async def crawl_one(url: str):
        async with sem:
            config = build_config(fallback=fallback)
            try:
                return await asyncio.wait_for(
                    crawler.arun(url=url, config=config),
                    timeout=120.0,
                )
            except asyncio.TimeoutError:
                debug_print(f"[TIMEOUT] URL не уложился в 120с: {url[:80]}")
                class _DummyResult:
                    url = url
                    success = False
                    cleaned_html = ""
                    error_message = "Timeout after 120s"
                    status_code = None
                    def __getattr__(self, name):
                        return None
                return _DummyResult()

    try:
        return await asyncio.wait_for(
            asyncio.gather(*[crawl_one(url) for url in urls]),
            timeout=600.0,
        )
    except asyncio.TimeoutError:
        debug_print(f"[TIMEOUT] Batch не уложился в 600с ({len(urls)} urls)")
        return []


async def run_single(
    crawler: AsyncWebCrawler,
    url: str,
    fallback: bool = False,
):
    config = build_single_run_config(url=url, fallback=fallback)
    return await crawler.arun(url=url, config=config)


def chunked(items: list[dict], batch_size: int):
    for start in range(0, len(items), batch_size):
        yield start // batch_size + 1, items[start:start + batch_size]


def build_success_output_row(
    row: dict,
    result,
    cleaned_html: str,
    mode: str,
    primary_error: str,
    started_total_iso: str,
    started_total_perf: float,
    match_method: str,
    batch_id: Optional[int],
) -> dict:
    html_length = len(cleaned_html)
    requested_url = row["url"]

    return {
        "name": row["name"],
        "url": requested_url,
        "description": row["description"],
        "error": row["error"],
        "cleaned_html": cleaned_html,
        "crawl_success": True,
        "crawl_error_message": "",
        "crawl_error_primary": primary_error if mode == "fallback" else "",
        "crawl_error_fallback": "",
        "crawl_mode": mode,
        "crawl_url_type": "WAYBACK" if is_wayback_url(requested_url) else "NORMAL",
        "cleaned_html_length": html_length,
        "crawl_suspect": html_length < 1000,
        "crawl_attempt_count": 1 if mode == "primary" else 2,
        "crawl_primary_success": mode == "primary",
        "crawl_fallback_used": mode == "fallback",
        "crawl_started_at_utc": started_total_iso,
        "crawl_finished_at_utc": now_iso_utc(),
        "crawl_elapsed_seconds": round(time.perf_counter() - started_total_perf, 3),
        "crawler_version_hint": "Crawl4AI 0.9.0",
        "debug_requested_url": requested_url,
        "debug_result_url": getattr(result, "url", requested_url),
        "debug_status_code": getattr(result, "status_code", None),
        "debug_match_method": match_method,
        "debug_batch_id": batch_id,
    }


def build_failed_output_row(
    row: dict,
    result,
    primary_error: str,
    fallback_error: str,
    started_total_iso: str,
    started_total_perf: float,
    match_method: str,
    batch_id: Optional[int],
) -> dict:
    requested_url = row["url"]

    return {
        "name": row["name"],
        "url": requested_url,
        "description": row["description"],
        "error": row["error"],
        "cleaned_html": "",
        "crawl_success": False,
        "crawl_error_message": f"primary: {primary_error} | fallback: {fallback_error}",
        "crawl_error_primary": primary_error,
        "crawl_error_fallback": fallback_error,
        "crawl_mode": "failed",
        "crawl_url_type": "WAYBACK" if is_wayback_url(requested_url) else "NORMAL",
        "cleaned_html_length": 0,
        "crawl_suspect": True,
        "crawl_attempt_count": 2,
        "crawl_primary_success": False,
        "crawl_fallback_used": True,
        "crawl_started_at_utc": started_total_iso,
        "crawl_finished_at_utc": now_iso_utc(),
        "crawl_elapsed_seconds": round(time.perf_counter() - started_total_perf, 3),
        "crawler_version_hint": "Crawl4AI 0.9.0",
        "debug_requested_url": requested_url,
        "debug_result_url": getattr(result, "url", requested_url),
        "debug_status_code": getattr(result, "status_code", None),
        "debug_match_method": match_method,
        "debug_batch_id": batch_id,
    }


def match_results_to_rows(
    rows_batch: list[dict],
    results: list,
) -> tuple[list[tuple[dict, object, str]], list[dict], list[object]]:
    rows_by_url: dict[str, deque] = defaultdict(deque)
    for row in rows_batch:
        rows_by_url[row["url"]].append(row)

    matched: list[tuple[dict, object, str]] = []
    unmatched_results: list[object] = []

    for result in results:
        result_url = getattr(result, "url", None)
        if isinstance(result_url, str) and rows_by_url.get(result_url):
            row = rows_by_url[result_url].popleft()
            matched.append((row, result, "batch_url_match"))
        else:
            unmatched_results.append(result)

    unresolved_rows: list[dict] = []
    for dq in rows_by_url.values():
        unresolved_rows.extend(list(dq))

    return matched, unresolved_rows, unmatched_results


async def primary_pass_for_batch(
    crawler: AsyncWebCrawler,
    rows_batch: list[dict],
    batch_id: int,
    started_total_iso: str,
    started_total_perf: float,
):
    success_rows: list[dict] = []
    failed_primary_rows: list[dict] = []

    batch_urls = [row["url"] for row in rows_batch]
    results = await run_batch(crawler=crawler, urls=batch_urls, fallback=False)

    matched, unresolved_rows, unmatched_results = match_results_to_rows(rows_batch, results)

    for row, result, match_method in matched:
        requested_url = row["url"]
        crawl_ok, cleaned_html = validate_success(result)

        if crawl_ok:
            success_rows.append(
                build_success_output_row(
                    row=row,
                    result=result,
                    cleaned_html=cleaned_html,
                    mode="primary",
                    primary_error="",
                    started_total_iso=started_total_iso,
                    started_total_perf=started_total_perf,
                    match_method=match_method,
                    batch_id=batch_id,
                )
            )
            debug_print(f"[OK][PRIMARY][BATCH {batch_id}] {requested_url} | cleaned_html length={len(cleaned_html)}")
        else:
            primary_error = getattr(result, "error_message", "") or "Primary crawl failed or HTML too short"
            failed_primary_rows.append(
                {
                    **row,
                    "primary_error": primary_error,
                    "match_method": match_method,
                    "batch_id": batch_id,
                }
            )
            debug_print(f"[FAIL][PRIMARY][BATCH {batch_id}] {requested_url} | {primary_error}")

    if unmatched_results:
        debug_print(f"[WARN][PRIMARY][BATCH {batch_id}] unmatched results: {len(unmatched_results)}")
        for result in unmatched_results:
            debug_print(f"    result.url={getattr(result, 'url', None)}")

    if unresolved_rows:
        debug_print(f"[RETRY][PRIMARY][BATCH {batch_id}] sequential retries for unresolved rows: {len(unresolved_rows)}")

    for row in unresolved_rows:
        requested_url = row["url"]
        try:
            result = await run_single(crawler=crawler, url=requested_url, fallback=False)
            crawl_ok, cleaned_html = validate_success(result)

            if crawl_ok and getattr(result, "url", requested_url) == requested_url:
                success_rows.append(
                    build_success_output_row(
                        row=row,
                        result=result,
                        cleaned_html=cleaned_html,
                        mode="primary",
                        primary_error="",
                        started_total_iso=started_total_iso,
                        started_total_perf=started_total_perf,
                        match_method="sequential_retry",
                        batch_id=batch_id,
                    )
                )
                debug_print(f"[OK][PRIMARY-RETRY][BATCH {batch_id}] {requested_url} | cleaned_html length={len(cleaned_html)}")
            else:
                primary_error = getattr(result, "error_message", "") or "Primary sequential retry failed or result.url mismatch"
                if getattr(result, "url", requested_url) != requested_url:
                    primary_error = f"{primary_error} | result.url={getattr(result, 'url', None)}"
                failed_primary_rows.append(
                    {
                        **row,
                        "primary_error": primary_error,
                        "match_method": "sequential_retry",
                        "batch_id": batch_id,
                    }
                )
                debug_print(f"[FAIL][PRIMARY-RETRY][BATCH {batch_id}] {requested_url} | {primary_error}")
        except Exception as e:
            primary_error = f"{type(e).__name__}: {e}"
            failed_primary_rows.append(
                {
                    **row,
                    "primary_error": primary_error,
                    "match_method": "sequential_retry_exception",
                    "batch_id": batch_id,
                }
            )
            debug_print(f"[FAIL][PRIMARY-RETRY][BATCH {batch_id}] {requested_url} | {primary_error}")

    return success_rows, failed_primary_rows


async def fallback_pass_for_batch(
    crawler: AsyncWebCrawler,
    rows_batch: list[dict],
    batch_id: int,
    started_total_iso: str,
    started_total_perf: float,
):
    success_rows: list[dict] = []
    failed_rows: list[dict] = []

    if not rows_batch:
        return success_rows, failed_rows

    batch_urls = [row["url"] for row in rows_batch]
    results = await run_batch(crawler=crawler, urls=batch_urls, fallback=True)

    matched, unresolved_rows, unmatched_results = match_results_to_rows(rows_batch, results)

    for row, result, match_method in matched:
        requested_url = row["url"]
        crawl_ok, cleaned_html = validate_success(result)

        if crawl_ok:
            success_rows.append(
                build_success_output_row(
                    row=row,
                    result=result,
                    cleaned_html=cleaned_html,
                    mode="fallback",
                    primary_error=row["primary_error"],
                    started_total_iso=started_total_iso,
                    started_total_perf=started_total_perf,
                    match_method=match_method,
                    batch_id=batch_id,
                )
            )
            debug_print(f"[OK][FALLBACK][BATCH {batch_id}] {requested_url} | cleaned_html length={len(cleaned_html)}")
        else:
            fallback_error = getattr(result, "error_message", "") or "Fallback crawl failed or HTML too short"
            failed_rows.append(
                build_failed_output_row(
                    row=row,
                    result=result,
                    primary_error=row["primary_error"],
                    fallback_error=fallback_error,
                    started_total_iso=started_total_iso,
                    started_total_perf=started_total_perf,
                    match_method=match_method,
                    batch_id=batch_id,
                )
            )
            debug_print(f"[FAIL][FALLBACK][BATCH {batch_id}] {requested_url} | {fallback_error}")

    if unmatched_results:
        debug_print(f"[WARN][FALLBACK][BATCH {batch_id}] unmatched results: {len(unmatched_results)}")
        for result in unmatched_results:
            debug_print(f"    result.url={getattr(result, 'url', None)}")

    if unresolved_rows:
        debug_print(f"[RETRY][FALLBACK][BATCH {batch_id}] sequential retries for unresolved rows: {len(unresolved_rows)}")

    for row in unresolved_rows:
        requested_url = row["url"]
        try:
            result = await run_single(crawler=crawler, url=requested_url, fallback=True)
            crawl_ok, cleaned_html = validate_success(result)

            if crawl_ok and getattr(result, "url", requested_url) == requested_url:
                success_rows.append(
                    build_success_output_row(
                        row=row,
                        result=result,
                        cleaned_html=cleaned_html,
                        mode="fallback",
                        primary_error=row["primary_error"],
                        started_total_iso=started_total_iso,
                        started_total_perf=started_total_perf,
                        match_method="sequential_retry",
                        batch_id=batch_id,
                    )
                )
                debug_print(f"[OK][FALLBACK-RETRY][BATCH {batch_id}] {requested_url} | cleaned_html length={len(cleaned_html)}")
            else:
                fallback_error = getattr(result, "error_message", "") or "Fallback sequential retry failed or result.url mismatch"
                if getattr(result, "url", requested_url) != requested_url:
                    fallback_error = f"{fallback_error} | result.url={getattr(result, 'url', None)}"
                failed_rows.append(
                    build_failed_output_row(
                        row=row,
                        result=result,
                        primary_error=row["primary_error"],
                        fallback_error=fallback_error,
                        started_total_iso=started_total_iso,
                        started_total_perf=started_total_perf,
                        match_method="sequential_retry",
                        batch_id=batch_id,
                    )
                )
                debug_print(f"[FAIL][FALLBACK-RETRY][BATCH {batch_id}] {requested_url} | {fallback_error}")
        except Exception as e:
            fallback_error = f"{type(e).__name__}: {e}"

            class DummyResult:
                url = requested_url
                status_code = None

            failed_rows.append(
                build_failed_output_row(
                    row=row,
                    result=DummyResult(),
                    primary_error=row["primary_error"],
                    fallback_error=fallback_error,
                    started_total_iso=started_total_iso,
                    started_total_perf=started_total_perf,
                    match_method="sequential_retry_exception",
                    batch_id=batch_id,
                )
            )
            debug_print(f"[FAIL][FALLBACK-RETRY][BATCH {batch_id}] {requested_url} | {fallback_error}")

    return success_rows, failed_rows


async def main():
    ensure_parent_dir(OUTPUT_FILE)
    print_output_schema()

    df = load_input_df(INPUT_FILE)

    if LIMIT_ROWS is not None:
        df = df.head(LIMIT_ROWS).copy()

    debug_print(f"[INFO] Всего строк во входном файле: {len(df)}")
    debug_print(f"[INFO] Размер batch: {BATCH_SIZE}")

    processed_urls = set()
    if RESUME_FROM_OUTPUT:
        processed_urls = load_processed_urls(OUTPUT_FILE)
        debug_print(f"[INFO] Уже обработано URL в output: {len(processed_urls)}")

    rows_to_process: list[dict] = []
    skipped_count = 0

    for source_index, row in df.iterrows():
        row_dict = {
            "source_index": int(source_index),
            "name": row["name"],
            "url": row["url"],
            "description": row["description"],
            "error": bool(row["error"]),
        }

        if RESUME_FROM_OUTPUT and row_dict["url"] in processed_urls:
            skipped_count += 1
            debug_print(f"[SKIP] Уже есть в output: {row_dict['url']}")
            continue

        rows_to_process.append(row_dict)

    debug_print(f"[INFO] К обработке осталось: {len(rows_to_process)}")
    debug_print(f"[INFO] Пропущено по resume: {skipped_count}")

    if not rows_to_process:
        debug_print("[INFO] Обрабатывать нечего.")
        return

    success_count = 0
    fail_count = 0

    browser_config = build_browser_config()
    started_total_perf = time.perf_counter()
    started_total_iso = now_iso_utc()

    async with AsyncWebCrawler(config=browser_config) as crawler:
        for batch_id, rows_batch in chunked(rows_to_process, BATCH_SIZE):
            debug_print("\n" + "=" * 80)
            debug_print(f"[BATCH {batch_id}] PRIMARY PASS | rows={len(rows_batch)}")

            primary_success_rows, primary_failed_rows = await primary_pass_for_batch(
                crawler=crawler,
                rows_batch=rows_batch,
                batch_id=batch_id,
                started_total_iso=started_total_iso,
                started_total_perf=started_total_perf,
            )

            for output_row in primary_success_rows:
                append_jsonl_row(OUTPUT_FILE, output_row)
                success_count += 1

            if primary_failed_rows:
                debug_print("\n" + "-" * 80)
                debug_print(f"[BATCH {batch_id}] FALLBACK PASS | rows={len(primary_failed_rows)}")

                fallback_success_rows, fallback_failed_rows = await fallback_pass_for_batch(
                    crawler=crawler,
                    rows_batch=primary_failed_rows,
                    batch_id=batch_id,
                    started_total_iso=started_total_iso,
                    started_total_perf=started_total_perf,
                )

                for output_row in fallback_success_rows:
                    append_jsonl_row(OUTPUT_FILE, output_row)
                    success_count += 1

                for output_row in fallback_failed_rows:
                    append_jsonl_row(OUTPUT_FILE, output_row)
                    fail_count += 1
            else:
                debug_print(f"[BATCH {batch_id}] Fallback не нужен.")

    debug_print("\n" + "=" * 80)
    debug_print("=== ГОТОВО ===")
    debug_print(f"Успешно: {success_count}")
    debug_print(f"С ошибкой: {fail_count}")
    debug_print(f"Пропущено: {skipped_count}")
    debug_print(f"Файл: {OUTPUT_FILE}")
    debug_print(f"Общее время: {round(time.perf_counter() - started_total_perf, 3)} сек")


if __name__ == "__main__":
    asyncio.run(main())
