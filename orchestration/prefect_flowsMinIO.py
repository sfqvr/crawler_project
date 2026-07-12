import json
import logging
import os
import subprocess
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from prefect import flow, task
from prefect.logging import get_run_logger
from prefect.deployments import run_deployment

PROJECT_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

SCRIPTS = {
    "1_generate_seed_urls": PROJECT_DIR / "1_generate_seed_urls.py",
    "2_validate_seed_dataset": PROJECT_DIR / "2_validate_seed_dataset.py",
    "3_crawl_cleaned_html": PROJECT_DIR / "3_crawl_cleaned_html_1row.py",
    "4_llm_filter_relevance": PROJECT_DIR / "4_llm_filter_relevance_1row.py",
    "5_html_to_markdown": PROJECT_DIR / "5_html_to_markdown.py",
    "6_extract_metadata": PROJECT_DIR / "6_extract_metadata.py",
    "7_prepare_qdrant_dataset": PROJECT_DIR / "7_prepare_qdrant_dataset.py",
    "8_upload_to_qdrant": PROJECT_DIR / "8_upload_to_qdrant.py"
}

PYTHON = PROJECT_DIR / ".venv" / "Scripts" / "python.exe"
DOCKER_COMPOSE_DIR = Path(__file__).resolve().parent



DICT = {
    "name": "Allegro", 
    "url": "https://allegro.tech/2018/08/postmortem-why-allegro-went-down.html",
    "description": "E-commerce site went down after a sudden traffic spike caused by a marketing campaign. The outage was caused by a configuration error in cluster resource management which prevented more service instances from starting even though hardware resources were available.",
    "error": False, 
    "cleaned_html": "<html>\n<head>\n    <!-- Begin Jekyll SEO tag v2.8.0 -->\n<title>Postmortem — why Allegro went down | blog.allegro.tech</title>\n<!-- End Jekyll SEO tag -->\n\n    \n        \n            </head>\n<body>\n\n\n<main>\n    <div>\n        <div>\n            <article>\n    <header>\n        \n            \n<div>\n    <div>\n        <img src=\"/assets/img/authors/michal.kosmulski.jpg\" alt=\"Michał Kosmulski\">\n        <div>\n            <a href=\"/authors/michal.kosmulski\">\n                Michał Kosmulski\n            </a>\n            <p>\n                <time title=\"&lt;strong&gt;Aug 31&lt;/strong&gt; 2018\">\n                    <strong>Aug 31</strong> 2018\n                </time>\n            </p>\n        </div>\n    </div>\n</div>\n\n        \n    </header>\n    <h1>\n        Postmortem — why Allegro went down\n    </h1>\n    <p>We messed up. On July 18<sup>th</sup>, 2018, at noon, Allegro went down and was unavailable for twenty minutes. The direct cause\nwas a special offer in which one hundred Honor 7C phones whose regular price is around PLN 850 (about € 200),\nwere offered at a price of PLN 1 (less than € 1). This attracted more traffic than we anticipated and at the same time\ntriggered a configuration error in the way services are scaled out. This caused the site to go down despite there\nbeing plenty of CPUs, RAM, and network capacity available in our data centers.</p>\n\n<p>In order to make up for the issues and apologize, <a href=\"https://www.spidersweb.pl/2018/07/allegro-honor-za-1zl-przeprosiny.html\">we made it possible to finish the transaction afterwards</a>\nto buyers who managed to buy the phone at the low price but whose transactions were aborted as the system went down.</p>\n\n<p>But we believe that we also owe our customers and the tech community an explanation of how the crash came about\nand what technical measures we are putting in place in order to make such events less likely in the future.\nWe prepare internal postmortems after any serious issue in order to analyze the causes and learn from our mistakes.\nThis text is based on such an internal postmortem, prepared by multiple people from the teams\nthat took part in dealing with the outage.</p>\n<h2>\n  \n  \n    Architecture overview <a href=\"#architecture-overview\">#</a>\n  \n  \n</h2>\n    \n\n<p>First of all, let’s start with an overview of our architecture. As you probably already know from <a href=\"/blog/\">our blog</a>,\nour system is based on microservices which run in a private cloud environment. In the typical case of a user\nsearching for an offer, clicking on it to view details, and then buying, following major services are involved:</p>\n<ul>\n  <li>Listing — prepares data related to item listing (search result) pages</li>\n  <li>Search — responsible for low-level search in offers, based on keywords, parameters and other criteria</li>\n  <li>Transaction — allows items to be bought</li>\n  <li>\n<a href=\"/2016/03/Managing-Frontend-in-the-microservices-architecture.html\">Opbox</a> — responsible for frontend rendering\nof the data returned by backend services</li>\n  <li>Item — service for frontend rendering of item pages</li>\n</ul>\n<h2>\n  \n  \n    Outage timeline <a href=\"#outage-timeline\">#</a>\n  \n  \n</h2>\n    \n\n<p>The special offer was to start at noon sharp, and a direct link to its item page had been published before.\nAt 11:15 we manually scaled out Listing service in order to be prepared for increased incoming traffic.</p>\n\n<figure>\n<img alt=\"Search service traffic around noon\" src=\"/assets/img/articles/2018-08-31-postmortem-why-allegro-went-down/search-traffic.png\" width=\"798\" height=\"166\">\n<figcaption>\nSearch service traffic around noon. The number of requests per unit of time rose before noon, causing some requests\nto fail after reaching a high enough level. Apart from natural changes in traffic, this chart also shows the time\nof low traffic caused by frontend services failing around 12:05 and traffic rising again after those issues were resolved.\n</figcaption>\n</figure>\n\n<p>At 11:50, traffic to the major services was already 50% higher than the day before at the same time of day.\nAt 11:55, further traffic increase caused response times of major services to rise, forcing us to scale out these services.\nA minute or two later, response times from Search and Listing services rose even more, forcing further scaling.</p>\n\n<p>By 11:58, almost all resources in the part of the cluster provisioned for these services had been reserved even though\nonly a fraction of the cluster’s capacity (or even that particular compartment) was actually used. When an application\nis deployed to our cloud, it declares the amount of resources such as processor cores and memory which it needs for each\ninstance. These resources are reserved for a particular instance and can’t be used by others even if the owner\nis not really consuming them. Some services share their cluster space with others while others have separate compartments\ndue to special needs.</p>\n\n<p>As we later found out, due to a misconfiguration, some services reserved much more resources than they actually needed.\nThis lead to a paradoxical situation in which there were plenty of resources available in the cluster but since they were\nreserved, they couldn’t be assigned to any other services. This prevented more instances from starting despite resources\nbeing there. Some other compartments within the cluster were not even affected at all, with lots of CPUs idling\nand tons of RAM laying around unused.</p>\n\n<figure>\n<img alt=\"Listing service response times\" src=\"/assets/img/articles/2018-08-31-postmortem-why-allegro-went-down/listing-response-times.png\" width=\"1366\" height=\"413\">\n<figcaption>\nListing service response times (avg median - average between instances of the median value, max p99 - maximum between\ninstances of 99<sup>th</sup> percentile). Response times stayed stable despite growing traffic but after reaching saturation,\nthey increased very quickly, only to fall due to frontend services failing and later successful scaling of Listing service.\n</figcaption>\n</figure>\n\n<p>Seconds before noon, the price of the special offer was decreased to PLN 1 in order to ensure that at 12:00 sharp\nit would already be visible in all channels, and the first sales took place.</p>\n\n<p>Also just before noon, traffic peaked at 200%-300% of the traffic from previous day, depending on service. At this stage,\ntraffic was at its highest but due to excessive resource reservations, in some parts of the cluster we could not use\navailable CPUs and RAM for starting new service instances. Meanwhile, the frontend service, Opbox, was starting to fail.\nThis caused a decrease in traffic to the backend services. It was still quite high, though, and autoscaler started to\nspin up new instances of Search service. We manually added even more instances, but the resource reservations created\npreviously prevented us from scaling up as far as to decrease response times significantly.</p>\n\n<p>Increased response times caused some Opbox instances to not report their health status to the cluster correctly and\nat 12:05 the cluster started killing off unresponsive instances. While automated and manual scaling efforts continued,\nbefore 12:15 we started adding more resources to the cluster. At the same time, we started shutting down some non-critical\nservices in order to free CPU and memory. Around 12:20, the situation was fully under control and Allegro became\nresponsive again.</p>\n<h2>\n  \n  \n    Analysis <a href=\"#analysis\">#</a>\n  \n  \n</h2>\n    \n\n<p>What is going on inside a service which experiences traffic higher than it can handle with available resources?\nAs response times increase, the autoscaler tries to scale up the service. On the other hand, instances whose health\nendpoint can’t respond within a specified timeout, are automatically shut down. During the outage, autoscaler did not\nrespond quickly enough to rising traffic and we had to scale up manually. There were also some bad interactions between\nthe autoscaler scaling services up and the cluster watchdog killing off unresponsive instances.</p>\n\n<p>Excessive resource reservations were a major cause of problems since they prevented more instances from being started\neven though there were still plenty of resources available. As the probably most important action resulting from this\npostmortem, we plan to change the cluster’s approach to reserving resources so that there is less waste and resources\nare not locked out of the pool if they are not really used.</p>\n\n<p>Apart from the obvious resources of the cluster: CPU and RAM, another resource which can become saturated are\nthe connection pools for incoming and outgoing network connections as well as file descriptors associated with them.\nIf we run out of them, our service becomes unresponsive even if CPU and RAM are available, and this is what happened\nto some of the backend services during the outage. By better tuning the configuration of thread and connection pools\nas well as the retry policies, we will be able to mitigate the impact of high traffic the next time it happens.</p>\n\n<figure>\n<img alt=\"Undertow thread count in Listing service\" src=\"/assets/img/articles/2018-08-31-postmortem-why-allegro-went-down/undertow-threads.png\" width=\"1596\" height=\"224\">\n<figcaption>\nUndertow thread count in Listing service. A sudden increase is visible during the time when there were too few instances\nto handle incoming traffic. Compare with the graph of response times above.\n</figcaption>\n</figure>\n\n<p>In most cases, requests which time out, are repeated after a short delay. Under normal conditions, the second or third\nattempt usually succeeds, so these retries can often fix the situation and allow a response to still be delivered\nto the end user. However, if the whole cluster is maxed out, retries only increase the load while the whole request\nfails anyway. In such a situation, a <a href=\"https://en.wikipedia.org/wiki/Circuit_breaker_design_pattern\">circuit breaker</a>\nshould prevent further requests, but as we found out during postmortem analysis, one of the circuit breakers between\nour services was not correctly configured: the failure threshold for triggering it was set to a high value which\nwe didn’t reach even during such a serious surge in traffic. Apart from fixing this, we are also adding an additional\nlayer of circuit breakers directly after the frontend service.</p>\n\n<p>The role of <a href=\"https://en.wikipedia.org/wiki/Rate_limiting\">rate limiters</a> is to cut off incoming traffic which displays\nsuspicious patterns before it even enters the system. Such rate limiters did in fact kick in and were the cause of many\n“blank pages” seen by our users during the outage. Unfortunately, the coverage of the site by rate limiters was not complete,\nso while some pages were protected very well, others were not. The “blank page” had an internal retry, so a user\nlooking at such a page was actually still generating requests to the system once in a while, further increasing the load.\nOn the other hand, upon seeing that the site was broken, some users tried to manually refresh the pages they were\nviewing or to enter allegro.pl into the address bar and searching for the phone’s name, thus generating even more search\nrequests manually.</p>\n\n<p>Another takeaway was the observation that new Opbox instances had issues while starting under high load. Newly started\ninstances very quickly reached “unresponsive” status and were automatically killed. We will try out several ideas which\nshould make the service start up faster even if it gets hit with lots of requests right away.</p>\n\n<p>Finally, by introducing smart caches, we should be able to eliminate the need for many requests altogether.\nDue to personalisation, item pages are normally not cached and neither is the data returned by backend services used\nfor rendering those pages. However, we plan to introduce a mechanism which will be able to tell backend services\nto generate simplified, cacheable responses under special conditions. This will allow us to decrease load under heavy traffic.</p>\n<h2>\n  \n  \n    Closing remarks <a href=\"#closing-remarks\">#</a>\n  \n  \n</h2>\n    \n\n<p>Apart from the need of introducing the improvements mentioned above, we learned a few other interesting things.</p>\n\n<p>First off, we certainly learned that traffic drawn in by an attractive offer can outgrow our expectations.\nWe should have been ready for more than we were, both in terms of using cluster capacity effectively and in terms of\ngeneral readiness to handle unexpected situations caused by a sudden surge in traffic. Apart from technical insights,\nwe also learned some lessons on the business side of things, related to dealing with attractive offers\nand organizing promotions, for example that publishing a direct link to the special offer ahead of time was a rather bad idea.</p>\n\n<p>Interestingly enough, the traffic which brought us down, was in large part bots rather than human users. Apparently,\nsome people were so eager to buy the phone cheaply that they used automated bots in order to increase their chances\nof being in the lucky hundred. Some even shared their custom solutions online. Since we want to create a level playing\nfield for all users, we plan to make it harder for bots to participate in this kind of offers.</p>\n\n<p>Even though it may have looked as if the site had gone down due to an exhaustion of resources such as processing power\nor memory, actually plenty of these resources were available. However, an invalid approach to reserving resources\nmade it impossible at one point to use them for starting new instances of the services which we needed to scale out.</p>\n\n<p>I think that despite the outage taking place, the way we handled it validated our approach to architecture.\nThanks to the cloud, we were able to scale out all critical services as long as the resource limits allowed us to.\nUsing microservices, we were able to scale different parts of the system differently which made it possible to use\nthe available cluster more effectively. Loose coupling and separation of concerns between the services\nallowed us to safely shut down those parts of the system which were not critical in order to make room\nfor more instances of the critical services.</p>\n\n<p>Our decentralized team structure was a mixed bag, but with advantages outweighing disadvantages. It certainly lead\nto some communication overhead and occasional miscommunication, but on the other hand, it allowed teams\nresponsible for different services to act mostly independently, which increased our reaction speed overall.\nNote, that “decentralized team structure” does not mean “free for all”. In particular, during an outage, there is\na formal command structure for coordinating the whole effort, but it does not mean micromanagement.</p>\n\n<p>We know that Allegro is an important place for our customers, and every day we work hard to make it better.\nWe hope that the information contained in this postmortem will be interesting for the IT community.\nWe are implementing actions outlined in a much more detailed internal report in order to make such events\nless probable in the future. Even in failure there is opportunity for learning.</p>\n\n<p>Allegro Engineering Team</p>\n\n    <section>\n        <div>\n            \n            <a href=\"/tag/postmortem\">\n                </a>\n            \n            <a href=\"/tag/devops\">\n                </a>\n            \n            <a href=\"/tag/deployment\">\n                </a>\n            \n            <a href=\"/tag/cloud\">\n                </a>\n            \n        </div>\n    </section>\n    <section>\n        <div>\n            <h2>Discussion</h2>\n        </div>\n        </section>\n</article>\n\n        </div>\n    </div>\n</main>\n<footer>\n    <div>\n        <div>\n            <a href=\"https://allegro.tech\">\n                <picture>\n                    <source>\n                    <source>\n                    <img alt=\"allegro.tech\" src=\"/assets/img/allegro-tech.svg\" height=\"63\" width=\"300\">\n                </source></source></picture>\n            </a>\n            <span>© 2026 <a href=\"https://about.allegro.eu/who-we-are/at-a-glance\">Allegro</a><br>Proudly built by <a href=\"/authors\">engineers</a></span>\n            <ul>\n                <li>\n                    <a href=\"https://facebook.com/allegro.tech\">\n                        </a>\n                </li>\n                <li>\n                    <a href=\"https://twitter.com/allegrotech\">\n                        </a>\n                </li>\n                <li>\n                    <a href=\"https://github.com/allegro\">\n                        </a>\n                </li>\n            </ul>\n        </div>\n    </div>\n</footer>\n\n\n\n</body>\n</html>",
    "crawl_success": True, 
    "crawl_error_message": "", 
    "crawl_error_primary": "", 
    "crawl_error_fallback": "", 
    "crawl_mode": "primary", 
    "crawl_url_type": "NORMAL", 
    "cleaned_html_length": 16715, 
    "crawl_suspect": False, 
    "crawl_attempt_count": 1, 
    "crawl_primary_success": True, 
    "crawl_fallback_used": False, 
    "crawl_started_at_utc": "2026-07-10T14:40:50.779469+00:00", 
    "crawl_finished_at_utc": "2026-07-10T14:40:56.921726+00:00", 
    "crawl_elapsed_seconds": 6.142, 
    "crawler_version_hint": "Crawl4AI 0.8.0", 
    "debug_requested_url": "https://allegro.tech/2018/08/postmortem-why-allegro-went-down.html", 
    "debug_result_url": "https://allegro.tech/2018/08/postmortem-why-allegro-went-down.html", 
    "debug_status_code": 301, 
    "debug_match_method": "batch_url_match",
    "debug_batch_id": 1}


WORK_POOL_NAME = "etl-pool"
DEPLOYMENT_NAME = "etl-auto-loop"
FULL_DEPLOYMENT_NAME = f"etl-pipeline-auto/{DEPLOYMENT_NAME}"


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


def _run_script(script_name: str, script_path: Path, arg: dict | None = None) -> dict | None:
    logger = get_run_logger()
    step_logger = _get_step_logger(script_name)
    log_path = LOG_DIR / f"{script_name}.log"

    step_logger.info("=" * 50)
    step_logger.info("ЗАПУСК")
    logger.info(f"Запуск: {script_name}")

    json_str = json.dumps(arg, ensure_ascii=False) if arg else ""

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


STATUS_TRANSITIONS = {
    "seeded": "crawling",
    "filtered": "markdown_processing",
}


def get_next_record(status: str) -> dict | None:
    """
    Claims the next unprocessed record with the given status,
    atomically marking it as in-progress so parallel workers
    never grab the same row.
    """
    next_status = STATUS_TRANSITIONS.get(status)
    if next_status is None:
        raise ValueError(f"Нет перехода статуса для '{status}'")
    return DICT
    # conn = psycopg2.connect(**DB_CONFIG)
    # try:
    #     with conn.cursor() as cur:
    #         cur.execute("""
    #             SELECT id, name, url, description, cleaned_html, minio_key
    #             FROM urls
    #             WHERE status = %s
    #             ORDER BY id
    #             LIMIT 1
    #             FOR UPDATE SKIP LOCKED
    #         """, (status,))
    #         row = cur.fetchone()
    #         if row is None:
    #             return None

    #         record_id, name, url, description, cleaned_html, minio_key = row

    #         cur.execute(
    #             "UPDATE urls SET status = %s WHERE id = %s",
    #             (next_status, record_id),
    #         )
    #         conn.commit()

    #         return {
    #             "id": record_id,
    #             "name": name,
    #             "url": url,
    #             "description": description,
    #             "cleaned_html": cleaned_html,
    #             "minio_key": minio_key,
    #         }
    # finally:
    #     conn.close()


def mark_error(record_id: int):
    """Marks a record as failed so it doesn't get picked up again automatically
    and stays visible for manual inspection. Error details live in the Prefect UI logs,
    not in the database. !!!NOW JUST PLACEHOLDER!!!"""
    # conn = psycopg2.connect(**DB_CONFIG)
    # try:
    #     with conn.cursor() as cur:
    #         cur.execute(
    #             "UPDATE urls SET status = 'ERROR' WHERE id = %s",
    #             (record_id,),
    #         )
    #         conn.commit()
    # finally:
    #     conn.close()


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
    return result


@task(name="4_llm_filter_relevance", retries=3, retry_delay_seconds=120, tags=["etl", "llm"])
def task_llm_filter_relevance(record: dict) -> dict:
    result = _run_script("4_llm_filter_relevance", SCRIPTS["4_llm_filter_relevance"], arg=record)
    if result is None:
        raise RuntimeError("4_llm_filter_relevance не вернул RESULT_JSON")
    return result


@task(name="5_html_to_markdown", retries=2, retry_delay_seconds=120, tags=["etl", "markdown"])
def task_html_to_markdown():
    _run_script("5_html_to_markdown", SCRIPTS["5_html_to_markdown"])


@task(name="6_extract_metadata", retries=2, retry_delay_seconds=120, tags=["etl", "metadata"])
def task_extract_metadata():
    _run_script("6_extract_metadata", SCRIPTS["6_extract_metadata"])


@task(name="7_prepare_qdrant_dataset", retries=1, retry_delay_seconds=10, tags=["etl", "prepare"])
def task_prepare_qdrant_dataset():
    _run_script("7_prepare_qdrant_dataset", SCRIPTS["7_prepare_qdrant_dataset"])


@task(name="8_upload_to_qdrant", retries=1, retry_delay_seconds=10, tags=["etl", "qdrant"])
def task_upload_to_qdrant():
    _run_script("8_upload_to_qdrant", SCRIPTS["8_upload_to_qdrant"])


@flow(name="Pipeline — Steps 1-8", log_prints=True)
def etl_pipeline():
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


@flow(name="etl-pipeline-auto", log_prints=True)
def etl_pipeline_auto():
    logger = get_run_logger()
    record = get_next_record()

    if record is None:
        logger.info("Нет новых записей — пауза")
        time.sleep(30)
        run_deployment(name=FULL_DEPLOYMENT_NAME, timeout=0, as_subflow=False)
        return

    logger.info(f"Запуск пайплайна для: {record['url']}")

    crawl_result = task_crawl_cleaned_html(record)          # step 3's output...
    filter_result = task_llm_filter_relevance(crawl_result)  # ...becomes step 4's input

    logger.info("Пайплайн завершён")
    run_deployment(name=FULL_DEPLOYMENT_NAME, timeout=0, as_subflow=False)


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
    record = get_next_record("seeded")

    if record is None:
        logger.info("Нет новых записей — пауза")
        time.sleep(15)
        run_deployment(name="stage34-crawl-filter/stage34-loop", timeout=0, as_subflow=False)
        return

    try:
        logger.info(f"Запуск пайплайна для: {record['url']}")
        crawl_result = task_crawl_cleaned_html(record)
        task_llm_filter_relevance(crawl_result)
        logger.info("Пайплайн завершён успешно")
        _log_pipeline(f"ЗАВЕРШЁН: {record['url']}")

    except Exception as e:
        logger.error(f"Ошибка обработки записи {record['id']} ({record['url']}): {e}")
        _log_pipeline(f"ОШИБКА: {record['url']} — {e}")
        mark_error(record["id"])
        raise  # flow run is marked Failed in the Prefect UI

    finally:
        run_deployment(name="stage34-crawl-filter/stage34-loop", timeout=0, as_subflow=False)


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

    stage1_seed.from_source(
        source=str(Path(__file__).resolve().parent),
        entrypoint=f"{Path(__file__).name}:stage1_seed",
    ).deploy(name="stage1-loop", work_pool_name="stage1-pool")

    stage34_crawl_filter.from_source(
        source=str(Path(__file__).resolve().parent),
        entrypoint=f"{Path(__file__).name}:stage34_crawl_filter",
    ).deploy(name="stage34-loop", work_pool_name="stage34-pool")
