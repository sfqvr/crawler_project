# 3_crawl_cleaned_html.py (новое)

import asyncio
import json
import time
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

import pandas as pd
from crawl4ai import AsyncWebCrawler, CacheMode
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig

from src.db.connection import db
from minio_client import MinIOStorage

INPUT_FOLDER_NAME = "parsed_jimmyl02"
INPUT_FILENAMES_PREFIX = "jimmyl02_postmortems"
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
    
    success_count = 0
    fail_count = 0
    skipped_count = 0
    
    async with AsyncWebCrawler(config=browser_config) as crawler:
        for batch_id, rows_batch in chunked(rows_to_process, BATCH_SIZE):
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

if __name__ == "__main__":
    asyncio.run(main())