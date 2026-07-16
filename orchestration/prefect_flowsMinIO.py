import json
import logging
import os
import subprocess
import threading
import time
from typing import Any, Dict, List
import urllib.request
from datetime import date, datetime
from pathlib import Path

from prefect import flow, task
from prefect.logging import get_run_logger
from prefect.deployments import run_deployment

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.connection import db



PROJECT_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

SCRIPTS = {
    "1_generate_seed_urls": PROJECT_DIR / "1_generate_seed_urls.py",
    "2_validate_seed_dataset": PROJECT_DIR / "2_validate_seed_dataset.py",
    "3_crawl_cleaned_html": PROJECT_DIR / "3_crawl_cleaned_html_1row.py",
    "4_llm_filter_relevance": PROJECT_DIR / "4_llm_filter_relevance_1row.py",
    "5_html_to_markdown": PROJECT_DIR / "5_html_to_markdown_1row.py",
    "6_extract_metadata": PROJECT_DIR / "6_extract_metadata_1row.py",
    "7_prepare_qdrant_dataset": PROJECT_DIR / "7_prepare_qdrant_dataset_1row.py",
    "8_upload_to_qdrant": PROJECT_DIR / "8_upload_to_qdrant.py"
}

PYTHON = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
DOCKER_COMPOSE_DIR = Path(__file__).resolve().parent



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


def _json_default(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _run_script(script_name: str, script_path: Path, arg: dict | None = None) -> dict | None:
    logger = get_run_logger()
    step_logger = _get_step_logger(script_name)
    log_path = LOG_DIR / f"{script_name}.log"

    step_logger.info("=" * 50)
    step_logger.info("ЗАПУСК")
    logger.info(f"Запуск: {script_name}")

    json_str = json.dumps(arg, ensure_ascii=False, default=_json_default) if arg else ""

    process = subprocess.Popen(
        [str(PYTHON), str(script_path), json_str],
        cwd=str(PROJECT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )

    stop_reader = threading.Event()
    result_holder = {"value": None}

    def reader_thread():
        with open(log_path, "a", encoding="utf-8") as log_f:
            while not stop_reader.is_set():
                try:
                    line = process.stdout.readline()
                    if not line:
                        break
                    line = line.rstrip()

                    if line.startswith("RESULT_JSON:"):
                        try:
                            result_holder["value"] = json.loads(line[len("RESULT_JSON:"):])
                            step_logger.info("RESULT_JSON получен")
                        except json.JSONDecodeError:
                            step_logger.error("Не удалось распарсить RESULT_JSON")
                        continue

                    log_f.write(line + "\n")
                    log_f.flush()
                    step_logger.info(line)
                    logger.info(f"[{script_name}] {line}")
                except Exception:
                    break

    reader = threading.Thread(target=reader_thread, daemon=True)
    reader.start()

    try:
        exit_code = process.wait(timeout=7200)
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
        raise RuntimeError(f"Скрипт {script_name} превысил таймаут и был убит")
    finally:
        stop_reader.set()
        reader.join(timeout=5)
        if process.stdout:
            process.stdout.close()

    return result_holder["value"]





def get_next_record_from_db(status: str, next_status: str) -> dict | None:
    """
    Получает следующий документ из БД с указанным статусом.
    
    Args:
        status: Текущий статус
        next_status: Статус, в который перевести после захвата
    
    Returns:
        Словарь с данными документа или None
    """
    try:
        db.connect()
        doc = db.get_next_document_by_status(status, next_status)
        db.close()
        
        if doc:
            # Добавляем id (используем url как id для совместимости)
            doc['id'] = doc.get('url')
            return doc
        return None
        
    except Exception as e:
        print(f"❌ Ошибка при получении записи из БД: {e}")
        return None


def mark_error_in_db(url: str, error_message: str = None):
    """Помечает документ как ошибочный в БД"""
    try:
        db.connect()
        db.update_document_status(url, 'error', error_message)
        db.close()
        print(f"✅ Отмечен как error: {url}")
    except Exception as e:
        print(f"❌ Ошибка при отметке error: {e}")



@task(name="1_generate_seed_urls", retries=2, retry_delay_seconds=30, tags=["etl", "seed"])
def task_generate_seed_urls():
    _run_script("1_generate_seed_urls", SCRIPTS["1_generate_seed_urls"])


@task(name="2_validate_seed_dataset", retries=1, retry_delay_seconds=10, tags=["etl", "validation"])
def task_validate_seed_dataset():
    _run_script("2_validate_seed_dataset", SCRIPTS["2_validate_seed_dataset"])


@task(name="3_crawl_cleaned_html", retries=2, retry_delay_seconds=60, tags=["etl", "crawl"])
def task_crawl_cleaned_html(record: dict) -> dict:
    result = _run_script("3_crawl_cleaned_html", SCRIPTS["3_crawl_cleaned_html"], arg=record)
    if result is None:
        raise RuntimeError("3_crawl_cleaned_html не вернул RESULT_JSON")
    # update_status(3, result, url=record.get("url"))
    return result


@task(name="4_llm_filter_relevance", retries=3, retry_delay_seconds=120, tags=["etl", "llm"])
def task_llm_filter_relevance(record: dict) -> dict:
    result = _run_script("4_llm_filter_relevance", SCRIPTS["4_llm_filter_relevance"], arg=record)
    if result is None:
        raise RuntimeError("4_llm_filter_relevance не вернул RESULT_JSON")
    return result


@task(name="5_html_to_markdown", retries=2, retry_delay_seconds=120, tags=["etl", "markdown"])
def task_html_to_markdown(record: dict) -> dict:
    result = _run_script("5_html_to_markdown", SCRIPTS["5_html_to_markdown"], arg=record)
    if result is None:
        raise RuntimeError("5_html_to_markdown не вернул RESULT_JSON")
    return result


@task(name="6_extract_metadata", retries=2, retry_delay_seconds=120, tags=["etl", "metadata"])
def task_extract_metadata(record: dict) -> dict:
    result = _run_script("6_extract_metadata", SCRIPTS["6_extract_metadata"], arg=record)
    if result is None:
        raise RuntimeError("6_extract_metadata не вернул RESULT_JSON")
    return result


@task(name="7_prepare_qdrant_dataset", retries=1, retry_delay_seconds=10, tags=["etl", "prepare"])
def task_prepare_qdrant_dataset(record: dict) -> dict:
    result =_run_script("7_prepare_qdrant_dataset", SCRIPTS["7_prepare_qdrant_dataset"], arg=record)
    if result is None:
        raise RuntimeError("7_prepare_qdrant_dataset не вернул RESULT_JSON")
    return result


@task(name="8_upload_to_qdrant", retries=1, retry_delay_seconds=10, tags=["etl", "qdrant"])
def task_upload_to_qdrant():
    _run_script("8_upload_to_qdrant", SCRIPTS["8_upload_to_qdrant"])


# @flow(name="Pipeline — Steps 1-8", log_prints=True)
# def etl_pipeline():
#     logger = get_run_logger()
#     logger.info("Запуск пайплайна: шаги 1-8")
#     _log_pipeline("ЗАПУСК пайплайна: шаги 1-8")
#     result_1 = task_generate_seed_urls()
#     result_2 = task_validate_seed_dataset(wait_for=[result_1])
#     result_3 = task_crawl_cleaned_html(wait_for=[result_2])
#     result_4 = task_llm_filter_relevance(wait_for=[result_3])
#     result_5 = task_html_to_markdown(wait_for=[result_4])
#     result_6 = task_extract_metadata(wait_for=[result_5])
#     result_7 = task_prepare_qdrant_dataset(wait_for=[result_6])
#     task_upload_to_qdrant(wait_for=[result_7])

#     logger.info("Пайплайн завершён")
#     _log_pipeline("ЗАВЕРШЁН: пайплайн шагов 1-8")


# @flow(name="etl-pipeline-auto", log_prints=True)
# def etl_pipeline_auto():
#     logger = get_run_logger()
#     record = get_next_record_from_db("new",'in_progress')

#     if record is None:
#         logger.info("Нет новых записей — пауза")
#         time.sleep(30)
#         run_deployment(name=FULL_DEPLOYMENT_NAME, timeout=0, as_subflow=False)
#         return

#     logger.info(f"Запуск пайплайна для: {record['url']}")

#     result_3 = task_crawl_cleaned_html(record)          # step 3's output...
#     result_4 = task_llm_filter_relevance(result_3)  # ...becomes step 4's input
#     result_5 = task_html_to_markdown(result_4)
#     result_6 = task_extract_metadata(result_5)
#     result_7 = task_prepare_qdrant_dataset(result_6)

#     logger.info("Пайплайн завершён")
#     run_deployment(name=FULL_DEPLOYMENT_NAME, timeout=0, as_subflow=False)


@flow(name="stage1-seed", log_prints=True)
def stage1_seed():
    logger = get_run_logger()
    try:
        task_generate_seed_urls()
        logger.info("Стадия 1 завершена успешно")
    except Exception as e:
        logger.error(f"Ошибка на стадии 1: {e}")
        _log_pipeline(f"ОШИБКА стадии 1: {e}")
    finally:
        run_deployment(name="stage1-seed/stage1-loop", timeout=120, as_subflow=False)


@flow(name="stage34-crawl-filter", log_prints=True)
def stage34_crawl_filter():
    logger = get_run_logger()
    record = get_next_record_from_db("new",'skipped')

    if record is None:
        logger.info("Нет новых записей — пауза")
        time.sleep(15)
        run_deployment(name="stage34-crawl-filter/stage34-loop", timeout=0, as_subflow=False)
        return

    try:
        logger.info(f"Запуск пайплайна для: {record['url']}")
        result_3 = task_crawl_cleaned_html(record)
        result_4 = task_llm_filter_relevance(result_3)

        db.mark_stage3_4_completed(record["url"], result_4)
        logger.info("Пайплайн 3-4 завершён успешно")
        _log_pipeline(f"ЗАВЕРШЁН: {record['url']}")

    except Exception as e:
        logger.error(f"Ошибка обработки записи {record['id']} ({record['url']}): {e}")
        _log_pipeline(f"ОШИБКА: {record['url']} — {e}")
        mark_error_in_db(record["id"])
        raise  # flow run is marked Failed in the Prefect UI

    finally:
        run_deployment(name="stage34-crawl-filter/stage34-loop", timeout=0, as_subflow=False)


@flow(name="stage567-markdown", log_prints=True)
def stage567_markdown_qdrant():
    logger = get_run_logger()
    record = get_next_record_from_db('with_html','skipped')

    if record is None:
        logger.info("Нет новых записей — пауза")
        time.sleep(30)
        run_deployment(name="stage567-markdown/stage567-loop", timeout=0, as_subflow=False)
        return

    try:
        logger.info(f"Запуск пайплайна для: {record['url']}")
        result_5 = task_html_to_markdown(record)
        result_6 = task_extract_metadata(result_5)
        result_7 = task_prepare_qdrant_dataset(result_6)

        db.mark_stage5_7_completed(record['url'], result_7)
        logger.info("Пайплайн 567 завершён успешно")
        _log_pipeline(f"ЗАВЕРШЁН: {record['url']}")

    except Exception as e:
        logger.error(f"Ошибка обработки записи {record['id']} ({record['url']}): {e}")
        _log_pipeline(f"ОШИБКА: {record['url']} — {e}")
        mark_error_in_db(record["id"])
        raise  # flow run is marked Failed in the Prefect UI

    finally:
        run_deployment(name="stage567-markdown/stage567-loop", timeout=0, as_subflow=False)


@flow(name="stage8-upload-to-qdrant", log_prints=True)
def stage8_upload_to_qdrant():
    logger = get_run_logger()

    try:
        logger.info("Загрузка записей в qdrant")
        task_upload_to_qdrant()

        logger.info("Загрузка записей в qdrant прошла успешно")

    except Exception as e:
        logger.error(f"Ошибка обработки записей: {e}")
        _log_pipeline(f"ОШИБКА: {e}")
        raise  # flow run is marked Failed in the Prefect UI


def _wait_for_minio():
    print("Запуск Docker Compose...")
    result = subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd=str(DOCKER_COMPOSE_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Docker Compose error:\n{result.stderr}")
        exit(1)

    print("Ожидание MinIO...")
    for _ in range(30):
        try:
            urllib.request.urlopen("http://localhost:9000/minio/health/live")
            print("MinIO запущен")
            return
        except Exception:
            time.sleep(2)
    print("MinIO не запустился")
    exit(1)


if __name__ == "__main__":
    _wait_for_minio()

    # etl_pipeline_auto.from_source(
    #     source=str(Path(__file__).resolve().parent),
    #     entrypoint=f"{Path(__file__).name}:etl_pipeline_auto",
    # ).deploy(name="etl-loop", work_pool_name="etl-pool")

    stage1_seed.from_source(
        source=str(Path(__file__).resolve().parent),
        entrypoint=f"{Path(__file__).name}:stage1_seed",
    ).deploy(name="stage1-loop", work_pool_name="stage1-pool")

    stage34_crawl_filter.from_source(
        source=str(Path(__file__).resolve().parent),
        entrypoint=f"{Path(__file__).name}:stage34_crawl_filter",
    ).deploy(name="stage34-loop", work_pool_name="stage34-pool")

    stage567_markdown_qdrant.from_source(
        source=str(Path(__file__).resolve().parent),
        entrypoint=f"{Path(__file__).name}:stage567_markdown_qdrant",
    ).deploy(name="stage567-loop", work_pool_name="stage567-pool")

    stage8_upload_to_qdrant.from_source(
        source=str(Path(__file__).resolve().parent),
        entrypoint=f"{Path(__file__).name}:stage8_upload_to_qdrant",
    ).deploy(name="stage8-loop", work_pool_name="stage8-pool")