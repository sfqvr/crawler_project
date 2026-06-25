# pipeline/tasks/validate.py
from prefect import task
import prefect
import json
from ..config import FILES, TIMEOUTS

@task(
    name="Validate Seed Data",
    timeout_seconds=TIMEOUTS["validate"],
    retries=1,
    log_prints=True
)
def task_validate(seed_data):
    """Валидирует полученные данные"""
    logger = prefect.get_run_logger()
    logger.info("🔍 Начинаем валидацию seed-данных...")
    
    # Проверки
    is_valid = True
    errors = []
    
    if not seed_data.get("id"):
        is_valid = False
        errors.append("Отсутствует ID")
    
    if not seed_data.get("payload", {}).get("urls"):
        is_valid = False
        errors.append("Отсутствуют URL-адреса")
    
    if seed_data.get("payload", {}).get("depth", 0) > 5:
        errors.append("Глубина краулинга превышает 5")
    
    validated_data = {
        "original": seed_data,
        "is_valid": is_valid,
        "errors": errors,
        "status": "valid" if is_valid else "invalid"
    }
    
    # Сохраняем результат валидации
    with open(FILES["validated"], "w") as f:
        json.dump(validated_data, f, indent=2)
    
    if is_valid:
        logger.info("✅ Данные прошли валидацию!")
    else:
        logger.warning(f"⚠️ Ошибки валидации: {', '.join(errors)}")
    
    logger.info(f"📁 Результат сохранен в {FILES['validated']}")
    
    return validated_data