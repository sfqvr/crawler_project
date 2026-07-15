"""
setup_prefect_env.py

One-shot bootstrap script: brings up Docker Compose (Prefect server + Postgres + MinIO),
waits for everything to be healthy, points the local Prefect client at the Dockerized
server, creates all work pools with their concurrency limits, and registers all
deployments — leaving the environment fully ready for `prefect worker start --pool ...`.

Safe to re-run: every step checks "does this already exist / is this already true"
before creating anything, so running it again after a code change just re-registers
deployments without erroring on existing pools.

Usage:
    python setup_prefect_env.py
"""

import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

# --------------------------------------------------------------------------
# CONFIG — adjust these to match your project
# --------------------------------------------------------------------------

PROJECT_DIR = Path(__file__).resolve().parent
DOCKER_COMPOSE_DIR = PROJECT_DIR  # folder containing docker-compose.yml

PREFECT_API_URL = "http://localhost:4200/api"
PREFECT_UI_HEALTH_URL = "http://localhost:4200/api/health"
MINIO_HEALTH_URL = "http://localhost:9000/minio/health/live"

# Flow file that contains your flows (adjust path/name to match your project)
FLOWS_FILE = PROJECT_DIR / "orchestration" / "prefect_flowsMinIO.py"

# (work_pool_name, concurrency_limit)
WORK_POOLS = [
    ("stage1-pool", 1),
    ("stage34-pool", 3),
    ("stage567-pool", 5),
]

# (flow_function_name_in_FLOWS_FILE, deployment_name, work_pool_name)
DEPLOYMENTS = [
    ("stage1_seed", "stage1-loop", "stage1-pool"),
    ("stage34_crawl_filter", "stage34-loop", "stage34-pool"),
    ("stage567_markdown_qdrant", "stage567-loop", "stage567-pool"),
]

MAX_WAIT_SECONDS = 90
POLL_INTERVAL_SECONDS = 2


# --------------------------------------------------------------------------
# HELPERS
# --------------------------------------------------------------------------

def log(msg: str):
    print(f"[setup] {msg}", flush=True)


def run(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    log(f"$ {' '.join(cmd)}")
    return subprocess.run(
        cmd,
        cwd=str(PROJECT_DIR),
        check=check,
        capture_output=capture,
        text=True,
    )


def wait_for_http(url: str, label: str, max_wait: int = MAX_WAIT_SECONDS) -> bool:
    log(f"Ожидание {label} ({url}) ...")
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status < 500:
                    log(f"{label} готов.")
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(POLL_INTERVAL_SECONDS)
    log(f"ОШИБКА: {label} не ответил за {max_wait}с.")
    return False


def work_pool_exists(name: str) -> bool:
    result = run(["prefect", "work-pool", "inspect", name], check=False, capture=True)
    return result.returncode == 0


def ensure_work_pool(name: str, concurrency_limit: int):
    if work_pool_exists(name):
        log(f"Пул '{name}' уже существует — пропускаю создание.")
    else:
        run(["prefect", "work-pool", "create", name, "--type", "process"])
        log(f"Пул '{name}' создан.")

    run(["prefect", "work-pool", "set-concurrency-limit", name, str(concurrency_limit)])
    log(f"Пул '{name}': лимит параллелизма установлен = {concurrency_limit}")


# --------------------------------------------------------------------------
# MAIN SEQUENCE
# --------------------------------------------------------------------------

def main():
    log("=" * 60)
    log("Запуск инфраструктуры Prefect + MinIO")
    log("=" * 60)

    # 1. Docker Compose up
    log("Запуск Docker Compose (server, postgres, minio)...")
    result = run(
        ["docker", "compose", "up", "-d"],
        check=False,
        capture=True,
    )
    if result.returncode != 0:
        log("ОШИБКА Docker Compose:")
        print(result.stderr)
        sys.exit(1)
    log("Docker Compose запущен (контейнеры поднимаются в фоне).")

    # 2. Wait for Prefect server + MinIO to be reachable
    server_ok = wait_for_http(PREFECT_UI_HEALTH_URL, "Prefect server")
    if not server_ok:
        log("Не удалось дождаться Prefect server. Проверьте: docker compose logs server")
        sys.exit(1)

    minio_ok = wait_for_http(MINIO_HEALTH_URL, "MinIO")
    if not minio_ok:
        log("Не удалось дождаться MinIO. Проверьте: docker compose logs minio")
        sys.exit(1)

    # 3. Point local Prefect CLI/client at the Dockerized server
    log(f"Настройка PREFECT_API_URL = {PREFECT_API_URL}")
    run(["prefect", "config", "set", f"PREFECT_API_URL={PREFECT_API_URL}"])

    # 4. Create work pools + concurrency limits (idempotent)
    log("-" * 60)
    log("Создание work pools...")
    for pool_name, limit in WORK_POOLS:
        ensure_work_pool(pool_name, limit)

    # 5. Register deployments by importing the flows file and calling
    #    from_source(...).deploy(...) for each flow
    log("-" * 60)
    log("Регистрация деплойментов...")

    if not FLOWS_FILE.exists():
        log(f"ОШИБКА: файл с флоу не найден: {FLOWS_FILE}")
        log("Отредактируйте FLOWS_FILE в начале этого скрипта.")
        sys.exit(1)

    sys.path.insert(0, str(FLOWS_FILE.parent))
    module_name = FLOWS_FILE.stem
    flows_module = __import__(module_name)

    for flow_func_name, deployment_name, pool_name in DEPLOYMENTS:
        flow_obj = getattr(flows_module, flow_func_name, None)
        if flow_obj is None:
            log(f"ПРЕДУПРЕЖДЕНИЕ: функция '{flow_func_name}' не найдена в {FLOWS_FILE.name} — пропускаю.")
            continue

        log(f"Деплой '{flow_func_name}' -> deployment='{deployment_name}', pool='{pool_name}'")
        flow_obj.from_source(
            source=str(FLOWS_FILE.parent),
            entrypoint=f"{FLOWS_FILE.name}:{flow_func_name}",
        ).deploy(
            name=deployment_name,
            work_pool_name=pool_name,
        )

    log("=" * 60)
    log("Готово! Инфраструктура поднята и деплойменты зарегистрированы.")
    log("=" * 60)
    log("")
    log("Дальнейшие шаги:")
    log("  1. Запустите воркеры (по одному терминалу на пул, можно несколько на пул):")
    for pool_name, _ in WORK_POOLS:
        log(f"       prefect worker start --pool {pool_name}")
    log("")
    log("  2. Запустите первый цикл каждого пайплайна:")
    for flow_func_name, deployment_name, _ in DEPLOYMENTS:
        log(f"       prefect deployment run \"{flow_func_name}/{deployment_name}\"")
    log("")
    log(f"  3. Откройте UI: http://localhost:4200")


if __name__ == "__main__":
    main()
