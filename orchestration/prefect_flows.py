import subprocess
from pathlib import Path

from prefect import flow, task
from prefect.logging import get_run_logger

PROJECT_DIR = Path(__file__).resolve().parent.parent

SCRIPTS = {
    "1_generate_seed_urls": PROJECT_DIR / "1_generate_seed_urls.py",
    "2_validate_seed_dataset": PROJECT_DIR / "2_validate_seed_dataset.py",
    "3_crawl_cleaned_html": PROJECT_DIR / "3_crawl_cleaned_html.py",
    "4_llm_filter_relevance": PROJECT_DIR / "4_llm_filter_relevance.py",
}

PYTHON = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"


def _run_script(script_name: str, script_path: Path) -> None:
    logger = get_run_logger()
    logger.info(f"Запуск: {script_name}")

    try:
        result = subprocess.run(
            [str(PYTHON), str(script_path)],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=3600,
        )

        if result.stdout:
            logger.info(f"STDOUT ({script_name}):\n{result.stdout[:2000]}")

        if result.returncode != 0:
            logger.error(f"Ошибка в {script_name} (exit code {result.returncode})")
            if result.stderr:
                logger.error(f"STDERR ({script_name}):\n{result.stderr[:2000]}")
            raise RuntimeError(f"Скрипт {script_name} упал с кодом {result.returncode}")

        if result.stderr:
            logger.warning(f"STDERR ({script_name}):\n{result.stderr[:1000]}")

        logger.info(f" Завершён: {script_name}")
    except subprocess.TimeoutExpired:
        logger.error(f"Таймаут скрипта {script_name} (>1 часа)")
        raise


@task(
    name="1_generate_seed_urls",
    retries=2,
    retry_delay_seconds=30,
    tags=["etl", "seed"],
)
def task_generate_seed_urls() -> None:
    _run_script("1_generate_seed_urls", SCRIPTS["1_generate_seed_urls"])


@task(
    name="2_validate_seed_dataset",
    retries=1,
    retry_delay_seconds=10,
    tags=["etl", "validation"],
)
def task_validate_seed_dataset() -> None:
    _run_script("2_validate_seed_dataset", SCRIPTS["2_validate_seed_dataset"])


@task(
    name="3_crawl_cleaned_html",
    retries=2,
    retry_delay_seconds=60,
    tags=["etl", "crawl"],
)
def task_crawl_cleaned_html() -> None:
    _run_script("3_crawl_cleaned_html", SCRIPTS["3_crawl_cleaned_html"])


@task(
    name="4_llm_filter_relevance",
    retries=3,
    retry_delay_seconds=120,
    tags=["etl", "llm"],
)
def task_llm_filter_relevance() -> None:
    _run_script("4_llm_filter_relevance", SCRIPTS["4_llm_filter_relevance"])


@flow(name="Pipeline — Steps 1-4", log_prints=True)
def etl_pipeline() -> None:
    logger = get_run_logger()
    logger.info("Запуск пайплайна: шаги 1-4")

    result_1 = task_generate_seed_urls()
    result_2 = task_validate_seed_dataset(wait_for=[result_1])
    result_3 = task_crawl_cleaned_html(wait_for=[result_2])
    task_llm_filter_relevance(wait_for=[result_3])

    logger.info("Пайплайн завершён")


if __name__ == "__main__":
    etl_pipeline()
