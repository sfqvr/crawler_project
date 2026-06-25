import asyncio
import os
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, LLMConfig, DefaultMarkdownGenerator
from crawl4ai.content_filter_strategy import LLMContentFilter
import litellm 

litellm._turn_on_debug()

async def main():
    my_llm_config = LLMConfig(
        provider="openai/qwen/qwen3.5-35b-a3b", 
        api_token="lm-studio",
        base_url="http://192.168.1.125:1234/v1"
    )

    # Инициализируем LLM фильтр с жесткой инструкцией для SRE/DevOps текстов
    llm_filter = LLMContentFilter(
        llm_config=my_llm_config,
        instruction="""
        You are an expert SRE and DevOps engineer. Your task is to extract the complete, unaltered text of an incident report, postmortem, or Root Cause Analysis (RCA) from the provided web page content.

        INCLUDE EXACTLY AS WRITTEN:
        - Incident timelines, chronologies, and timestamps.
        - Descriptions of customer impact, symptoms, and outage duration.
        - Root cause analysis (RCA) and deep technical explanations.
        - Code snippets, server logs, metrics, tracebacks, or configuration files related to the incident.
        - Mitigation steps, resolution, and future action items (lessons learned).

        EXCLUDE ENTIRELY:
        - Site navigation, menus, footers, headers, and cookie banners.
        - Marketing fluff, "subscribe to our newsletter" blocks, career pages, or unrelated blog post recommendations.
        - Author bios, comment sections, or generic corporate boilerplate.

        CRITICAL INSTRUCTION: Do NOT summarize, rephrase, or alter the original text. Preserve the original narrative flow. Return the relevant content using its original Markdown formatting, headers, and code blocks. If the entire chunk consists of irrelevant web junk, return an empty string.
        """,
        chunk_token_threshold=4096,  # Размер куска, который отправляется в LLM за раз
        verbose=True
    )

    # Передаем наш умный фильтр в генератор Markdown
    md_generator = DefaultMarkdownGenerator(
        content_filter=llm_filter,
        options={"ignore_links": True} # Ссылки из меню нам не нужны
    )
    
    config = CrawlerRunConfig(
        markdown_generator=md_generator,
    )

    print("Запускаем краулер с LLM-фильтрацией. Это займет чуть больше времени...")
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun("https://blog.allegro.tech/2018/08/postmortem-why-allegro-went-down.html", config=config)
        
        if result.success and result.markdown:
            final_text = result.markdown.fit_markdown
            filename = f"report.md"

            # 2. Сохраняем текст в файл
            with open(filename, "w", encoding="utf-8") as file:
                file.write(final_text)

            print(f"\n✅ Красота! Чистый Markdown успешно сохранен в файл: {filename}")
        else:
            print("Что-то пошло не так:", result.error_message)

if __name__ == "__main__":
    asyncio.run(main())