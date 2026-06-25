# pipeline/tasks/crawl.py
from prefect import task
import prefect
import json
import time
from ..config import FILES, TIMEOUTS

@task(
    name="Crawl Data",
    timeout_seconds=TIMEOUTS["crawl"],
    retries=3,
    retry_delay_seconds=5,
    log_prints=True
)
def task_crawl(validated_data):
    """Краулинг данных на основе валидированных данных"""
    logger = prefect.get_run_logger()
    logger.info("🕷️ Начинаем краулинг данных...")
    
    if not validated_data["is_valid"]:
        logger.error("❌ Данные невалидны, краулинг отменен")
        return {"status": "failed", "reason": "invalid_data"}
    
    urls = validated_data["original"]["payload"]["urls"]
    depth = validated_data["original"]["payload"]["depth"]
    
    logger.info(f"🌐 URLs для краулинга: {urls}")
    logger.info(f"📏 Глубина краулинга: {depth}")
    
    # Имитация краулинга
    crawled_results = []
    for i, url in enumerate(urls):
        logger.info(f"🔗 Краулинг {i+1}/{len(urls)}: {url}")
        time.sleep(1)  # Имитация работы
        
        result = {
            "url": url,
            "title": f"Page {i+1} from {url}",
            "content_length": 1024 * (i + 1),
            "links_found": [f"{url}/link{i}" for i in range(3)]
        }
        crawled_results.append(result)
    
    final_data = {
        "status": "success",
        "pages_crawled": len(crawled_results),
        "results": crawled_results,
        "metadata": {
            "depth": depth,
            "timestamp": "2026-06-25T12:00:00"
        }
    }
    
    # Сохраняем результаты краулинга
    with open(FILES["crawled"], "w") as f:
        json.dump(final_data, f, indent=2)
    
    logger.info(f"✅ Краулинг завершен: {len(crawled_results)} страниц обработано")
    logger.info(f"📁 Результаты сохранены в {FILES['crawled']}")
    
    return final_data