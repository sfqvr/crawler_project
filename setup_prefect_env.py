"""
setup_prefect_env.py

Минимальный скрипт настройки: создаёт work pool'ы (с лимитами параллелизма)
и регистрирует деплойменты для всех flow'ов проекта.

Предполагается, что Prefect API (сервер) уже поднят и доступен —
этот скрипт им не управляет и не проверяет его здоровье.

Безопасно перезапускать: work pool'ы создаются только если их ещё нет,
а деплойменты просто перезаписываются актуальной версией.

Использование:
    python setup_prefect_env.py
"""

import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# CONFIG — подстрой под свой проект
# --------------------------------------------------------------------------

PROJECT_DIR = Path(__file__).resolve().parent

# Файл с определениями flow'ов (путь/имя поменяй под свой проект)
FLOWS_FILE = PROJECT_DIR / "orchestration" / "prefect_flowsMinIO.py"

# (имя work pool'а, лимит параллелизма)
WORK_POOLS = [
    ("stage1-pool", 1),
    ("stage34-pool", 3),
    ("stage567-pool", 5),
    ("stage8-pool", 1)
]

# (имя функции flow в FLOWS_FILE, имя деплоймента, имя work pool'а)
DEPLOYMENTS = [
    ("stage1_seed", "stage1-loop", "stage1-pool"),
    ("stage34_crawl_filter", "stage34-loop", "stage34-pool"),
    ("stage567_markdown_qdrant", "stage567-loop", "stage567-pool"),
    ("stage8_upload_to_qdrant", "stage8-loop", "stage8-pool")
]


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
    log("Настройка work pool'ов и деплойментов Prefect")
    log("=" * 60)

    # 1. Создание work pool'ов + лимитов параллелизма (идемпотентно)
    log("-" * 60)
    log("Создание work pools...")
    for pool_name, limit in WORK_POOLS:
        ensure_work_pool(pool_name, limit)

    # 2. Регистрация деплойментов: импортируем файл с flow'ами и вызываем
    #    from_source(...).deploy(...) для каждого нужного flow
    log("-" * 60)
    log("Регистрация деплойментов...")

    if not FLOWS_FILE.exists():
        log(f"ОШИБКА: файл с flow'ами не найден: {FLOWS_FILE}")
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
    log("Готово! Work pool'ы созданы, деплойменты зарегистрированы.")
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


if __name__ == "__main__":
    main()