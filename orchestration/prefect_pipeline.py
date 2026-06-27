import os
import subprocess
import sys
import threading
from pathlib import Path

from dotenv import load_dotenv
from prefect import flow, task, get_run_logger

PROJECT_DIR = Path(__file__).resolve().parent.parent / "my_crawler_project"
LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

dotenv_path = PROJECT_DIR / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path=dotenv_path)
else:
    load_dotenv()

SCRIPTS = {
    1: {"name": "1_generate_seed_urls",      "path": PROJECT_DIR / "1_generate_seed_urls.py",      "tags": ["seed", "etl"],       "retries": 2, "delay": 30},
    2: {"name": "2_validate_seed_dataset",    "path": PROJECT_DIR / "2_validate_seed_dataset.py",    "tags": ["validation", "etl"], "retries": 1, "delay": 10},
    3: {"name": "3_crawl_cleaned_html",       "path": PROJECT_DIR / "3_crawl_cleaned_html.py",       "tags": ["crawl", "etl"],      "retries": 2, "delay": 60},
    4: {"name": "4_llm_filter_relevance",     "path": PROJECT_DIR / "4_llm_filter_relevance.py",     "tags": ["llm", "filter"],     "retries": 3, "delay": 120},
}

PYTHON = Path(sys.executable)

SCRIPT_TIMEOUT = 7200


def _run_script(step_num: int):
    logger = get_run_logger()
    info = SCRIPTS[step_num]
    name = info["name"]
    script_path = info["path"]

    if not script_path.exists():
        raise FileNotFoundError(f"Скрипт не найден: {script_path}")

    log_path = LOG_DIR / f"{name}.log"
    logger.info(f"[{name}] Запуск этапа {step_num}/{len(SCRIPTS)}: {script_path.name}")
    logger.info(f"[{name}] Лог: {log_path}")

    process = subprocess.Popen(
        [str(PYTHON), str(script_path)],
        cwd=str(PROJECT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        bufsize=1,
        text=True,
        encoding="utf-8",
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
                    logger.info(f"[{name}] {line}")
                except Exception:
                    break

    reader = threading.Thread(target=reader_thread, daemon=True)
    reader.start()

    try:
        exit_code = process.wait(timeout=SCRIPT_TIMEOUT)
        logger.info(f"[{name}] exit code: {exit_code}")

        if exit_code != 0:
            raise RuntimeError(f"Скрипт {name} упал с кодом {exit_code}")

        logger.info(f"[{name}] Этап {step_num} завершён успешно")

    except subprocess.TimeoutExpired:
        logger.error(f"[{name}] ТАЙМАУТ (> {SCRIPT_TIMEOUT // 60} мин) — принудительное убийство процесса")
        process.kill()
        process.wait(timeout=10)
        raise RuntimeError(
            f"Скрипт {name} превысил таймаут {SCRIPT_TIMEOUT // 60} мин и был убит"
        )

    finally:
        stop_reader.set()
        reader.join(timeout=5)
        if process.stdout:
            process.stdout.close()


@task(
    name="1_generate_seed_urls",
    retries=SCRIPTS[1]["retries"],
    retry_delay_seconds=SCRIPTS[1]["delay"],
    tags=SCRIPTS[1]["tags"],
    log_prints=True,
)
def task_generate_seed_urls():
    _run_script(1)


@task(
    name="2_validate_seed_dataset",
    retries=SCRIPTS[1]["retries"],
    retry_delay_seconds=SCRIPTS[2]["delay"],
    tags=SCRIPTS[2]["tags"],
    log_prints=True,
)
def task_validate_seed_dataset():
    _run_script(2)


@task(
    name="3_crawl_cleaned_html",
    retries=SCRIPTS[3]["retries"],
    retry_delay_seconds=SCRIPTS[3]["delay"],
    tags=SCRIPTS[3]["tags"],
    log_prints=True,
)
def task_crawl_cleaned_html():
    _run_script(3)


@task(
    name="4_llm_filter_relevance",
    retries=SCRIPTS[4]["retries"],
    retry_delay_seconds=SCRIPTS[4]["delay"],
    tags=SCRIPTS[4]["tags"],
    log_prints=True,
    timeout_seconds=3600,
)
def task_llm_filter_relevance():
    _run_script(4)


@flow(
    name="ETL Pipeline (8 stages)",
    timeout_seconds=7200,
    log_prints=True,
)
def etl_pipeline():
    logger = get_run_logger()
    logger.info("=" * 60)
    logger.info("ЗАПУСК ETL-ПАЙПЛАЙНА (8 ЭТАПОВ)")
    logger.info("=" * 60)

    logger.info("ЭТАП 1/8: Генерация seed-URL")
    task_generate_seed_urls()

    logger.info("ЭТАП 2/8: Валидация seed-датасета")
    task_validate_seed_dataset()

    logger.info("ЭТАП 3/8: Краулинг cleaned HTML")
    task_crawl_cleaned_html()

    logger.info("ЭТАП 4/8: LLM-фильтрация релевантности")
    task_llm_filter_relevance()

    logger.info("=" * 60)
    logger.info("ВСЕ 8 ЭТАПОВ УСПЕШНО ЗАВЕРШЕНЫ")
    logger.info("=" * 60)


if __name__ == "__main__":
    etl_pipeline()
