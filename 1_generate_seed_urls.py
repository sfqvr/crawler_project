import os
import asyncio
import json
from pydantic import BaseModel, Field
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode, LLMConfig
from crawl4ai import LLMExtractionStrategy
import litellm
from my_export import export_data
from dotenv import load_dotenv

load_dotenv()

# 0. Входные данные
URL_TO_CRAWL = "https://github.com/jimmyl02/awesome-postmortems" 
OUTPUT_FOLDER_NAME = "parsed_jimmyl02"
OUTPUT_FILENAMES_PREFIX = "jimmyl02_postmortems"

# litellm._turn_on_debug()

# 1. Модель.
class PostMortemEntry(BaseModel):
    name: str = Field(..., description="The name of the company, service, or security threat (usually the text of the link itself)")
    url: str = Field(..., description="Direct URL link to the incident report (post-mortem)")
    description: str = Field(..., description="The context or description of the cause of the crash or problem, which is written next to the link")

async def main():
    # 2. Настраиваем подключение к локальной LMStudio
    my_llm_config = LLMConfig(
        provider=f"openai/{os.getenv("OPENAI_MODEL", "gpt-4o-mini")}", 
        api_token=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL")
    )

    # 3. Обновляем стратегию извлечения
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

    # 4. Настраиваем краулер
    crawl_config = CrawlerRunConfig(
        extraction_strategy=llm_strategy,
        cache_mode=CacheMode.BYPASS,
    )

    browser_cfg = BrowserConfig(headless=True)

    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        print("🚀 Запускаем краулер и отправляем данные в локальную LLM...")
        
        # 5. Меняем URL на нужный репозиторий
        result = await crawler.arun(
            url=URL_TO_CRAWL,
            config=crawl_config
        )

        if result.success:
            data = json.loads(result.extracted_content)
            print(f"\n✅ Успешно извлечено {len(data)} записей!\n")
                
            for item in data[:3]:
                print(item)

            export_data(
                data=data, 
                folder_name=OUTPUT_FOLDER_NAME,
                jsonl_filename=f"{OUTPUT_FILENAMES_PREFIX}.jsonl",
                html_filename=f"{OUTPUT_FILENAMES_PREFIX}.html"
            )
                    
            llm_strategy.show_usage()
        else:
            print("Error:", result.error_message)

if __name__ == "__main__":
    asyncio.run(main())