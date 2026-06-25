import asyncio
from crawl4ai import AsyncWebCrawler
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig

import os
import json

def export_raw_text_file(filename, data):
    with open(filename, "w", encoding="utf-8") as file:
        file.write(data)

    print(f"\n[export_raw_text file]: Content saved to {filename}")

URL_TO_CRAWL = "https://web.archive.org/web/20211016040522/https://blog.cloudflare.com/cloudflare-outage-on-july-17-2020/"

async def main():
    browser_config = BrowserConfig()
    run_config = CrawlerRunConfig()

    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(
            url=URL_TO_CRAWL,
            config=run_config
        )
        # print(result.markdown)

        if result.success:
            export_raw_text_file(
                filename = "report.html",
                data = result.cleaned_html,
            )

if __name__ == "__main__":
    asyncio.run(main())
