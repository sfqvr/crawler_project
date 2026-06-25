# pipeline/tasks/generate_seed.py
from prefect import task
import prefect
import random
import json
from ..config import FILES, TIMEOUTS

@task(
    name="Generate Seed Data",
    timeout_seconds=TIMEOUTS["generate"],
    retries=2,
    log_prints=True
)
def task_generate_seed():
    """Генерирует начальные данные (seed)"""
    logger = prefect.get_run_logger()
    logger.info("🚀 Начинаем генерацию seed-данных...")
    
    # Пример генерации
    seed_data = {
        "id": random.randint(1000, 9999),
        "timestamp": "2026-06-25T12:00:00",
        "payload": {
            "urls": [
                "https://example.com/page1",
                "https://example.com/page2"
            ],
            "depth": 2,
            "priority": random.choice(["high", "medium", "low"])
        }
    }
    
    # Сохраняем в файл
    with open(FILES["seed"], "w") as f:
        json.dump(seed_data, f, indent=2)
    
    logger.info(f"✅ Seed-данные сгенерированы: ID={seed_data['id']}")
    logger.info(f"📁 Сохранено в {FILES['seed']}")
    
    return seed_data