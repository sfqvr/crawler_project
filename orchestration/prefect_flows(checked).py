import subprocess
import threading
import logging
import os
from pathlib import Path
from datetime import datetime

from prefect import flow, task
from prefect.logging import get_run_logger

PROJECT_DIR = Path(__file__).resolve().parent / "my_crawler_project"
LOG_DIR = PROJECT_DIR / "logs"

LOG_DIR.mkdir(exist_ok=True)

SCRIPTS = {
    "1_generate_seed_urls": PROJECT_DIR / "1_generate_seed_urls.py",
    "2_validate_seed_dataset": PROJECT_DIR / "2_validate_seed_dataset.py",
    "3_crawl_cleaned_html": PROJECT_DIR / "3_crawl_cleaned_html.py",
    "4_llm_filter_relevance": PROJECT_DIR / "4_llm_filter_relevance.py",
    "5_html_to_markdown": PROJECT_DIR / "5_html_to_markdown.py",
    "6_extract_metadata": PROJECT_DIR / "6_extract_metadata.py",
    "7_prepare_qdrant_dataset": PROJECT_DIR / "7_prepare_qdrant_dataset.py",
    "8_upload_to_qdrant": PROJECT_DIR / "8_upload_to_qdrant.py"
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
    log_path = LOG_DIR / f"{script_name}.log"

    step_logger.info("=" * 50)
    step_logger.info("ЗАПУСК")
    logger.info(f"Запуск: {script_name}")

    process = subprocess.Popen(
        [str(PYTHON), str(script_path)],
        cwd=str(PROJECT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )

    stop_reader = threading.Event()

    def reader_thread():
        with open(log_path, "a", encoding="utf-8") as log_f:
            while not stop_reader.is_set():
                try:
                    line = process.stdout.readline()
                    if not line:
                        break
                    line = line.rstrip()
                    log_f.write(line + "\n")
                    log_f.flush()
                    step_logger.info(line)
                    logger.info(f"[{script_name}] {line}")
                except Exception:
                    break

    reader = threading.Thread(target=reader_thread, daemon=True)
    reader.start()

    try:
        exit_code = process.wait(timeout=3600)
        logger.info(f"[{script_name}] exit code: {exit_code}")

        if exit_code != 0:
            raise RuntimeError(f"Скрипт {script_name} упал с кодом {exit_code}")

        step_logger.info("ЗАВЕРШЁН УСПЕШНО")
        step_logger.info("=" * 50)
        logger.info(f"Завершён: {script_name}")

    except subprocess.TimeoutExpired:
        logger.error(f"[{script_name}] Таймаут (>1 часа) — принудительное убийство")
        process.kill()
        process.wait(timeout=10)
        raise RuntimeError(f"Скрипт {script_name} превысил таймаут 1 час и был убит")

    finally:
        stop_reader.set()
        reader.join(timeout=5)
        if process.stdout:
            process.stdout.close()


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

#-------------------------
@task(
    name="5_html_to_markdown",
    retries=2,
    retry_delay_seconds=120,
    tags=["etl", "markdown"],
)
def task_html_to_markdown():
    _run_script("5_html_to_markdown", SCRIPTS["5_html_to_markdown"])


@task(
    name="6_extract_metadata",
    retries=2,
    retry_delay_seconds=120,
    tags=["etl", "metadata"],
)
def task_extract_metadata():
    _run_script("6_extract_metadata", SCRIPTS["6_extract_metadata"])


@task(
    name="7_prepare_qdrant_dataset",
    retries=1,
    retry_delay_seconds=10,
    tags=["etl", "prepare"],
)
def task_prepare_qdrant_dataset():
    _run_script("7_prepare_qdrant_dataset", SCRIPTS["7_prepare_qdrant_dataset"])


@task(
    name="8_upload_to_qdrant",
    retries=1,
    retry_delay_seconds=10,
    tags=["etl", "qdrant"],
)
def task_upload_to_qdrant():
    _run_script("8_upload_to_qdrant", SCRIPTS["8_upload_to_qdrant"])



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


@flow(name="Pipeline — Steps 1-8", log_prints=True)
def etl_pipeline_full():
    logger = get_run_logger()
    logger.info("Запуск пайплайна: шаги 1-8")
    _log_pipeline("ЗАПУСК пайплайна: шаги 1-8")

    result_1 = task_generate_seed_urls()
    result_2 = task_validate_seed_dataset(wait_for=[result_1])
    result_3 = task_crawl_cleaned_html(wait_for=[result_2])
    result_4 = task_llm_filter_relevance(wait_for=[result_3])
    result_5 = task_html_to_markdown(wait_for=[result_4])
    result_6 = task_extract_metadata(wait_for=[result_5])
    result_7 = task_prepare_qdrant_dataset(wait_for=[result_6])
    task_upload_to_qdrant(wait_for=[result_7])

    logger.info("Пайплайн завершён")
    _log_pipeline("ЗАВЕРШЁН: пайплайн шагов 1-8")

if __name__ == "__main__":
    etl_pipeline_full()
