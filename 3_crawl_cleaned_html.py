# 3_crawl_cleaned_html.py (новое)

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

import pandas as pd
from crawl4ai import AsyncWebCrawler, CacheMode
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig
from minio_client import MinIOStorage

from src.db.connection import db
from minio_client import MinIOStorage

INPUT_FOLDER_NAME = "parsed_jimmyl02"
<<<<<<< HEAD
INPUT_FILENAMES_PREFIX = "jimmyl02_postmortems"
=======
INPUT_FILENAMES_PREFIX = "test" #"jimmyl02_postmortems"

storage = MinIOStorage()
# storage.client.fget_object(
#     bucket_name='raw-data',
#     object_name=f"{INPUT_FILENAMES_PREFIX}.jsonl",
#     file_path=f"{INPUT_FOLDER_NAME}/{INPUT_FILENAMES_PREFIX}.jsonl",
# )

# INPUT_FILE = Path(f"{INPUT_FOLDER_NAME}/{INPUT_FILENAMES_PREFIX}.jsonl")
OUTPUT_FILE = Path(f"{INPUT_FOLDER_NAME}/{INPUT_FILENAMES_PREFIX}_stage3.jsonl")

LIMIT_ROWS: Optional[int] = None
RESUME_FROM_OUTPUT = False
DEBUG = True
HEADLESS = True

# Параллельность внутри arun_many()
SEMAPHORE_COUNT = 3

# Размер батча URL, который отправляется в arun_many() за один проход
>>>>>>> 4c998e279da7a085c94046f9f4a10a76a5260ac7
BATCH_SIZE = 30
SEMAPHORE_COUNT = 3
HEADLESS = True
DEBUG = True

storage = MinIOStorage()

def debug_print(msg: str) -> None:
    if DEBUG:
        print(msg)

def now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()

async def process_single_url(crawler, row: dict) -> dict:
    url = row["url"]

    if db.is_stage_completed(url, 3):
        debug_print(f"⏭️ Stage 3 уже выполнен для {url}, пропускаем")
        return {"url": url, "skipped": True}

    db.update_status(url, "in_progress")
    
    debug_print(f"🔄 Начинаем краулинг: {url}")
    started_at = now_iso_utc()
    started_perf = time.perf_counter()
    
    try:
        config = build_single_run_config(url)
        result = await crawler.arun(url=url, config=config)
        
        crawl_ok, cleaned_html = validate_success(result)
        debug_info = {
            "cleaned_html": cleaned_html,
            "crawl_success": crawl_ok,
            "crawl_error_message": getattr(result, "error_message", "") or "",
            "crawl_error_primary": "",
            "crawl_error_fallback": "",
            "crawl_mode": "primary" if crawl_ok else "failed",
            "crawl_url_type": "WAYBACK" if "web.archive.org" in url else "NORMAL",
            "cleaned_html_length": len(cleaned_html) if cleaned_html else 0,
            "crawl_suspect": len(cleaned_html) < 1000 if cleaned_html else True,
            "crawl_attempt_count": 1,
            "crawl_primary_success": crawl_ok,
            "crawl_fallback_used": False,
            "crawl_started_at_utc": started_at,
            "crawl_finished_at_utc": now_iso_utc(),
            "crawl_elapsed_seconds": round(time.perf_counter() - started_perf, 3),
            "crawler_version_hint": "Crawl4AI 0.8.0",
            "debug_requested_url": url,
            "debug_result_url": getattr(result, "url", url),
            "debug_status_code": getattr(result, "status_code", None),
            "debug_match_method": "sequential",
            "debug_batch_id": None,
        }
        
        db.update_stage3_result(url, debug_info)
        db.mark_stage3_completed(url, crawl_ok, debug_info.get("crawl_error_message"))
        
        if crawl_ok:
            debug_print(f"✅ Stage 3 успешно завершен для {url}, длина HTML: {len(cleaned_html)}")
        else:
            debug_print(f"❌ Stage 3 завершен с ошибкой для {url}: {debug_info['crawl_error_message']}")
        
        return {"url": url, "success": crawl_ok, "skipped": False}
        
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        debug_print(f"❌ Критическая ошибка при краулинге {url}: {error_msg}")
        
        db.update_status(url, "error", error_msg)
        return {"url": url, "success": False, "skipped": False, "error": error_msg}

async def main():
    db.connect()

    urls_to_process = db.get_urls_needing_stage(3)
    debug_print(f"📊 Найдено {len(urls_to_process)} URL для Stage 3")
    
    if not urls_to_process:
        debug_print("ℹ️ Нет URL для обработки на Stage 3")
        db.close()
        return

    rows_to_process = []
    for url in urls_to_process:
        doc = db.get_document_by_url(url)
        if doc:
            rows_to_process.append({
                "url": url,
                "name": doc.get("name", ""),
                "description": doc.get("description", ""),
                "error": doc.get("error", False)
            })

    browser_config = BrowserConfig(
        browser_type="chromium",
        headless=HEADLESS,
        verbose=DEBUG,
        viewport_width=1400,
        viewport_height=900,
    )
<<<<<<< HEAD
    
=======


def build_normal_config(fallback: bool = False) -> CrawlerRunConfig:
    return CrawlerRunConfig(
        url_matcher=lambda url: "web.archive.org" not in url,
        cache_mode=CacheMode.BYPASS,
        wait_until="load" if not fallback else "domcontentloaded",
        page_timeout=60000 if not fallback else 90000,
        wait_for_timeout=30000 if not fallback else 45000,
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
        stream=False,
        semaphore_count=SEMAPHORE_COUNT,
    )


def build_wayback_config(fallback: bool = False) -> CrawlerRunConfig:
    return CrawlerRunConfig(
        url_matcher=lambda url: "web.archive.org" in url,
        cache_mode=CacheMode.BYPASS,
        wait_until="domcontentloaded",
        page_timeout=90000 if not fallback else 120000,
        wait_for_timeout=45000 if not fallback else 60000,
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
        semaphore_count=SEMAPHORE_COUNT,
    )


def build_config_list(fallback: bool = False) -> list[CrawlerRunConfig]:
    return [
        build_wayback_config(fallback=fallback),
        build_normal_config(fallback=fallback),
    ]


def build_single_run_config(url: str, fallback: bool = False) -> CrawlerRunConfig:
    if is_wayback_url(url):
        return build_wayback_config(fallback=fallback)
    return build_normal_config(fallback=fallback)


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
        "crawl_mode": "str",  # primary | fallback | failed
        "crawl_url_type": "str",  # WAYBACK | NORMAL
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
        "debug_match_method": "str",  # batch_url_match | sequential_retry | sequential_retry_exception
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

    configs = build_config_list(fallback=fallback)
    return await crawler.arun_many(
        urls=urls,
        config=configs,
    )


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
        "crawler_version_hint": "Crawl4AI 0.8.0",
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
        "crawler_version_hint": "Crawl4AI 0.8.0",
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
    """
    Пытаемся надёжно сматчить batch-результаты с исходными строками по result.url.
    Всё, что не сматчилось, уходит в sequential retry.
    """
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
    if len(sys.argv) < 2:
        print("Usage: script1.py <json_string>")
        sys.exit(1)

    
    data = json.loads(sys.argv[1])
        

    ensure_parent_dir(OUTPUT_FILE)
    print_output_schema()

    # df = load_input_df(INPUT_FILE)
    # df = storage.load_dataframe('raw-data', INPUT_FILENAMES_PREFIX)
    # df = pd.read_json(sys.argv[1])

    # if LIMIT_ROWS is not None:
    #     df = df.head(LIMIT_ROWS).copy()

    # debug_print(f"[INFO] Всего строк во входном файле: {len(df)}")
    debug_print(f"[INFO] Параллельность arun_many (semaphore_count): {SEMAPHORE_COUNT}")
    debug_print(f"[INFO] Размер batch: {BATCH_SIZE}")

    processed_urls = set()
    if RESUME_FROM_OUTPUT:
        processed_urls = load_processed_urls(OUTPUT_FILE)
        debug_print(f"[INFO] Уже обработано URL в output: {len(processed_urls)}")

    rows_to_process = [data] 
    skipped_count = 0

    # for source_index, row in df.iterrows():
    #     row_dict = {
    #         "source_index": int(source_index),
    #         "name": row["name"],
    #         "url": row["url"],
    #         "description": row["description"],
    #         "error": bool(row["error"]),
    #     }

    #     if RESUME_FROM_OUTPUT and row_dict["url"] in processed_urls:
    #         skipped_count += 1
    #         debug_print(f"[SKIP] Уже есть в output: {row_dict['url']}")
    #         continue

    #     rows_to_process.append(row_dict)

    debug_print(f"[INFO] К обработке осталось: {len(rows_to_process)}")
    debug_print(f"[INFO] Пропущено по resume: {skipped_count}")

    if not rows_to_process:
        debug_print("[INFO] Обрабатывать нечего.")
        return

>>>>>>> 4c998e279da7a085c94046f9f4a10a76a5260ac7
    success_count = 0
    fail_count = 0
    skipped_count = 0
    
    async with AsyncWebCrawler(config=browser_config) as crawler:
        for batch_id, rows_batch in chunked(rows_to_process, BATCH_SIZE):
<<<<<<< HEAD
            debug_print(f"\n📦 Batch {batch_id}: {len(rows_batch)} URL")
            
            # Обрабатываем каждый URL в batch
            for row in rows_batch:
                result = await process_single_url(crawler, row)
                
                if result.get("skipped"):
                    skipped_count += 1
                elif result.get("success"):
                    success_count += 1
                else:
                    fail_count += 1
    
    # 5. Итоги
    debug_print("\n" + "=" * 80)
    debug_print("=== STAGE 3 ЗАВЕРШЕН ===")
    debug_print(f"✅ Успешно: {success_count}")
    debug_print(f"❌ С ошибкой: {fail_count}")
    debug_print(f"⏭️ Пропущено (уже обработано): {skipped_count}")
    debug_print("=" * 80)
    
    db.close()
=======
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
                # append_jsonl_row(OUTPUT_FILE, output_row)
                storage.append_html('raw-data', INPUT_FILENAMES_PREFIX, output_row["url"], output_row["cleaned_html"])
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
                    # append_jsonl_row(OUTPUT_FILE, output_row)
                    storage.append_html('raw-data', INPUT_FILENAMES_PREFIX, output_row["url"], output_row["cleaned_html"])
                    success_count += 1

                for output_row in fallback_failed_rows:
                    # append_jsonl_row(OUTPUT_FILE, output_row)
                    storage.append_html('raw-data', INPUT_FILENAMES_PREFIX, output_row["url"], output_row["cleaned_html"])

                    fail_count += 1
            else:
                debug_print(f"[BATCH {batch_id}] Fallback не нужен.")

    # storage.client.fput_object(
    #     bucket_name='raw-data',
    #     object_name=f"{INPUT_FILENAMES_PREFIX}_stage3.jsonl",
    #     file_path=OUTPUT_FILE,
    # )

    debug_print("\n" + "=" * 80)
    debug_print("=== ГОТОВО ===")
    debug_print(f"Успешно: {success_count}")
    debug_print(f"С ошибкой: {fail_count}")
    debug_print(f"Пропущено: {skipped_count}")
    # debug_print(f"Файл: {OUTPUT_FILE}")
    debug_print(f"Общее время: {round(time.perf_counter() - started_total_perf, 3)} сек")

>>>>>>> 4c998e279da7a085c94046f9f4a10a76a5260ac7

if __name__ == "__main__":
    asyncio.run(main())