# 1_generate_seed_urls.py (НОВАЯ ВЕРСИЯ С БД)
import os
import asyncio
import json
import hashlib
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode, LLMConfig
from crawl4ai import LLMExtractionStrategy
from dotenv import load_dotenv
from minio_client import MinIOStorage

from src.connection import db

load_dotenv()

URL_TO_CRAWL = "https://github.com/jimmyl02/awesome-postmortems"
OUTPUT_FOLDER_NAME = "parsed_jimmyl02"
OUTPUT_FILENAMES_PREFIX = "jimmyl02_postmortems"

storage = MinIOStorage()
storage.create_bucket('raw-data')
storage.create_bucket('silver-data')

class PostMortemEntry(BaseModel):
    name: str = Field(..., description="The name of the company, service, or security threat")
    url: str = Field(..., description="Direct URL link to the incident report")
    description: str = Field(..., description="The context or description of the cause")


def compute_content_hash(data: list) -> str:
    urls_sorted = sorted([item.get('url', '') for item in data])
    content = "|".join(urls_sorted)
    return hashlib.sha256(content.encode()).hexdigest()


async def main():
    print("=" * 80)
    print("🚀 STAGE 1: Генерация seed URL (с PostgreSQL)")
    print("=" * 80)

    # 1. Подключаемся к БД
    db.connect()
    print("✅ Подключение к PostgreSQL установлено")

    # 2. Настраиваем LLM
    my_llm_config = LLMConfig(
        provider=f"openai/{os.getenv('OPENAI_MODEL', 'gpt-4o-mini')}",
        api_token=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL")
    )

    llm_strategy = LLMExtractionStrategy(
        llm_config=my_llm_config,
        schema=PostMortemEntry.model_json_schema(),
        extraction_type="schema",
        instruction="From the crawled content, extract postmortem incidents, along with titles, descriptions, and links to reports on these incidents. Do not miss any incident in the entire content.",
        chunk_token_threshold=1500,
        overlap_rate=0.1,
        apply_chunking=True,
        input_format="markdown",
    )

    crawl_config = CrawlerRunConfig(
        extraction_strategy=llm_strategy,
        cache_mode=CacheMode.BYPASS,
    )

    browser_cfg = BrowserConfig(headless=True)

    # 3. Краулим GitHub репозиторий
    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        print(f"🔄 Краулим: {URL_TO_CRAWL}")
        result = await crawler.arun(
            url=URL_TO_CRAWL,
            config=crawl_config
        )

        if not result.success:
            print(f"❌ Ошибка краулинга: {result.error_message}")
            db.close()
            return

        data = json.loads(result.extracted_content)
        print(f"✅ Извлечено {len(data)} записей")

        # 4. Вычисляем хэш текущего состояния репозитория
        current_hash = compute_content_hash(data)
        print(f"🔑 Хэш репозитория: {current_hash[:16]}...")

        # 5. Получаем сохраненный хэш из БД
        stored_hash = db.get_repo_hash(URL_TO_CRAWL)

        if stored_hash == current_hash:
            print("ℹ️ Репозиторий не изменился, новых URL нет")
            db.close()
            return

        # 6. Получаем уже обработанные URL из БД
        processed_urls = db.get_processed_urls()
        print(f"📊 Уже обработано URL в БД: {len(processed_urls)}")

        # 7. Фильтруем новые URL
        new_urls = []
        for item in data:
            url = item.get('url')
            if url not in processed_urls:
                new_urls.append(item)
                storage.append_json('raw-data',OUTPUT_FILENAMES_PREFIX,item)
            else:
                print(f"⏭️ URL уже есть в БД: {url}")

        print(f"🆕 Новых URL для обработки: {len(new_urls)}")

        # 8. Сохраняем новые URL в БД
        for item in new_urls:
            db.upsert_document_from_stage1({
                'url': item.get('url'),
                'name': item.get('name', ''),
                'description': item.get('description', ''),
                'error': False,
                'status': 'new'
            })
            print(f"✅ Добавлен в БД: {item.get('url')}")

        # 9. Обновляем хэш репозитория в БД
        db.update_repo_hash(URL_TO_CRAWL, current_hash)
        print(f"✅ Обновлен хэш репозитория")

        # 10. Сохраняем полный список в MinIO (бэкап)
        # storage.upload_jsonl('raw-data', f"{OUTPUT_FILENAMES_PREFIX}.jsonl", data)
        # print(f"💾 Полный список сохранен в MinIO: raw-data/{OUTPUT_FILENAMES_PREFIX}.jsonl")

        # 11. Статистика
        llm_strategy.show_usage()

        print("\n" + "=" * 80)
        print("=== STAGE 1 ЗАВЕРШЕН ===")
        print(f"✅ Новых URL в БД: {len(new_urls)}")
        print(f"📊 Всего URL в БД: {len(processed_urls) + len(new_urls)}")
        print("=" * 80)

        # 12. Закрываем соединение
        db.close()
        print("✅ Соединение с PostgreSQL закрыто")


if __name__ == "__main__":
    asyncio.run(main())