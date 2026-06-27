import subprocess
import sys
from pathlib import Path
import os

from prefect import flow, task
from prefect.logging import get_run_logger
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
dotenv_path = PROJECT_DIR / "my_crawler_project" / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path=dotenv_path)
else:
    load_dotenv()

LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

SCRIPTS = {
    "1_generate_seed_urls": PROJECT_DIR / "1_generate_seed_urls.py",
    "2_validate_seed_dataset": PROJECT_DIR / "2_validate_seed_dataset.py",
    "3_crawl_cleaned_html": PROJECT_DIR / "3_crawl_cleaned_html.py",
    "4_llm_filter_relevance": PROJECT_DIR / "4_llm_filter_relevance.py",
}

PYTHON = Path(sys.executable)


def _run_script(script_name: str, script_path: Path):
    logger = get_run_logger()
    log_path = LOG_DIR / f"{script_name}.log"
    logger.info(f"Запуск: {script_name}")

    try:
        with open(log_path, "a", encoding="utf-8") as log_f:
            result = subprocess.run(
                [str(PYTHON), str(script_path)],
                cwd=str(PROJECT_DIR),
                stdout=log_f,
                stderr=subprocess.STDOUT,
                timeout=3600,
                env={
                    **os.environ,
                    'PYTHONIOENCODING': 'utf-8',
                },
            )

        logger.info(f"{script_name}: exit code {result.returncode}")

        if result.returncode != 0:
            raise RuntimeError(f"Скрипт {script_name} упал с кодом {result.returncode}")

        logger.info(f"Завершён: {script_name}")

    except subprocess.TimeoutExpired:
        logger.error(f"Таймаут скрипта {script_name} (>1 часа)")
        raise


@task(
    name="1_generate_seed_urls",
    retries=2,
    retry_delay_seconds=30,
    tags=["etl", "seed"],
)
def task_generate_seed_urls():
    _run_script("1_generate_seed_urls", SCRIPTS["1_generate_seed_urls"])


@task(
    name="2_validate_seed_dataset",
    retries=1,
    retry_delay_seconds=10,
    tags=["etl", "validation"],
)
def task_validate_seed_dataset():
    _run_script("2_validate_seed_dataset", SCRIPTS["2_validate_seed_dataset"])


@task(
    name="3_crawl_cleaned_html",
    retries=2,
    retry_delay_seconds=60,
    tags=["etl", "crawl"],
)
def task_crawl_cleaned_html():
    _run_script("3_crawl_cleaned_html", SCRIPTS["3_crawl_cleaned_html"])


@task(
    name="4_llm_filter_relevance",
    retries=3,
    retry_delay_seconds=120,
    tags=["etl", "llm"],
)
def task_llm_filter_relevance():
    _run_script("4_llm_filter_relevance", SCRIPTS["4_llm_filter_relevance"])


@flow(name="Prefect Pipeline", log_prints=True)
def etl_pipeline():
    logger = get_run_logger()
    logger.info("Запуск пайплайна")

    result_1 = task_generate_seed_urls()
    result_2 = task_validate_seed_dataset(wait_for=[result_1])
    result_3 = task_crawl_cleaned_html(wait_for=[result_2])
    task_llm_filter_relevance(wait_for=[result_3])

    logger.info("Пайплайн завершён")


if __name__ == "__main__":
    etl_pipeline()
