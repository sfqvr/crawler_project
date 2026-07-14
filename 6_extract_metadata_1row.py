import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Literal, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain.chat_models import init_chat_model
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.prompts import ChatPromptTemplate

from minio_client import MinIOStorage


# =============================================================================
# CONFIG
# =============================================================================
load_dotenv()

INPUT_FOLDER_NAME = "parsed_jimmyl02"
INPUT_FILENAMES_PREFIX = "jimmyl02_postmortems"

storage = MinIOStorage()

DEBUG = True

# LM Studio / OpenAI-compatible endpoint
LLM_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL")
LLM_API_KEY = os.getenv("OPENAI_API_KEY")
LLM_PROVIDER = "openai"

PROMPT_VERSION = "stage6_v1"

# None = send full markdown to the model
MAX_MARKDOWN_CHARS_FOR_LLM: Optional[int] = None

ALLOWED_DOCUMENT_KINDS = {"postmortem", "incident_report", "status_update"}


# =============================================================================
# HELPERS
# =============================================================================
def debug_print(msg: str) -> None:
    if DEBUG:
        print(msg)


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_final_chat_message(agent_result) -> str:
    messages = agent_result.get("messages", []) if isinstance(agent_result, dict) else []
    for msg in reversed(messages):
        content = getattr(msg, "content", None)
        if isinstance(content, str) and content.strip():
            return content.strip()

        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()

    return ""


def prepare_markdown_for_llm(markdown_content: str) -> tuple[str, bool, int, int]:
    original_len = len(markdown_content)

    if MAX_MARKDOWN_CHARS_FOR_LLM is None:
        return markdown_content, False, original_len, original_len

    truncated = markdown_content[:MAX_MARKDOWN_CHARS_FOR_LLM]
    was_truncated = len(truncated) < original_len
    return truncated, was_truncated, original_len, len(truncated)


def get_stage4(row: dict) -> dict | None:
    stage4 = row.get("stage4")
    return stage4 if isinstance(stage4, dict) else None


def get_stage4_assessment(row: dict) -> dict | None:
    stage4 = get_stage4(row)
    if not stage4:
        return None
    assessment = stage4.get("assessment")
    return assessment if isinstance(assessment, dict) else None


def get_stage5(row: dict) -> dict | None:
    stage5 = row.get("stage5")
    return stage5 if isinstance(stage5, dict) else None


def get_stage5_markdown(row: dict) -> str:
    stage5 = get_stage5(row)
    if not stage5:
        return ""
    markdown_content = stage5.get("markdown_content", "")
    return markdown_content if isinstance(markdown_content, str) else ""


def is_stage6_candidate(row: dict) -> bool:
    stage4 = get_stage4(row)
    assessment = get_stage4_assessment(row)
    stage5 = get_stage5(row)
    markdown_content = get_stage5_markdown(row)

    if not stage4 or not assessment or not stage5:
        return False

    if stage4.get("success") is not True:
        return False

    if assessment.get("is_relevant") is not True:
        return False

    if assessment.get("document_kind") not in ALLOWED_DOCUMENT_KINDS:
        return False

    if stage5.get("success") is not True:
        return False

    if not markdown_content.strip():
        return False

    return True


def print_output_schema() -> None:
    print("=" * 80)
    print("=== OUTPUT SCHEMA (stage 6) ===")
    print("Root level: same fields as stage 5 output + one new field:")
    print("stage6: object | null")
    print()
    print("stage6 = null")
    print("  -> when the record does not satisfy stage6 candidate filters")
    print()
    print("stage6 object schema:")
    print("  success: bool")
    print("  error_message: str")
    print("  model_name: str")
    print("  prompt_version: str")
    print("  processed_at_utc: str")
    print("  input_markdown_length: int")
    print("  llm_input_markdown_length: int")
    print("  markdown_was_truncated: bool")
    print("  agent_final_message: str")
    print("  extraction: object | null")
    print()
    print("stage6.extraction schema:")
    print("  company: str | null")
    print("  date: str | null")
    print("  short_description: str")
    print("  metadata_filters: object")
    print("  searchable_text: object")
    print("  confidence: float")
    print("=" * 80)


# =============================================================================
# PYDANTIC SCHEMAS
# =============================================================================
class Stage6MetadataFilters(BaseModel):
    incident_categories: list[
        Literal[
            "Database",
            "Network",
            "Security/DDoS",
            "Hardware",
            "Application",
            "Configuration",
            "Deployment",
            "Capacity",
            "Performance/Latency",
            "Data Integrity/Corruption",
            "Human Error/Operational",
            "Authentication/Authorization",
            "Third-party dependency",
            "Unknown",
        ]
    ] = Field(
        default_factory=list,
        description=(
            "Normalized incident categories inferred from the document. "
            "Select ALL applicable categories. Multiple categories are allowed if the incident spans multiple domains "
            "(for example, a network misconfiguration causing a database outage)."
        ),
    )

    tech_stack: list[str] = Field(
        default_factory=list,
        description=(
            "Concrete technologies, protocols, databases, platforms, or systems mentioned in the document, "
            "for example: Postgres, BGP, TCP, Kafka, RabbitMQ, Redis, Kubernetes."
        ),
    )

    infrastructure: list[
        Literal[
            "Cloud",
            "Bare-metal",
            "Network Provider",
            "CDN",
            "DNS",
            "Kubernetes",
            "Datacenter",
            "Third-party service",
            "Message Broker/Queue",
            "Serverless",
            "Virtualization/Hypervisor",
            "Unknown",
        ]
    ] = Field(
        default_factory=list,
        description=(
            "Infrastructure layer or environment where the issue occurred. "
            "Select ALL applicable infrastructure layers if the incident spans multiple environments."
        ),
    )

    key_terms: list[str] = Field(
        default_factory=list,
        description=(
            "Concise domain-specific key terms useful for rough keyword search. "
            "Prefer concrete technical terms over generic words like outage, incident, or service."
        ),
    )


class Stage6SearchableText(BaseModel):
    symptoms: str = Field(
        description="Short English description of externally visible symptoms, failures, or user-facing effects."
    )

    root_cause: str = Field(
        description="Short English description of the actual root cause, if known from the document."
    )

    resolution: str = Field(
        description="Short English description of how the incident was resolved or mitigated."
    )

    lessons_learned: str = Field(
        description="Short English description of follow-up actions, lessons learned, or prevention steps. May be empty if not present."
    )


class Stage6Extraction(BaseModel):
    company: Optional[str] = Field(
        default=None,
        description="Company or organization name, if clearly identifiable from the document or provided metadata."
    )

    date: Optional[str] = Field(
        default=None,
        description="Main incident date in YYYY-MM-DD format if confidently identifiable, otherwise null."
    )

    short_description: str = Field(
        description="Short English description of the document and the incident it describes."
    )

    metadata_filters: Stage6MetadataFilters = Field(
        description="Normalized filterable metadata extracted from the document."
    )

    searchable_text: Stage6SearchableText = Field(
        description="Short structured English text fields useful for search and retrieval."
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Model confidence in the extracted metadata."
    )


class Stage6Result(BaseModel):
    success: bool = Field(
        description="Whether metadata extraction completed successfully."
    )

    error_message: str = Field(
        description="Error message if extraction failed, otherwise empty string."
    )

    model_name: str = Field(
        description="Model identifier used for stage6 extraction."
    )

    prompt_version: str = Field(
        description="Prompt version identifier for reproducibility."
    )

    processed_at_utc: str = Field(
        description="UTC timestamp when stage6 processing finished."
    )

    input_markdown_length: int = Field(
        description="Length of the original markdown content passed into stage6."
    )

    llm_input_markdown_length: int = Field(
        description="Length of markdown actually sent to the model."
    )

    markdown_was_truncated: bool = Field(
        description="Whether the markdown was truncated before being sent to the model."
    )

    agent_final_message: str = Field(
        description="Final short assistant chat message after tool submission, for debugging."
    )

    extraction: Optional[Stage6Extraction] = Field(
        default=None,
        description="Structured metadata extraction result."
    )


# =============================================================================
# AGENT + TOOL
# =============================================================================
submission_buffer: dict = {"extraction": None}


def _normalize_metadata_filters(value) -> Stage6MetadataFilters:
    if isinstance(value, Stage6MetadataFilters):
        return value
    if isinstance(value, dict):
        return Stage6MetadataFilters(**value)
    if value is None:
        return Stage6MetadataFilters()
    raise TypeError(f"Unsupported metadata_filters type: {type(value).__name__}")


def _normalize_searchable_text(value) -> Stage6SearchableText:
    if isinstance(value, Stage6SearchableText):
        return value
    if isinstance(value, dict):
        return Stage6SearchableText(**value)
    if value is None:
        return Stage6SearchableText(
            symptoms="",
            root_cause="",
            resolution="",
            lessons_learned="",
        )
    raise TypeError(f"Unsupported searchable_text type: {type(value).__name__}")


@tool(args_schema=Stage6Extraction)
def submit_stage6_metadata_to_system(
    company: Optional[str] = None,
    date: Optional[str] = None,
    short_description: str = "",
    metadata_filters=None,
    searchable_text=None,
    confidence: float = 0.0,
) -> str:
    """
    You MUST use this tool to submit the final stage6 metadata extraction to the system.
    """
    submission_buffer["extraction"] = Stage6Extraction(
        company=company,
        date=date,
        short_description=short_description,
        metadata_filters=_normalize_metadata_filters(metadata_filters),
        searchable_text=_normalize_searchable_text(searchable_text),
        confidence=confidence,
    )

    return (
        "SYSTEM SUCCESS: The stage6 metadata has been saved to the system. "
        "Your task is complete. Now write a very short acknowledgement in chat. "
        "STRICTLY DO NOT repeat the extracted metadata in chat."
    )


SYSTEM_PROMPT = """
You are a metadata extraction agent for incident-related technical documents.

You will receive:
1. Small metadata hints (name, source URL, source description, document kind)
2. A cleaned markdown document that already passed the relevance and markdown conversion stages

Your task is to extract structured metadata for downstream search, filtering, and RAG.

ALLOWED INTERNAL REASONING:
- You may think privately, including inside <think></think> tags if the model uses them.
- Do not expose your reasoning to the user in normal chat output.

STRICT WORKFLOW:
1. Read the provided metadata hints and markdown carefully.
2. Extract structured metadata only from what is supported by the content.
3. Use NULL or empty lists when information is not confidently available.
4. Fill EVERY required field exactly once.
5. Call `submit_stage6_metadata_to_system`.
6. After tool confirmation, write only a very short acknowledgement in chat.

IMPORTANT EXTRACTION RULES:
- Write all textual descriptive fields in English.
- `short_description` should be concise and factual.
- `company` may use metadata hints if clearly supported.
- `date` must be the main incident date in YYYY-MM-DD format if confidently identifiable; otherwise null.
- `incident_categories` is multi-label: select ALL applicable categories.
- `infrastructure` is multi-label: select ALL applicable infrastructure layers.
- Use `Unknown` only when a normalized category is needed but cannot be confidently determined.
- `tech_stack` should include concrete technologies and systems, not vague words.
- `key_terms` should be concise, concrete, and domain-specific. Avoid generic words like outage, incident, service.
- `searchable_text` fields should be short, factual, and useful for retrieval.
- Do not invent facts not supported by the markdown.
""".strip()


PROMPT_TEMPLATE = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        (
            "human",
            """Extract structured metadata from the following incident-related markdown document.

Metadata hints:
- name: {name}
- source_url: {url}
- source_description: {description}
- document_kind: {document_kind}
- markdown_length: {markdown_length}

Requirements:
- Use the markdown as the primary source of truth.
- Use the metadata hints only as supporting context.
- Return all descriptive text fields in English.
- Use null or empty lists when appropriate.
- Select ALL applicable categories and infrastructure tags when multiple apply.

Markdown document:
```markdown
{markdown_content}
```""",
        ),
    ]
)


def build_model():
    return init_chat_model(
        model=LLM_MODEL,
        model_provider=LLM_PROVIDER,
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY,
        temperature=0.0,
    )


def build_agent(model):
    return create_agent(
        model=model,
        tools=[submit_stage6_metadata_to_system],
    )


def run_stage6_for_row(agent, row: dict) -> Stage6Result:
    submission_buffer["extraction"] = None

    markdown_content = get_stage5_markdown(row)
    stage4_assessment = get_stage4_assessment(row) or {}

    markdown_for_llm, was_truncated, original_len, llm_len = prepare_markdown_for_llm(markdown_content)

    messages = PROMPT_TEMPLATE.invoke(
        {
            "name": row.get("name", ""),
            "url": row.get("url", ""),
            "description": row.get("description", ""),
            "document_kind": stage4_assessment.get("document_kind", "unknown"),
            "markdown_length": original_len,
            "markdown_content": markdown_for_llm,
        }
    ).messages

    try:
        result = agent.invoke({"messages": messages})
        final_chat_message = extract_final_chat_message(result)
        extraction = submission_buffer.get("extraction")

        if extraction is None:
            return Stage6Result(
                success=False,
                error_message="The model did not submit a structured metadata tool call.",
                model_name=LLM_MODEL,
                prompt_version=PROMPT_VERSION,
                processed_at_utc=now_iso_utc(),
                input_markdown_length=original_len,
                llm_input_markdown_length=llm_len,
                markdown_was_truncated=was_truncated,
                agent_final_message=final_chat_message,
                extraction=None,
            )

        return Stage6Result(
            success=True,
            error_message="",
            model_name=LLM_MODEL,
            prompt_version=PROMPT_VERSION,
            processed_at_utc=now_iso_utc(),
            input_markdown_length=original_len,
            llm_input_markdown_length=llm_len,
            markdown_was_truncated=was_truncated,
            agent_final_message=final_chat_message,
            extraction=extraction,
        )

    except Exception as e:
        return Stage6Result(
            success=False,
            error_message=f"{type(e).__name__}: {e}",
            model_name=LLM_MODEL,
            prompt_version=PROMPT_VERSION,
            processed_at_utc=now_iso_utc(),
            input_markdown_length=original_len,
            llm_input_markdown_length=llm_len,
            markdown_was_truncated=was_truncated,
            agent_final_message="",
            extraction=None,
        )


# =============================================================================
# MAIN
# =============================================================================
TEST_DICT = {"name": "Heroku", "url": "https://status.heroku.com/incidents/1091", "description": "Google Cloud Networking experienced reduced capacity for lower priority traffic such as batch, streaming and transfer operations from 19:30 US/Pacific on Thursday, 14 July 2022, through 15:02 US/Pacific on Friday, 15 July 2022. High-priority user-facing traffic was not affected. This service disruption resulted from an issue encountered during a combination of repair work and a routine network software upgrade rollout.", "error": False, "cleaned_html": "<html>\n<head>\n    <title>Incident 1091 | Heroku Status</title>\n    <!-- JavaScript for preloading header and footer web components -->\n    </head>\n  <body>\n    \n    <dialog>\n<!----></dialog>\n\n<div>\n    <section>\n      <div>\n        <div>\n          <span>i</span>\n        </div>\n      </div>\n      <div>\n        <a href=\"https://status.salesforce.com/products/Heroku\">Salesforce\n          Trust</a>\n        is now the primary channel for all Heroku incident and maintenance communications. The Heroku Status site,\n        API, and email notifications will remain in place as a parallel backup incident communications channel until\n        a longer-term strategy is finalized.\n        <a href=\"https://devcenter.heroku.com/articles/heroku-status\">Learn more.</a>\n      </div>\n    </section>\n  \n<div>\n  <h2>\n    Increased rate of \"H10 App Crashed\" errors\n  </h2>\n\n    <div>\n        <div>\n<!---->\n<!---->\n    <span>EU</span>\n</div>\n\n        <div>\n    <div>\n      <span>\n        Apps\n      </span>\n\n        <span>\n          28 hours, 36 minutes\n        </span>\n    </div>\n</div>\n    </div>\n\n  <div>\n      <h3>Follow-up Report</h3>\n      <div>\n  <div>\n        <div><p>Between April 3rd, 17:20 UTC and April 6th, 10:00 UTC, customers in the EU experienced elevated H10, H19, H21, and H26 errors. These errors accounted for less than 1% of the EU traffic during the impacted period. We sincerely apologize for any downtime arising from this issue and any adverse effects…</p></div>\n      <button>\n          Read more\n      </button>\n  </div>\n</div>\n\n  <h3>Activity</h3>\n\n  <div>\n    <ul>\n        <li>\n  <p>Resolved</p>\n  <div>\n<p>After an extended monitoring period, this incident is now resolved. We'd like to thank everyone for their patience over the past few days and we would again like to apologise for any inconvenience caused during this time.</p>\n<p>We will be publishing a full analysis as soon as possible, accessible via the incident page on the Heroku Status site.</p>\n</div>\n  <p>\n    Posted\n    9 years ago,\n    Apr 6, 2017 11:40 UTC\n  </p>\n</li>\n        <li>\n  <p>Monitoring</p>\n  <div><p>Our current metrics show H10 and H21 errors in the EU region have returned to normal levels and have been stable for the past 10 hours. We are continuing to monitor for any recurrence, but for customers who have been waiting to deploy we believe it is now safe to do so.</p></div>\n  <p>\n    Posted\n    9 years ago,\n    Apr 6, 2017 09:28 UTC\n  </p>\n</li>\n        <li>\n  <p>Monitoring</p>\n  <div>\n<p>Our engineers have pushed some changes that should mitigate the causes of the H10 errors. We will be closely monitoring the situation over the next few hours, as these changes take effect. </p>\n<p>Please note, some applications will see a large performance drop, 5000ms connect times in the majority of requests, or H21 errors, instead of H10 errors. </p>\n</div>\n  <p>\n    Posted\n    9 years ago,\n    Apr 5, 2017 23:48 UTC\n  </p>\n</li>\n        <li>\n  <p>Update</p>\n  <div><p>Our engineers are continuing to investigate the issue with increased H10 errors for EU apps. We will continue to update approximately every 6 hours, with the next one at about 0:00 UTC.</p></div>\n  <p>\n    Posted\n    9 years ago,\n    Apr 5, 2017 17:48 UTC\n  </p>\n</li>\n        <li>\n  <p>Update</p>\n  <div>\n<p>Our engineers are still investigating the issue with increased H10 errors for EU apps.</p>\n<p>The next update will be at 18:00UTC if the status of the investigation is still ongoing.</p>\n</div>\n  <p>\n    Posted\n    9 years ago,\n    Apr 5, 2017 12:46 UTC\n  </p>\n</li>\n        <li>\n  <p>Update</p>\n  <div>\n<p>Our engineers are continuing to investigate the issue with increased H10 errors for EU apps.</p>\n<p>As mentioned previously, this issue is more likely to be triggered during new releases - at this point we'd recommend holding off any non-essential deploys for as long as possible, or until the incident is finished.</p>\n<p>As part of ongoing maintenance work, EU apps may also encounter H19 errors for some requests.</p>\n</div>\n  <p>\n    Posted\n    9 years ago,\n    Apr 5, 2017 11:33 UTC\n  </p>\n</li>\n        <li>\n  <p>Update</p>\n  <div>\n<p>Following the scheduled EU maintenance on April 3rd, the dyno cache on a subset of our EU routing nodes drifted out of sync with our database, leading some applications to see an increase in H10 errors. This was identified in incident 1089 and a manual resync by one of our engineers caused the H10 errors for the EU region to drop back to normal levels.</p>\n<p>In incident 1090 we became aware that the number of H10s were rising again. This appears to relate to app releases (deploys and rollbacks) throughout the day in the EU region. Our engineers are continuing to mitigate the issue manually while continuing to investigate the root cause. These interventions are restricting the number of errors to a small percentage of the requests for the EU region.</p>\n<p>This issue is more likely to be triggered during new releases - at this point we'd recommend holding off any non-essential deploys for as long as possible, or until the incident is finished.</p>\n</div>\n  <p>\n    Posted\n    9 years ago,\n    Apr 5, 2017 09:34 UTC\n  </p>\n</li>\n        <li>\n  <p>Update</p>\n  <div><p>Our engineers are continuing to investigate. H10 errors are still affecting a small number of EU apps.</p></div>\n  <p>\n    Posted\n    9 years ago,\n    Apr 5, 2017 08:34 UTC\n  </p>\n</li>\n        <li>\n  <p>Issue</p>\n  <div><p>Our engineers are continuing to investigate “H10 App Crashed” errors</p></div>\n  <p>\n    Posted\n    9 years ago,\n    Apr 5, 2017 07:29 UTC\n  </p>\n</li>\n        <li>\n  <p>Investigating</p>\n  <div><p>Our engineers are investigating a reoccurrence of “H10 App Crashed” errors.</p></div>\n  <p>\n    Posted\n    9 years ago,\n    Apr 5, 2017 07:03 UTC\n  </p>\n</li>\n    </ul>\n  </div>\n</div>\n  <a href=\"/\">\n    Current status\n  </a>\n</div>\n\n</div>\n<footer>\n  <div>\n    <div>\n      <nav>\n        <h4>Resources &amp; Support</h4>\n        <ul>\n          <li><a href=\"https://devcenter.heroku.com/\">Documentation</a></li>\n          <li><a href=\"https://www.heroku.com/pricing\">Pricing</a></li>\n          <li><a href=\"https://blog.heroku.com/\">Blog</a></li>\n          <li><a href=\"https://devcenter.heroku.com/start\">Get Started</a></li>\n          <li><a href=\"https://devcenter.heroku.com/changelog\">Changelog</a></li>\n          <li><a href=\"https://heroku.com/support\">Support</a></li>\n          <li><a href=\"https://heroku.com/contact\">Contact</a></li>\n          <li><a href=\"https://www.salesforce.com/company/careers/\">Careers</a></li>\n        </ul>\n      </nav>\n      <nav>\n        <h4>Products</h4>\n        <ul>\n          <li><a href=\"https://heroku.com/platform\">Heroku Platform</a></li>\n          <li><a href=\"https://heroku.com/connect\">Heroku Connect</a></li>\n          <li><a href=\"https://heroku.com/postgres\">Heroku Postgres</a></li>\n          <li><a href=\"https://heroku.com/redis\">Heroku Key-Value Store</a></li>\n          <li><a href=\"https://heroku.com/kafka\">Kafka on Heroku</a></li>\n          <li><a href=\"https://heroku.com/enterprise\">Heroku Enterprise</a></li>\n          <li><a href=\"https://www.heroku.com/teams\">Heroku Teams</a></li>\n          <li><a href=\"https://elements.heroku.com/\">Elements Marketplace</a></li>\n        </ul>\n      </nav>\n      <nav>\n        <h4>Language Reference</h4>\n        <ul>\n          <li><a href=\"https://devcenter.heroku.com/categories/nodejs\">Node.js</a></li>\n          <li><a href=\"https://devcenter.heroku.com/categories/ruby\">Ruby</a></li>\n          <li><a href=\"https://devcenter.heroku.com/categories/java\">Java</a></li>\n          <li><a href=\"https://devcenter.heroku.com/categories/php\">PHP</a></li>\n          <li><a href=\"https://devcenter.heroku.com/categories/python\">Python</a></li>\n          <li><a href=\"https://devcenter.heroku.com/categories/go\">Go</a></li>\n          <li><a href=\"https://devcenter.heroku.com/categories/scala\">Scala</a></li>\n          <li><a href=\"https://devcenter.heroku.com/categories/clojure\">Clojure</a></li>\n        </ul>\n      </nav>\n      <nav>\n        <h4>Using Heroku</h4>\n        <ul>\n          <li><a href=\"https://dashboard.heroku.com/\">Dashboard</a></li>\n          <li><a href=\"https://data.heroku.com/\">Databases</a></li>\n          <li><a href=\"https://dataclips.heroku.com/\">Dataclips</a></li>\n        </ul>\n      </nav>\n    </div>\n    <div>\n      <ul>\n        <li>\n          <a href=\"https://status.heroku.com/feed\"> RSS</a>\n          <div>\n            <ul>\n              <li><a href=\"https://blog.heroku.com/feed\"></a></li>\n              <li><a href=\"https://blog.heroku.com/news/feed\"></a></li>\n              <li><a href=\"https://blog.heroku.com/engineering/feed\"></a></li>\n              <li><a href=\"https://devcenter.heroku.com/articles/feed\"></a></li>\n              <li><a href=\"https://devcenter.heroku.com/changelog/feed\"></a></li>\n              <li><a href=\"https://status.heroku.com/feed\"></a></li>\n            </ul>\n          </div>\n        </li>\n        <li>\n          <a href=\"https://twitter.com/herokustatus\"> Twitter</a>\n          <div>\n            <ul>\n              <li><a href=\"https://twitter.com/heroku\"></a></li>\n              <li><a href=\"https://twitter.com/herokudevcenter\"></a></li>\n              <li><a href=\"https://twitter.com/herokuchangelog\"></a></li>\n              <li><a href=\"https://twitter.com/herokustatus\"></a></li>\n            </ul>\n          </div>\n        </li>\n        <li><a href=\"https://facebook.com/heroku\">Facebook</a></li>\n        <li><a href=\"https://www.instagram.com/heroku/\" title=\"Heroku's Instagram\">Instagram</a></li>\n        <li><a href=\"https://github.com/heroku\">Github</a></li>\n        <li><a href=\"https://www.linkedin.com/company/heroku\">LinkedIn</a></li>\n      </ul>\n    </div>\n  </div>\n  <div>\n    <div>\n      <div>\n        <p>\n          ©\n          2026\n          Salesforce, Inc. All rights reserved. Various trademarks held by their respective owners. Salesforce Tower,\n          415 Mission Street, 3rd Floor, San Francisco, CA 94105, United States\n        </p>\n        <ul>\n          <li><a href=\"https://www.heroku.com/home\">heroku.com</a></li>\n          <li><a href=\"https://www.salesforce.com/company/legal/\">Legal</a></li>\n          <li><a href=\"https://www.salesforce.com/company/legal/sfdc-website-terms-of-service/\">Terms\n              of Service</a></li>\n          <li><a href=\"https://www.salesforce.com/company/privacy/\">Privacy Information</a></li>\n          <li><a href=\"https://www.salesforce.com/company/disclosure/\">Responsible Disclosure</a></li>\n          <li><a href=\"https://trust.salesforce.com/en/\">Trust</a></li>\n          <li><a href=\"https://www.salesforce.com/form/contact/contactme/?d=70130000000EeYa\">Contact</a></li>\n          <li><a href=\"#\">Cookie Preferences</a></li>\n          <li><a href=\"https://www.salesforce.com/form/other/privacy-request/?_gl=1*1fit1l2*_ga*MjI0Nzg1NjkyLjE3NDE4MDEwMzg.*_ga_62RHPFWB9M*MTc0NTk0NDY4NC40Ni4xLjE3NDU5NDYyNDIuMC4wLjA.\"><img src=\"/images/privacy-options.png\" alt=\"Privacy Choices  Icon\" width=\"30px\" height=\"14px\">Your Privacy Choices</a></li>\n        </ul>\n      </div>\n    </div>\n  </div>\n</footer>\n\n\n    </body>\n</html>", "crawl_success": True, "crawl_error_message": "", "crawl_error_primary": "", "crawl_error_fallback": "", "crawl_mode": "primary", "crawl_url_type": "NORMAL", "cleaned_html_length": 11180, "crawl_suspect": False, "crawl_attempt_count": 1, "crawl_primary_success": True, "crawl_fallback_used": False, "crawl_started_at_utc": "2026-03-16T03:28:20.985946+00:00", "crawl_finished_at_utc": "2026-03-16T03:29:34.515159+00:00", "crawl_elapsed_seconds": 73.53, "crawler_version_hint": "Crawl4AI 0.8.0", "debug_requested_url": "https://status.heroku.com/incidents/1091", "debug_result_url": "https://status.heroku.com/incidents/1091", "debug_status_code": 200.0, "debug_match_method": "batch_url_match", "debug_batch_id": 1, "stage4": {"success": True, "error_message": "", "model_name": "openrouter/hunter-alpha", "prompt_version": "stage4_v1", "processed_at_utc": "2026-03-16T10:11:28.113142+00:00", "input_html_length": 11180, "llm_input_html_length": 11180, "html_was_truncated": False, "agent_final_message": "Assessment submitted successfully.", "assessment": {"is_relevant": True, "can_extract_markdown": True, "reason": "The page contains a detailed incident report for Heroku's \"Increased rate of H10 App Crashed errors\" with timeline updates, impact description (less than 1% of EU traffic affected), root cause information (dyno cache drift), and resolution details. The content is well-structured and provides valuable incident information.", "document_kind": "incident_report", "contains_main_text": True, "contains_incident_information": True, "has_timeline": True, "has_root_cause": True, "has_impact_description": True, "has_resolution_or_mitigation": True, "has_action_items_or_lessons_learned": False, "language": "en", "confidence": 0.85}}, "stage5": {"success": True, "error_message": "", "model_name": "openrouter/hunter-alpha", "prompt_version": "stage5_v1", "processed_at_utc": "2026-03-16T16:21:46.102147+00:00", "input_html_length": 11180, "llm_input_html_length": 11180, "html_was_truncated": False, "agent_final_message": "The incident report has been converted to clean markdown and submitted successfully.", "markdown_length": 4307, "markdown_content": "**Note:** Salesforce Trust is now the primary channel for all Heroku incident and maintenance communications. The Heroku Status site, API, and email notifications will remain in place as a parallel backup incident communications channel until a longer-term strategy is finalized. [Learn more.](https://devcenter.heroku.com/articles/heroku-status)\n\n# Increased rate of \"H10 App Crashed\" errors\n\n**Region:** EU  \n**Service:** Apps  \n**Duration:** 28 hours, 36 minutes\n\n## Follow-up Report\n\nBetween April 3rd, 17:20 UTC and April 6th, 10:00 UTC, customers in the EU experienced elevated H10, H19, H21, and H26 errors. These errors accounted for less than 1% of the EU traffic during the impacted period. We sincerely apologize for any downtime arising from this issue and any adverse effects…\n\n## Activity\n\n### Resolved\n*Posted 9 years ago, Apr 6, 2017 11:40 UTC*\n\nAfter an extended monitoring period, this incident is now resolved. We'd like to thank everyone for their patience over the past few days and we would again like to apologise for any inconvenience caused during this time.\n\nWe will be publishing a full analysis as soon as possible, accessible via the incident page on the Heroku Status site.\n\n### Monitoring\n*Posted 9 years ago, Apr 6, 2017 09:28 UTC*\n\nOur current metrics show H10 and H21 errors in the EU region have returned to normal levels and have been stable for the past 10 hours. We are continuing to monitor for any recurrence, but for customers who have been waiting to deploy we believe it is now safe to do so.\n\n### Monitoring\n*Posted 9 years ago, Apr 5, 2017 23:48 UTC*\n\nOur engineers have pushed some changes that should mitigate the causes of the H10 errors. We will be closely monitoring the situation over the next few hours, as these changes take effect.\n\nPlease note, some applications will see a large performance drop, 5000ms connect times in the majority of requests, or H21 errors, instead of H10 errors.\n\n### Update\n*Posted 9 years ago, Apr 5, 2017 17:48 UTC*\n\nOur engineers are continuing to investigate the issue with increased H10 errors for EU apps. We will continue to update approximately every 6 hours, with the next one at about 0:00 UTC.\n\n### Update\n*Posted 9 years ago, Apr 5, 2017 12:46 UTC*\n\nOur engineers are still investigating the issue with increased H10 errors for EU apps.\n\nThe next update will be at 18:00UTC if the status of the investigation is still ongoing.\n\n### Update\n*Posted 9 years ago, Apr 5, 2017 11:33 UTC*\n\nOur engineers are continuing to investigate the issue with increased H10 errors for EU apps.\n\nAs mentioned previously, this issue is more likely to be triggered during new releases - at this point we'd recommend holding off any non-essential deploys for as long as possible, or until the incident is finished.\n\nAs part of ongoing maintenance work, EU apps may also encounter H19 errors for some requests.\n\n### Update\n*Posted 9 years ago, Apr 5, 2017 09:34 UTC*\n\nFollowing the scheduled EU maintenance on April 3rd, the dyno cache on a subset of our EU routing nodes drifted out of sync with our database, leading some applications to see an increase in H10 errors. This was identified in incident 1089 and a manual resync by one of our engineers caused the H10 errors for the EU region to drop back to normal levels.\n\nIn incident 1090 we became aware that the number of H10s were rising again. This appears to relate to app releases (deploys and rollbacks) throughout the day in the EU region. Our engineers are continuing to mitigate the issue manually while continuing to investigate the root cause. These interventions are restricting the number of errors to a small percentage of the requests for the EU region.\n\nThis issue is more likely to be triggered during new releases - at this point we'd recommend holding off any non-essential deploys for as long as possible, or until the incident is finished.\n\n### Update\n*Posted 9 years ago, Apr 5, 2017 08:34 UTC*\n\nOur engineers are continuing to investigate. H10 errors are still affecting a small number of EU apps.\n\n### Issue\n*Posted 9 years ago, Apr 5, 2017 07:29 UTC*\n\nOur engineers are continuing to investigate \"H10 App Crashed\" errors\n\n### Investigating\n*Posted 9 years ago, Apr 5, 2017 07:03 UTC*\n\nOur engineers are investigating a reoccurrence of \"H10 App Crashed\" errors."}}

async def main():
    # if len(sys.argv) < 2:
    #     print("Usage: script4.py <json_string>")
    #     sys.exit(1)

    # row_dict: dict = json.loads(sys.argv[1])
    row_dict = TEST_DICT
    if "url" not in row_dict:
        print("Error: input JSON must contain a 'url' field")
        sys.exit(1)

    print_output_schema()

    url = row_dict.get("url", "")

    debug_print("\n" + "=" * 80)
    debug_print(f"[START] {row_dict.get('name', '')}")
    debug_print(f"[URL] {url}")

    output_row = dict(row_dict)

    if not is_stage6_candidate(row_dict):
        output_row["stage6"] = None
        storage.append_json('silver-data', INPUT_FILENAMES_PREFIX, output_row)

        debug_print("[SKIP][STAGE6] stage6=null, запись не проходит фильтры stage6")
        debug_print("\n" + "=" * 80)
        debug_print("=== ГОТОВО ===")
        return

    assessment = get_stage4_assessment(row_dict) or {}
    kind = assessment.get("document_kind", "unknown")
    debug_print(f"[CANDIDATE][STAGE6] document_kind={kind}")

    model = build_model()
    agent = build_agent(model)

    stage6_result = run_stage6_for_row(agent, row_dict)
    output_row["stage6"] = stage6_result.model_dump()

    storage.append_json('silver-data', INPUT_FILENAMES_PREFIX, output_row)

    if stage6_result.success and stage6_result.extraction is not None:
        extraction = stage6_result.extraction
        debug_print(
            "[OK][STAGE6] "
            f"kind={kind}, "
            f"company={extraction.company}, "
            f"date={extraction.date}, "
            f"confidence={extraction.confidence:.2f}"
        )
    else:
        debug_print(f"[FAIL][STAGE6] {stage6_result.error_message}")

    debug_print("\n" + "=" * 80)
    debug_print("=== ГОТОВО ===")
    debug_print(f"Успех: {stage6_result.success}")


if __name__ == "__main__":
    asyncio.run(main())
