import asyncio
import json
import sys
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field
from langchain.chat_models import init_chat_model
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.prompts import ChatPromptTemplate

from dotenv import load_dotenv
import os

from minio_client import MinIOStorage

load_dotenv()

# =============================================================================
# CONFIG
# =============================================================================
INPUT_FOLDER_NAME = "parsed_jimmyl02"
INPUT_FILENAMES_PREFIX = "jimmyl02_postmortems"

storage = MinIOStorage()

DEBUG = True

# LM Studio / OpenAI-compatible endpoint
LLM_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL")
LLM_API_KEY = os.getenv("OPENAI_API_KEY")
LLM_PROVIDER = "openai"

# Если HTML слишком большой, можно ограничить размер.
# None = отправлять весь cleaned_html в модель.
MAX_HTML_CHARS_FOR_LLM: Optional[int] = None

PROMPT_VERSION = "stage5_v1"

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


def print_output_schema() -> None:
    print("=" * 80)
    print("=== OUTPUT SCHEMA (stage 5) ===")
    print("Root level: same fields as stage 4 output + one new field:")
    print("stage5: object | null")
    print()
    print("stage5 = null")
    print("  -> when the record does not satisfy stage5 candidate filters")
    print()
    print("stage5 object schema:")
    print("  success: bool")
    print("  error_message: str")
    print("  model_name: str")
    print("  prompt_version: str")
    print("  processed_at_utc: str")
    print("  input_html_length: int")
    print("  llm_input_html_length: int")
    print("  html_was_truncated: bool")
    print("  agent_final_message: str")
    print("  markdown_length: int")
    print("  markdown_content: str")
    print("=" * 80)


def prepare_html_for_llm(cleaned_html: str) -> tuple[str, bool, int, int]:
    original_len = len(cleaned_html)

    if MAX_HTML_CHARS_FOR_LLM is None:
        return cleaned_html, False, original_len, original_len

    truncated = cleaned_html[:MAX_HTML_CHARS_FOR_LLM]
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


def is_stage5_candidate(row: dict) -> bool:
    stage4 = get_stage4(row)
    assessment = get_stage4_assessment(row)

    if not stage4 or not assessment:
        return False

    if stage4.get("success") is not True:
        return False

    if assessment.get("is_relevant") is not True:
        return False

    if assessment.get("can_extract_markdown") is not True:
        return False

    if assessment.get("document_kind") not in ALLOWED_DOCUMENT_KINDS:
        return False

    cleaned_html = row.get("cleaned_html", "")
    if not isinstance(cleaned_html, str) or not cleaned_html.strip():
        return False

    return True


# =============================================================================
# PYDANTIC SCHEMAS
# =============================================================================
class Stage5MarkdownSubmission(BaseModel):
    markdown_content: str = Field(
        description=(
            "Clean markdown version of the main document content only. "
            "Remove HTML markup, navigation, boilerplate, cookie banners, share widgets, "
            "related links, comments, and other irrelevant content. "
            "Preserve the document meaning and structure as markdown."
        )
    )


class Stage5Result(BaseModel):
    success: bool
    error_message: str
    model_name: str
    prompt_version: str
    processed_at_utc: str
    input_html_length: int
    llm_input_html_length: int
    html_was_truncated: bool
    agent_final_message: str
    markdown_length: int
    markdown_content: str


# =============================================================================
# AGENT + TOOL
# =============================================================================
submission_buffer: dict = {"markdown_content": None}


@tool(args_schema=Stage5MarkdownSubmission)
def submit_stage5_markdown_to_system(markdown_content: str) -> str:
    """
    You MUST use this tool to submit the final markdown document to the system.
    """
    submission_buffer["markdown_content"] = markdown_content

    return (
        "SYSTEM SUCCESS: The markdown document has been saved to the system. "
        "Your task is complete. Now write a very short acknowledgement in chat. "
        "STRICTLY DO NOT repeat the markdown content in chat."
    )


SYSTEM_PROMPT = """
You are a document-to-markdown conversion agent.

You will receive cleaned HTML from a candidate incident-related document that already passed a relevance filter.
Your task is to convert it into clean, readable, high-quality markdown suitable for downstream RAG usage.

ALLOWED INTERNAL REASONING:
- You may think privately, including inside <think></think> tags if the model uses them.
- Do not expose your reasoning to the user in normal chat output.

STRICT WORKFLOW:
1. Read the provided cleaned HTML carefully.
2. Extract only the main document content.
3. Convert that content into clean markdown.
4. Remove HTML markup, navigation, decorative boilerplate, legal/cookie banners, share widgets, social buttons, repeated headers/footers, unrelated links, and other irrelevant noise.
5. Preserve the original document meaning and ordering.
6. Use markdown headings, lists, blockquotes, code fences, and tables only when they truly reflect the source structure.
7. Do NOT summarize or invent content.
8. Call `submit_stage5_markdown_to_system` exactly once with the final markdown.
9. After the tool confirms success, write only a very short acknowledgement in chat.

MARKDOWN CONVERSION RULES:
- Output only the main content as markdown.
- Keep the text faithful to the source.
- Preserve important headings and section structure.
- Preserve meaningful bullet lists and numbered lists.
- Preserve meaningful tables if they exist and are readable in markdown.
- Preserve inline code and code blocks if present.
- Remove empty sections, boilerplate fragments, and obvious UI leftovers.
- Do not include explanations about the conversion.
- Do not wrap the entire markdown document in triple backticks.
""".strip()


PROMPT_TEMPLATE = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        (
            "human",
            """Convert the following cleaned HTML into clean markdown.

Metadata:
- service_name: {name}
- source_url: {url}
- source_description: {description}
- document_kind: {document_kind}
- cleaned_html_length: {cleaned_html_length}

Requirements:
- Keep only the main document/article/postmortem content.
- Remove irrelevant boilerplate and leftover HTML/UI noise.
- Return the final document as clean markdown through the tool.

Cleaned HTML:
```html
{cleaned_html}
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
        tools=[submit_stage5_markdown_to_system],
    )


# =============================================================================
# STAGE5 LOGIC
# =============================================================================
def run_stage5_for_row(agent, row: dict) -> Stage5Result:
    submission_buffer["markdown_content"] = None

    cleaned_html = row["cleaned_html"]
    assessment = get_stage4_assessment(row) or {}
    html_for_llm, was_truncated, original_len, llm_len = prepare_html_for_llm(cleaned_html)

    messages = PROMPT_TEMPLATE.invoke(
        {
            "name": row.get("name", ""),
            "url": row.get("url", ""),
            "description": row.get("description", ""),
            "document_kind": assessment.get("document_kind", "unknown"),
            "cleaned_html_length": original_len,
            "cleaned_html": html_for_llm,
        }
    ).messages

    try:
        result = agent.invoke({"messages": messages})
        final_chat_message = extract_final_chat_message(result)
        markdown_content = submission_buffer.get("markdown_content")

        if markdown_content is None:
            return Stage5Result(
                success=False,
                error_message="The model did not submit a markdown tool call.",
                model_name=LLM_MODEL,
                prompt_version=PROMPT_VERSION,
                processed_at_utc=now_iso_utc(),
                input_html_length=original_len,
                llm_input_html_length=llm_len,
                html_was_truncated=was_truncated,
                agent_final_message=final_chat_message,
                markdown_length=0,
                markdown_content="",
            )

        if not isinstance(markdown_content, str):
            markdown_content = str(markdown_content)

        markdown_content = markdown_content.strip()

        if not markdown_content:
            return Stage5Result(
                success=False,
                error_message="The model submitted an empty markdown document.",
                model_name=LLM_MODEL,
                prompt_version=PROMPT_VERSION,
                processed_at_utc=now_iso_utc(),
                input_html_length=original_len,
                llm_input_html_length=llm_len,
                html_was_truncated=was_truncated,
                agent_final_message=final_chat_message,
                markdown_length=0,
                markdown_content="",
            )

        return Stage5Result(
            success=True,
            error_message="",
            model_name=LLM_MODEL,
            prompt_version=PROMPT_VERSION,
            processed_at_utc=now_iso_utc(),
            input_html_length=original_len,
            llm_input_html_length=llm_len,
            html_was_truncated=was_truncated,
            agent_final_message=final_chat_message,
            markdown_length=len(markdown_content),
            markdown_content=markdown_content,
        )

    except Exception as e:
        return Stage5Result(
            success=False,
            error_message=f"{type(e).__name__}: {e}",
            model_name=LLM_MODEL,
            prompt_version=PROMPT_VERSION,
            processed_at_utc=now_iso_utc(),
            input_html_length=original_len,
            llm_input_html_length=llm_len,
            html_was_truncated=was_truncated,
            agent_final_message="",
            markdown_length=0,
            markdown_content="",
        )


# =============================================================================
# MAIN
# =============================================================================
TEST_DICT = {"name": "Google", "url": "https://status.cloud.google.com/incident/compute/17007#5659118702428160", "description": "A bug in configuration roll-out to a load balancer lead to increased error rates for 22 minutes.", "error": False, "cleaned_html": "<html>\n<head>\n    <title>Google Cloud Status Dashboard</title>\n\n    </head>\n  <body>\n    <div>\n      <div>\n        <div>\n          <h1><a href=\"//cloud.google.com\"><img alt=\"Google Cloud Platform\" src=\"//cloud.google.com/_static/images/new-gcp-logo.png\" width=\"344\" height=\"44\"></a></h1>\n        </div>\n      </div>\n        <div>\n          <div>\n          <span>\n            April 13, 2018\n          </span>\n          <span>\n          \n            All services available\n          \n          </span>\n        </div>\n      </div>\n      <div>\n        <ul>\n          \n  <li><a href=\"/\">Google Cloud Status Dashboard</a></li>\n  <li><a href=\"/summary\">Incidents</a></li>\n  <li><a href=\"/incident/compute/17007\">Google Compute Engine</a></li>\n\n        </ul>\n      </div>\n      <div>\n        <h1><a href=\"/\">Google Cloud Status Dashboard</a></h1>\n        <p>\n        This page provides status information on the services that are part of Google Cloud Platform.\n        Check back here to view the current status of the services listed below. If you are experiencing an\n        issue not listed here, please <a href=\"//cloud.google.com/support/\">contact Support</a>.\n        Learn more about what's posted on the dashboard in <a href=\"//cloud.google.com/support/docs/dashboard\">\n        this FAQ</a>. For additional information on these services, please visit\n        <a href=\"//cloud.google.com\">cloud.google.com</a>.\n        </p>\n        \n  \n\n  <h1>Google Compute Engine Incident #17007</h1>\n\n \n    <div>\n      <p>502 errors for HTTP(S) Load Balancers</p>\n   </div>\n \n\n \n\n  <div>\n    <p>\n    \n    Incident began at <strong>2017-04-05 01:13</strong>\n    \n\n    \n    and ended at <strong>2017-04-05 01:35</strong>\n    \n\n    (all times are <strong>US/Pacific</strong>).\n    </p>\n  </div>\n\n  <table>\n    <thead>\n      <tr>\n        <th></th>\n        <th>Date</th>\n        <th>Time</th>\n        <th>Description</th>\n      </tr>\n    </thead>\n    <tbody>\n      \n        \n        <tr>\n          \n          \n              <td></td>\n          \n          <td>Apr 12, 2017</td>\n          <td>14:11</td>\n          <td>\n<p>ISSUE SUMMARY</p>\n\n<p>On Wednesday 5 April 2017, requests to the Google Cloud HTTP(S) Load Balancer experienced a 25% error rate for a duration of 22 minutes.</p>\n\n<p>We apologize for this incident. We understand that the Load Balancer needs to be very reliable for you to offer a high quality service to your customers. We have taken and will be taking various measures to prevent this type of incident from recurring.</p>\n\n<p>DETAILED DESCRIPTION OF IMPACT</p>\n\n<p>On Wednesday 5 April 2017 from 01:13 to 01:35 PDT, requests to the Google Cloud HTTP(S) Load Balancer experienced a 25% error rate for a duration of 22 minutes. Clients received 502 errors for failed requests. Some HTTP(S) Load Balancers that were recently modified experienced error rates of 100%.</p>\n\n<p>Google paused all configuration changes to the HTTP(S) Load Balancer for three hours and 41 minutes after the incident, until our engineers had understood the root cause. This caused deployments of App Engine Flexible apps to fail during that period.</p>\n\n<p>ROOT CAUSE</p>\n\n<p>A bug in the HTTP(S) Load Balancer configuration update process caused it to revert to a configuration that was substantially out of date.</p>\n\n<p>The configuration update process is controlled by a master server. In this case, one of the replicas of the master servers lost access to Google's distributed file system and was unable to read recent configuration files. Mastership then passed to the server that could not access Google's distributed file system. When the mastership changes, it begins the next configuration push as normal by testing on a subset of HTTP(S) Load Balancers. If this test succeeds, the configuration is pushed globally to all HTTP(S) Load Balancers.  If the test fails (as it did in this case), the new master will revert all HTTP(S) Load Balancers to the last \"known good\" configuration. The combination of a mastership change, lack of access to more recent updates, and the initial test failure for the latest config caused the HTTP(S) Load Balancers to revert to the latest configuration that the master could read, which was substantially out-of-date.</p>\n\n<p>In addition, the update with the out-of-date configuration triggered a garbage collection process on the Google Frontend servers to free up memory used by the deleted configurations. The high number of deleted configurations caused the Google Frontend servers to spend a large proportion of CPU cycles on garbage collection which lead to failed health checks and eventual restart of the affected Google Frontend server. Any client requests served by a restarting server received 502 errors.</p>\n\n<p>REMEDIATION AND PREVENTION</p>\n\n<p>Google engineers were paged at 01:22 PDT. They switched the configuration update process to use a different master server at 01:34 which mitigated the issue for most services within one minute. Our engineers then paused the configuration updates to the HTTP(S) Load Balancer until 05:16 while the root cause was confirmed.</p>\n\n<p>To prevent incidents of this type in future, we are taking the following actions:</p>\n\n<ul>\n<li><p>Master servers will be configured to never push HTTP(S) Load Balancer configurations that are more than a few hours old.</p></li>\n<li><p>Google Frontend servers will reject loading a configuration file that is more than a few hours old.</p></li>\n<li><p>Improve testing for new HTTP(S) Load Balancer configurations so that out-of-date configurations are flagged before being pushed to production.</p></li>\n<li><p>Fix the issue that caused the master server to fail when reading files from Google's distributed file system.</p></li>\n<li><p>Fix the issue that caused health check failures on Google Frontends during heavy garbage collection.</p></li>\n</ul>\n\n<p>Once again, we apologize for the impact that this incident had on your service.</p>\n</td>\n        </tr>\n        <tr>\n          <td></td>\n          <td>\n<p>ISSUE SUMMARY</p>\n\n<p>On Wednesday 5 April 2017, requests to the Google Cloud HTTP(S) Load Balancer experienced a 25% error rate for a duration of 22 minutes.</p>\n\n<p>We apologize for this incident. We understand that the Load Balancer needs to be very reliable for you to offer a high quality service to your customers. We have taken and will be taking various measures to prevent this type of incident from recurring.</p>\n\n<p>DETAILED DESCRIPTION OF IMPACT</p>\n\n<p>On Wednesday 5 April 2017 from 01:13 to 01:35 PDT, requests to the Google Cloud HTTP(S) Load Balancer experienced a 25% error rate for a duration of 22 minutes. Clients received 502 errors for failed requests. Some HTTP(S) Load Balancers that were recently modified experienced error rates of 100%.</p>\n\n<p>Google paused all configuration changes to the HTTP(S) Load Balancer for three hours and 41 minutes after the incident, until our engineers had understood the root cause. This caused deployments of App Engine Flexible apps to fail during that period.</p>\n\n<p>ROOT CAUSE</p>\n\n<p>A bug in the HTTP(S) Load Balancer configuration update process caused it to revert to a configuration that was substantially out of date.</p>\n\n<p>The configuration update process is controlled by a master server. In this case, one of the replicas of the master servers lost access to Google's distributed file system and was unable to read recent configuration files. Mastership then passed to the server that could not access Google's distributed file system. When the mastership changes, it begins the next configuration push as normal by testing on a subset of HTTP(S) Load Balancers. If this test succeeds, the configuration is pushed globally to all HTTP(S) Load Balancers.  If the test fails (as it did in this case), the new master will revert all HTTP(S) Load Balancers to the last \"known good\" configuration. The combination of a mastership change, lack of access to more recent updates, and the initial test failure for the latest config caused the HTTP(S) Load Balancers to revert to the latest configuration that the master could read, which was substantially out-of-date.</p>\n\n<p>In addition, the update with the out-of-date configuration triggered a garbage collection process on the Google Frontend servers to free up memory used by the deleted configurations. The high number of deleted configurations caused the Google Frontend servers to spend a large proportion of CPU cycles on garbage collection which lead to failed health checks and eventual restart of the affected Google Frontend server. Any client requests served by a restarting server received 502 errors.</p>\n\n<p>REMEDIATION AND PREVENTION</p>\n\n<p>Google engineers were paged at 01:22 PDT. They switched the configuration update process to use a different master server at 01:34 which mitigated the issue for most services within one minute. Our engineers then paused the configuration updates to the HTTP(S) Load Balancer until 05:16 while the root cause was confirmed.</p>\n\n<p>To prevent incidents of this type in future, we are taking the following actions:</p>\n\n<ul>\n<li><p>Master servers will be configured to never push HTTP(S) Load Balancer configurations that are more than a few hours old.</p></li>\n<li><p>Google Frontend servers will reject loading a configuration file that is more than a few hours old.</p></li>\n<li><p>Improve testing for new HTTP(S) Load Balancer configurations so that out-of-date configurations are flagged before being pushed to production.</p></li>\n<li><p>Fix the issue that caused the master server to fail when reading files from Google's distributed file system.</p></li>\n<li><p>Fix the issue that caused health check failures on Google Frontends during heavy garbage collection.</p></li>\n</ul>\n\n<p>Once again, we apologize for the impact that this incident had on your service.</p>\n</td>\n        </tr>\n      \n    </tbody>\n  </table>\n\n  <ul>\n    <li>All times are US/Pacific</li>\n    <li><a href=\"\">Send Feedback</a></li>\n  </ul>\n\n      </div>\n      \n      \n    </div>\n  \n</body>\n</html>", "crawl_success": True, "crawl_error_message": "", "crawl_error_primary": "", "crawl_error_fallback": "", "crawl_mode": "primary", "crawl_url_type": "NORMAL", "cleaned_html_length": 10079, "crawl_suspect": False, "crawl_attempt_count": 1, "crawl_primary_success": True, "crawl_fallback_used": False, "crawl_started_at_utc": "2026-03-16T03:28:20.985946+00:00", "crawl_finished_at_utc": "2026-03-16T03:29:34.513149+00:00", "crawl_elapsed_seconds": 73.528, "crawler_version_hint": "Crawl4AI 0.8.0", "debug_requested_url": "https://status.cloud.google.com/incident/compute/17007#5659118702428160", "debug_result_url": "https://status.cloud.google.com/incident/compute/17007#5659118702428160", "debug_status_code": 200.0, "debug_match_method": "batch_url_match", "debug_batch_id": 1, "stage4": {"success": True, "error_message": "", "model_name": "openrouter/hunter-alpha", "prompt_version": "stage4_v1", "processed_at_utc": "2026-03-16T10:07:24.799273+00:00", "input_html_length": 10079, "llm_input_html_length": 10079, "html_was_truncated": False, "agent_final_message": "Assessment submitted.", "assessment": {"is_relevant": True, "can_extract_markdown": True, "reason": "The HTML contains a complete postmortem document for Google Compute Engine Incident #17007, including issue summary, detailed impact description, root cause analysis, remediation steps, and prevention action items. The content is well-structured and can be converted to clean markdown.", "document_kind": "postmortem", "contains_main_text": True, "contains_incident_information": True, "has_timeline": True, "has_root_cause": True, "has_impact_description": True, "has_resolution_or_mitigation": True, "has_action_items_or_lessons_learned": True, "language": "en", "confidence": 0.95}}}

async def main():
    if len(sys.argv) < 2:
        print("Usage: script3.py <json_string>")
        sys.exit(1)

    row_dict: dict = json.loads(sys.argv[1])
    # row_dict = TEST_DICT
    if "url" not in row_dict:
        print("Error: input JSON must contain a 'url' field")
        sys.exit(1)

    print_output_schema()

    url = row_dict.get("url", "")

    debug_print("\n" + "=" * 80)
    debug_print(f"[START] {row_dict.get('name', '')}")
    debug_print(f"[URL] {url}")

    output_row = dict(row_dict)

    if not is_stage5_candidate(row_dict):
        output_row["stage5"] = None
        storage.append_json('silver-data', INPUT_FILENAMES_PREFIX, output_row)

        debug_print("[SKIP][STAGE5] stage5=null, запись не проходит фильтры stage5")
        debug_print("\n" + "=" * 80)
        debug_print("=== ГОТОВО ===")
        return

    assessment = get_stage4_assessment(row_dict) or {}
    debug_print(f"[CANDIDATE][STAGE5] document_kind={assessment.get('document_kind', 'unknown')}")

    model = build_model()
    agent = build_agent(model)

    stage5_result = run_stage5_for_row(agent, row_dict)
    output_row["stage5"] = stage5_result.model_dump()

    storage.append_json('silver-data', INPUT_FILENAMES_PREFIX, output_row)

    if stage5_result.success:
        debug_print(
            "[OK][STAGE5] "
            f"kind={assessment.get('document_kind')}, "
            f"markdown_length={stage5_result.markdown_length}"
        )
    else:
        debug_print(f"[FAIL][STAGE5] {stage5_result.error_message}")

    debug_print("\n" + "=" * 80)
    debug_print("=== ГОТОВО ===")
    debug_print(f"Успех: {stage5_result.success}")


if __name__ == "__main__":
    asyncio.run(main())
