import subprocess
import logging
from pathlib import Path
from datetime import datetime

from prefect import flow, task
from prefect.logging import get_run_logger

PROJECT_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_DIR / "logs"

LOG_DIR.mkdir(exist_ok=True)

SCRIPTS = {
    "1_generate_seed_urls": PROJECT_DIR / "1_generate_seed_urls.py",
    "2_validate_seed_dataset": PROJECT_DIR / "2_validate_seed_dataset.py",
    "3_crawl_cleaned_html": PROJECT_DIR / "3_crawl_cleaned_html.py",
    "4_llm_filter_relevance": PROJECT_DIR / "4_llm_filter_relevance.py",
}

PYTHON = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"


def _get_step_logger(step_name: str):
    logger = logging.getLogger(f"step.{step_name}")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        log_path = LOG_DIR / f"{step_name}.log"
        handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


PIPELINE_LOG_PATH = LOG_DIR / "pipeline.log"


def _log_pipeline(message: str):
    with open(PIPELINE_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} | {message}\n")


def _run_script(script_name: str, script_path: Path):
    logger = get_run_logger()
    step_logger = _get_step_logger(script_name)

    step_logger.info("=" * 50)
    step_logger.info("ЗАПУСК")
    logger.info(f"Запуск: {script_name}")

    try:
        result = subprocess.run(
            [str(PYTHON), str(script_path)],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=3600,
        )

        step_logger.info(f"Exit code: {result.returncode}")

        if result.stdout:
            step_logger.info(f"STDOUT:\n{result.stdout[:3000]}")
            logger.info(f"STDOUT ({script_name}):\n{result.stdout[:2000]}")

        if result.returncode != 0:
            logger.error(f"Ошибка в {script_name} (exit code {result.returncode})")
            step_logger.error(f"ОШИБКА (exit code {result.returncode})")
            if result.stderr:
                logger.error(f"STDERR ({script_name}):\n{result.stderr[:2000]}")
                step_logger.error(f"STDERR:\n{result.stderr[:3000]}")
            raise RuntimeError(f"Скрипт {script_name} упал с кодом {result.returncode}")

        if result.stderr:
            logger.warning(f"STDERR ({script_name}):\n{result.stderr[:1000]}")
            step_logger.warning(f"STDERR:\n{result.stderr[:1000]}")

        step_logger.info("ЗАВЕРШЁН УСПЕШНО")
        step_logger.info("=" * 50)
        logger.info(f"Завершён: {script_name}")

    except subprocess.TimeoutExpired:
        logger.error(f"Таймаут скрипта {script_name} (>1 часа)")
        step_logger.error("ТАЙМАУТ (>1 часа)")
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


@flow(name="Pipeline — Steps 1-4", log_prints=True)
def etl_pipeline():
    logger = get_run_logger()
    logger.info("Запуск пайплайна: шаги 1-4")
    _log_pipeline("ЗАПУСК пайплайна: шаги 1-4")

    result_1 = task_generate_seed_urls()
    result_2 = task_validate_seed_dataset(wait_for=[result_1])
    result_3 = task_crawl_cleaned_html(wait_for=[result_2])
    task_llm_filter_relevance(wait_for=[result_3])

    logger.info("Пайплайн завершён")
    _log_pipeline("ЗАВЕРШЁН: пайплайн шагов 1-4")


if __name__ == "__main__":
    etl_pipeline()
