import asyncio

from prefect import flow, task
import subprocess
import sys

from generate_seed_urls_1 import main as generate_seed_urls_1
from validate_seed_dataset_2 import main as validate_seed_dataset_2
from crawl_cleaned_html_3 import main as crawl_cleaned_html_3
from llm_filter_relevance_4 import main as llm_filter_relevance_4
from html_to_markdown_5 import main as html_to_markdown_5
from extract_metadata_6 import main as extract_metadata_6
from prepare_qdrant_dataset_7 import main as prepare_qdrant_dataset_7
from upload_to_qdrant_8 import main as upload_to_qdrant_8

# @task(retries=2, retry_delay_seconds=10)   # автоматические повторы при ошибке
# def run_script(script_name: str):
#     result = subprocess.run(
#         [sys.executable, script_name],
#         capture_output=True,
#         text=True
#     )
#     if result.returncode != 0:
#         raise RuntimeError(f"Ошибка в {script_name}:\n{result.stderr}")
#     print(result.stdout)

@flow
async def my_pipeline():
    # await generate_seed_urls_1()
    # validate_seed_dataset_2()
    # await crawl_cleaned_html_3()
    # await llm_filter_relevance_4()
    await html_to_markdown_5()
    await extract_metadata_6()
    prepare_qdrant_dataset_7()
    upload_to_qdrant_8()

if __name__ == "__main__":
    asyncio.run(my_pipeline())