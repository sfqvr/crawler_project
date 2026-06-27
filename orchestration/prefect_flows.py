import subprocess
import sys
import logging
from pathlib import Path
from datetime import datetime
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

PIPELINE_LOG_PATH = LOG_DIR / "pipeline.log"


def _log_pipeline(message: str):
    with open(PIPELINE_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} | {message}\n")

SCRIPTS = {
    "1_generate_seed_urls": PROJECT_DIR / "1_generate_seed_urls.py",
    "2_validate_seed_dataset": PROJECT_DIR / "2_validate_seed_dataset.py",
    "3_crawl_cleaned_html": PROJECT_DIR / "3_crawl_cleaned_html.py",
    "4_llm_filter_relevance": PROJECT_DIR / "4_llm_filter_relevance.py",
}

PYTHON = Path(sys.executable)


def _step_log(script_name: str, message: str):
    log_path = LOG_DIR / f"{script_name}.log"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} | {message}\n")


def _run_script(script_name: str, script_path: Path):
    logger = get_run_logger()
    log_path = LOG_DIR / f"{script_name}.log"

    _step_log(script_name, "=" * 50)
    _step_log(script_name, "ЗАПУСК")
    logger.info(f"Запуск: {script_name}")

    try:
        with open(log_path, "a", encoding="utf-8") as log_f:
            process = subprocess.Popen(
                [str(PYTHON), str(script_path)],
                cwd=str(PROJECT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env={
                    **os.environ,
                    'PYTHONIOENCODING': 'utf-8',
                },
                bufsize=1,
                text=True,
                encoding='utf-8',
            )

            while True:
                line = process.stdout.readline()
                if not line:
                    break
                line = line.rstrip()
                log_f.write(line + "\n")
                log_f.flush()
                if line.startswith("[OK]") or line.startswith("[FAIL]") or "BATCH" in line or "=== ГОТОВО" in line or line.startswith("[INFO]"):
                    logger.info(f"[{script_name}] {line}")

            process.wait(timeout=3600)

        _step_log(script_name, f"Exit code: {process.returncode}")
        logger.info(f"{script_name}: exit code {process.returncode}")

        if process.returncode != 0:
            _step_log(script_name, f"ОШИБКА (exit code {process.returncode})")
            raise RuntimeError(f"Скрипт {script_name} упал с кодом {process.returncode}")

        _step_log(script_name, "ЗАВЕРШЁН УСПЕШНО")
        _step_log(script_name, "=" * 50)
        logger.info(f"Завершён: {script_name}")

    except subprocess.TimeoutExpired:
        logger.error(f"Таймаут скрипта {script_name} (>1 часа)")
        _step_log(script_name, "ТАЙМАУТ (>1 часа)")
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
