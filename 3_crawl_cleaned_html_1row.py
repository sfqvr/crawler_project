import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from typing import Optional

from crawl4ai import AsyncWebCrawler, CacheMode
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig
from minio_client import MinIOStorage


INPUT_FOLDER_NAME = "parsed_jimmyl02"
INPUT_FILENAMES_PREFIX = "test"  # "jimmyl02_postmortems"

storage = MinIOStorage()

DEBUG = True
HEADLESS = True

# Kept for parity with the original crawler configuration (unused for a single
# arun() call, but harmless to leave in place in case batching is reintroduced).
SEMAPHORE_COUNT = 3


def debug_print(msg: str) -> None:
    if DEBUG:
        print(msg)


def is_wayback_url(url: str) -> bool:
    return "web.archive.org" in url


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_browser_config() -> BrowserConfig:
    return BrowserConfig(
        browser_type="chromium",
        headless=HEADLESS,
        verbose=DEBUG,
        viewport_width=1400,
        viewport_height=900,
    )


def build_normal_config(fallback: bool = False) -> CrawlerRunConfig:
    return CrawlerRunConfig(
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
    )


def build_wayback_config(fallback: bool = False) -> CrawlerRunConfig:
    return CrawlerRunConfig(
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
    )


def build_single_run_config(url: str, fallback: bool = False) -> CrawlerRunConfig:
    if is_wayback_url(url):
        return build_wayback_config(fallback=fallback)
    return build_normal_config(fallback=fallback)


def validate_success(result) -> tuple[bool, str]:
    if not getattr(result, "success", False):
        return False, ""

    cleaned_html = getattr(result, "cleaned_html", "") or ""
    if not cleaned_html.strip():
        return False, ""

    if len(cleaned_html) < 500:
        return False, cleaned_html

    return True, cleaned_html


async def run_single(crawler: AsyncWebCrawler, url: str, fallback: bool = False):
    config = build_single_run_config(url=url, fallback=fallback)
    return await crawler.arun(url=url, config=config)


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
    }

    print("=" * 80)
    print("=== OUTPUT ROW SCHEMA ===")
    for key, value in schema.items():
        print(f"{key}: {value}")
    print("=" * 80)


def build_output_row(
    row: dict,
    result,
    cleaned_html: str,
    success: bool,
    mode: str,
    primary_error: str,
    fallback_error: str,
    started_iso: str,
    started_perf: float,
) -> dict:
    html_length = len(cleaned_html)
    requested_url = row["url"]

    return {
        "name": row.get("name", ""),
        "url": requested_url,
        "description": row.get("description", ""),
        "error": row.get("error", False),
        "cleaned_html": cleaned_html,
        "crawl_success": success,
        "crawl_error_message": (
            "" if success else f"primary: {primary_error} | fallback: {fallback_error}"
        ),
        "crawl_error_primary": primary_error,
        "crawl_error_fallback": fallback_error,
        "crawl_mode": mode,
        "crawl_url_type": "WAYBACK" if is_wayback_url(requested_url) else "NORMAL",
        "cleaned_html_length": html_length,
        "crawl_suspect": (not success) or html_length < 1000,
        "crawl_attempt_count": 1 if mode == "primary" else 2,
        "crawl_primary_success": mode == "primary",
        "crawl_fallback_used": mode != "primary",
        "crawl_started_at_utc": started_iso,
        "crawl_finished_at_utc": now_iso_utc(),
        "crawl_elapsed_seconds": round(time.perf_counter() - started_perf, 3),
        "crawler_version_hint": "Crawl4AI 0.8.0",
        "debug_requested_url": requested_url,
        "debug_result_url": getattr(result, "url", requested_url) if result is not None else requested_url,
        "debug_status_code": getattr(result, "status_code", None) if result is not None else None,
    }


async def process_row(
    crawler: AsyncWebCrawler,
    row: dict,
    started_iso: str,
    started_perf: float,
) -> dict:
    """
    Runs the primary crawl attempt for a single row, falling back to a slower/
    more lenient config if the primary attempt fails or returns too little HTML.
    """
    url = row["url"]

    # --- Primary attempt ---
    result = None
    try:
        result = await run_single(crawler, url, fallback=False)
        crawl_ok, cleaned_html = validate_success(result)
        primary_exc_error: Optional[str] = None
    except Exception as e:
        crawl_ok = False
        cleaned_html = ""
        primary_exc_error = f"{type(e).__name__}: {e}"

    if crawl_ok:
        debug_print(f"[OK][PRIMARY] {url} | cleaned_html length={len(cleaned_html)}")
        return build_output_row(
            row=row,
            result=result,
            cleaned_html=cleaned_html,
            success=True,
            mode="primary",
            primary_error="",
            fallback_error="",
            started_iso=started_iso,
            started_perf=started_perf,
        )

    primary_error = (
        primary_exc_error
        or (getattr(result, "error_message", "") if result is not None else "")
        or "Primary crawl failed or HTML too short"
    )
    debug_print(f"[FAIL][PRIMARY] {url} | {primary_error}")

    # --- Fallback attempt ---
    debug_print(f"[RETRY][FALLBACK] {url}")
    fb_result = None
    try:
        fb_result = await run_single(crawler, url, fallback=True)
        fb_ok, fb_html = validate_success(fb_result)
        fallback_exc_error: Optional[str] = None
    except Exception as e:
        fb_ok = False
        fb_html = ""
        fallback_exc_error = f"{type(e).__name__}: {e}"

    if fb_ok:
        debug_print(f"[OK][FALLBACK] {url} | cleaned_html length={len(fb_html)}")
        return build_output_row(
            row=row,
            result=fb_result,
            cleaned_html=fb_html,
            success=True,
            mode="fallback",
            primary_error=primary_error,
            fallback_error="",
            started_iso=started_iso,
            started_perf=started_perf,
        )

    fallback_error = (
        fallback_exc_error
        or (getattr(fb_result, "error_message", "") if fb_result is not None else "")
        or "Fallback crawl failed or HTML too short"
    )
    debug_print(f"[FAIL][FALLBACK] {url} | {fallback_error}")

    return build_output_row(
        row=row,
        result=fb_result or result,
        cleaned_html="",
        success=False,
        mode="failed",
        primary_error=primary_error,
        fallback_error=fallback_error,
        started_iso=started_iso,
        started_perf=started_perf,
    )


async def main():
    if len(sys.argv) < 2:
        print("Usage: 3_crawl_cleaned_html_1row.py <json_string>")
        sys.exit(1)

    row: dict = json.loads(sys.argv[1])

    if "url" not in row:
        print("Error: input JSON must contain a 'url' field")
        sys.exit(1)

    print_output_schema()
    debug_print(f"[INFO] Обрабатываем одну запись: {row.get('url')}")

    browser_config = build_browser_config()
    started_perf = time.perf_counter()
    started_iso = now_iso_utc()

    async with AsyncWebCrawler(config=browser_config) as crawler:
        output_row = await process_row(
            crawler=crawler,
            row=row,
            started_iso=started_iso,
            started_perf=started_perf,
        )

    storage.append_html(
        "raw-data",
        INPUT_FILENAMES_PREFIX,
        output_row["url"],
        output_row["cleaned_html"],
    )

    debug_print("\n" + "=" * 80)
    debug_print("=== ГОТОВО ===")
    debug_print(f"Успех: {output_row['crawl_success']}")
    debug_print(f"Режим: {output_row['crawl_mode']}")
    if not output_row["crawl_success"]:
        debug_print(f"Ошибка: {output_row['crawl_error_message']}")
    debug_print(f"Общее время: {round(time.perf_counter() - started_perf, 3)} сек")


if __name__ == "__main__":
    asyncio.run(main())
