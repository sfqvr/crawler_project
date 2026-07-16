import json
import sys
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from minio_client import MinIOStorage, INPUT_FILENAMES_PREFIX


INPUT_FOLDER_NAME = "parsed_jimmyl02"
# INPUT_FILENAMES_PREFIX = "jimmyl02_postmortems"

storage = MinIOStorage()


def safe_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    result = []
    seen = set()

    for item in value:
        text = safe_str(item)
        if not text:
            continue
        if text not in seen:
            seen.add(text)
            result.append(text)

    return result


def join_terms(values: list[str]) -> str:
    return " ".join(values)


def build_embedding_text(
    company: str,
    short_description: str,
    symptoms: str,
    root_cause: str,
    resolution: str,
    lessons_learned: str,
) -> str:
    parts = [
        company,
        short_description,
        symptoms,
        root_cause,
        resolution,
        lessons_learned,
    ]
    parts = [part.strip() for part in parts if isinstance(part, str) and part.strip()]
    return "\n\n".join(parts)


def is_qdrant_candidate(row: dict) -> bool:
    # stage4 = row.get("stage4")
    stage5 = row.get("stage5")
    stage6 = row.get("stage6")

    # if not isinstance(stage4, dict):
    #     return False
    if not isinstance(stage5, dict):
        return False
    if not isinstance(stage6, dict):
        return False

    if row.get("stage4_success") is not True:
        return False
    if stage5.get("success") is not True:
        return False
    if stage6.get("success") is not True:
        return False

    # assessment = stage4.get("assessment")
    extraction = stage6.get("extraction")

    # if not isinstance(assessment, dict):
    #     return False
    if not isinstance(extraction, dict):
        return False

    if row.get("is_relevant") is not True:
        return False

    markdown_content = stage5.get("markdown_content", "")
    if not isinstance(markdown_content, str) or not markdown_content.strip():
        return False

    return True


def transform_row(row: dict) -> dict:
    # stage4 = row["stage4"]
    stage5 = row["stage5"]
    stage6 = row["stage6"]

    # assessment = stage4["assessment"]
    extraction = stage6["extraction"]

    metadata_filters = extraction.get("metadata_filters", {}) or {}
    searchable_text = extraction.get("searchable_text", {}) or {}

    url = safe_str(row.get("url"))
    name = safe_str(row.get("name"))
    description = safe_str(row.get("description"))
    company = safe_str(extraction.get("company"))
    date = extraction.get("date")
    short_description = safe_str(extraction.get("short_description"))
    document_kind = safe_str(row.get("document_kind"))

    incident_categories = normalize_string_list(metadata_filters.get("incident_categories"))
    tech_stack = normalize_string_list(metadata_filters.get("tech_stack"))
    infrastructure = normalize_string_list(metadata_filters.get("infrastructure"))
    key_terms = normalize_string_list(metadata_filters.get("key_terms"))

    symptoms = safe_str(searchable_text.get("symptoms"))
    root_cause = safe_str(searchable_text.get("root_cause"))
    resolution = safe_str(searchable_text.get("resolution"))
    lessons_learned = safe_str(searchable_text.get("lessons_learned"))

    markdown_content = safe_str(stage5.get("markdown_content"))

    embedding_text = build_embedding_text(
        company=company,
        short_description=short_description,
        symptoms=symptoms,
        root_cause=root_cause,
        resolution=resolution,
        lessons_learned=lessons_learned,
    )

    qdrant_point_id = str(uuid5(NAMESPACE_URL, url)) if url else None

    return {
        "qdrant_point_id": qdrant_point_id,
        "url": url,
        "name": name,
        "description": description,
        "company": company or None,
        "date": date,
        "document_kind": document_kind or None,
        "short_description": short_description,
        "incident_categories": incident_categories,
        "tech_stack": tech_stack,
        "tech_stack_text": join_terms(tech_stack).lower(),
        "infrastructure": infrastructure,
        "key_terms": key_terms,
        "key_terms_text": join_terms(key_terms).lower(),
        "symptoms": symptoms,
        "root_cause": root_cause,
        "resolution": resolution,
        "lessons_learned": lessons_learned,
        "markdown_content": markdown_content,
        "embedding_text": embedding_text,
        "stage6_confidence": extraction.get("confidence"),
    }


def print_output_schema() -> None:
    schema = {
        "qdrant_point_id": "str | null",
        "url": "str",
        "name": "str",
        "description": "str",
        "company": "str | null",
        "date": "str | null",
        "document_kind": "str | null",
        "short_description": "str",
        "incident_categories": "list[str]",
        "tech_stack": "list[str]",
        "tech_stack_text": "str",
        "infrastructure": "list[str]",
        "key_terms": "list[str]",
        "key_terms_text": "str",
        "symptoms": "str",
        "root_cause": "str",
        "resolution": "str",
        "lessons_learned": "str",
        "markdown_content": "str",
        "embedding_text": "str",
        "stage6_confidence": "float | null",
    }

    print("=" * 80)
    print("=== OUTPUT QDRANT-READY SCHEMA ===")
    for key, value in schema.items():
        print(f"{key}: {value}")
    print("=" * 80)


# TEST_DICT = {"name": "Heroku", "url": "https://status.heroku.com/incidents/1091", "description": "Google Cloud Networking experienced reduced capacity for lower priority traffic such as batch, streaming and transfer operations from 19:30 US/Pacific on Thursday, 14 July 2022, through 15:02 US/Pacific on Friday, 15 July 2022. High-priority user-facing traffic was not affected. This service disruption resulted from an issue encountered during a combination of repair work and a routine network software upgrade rollout.", "error": False, "cleaned_html": "<html>\n<head>\n    <title>Incident 1091 | Heroku Status</title>\n    <!-- JavaScript for preloading header and footer web components -->\n    </head>\n  <body>\n    \n    <dialog>\n<!----></dialog>\n\n<div>\n    <section>\n      <div>\n        <div>\n          <span>i</span>\n        </div>\n      </div>\n      <div>\n        <a href=\"https://status.salesforce.com/products/Heroku\">Salesforce\n          Trust</a>\n        is now the primary channel for all Heroku incident and maintenance communications. The Heroku Status site,\n        API, and email notifications will remain in place as a parallel backup incident communications channel until\n        a longer-term strategy is finalized.\n        <a href=\"https://devcenter.heroku.com/articles/heroku-status\">Learn more.</a>\n      </div>\n    </section>\n  \n<div>\n  <h2>\n    Increased rate of \"H10 App Crashed\" errors\n  </h2>\n\n    <div>\n        <div>\n<!---->\n<!---->\n    <span>EU</span>\n</div>\n\n        <div>\n    <div>\n      <span>\n        Apps\n      </span>\n\n        <span>\n          28 hours, 36 minutes\n        </span>\n    </div>\n</div>\n    </div>\n\n  <div>\n      <h3>Follow-up Report</h3>\n      <div>\n  <div>\n        <div><p>Between April 3rd, 17:20 UTC and April 6th, 10:00 UTC, customers in the EU experienced elevated H10, H19, H21, and H26 errors. These errors accounted for less than 1% of the EU traffic during the impacted period. We sincerely apologize for any downtime arising from this issue and any adverse effects…</p></div>\n      <button>\n          Read more\n      </button>\n  </div>\n</div>\n\n  <h3>Activity</h3>\n\n  <div>\n    <ul>\n        <li>\n  <p>Resolved</p>\n  <div>\n<p>After an extended monitoring period, this incident is now resolved. We'd like to thank everyone for their patience over the past few days and we would again like to apologise for any inconvenience caused during this time.</p>\n<p>We will be publishing a full analysis as soon as possible, accessible via the incident page on the Heroku Status site.</p>\n</div>\n  <p>\n    Posted\n    9 years ago,\n    Apr 6, 2017 11:40 UTC\n  </p>\n</li>\n        <li>\n  <p>Monitoring</p>\n  <div><p>Our current metrics show H10 and H21 errors in the EU region have returned to normal levels and have been stable for the past 10 hours. We are continuing to monitor for any recurrence, but for customers who have been waiting to deploy we believe it is now safe to do so.</p></div>\n  <p>\n    Posted\n    9 years ago,\n    Apr 6, 2017 09:28 UTC\n  </p>\n</li>\n        <li>\n  <p>Monitoring</p>\n  <div>\n<p>Our engineers have pushed some changes that should mitigate the causes of the H10 errors. We will be closely monitoring the situation over the next few hours, as these changes take effect. </p>\n<p>Please note, some applications will see a large performance drop, 5000ms connect times in the majority of requests, or H21 errors, instead of H10 errors. </p>\n</div>\n  <p>\n    Posted\n    9 years ago,\n    Apr 5, 2017 23:48 UTC\n  </p>\n</li>\n        <li>\n  <p>Update</p>\n  <div><p>Our engineers are continuing to investigate the issue with increased H10 errors for EU apps. We will continue to update approximately every 6 hours, with the next one at about 0:00 UTC.</p></div>\n  <p>\n    Posted\n    9 years ago,\n    Apr 5, 2017 17:48 UTC\n  </p>\n</li>\n        <li>\n  <p>Update</p>\n  <div>\n<p>Our engineers are still investigating the issue with increased H10 errors for EU apps.</p>\n<p>The next update will be at 18:00UTC if the status of the investigation is still ongoing.</p>\n</div>\n  <p>\n    Posted\n    9 years ago,\n    Apr 5, 2017 12:46 UTC\n  </p>\n</li>\n        <li>\n  <p>Update</p>\n  <div>\n<p>Our engineers are continuing to investigate the issue with increased H10 errors for EU apps.</p>\n<p>As mentioned previously, this issue is more likely to be triggered during new releases - at this point we'd recommend holding off any non-essential deploys for as long as possible, or until the incident is finished.</p>\n<p>As part of ongoing maintenance work, EU apps may also encounter H19 errors for some requests.</p>\n</div>\n  <p>\n    Posted\n    9 years ago,\n    Apr 5, 2017 11:33 UTC\n  </p>\n</li>\n        <li>\n  <p>Update</p>\n  <div>\n<p>Following the scheduled EU maintenance on April 3rd, the dyno cache on a subset of our EU routing nodes drifted out of sync with our database, leading some applications to see an increase in H10 errors. This was identified in incident 1089 and a manual resync by one of our engineers caused the H10 errors for the EU region to drop back to normal levels.</p>\n<p>In incident 1090 we became aware that the number of H10s were rising again. This appears to relate to app releases (deploys and rollbacks) throughout the day in the EU region. Our engineers are continuing to mitigate the issue manually while continuing to investigate the root cause. These interventions are restricting the number of errors to a small percentage of the requests for the EU region.</p>\n<p>This issue is more likely to be triggered during new releases - at this point we'd recommend holding off any non-essential deploys for as long as possible, or until the incident is finished.</p>\n</div>\n  <p>\n    Posted\n    9 years ago,\n    Apr 5, 2017 09:34 UTC\n  </p>\n</li>\n        <li>\n  <p>Update</p>\n  <div><p>Our engineers are continuing to investigate. H10 errors are still affecting a small number of EU apps.</p></div>\n  <p>\n    Posted\n    9 years ago,\n    Apr 5, 2017 08:34 UTC\n  </p>\n</li>\n        <li>\n  <p>Issue</p>\n  <div><p>Our engineers are continuing to investigate “H10 App Crashed” errors</p></div>\n  <p>\n    Posted\n    9 years ago,\n    Apr 5, 2017 07:29 UTC\n  </p>\n</li>\n        <li>\n  <p>Investigating</p>\n  <div><p>Our engineers are investigating a reoccurrence of “H10 App Crashed” errors.</p></div>\n  <p>\n    Posted\n    9 years ago,\n    Apr 5, 2017 07:03 UTC\n  </p>\n</li>\n    </ul>\n  </div>\n</div>\n  <a href=\"/\">\n    Current status\n  </a>\n</div>\n\n</div>\n<footer>\n  <div>\n    <div>\n      <nav>\n        <h4>Resources &amp; Support</h4>\n        <ul>\n          <li><a href=\"https://devcenter.heroku.com/\">Documentation</a></li>\n          <li><a href=\"https://www.heroku.com/pricing\">Pricing</a></li>\n          <li><a href=\"https://blog.heroku.com/\">Blog</a></li>\n          <li><a href=\"https://devcenter.heroku.com/start\">Get Started</a></li>\n          <li><a href=\"https://devcenter.heroku.com/changelog\">Changelog</a></li>\n          <li><a href=\"https://heroku.com/support\">Support</a></li>\n          <li><a href=\"https://heroku.com/contact\">Contact</a></li>\n          <li><a href=\"https://www.salesforce.com/company/careers/\">Careers</a></li>\n        </ul>\n      </nav>\n      <nav>\n        <h4>Products</h4>\n        <ul>\n          <li><a href=\"https://heroku.com/platform\">Heroku Platform</a></li>\n          <li><a href=\"https://heroku.com/connect\">Heroku Connect</a></li>\n          <li><a href=\"https://heroku.com/postgres\">Heroku Postgres</a></li>\n          <li><a href=\"https://heroku.com/redis\">Heroku Key-Value Store</a></li>\n          <li><a href=\"https://heroku.com/kafka\">Kafka on Heroku</a></li>\n          <li><a href=\"https://heroku.com/enterprise\">Heroku Enterprise</a></li>\n          <li><a href=\"https://www.heroku.com/teams\">Heroku Teams</a></li>\n          <li><a href=\"https://elements.heroku.com/\">Elements Marketplace</a></li>\n        </ul>\n      </nav>\n      <nav>\n        <h4>Language Reference</h4>\n        <ul>\n          <li><a href=\"https://devcenter.heroku.com/categories/nodejs\">Node.js</a></li>\n          <li><a href=\"https://devcenter.heroku.com/categories/ruby\">Ruby</a></li>\n          <li><a href=\"https://devcenter.heroku.com/categories/java\">Java</a></li>\n          <li><a href=\"https://devcenter.heroku.com/categories/php\">PHP</a></li>\n          <li><a href=\"https://devcenter.heroku.com/categories/python\">Python</a></li>\n          <li><a href=\"https://devcenter.heroku.com/categories/go\">Go</a></li>\n          <li><a href=\"https://devcenter.heroku.com/categories/scala\">Scala</a></li>\n          <li><a href=\"https://devcenter.heroku.com/categories/clojure\">Clojure</a></li>\n        </ul>\n      </nav>\n      <nav>\n        <h4>Using Heroku</h4>\n        <ul>\n          <li><a href=\"https://dashboard.heroku.com/\">Dashboard</a></li>\n          <li><a href=\"https://data.heroku.com/\">Databases</a></li>\n          <li><a href=\"https://dataclips.heroku.com/\">Dataclips</a></li>\n        </ul>\n      </nav>\n    </div>\n    <div>\n      <ul>\n        <li>\n          <a href=\"https://status.heroku.com/feed\"> RSS</a>\n          <div>\n            <ul>\n              <li><a href=\"https://blog.heroku.com/feed\"></a></li>\n              <li><a href=\"https://blog.heroku.com/news/feed\"></a></li>\n              <li><a href=\"https://blog.heroku.com/engineering/feed\"></a></li>\n              <li><a href=\"https://devcenter.heroku.com/articles/feed\"></a></li>\n              <li><a href=\"https://devcenter.heroku.com/changelog/feed\"></a></li>\n              <li><a href=\"https://status.heroku.com/feed\"></a></li>\n            </ul>\n          </div>\n        </li>\n        <li>\n          <a href=\"https://twitter.com/herokustatus\"> Twitter</a>\n          <div>\n            <ul>\n              <li><a href=\"https://twitter.com/heroku\"></a></li>\n              <li><a href=\"https://twitter.com/herokudevcenter\"></a></li>\n              <li><a href=\"https://twitter.com/herokuchangelog\"></a></li>\n              <li><a href=\"https://twitter.com/herokustatus\"></a></li>\n            </ul>\n          </div>\n        </li>\n        <li><a href=\"https://facebook.com/heroku\">Facebook</a></li>\n        <li><a href=\"https://www.instagram.com/heroku/\" title=\"Heroku's Instagram\">Instagram</a></li>\n        <li><a href=\"https://github.com/heroku\">Github</a></li>\n        <li><a href=\"https://www.linkedin.com/company/heroku\">LinkedIn</a></li>\n      </ul>\n    </div>\n  </div>\n  <div>\n    <div>\n      <div>\n        <p>\n          ©\n          2026\n          Salesforce, Inc. All rights reserved. Various trademarks held by their respective owners. Salesforce Tower,\n          415 Mission Street, 3rd Floor, San Francisco, CA 94105, United States\n        </p>\n        <ul>\n          <li><a href=\"https://www.heroku.com/home\">heroku.com</a></li>\n          <li><a href=\"https://www.salesforce.com/company/legal/\">Legal</a></li>\n          <li><a href=\"https://www.salesforce.com/company/legal/sfdc-website-terms-of-service/\">Terms\n              of Service</a></li>\n          <li><a href=\"https://www.salesforce.com/company/privacy/\">Privacy Information</a></li>\n          <li><a href=\"https://www.salesforce.com/company/disclosure/\">Responsible Disclosure</a></li>\n          <li><a href=\"https://trust.salesforce.com/en/\">Trust</a></li>\n          <li><a href=\"https://www.salesforce.com/form/contact/contactme/?d=70130000000EeYa\">Contact</a></li>\n          <li><a href=\"#\">Cookie Preferences</a></li>\n          <li><a href=\"https://www.salesforce.com/form/other/privacy-request/?_gl=1*1fit1l2*_ga*MjI0Nzg1NjkyLjE3NDE4MDEwMzg.*_ga_62RHPFWB9M*MTc0NTk0NDY4NC40Ni4xLjE3NDU5NDYyNDIuMC4wLjA.\"><img src=\"/images/privacy-options.png\" alt=\"Privacy Choices  Icon\" width=\"30px\" height=\"14px\">Your Privacy Choices</a></li>\n        </ul>\n      </div>\n    </div>\n  </div>\n</footer>\n\n\n    </body>\n</html>", "crawl_success": True, "crawl_error_message": "", "crawl_error_primary": "", "crawl_error_fallback": "", "crawl_mode": "primary", "crawl_url_type": "NORMAL", "cleaned_html_length": 11180, "crawl_suspect": False, "crawl_attempt_count": 1, "crawl_primary_success": True, "crawl_fallback_used": False, "crawl_started_at_utc": "2026-03-16T03:28:20.985946+00:00", "crawl_finished_at_utc": "2026-03-16T03:29:34.515159+00:00", "crawl_elapsed_seconds": 73.53, "crawler_version_hint": "Crawl4AI 0.8.0", "debug_requested_url": "https://status.heroku.com/incidents/1091", "debug_result_url": "https://status.heroku.com/incidents/1091", "debug_status_code": 200.0, "debug_match_method": "batch_url_match", "debug_batch_id": 1, "stage4": {"success": True, "error_message": "", "model_name": "openrouter/hunter-alpha", "prompt_version": "stage4_v1", "processed_at_utc": "2026-03-16T10:11:28.113142+00:00", "input_html_length": 11180, "llm_input_html_length": 11180, "html_was_truncated": False, "agent_final_message": "Assessment submitted successfully.", "assessment": {"is_relevant": True, "can_extract_markdown": True, "reason": "The page contains a detailed incident report for Heroku's \"Increased rate of H10 App Crashed errors\" with timeline updates, impact description (less than 1% of EU traffic affected), root cause information (dyno cache drift), and resolution details. The content is well-structured and provides valuable incident information.", "document_kind": "incident_report", "contains_main_text": True, "contains_incident_information": True, "has_timeline": True, "has_root_cause": True, "has_impact_description": True, "has_resolution_or_mitigation": True, "has_action_items_or_lessons_learned": False, "language": "en", "confidence": 0.85}}, "stage5": {"success": True, "error_message": "", "model_name": "openrouter/hunter-alpha", "prompt_version": "stage5_v1", "processed_at_utc": "2026-03-16T16:21:46.102147+00:00", "input_html_length": 11180, "llm_input_html_length": 11180, "html_was_truncated": False, "agent_final_message": "The incident report has been converted to clean markdown and submitted successfully.", "markdown_length": 4307, "markdown_content": "**Note:** Salesforce Trust is now the primary channel for all Heroku incident and maintenance communications. The Heroku Status site, API, and email notifications will remain in place as a parallel backup incident communications channel until a longer-term strategy is finalized. [Learn more.](https://devcenter.heroku.com/articles/heroku-status)\n\n# Increased rate of \"H10 App Crashed\" errors\n\n**Region:** EU  \n**Service:** Apps  \n**Duration:** 28 hours, 36 minutes\n\n## Follow-up Report\n\nBetween April 3rd, 17:20 UTC and April 6th, 10:00 UTC, customers in the EU experienced elevated H10, H19, H21, and H26 errors. These errors accounted for less than 1% of the EU traffic during the impacted period. We sincerely apologize for any downtime arising from this issue and any adverse effects…\n\n## Activity\n\n### Resolved\n*Posted 9 years ago, Apr 6, 2017 11:40 UTC*\n\nAfter an extended monitoring period, this incident is now resolved. We'd like to thank everyone for their patience over the past few days and we would again like to apologise for any inconvenience caused during this time.\n\nWe will be publishing a full analysis as soon as possible, accessible via the incident page on the Heroku Status site.\n\n### Monitoring\n*Posted 9 years ago, Apr 6, 2017 09:28 UTC*\n\nOur current metrics show H10 and H21 errors in the EU region have returned to normal levels and have been stable for the past 10 hours. We are continuing to monitor for any recurrence, but for customers who have been waiting to deploy we believe it is now safe to do so.\n\n### Monitoring\n*Posted 9 years ago, Apr 5, 2017 23:48 UTC*\n\nOur engineers have pushed some changes that should mitigate the causes of the H10 errors. We will be closely monitoring the situation over the next few hours, as these changes take effect.\n\nPlease note, some applications will see a large performance drop, 5000ms connect times in the majority of requests, or H21 errors, instead of H10 errors.\n\n### Update\n*Posted 9 years ago, Apr 5, 2017 17:48 UTC*\n\nOur engineers are continuing to investigate the issue with increased H10 errors for EU apps. We will continue to update approximately every 6 hours, with the next one at about 0:00 UTC.\n\n### Update\n*Posted 9 years ago, Apr 5, 2017 12:46 UTC*\n\nOur engineers are still investigating the issue with increased H10 errors for EU apps.\n\nThe next update will be at 18:00UTC if the status of the investigation is still ongoing.\n\n### Update\n*Posted 9 years ago, Apr 5, 2017 11:33 UTC*\n\nOur engineers are continuing to investigate the issue with increased H10 errors for EU apps.\n\nAs mentioned previously, this issue is more likely to be triggered during new releases - at this point we'd recommend holding off any non-essential deploys for as long as possible, or until the incident is finished.\n\nAs part of ongoing maintenance work, EU apps may also encounter H19 errors for some requests.\n\n### Update\n*Posted 9 years ago, Apr 5, 2017 09:34 UTC*\n\nFollowing the scheduled EU maintenance on April 3rd, the dyno cache on a subset of our EU routing nodes drifted out of sync with our database, leading some applications to see an increase in H10 errors. This was identified in incident 1089 and a manual resync by one of our engineers caused the H10 errors for the EU region to drop back to normal levels.\n\nIn incident 1090 we became aware that the number of H10s were rising again. This appears to relate to app releases (deploys and rollbacks) throughout the day in the EU region. Our engineers are continuing to mitigate the issue manually while continuing to investigate the root cause. These interventions are restricting the number of errors to a small percentage of the requests for the EU region.\n\nThis issue is more likely to be triggered during new releases - at this point we'd recommend holding off any non-essential deploys for as long as possible, or until the incident is finished.\n\n### Update\n*Posted 9 years ago, Apr 5, 2017 08:34 UTC*\n\nOur engineers are continuing to investigate. H10 errors are still affecting a small number of EU apps.\n\n### Issue\n*Posted 9 years ago, Apr 5, 2017 07:29 UTC*\n\nOur engineers are continuing to investigate \"H10 App Crashed\" errors\n\n### Investigating\n*Posted 9 years ago, Apr 5, 2017 07:03 UTC*\n\nOur engineers are investigating a reoccurrence of \"H10 App Crashed\" errors."}, "stage6": {"success": True, "error_message": "", "model_name": "deepseek/deepseek-v4-flash", "prompt_version": "stage6_v1", "processed_at_utc": "2026-07-14T10:20:00.605351+00:00", "input_markdown_length": 4307, "llm_input_markdown_length": 4307, "markdown_was_truncated": False, "agent_final_message": "Metadata extracted and submitted successfully.", "extraction": {"company": "Heroku", "date": "2017-04-03", "short_description": "Heroku experienced an increased rate of H10 App Crashed errors (along with H19, H21, H26 errors) affecting less than 1% of EU traffic following scheduled EU maintenance. The root cause was a dyno cache drift on EU routing nodes that led to intermittent app crashes, especially during new releases.", "metadata_filters": {"incident_categories": ["Application", "Configuration"], "tech_stack": ["Heroku dynos", "EU routing nodes", "Dyno cache", "Heroku database"], "infrastructure": ["Cloud"], "key_terms": ["H10 errors", "H19 errors", "H21 errors", "H26 errors", "dyno cache drift", "app crashes", "EU region", "app deploys", "rollbacks"]}, "searchable_text": {"symptoms": "Elevated H10, H19, H21, and H26 errors affecting less than 1% of EU traffic; app crashes, performance drops with 5000ms connect times, and errors triggered especially during new releases and deploys.", "root_cause": "Following scheduled EU maintenance on April 3rd, the dyno cache on a subset of EU routing nodes drifted out of sync with the database. Manual resync temporarily resolved the issue, but errors resurged due to app deploys and rollbacks triggering further cache drift.", "resolution": "Engineers manually resynced the dyno cache multiple times and pushed mitigation changes. After monitoring for 10 hours with stable metrics, the incident was declared resolved.", "lessons_learned": "Full analysis to be published. Customers were advised to hold off non-essential deploys during the incident to reduce error triggers."}, "confidence": 0.92}}}
def main():
    if len(sys.argv) < 2:
        print("Usage: script5.py <json_string>")
        sys.exit(1)

    row: dict = json.loads(sys.argv[1])
    # row = TEST_DICT

    if "url" not in row:
        print("Error: input JSON must contain a 'url' field")
        sys.exit(1)

    print_output_schema()

    url = row.get("url", "")
    output_row = dict(row)
    output_row["cleaned_html"] = storage.get_html('raw-data',INPUT_FILENAMES_PREFIX,url)
    output_row["stage5"]["markdown_content"] = storage.get_markdown('raw-data',INPUT_FILENAMES_PREFIX,url)


    if not is_qdrant_candidate(output_row):
        print("[SKIP] Запись не проходит qdrant-candidate фильтры")
        print("=" * 80)
        print("=== QDRANT DATASET PREPARATION COMPLETE ===")
        print("Output rows: 0")
        print("Filtered out: 1")
        print("=" * 80)
        return

    transformed = transform_row(output_row)
    # storage.append_json('silver-data', INPUT_FILENAMES_PREFIX, transformed)

    print("=" * 80)
    print("=== QDRANT DATASET PREPARATION COMPLETE ===")
    print("Output rows: 1")
    print("Filtered out: 0")
    print(f"URL: {transformed['url']}")
    print("=" * 80)

    output_row |= transformed

    storage.append_json('silver-data', INPUT_FILENAMES_PREFIX, transformed)
    
# удаляем большие объекты из выходной строки так как иначе консольный аргумент слишком длинный
    output_row["cleaned_html"] = ""
    output_row["stage5"]["markdown_content"] = ""
    print("RESULT_JSON:" + json.dumps(output_row, ensure_ascii=False))

if __name__ == "__main__":
    main()
