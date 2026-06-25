# pipeline/flow.py
from prefect import flow, get_run_logger
from prefect.tasks import task_input_hash
from datetime import timedelta

# Импортируем задачи коллег
from pipeline.tasks.generate_seed import task_generate_seed
from pipeline.tasks.validate import task_validate
from pipeline.tasks.crawl import task_crawl
from pipeline.config import TIMEOUTS

@flow(
    name="ETL Pipeline",
    description="Главный ETL пайплайн для обработки данных",
    timeout_seconds=TIMEOUTS["total"],
    log_prints=True,
    retries=1
)
def etl_pipeline():
    """
    Главный ETL пайплайн:
    1. Генерация seed-данных
    2. Валидация данных
    3. Краулинг (если данные валидны)
    """
    logger = get_run_logger()
    logger.info("=" * 60)
    logger.info("🚀 ЗАПУСК ETL ПАЙПЛАЙНА")
    logger.info("=" * 60)
    
    try:
        # Шаг 1: Генерация seed
        logger.info("\n📌 ШАГ 1: Генерация seed-данных")
        seed = task_generate_seed()
        logger.info(f"✅ Seed получен: ID={seed.get('id')}")
        
        # Шаг 2: Валидация
        logger.info("\n📌 ШАГ 2: Валидация данных")
        validated = task_validate(seed)
        logger.info(f"✅ Статус валидации: {validated.get('status')}")
        
        # Шаг 3: Краулинг (только если данные валидны)
        if validated["is_valid"]:
            logger.info("\n📌 ШАГ 3: Краулинг данных")
            crawled = task_crawl(validated)
            logger.info(f"✅ Краулинг завершен: {crawled.get('pages_crawled', 0)} страниц")
        else:
            logger.warning("⚠️ Данные невалидны, краулинг пропущен")
            crawled = None
        
        # Итоговый результат
        logger.info("\n" + "=" * 60)
        logger.info("✅ ETL ПАЙПЛАЙН УСПЕШНО ЗАВЕРШЕН")
        logger.info("=" * 60)
        
        return {
            "status": "success",
            "seed": seed,
            "validated": validated,
            "crawled": crawled
        }
        
    except Exception as e:
        logger.error(f"❌ Ошибка в ETL пайплайне: {str(e)}")
        raise

# Точка входа для запуска из командной строки
if __name__ == "__main__":
    etl_pipeline()