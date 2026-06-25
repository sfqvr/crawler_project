import asyncio
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

import pandas as pd
from pydantic import BaseModel, Field
from langchain.chat_models import init_chat_model
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.prompts import ChatPromptTemplate
from dotenv import load_dotenv
import os

load_dotenv()

# =============================================================================
# CONFIG
# =============================================================================
INPUT_FILE = Path("parsed_danluu/danluu_postmortems_with_html.jsonl")
OUTPUT_FILE = Path("parsed_danluu/danluu_postmortems_stage4.jsonl")

LIMIT_ROWS: Optional[int] = None
RESUME_FROM_OUTPUT = False
DEBUG = True

# LM Studio / OpenAI-compatible endpoint
LLM_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL")
LLM_API_KEY = os.getenv("OPENAI_API_KEY")
LLM_PROVIDER = "openai"

# Для отладки/экономии можно ограничить HTML.
# None = отправлять весь cleaned_html в модель.
MAX_HTML_CHARS_FOR_LLM: Optional[int] = None

PROMPT_VERSION = "stage4_v1"


# =============================================================================
# HELPERS
# =============================================================================
def debug_print(msg: str) -> None:
    if DEBUG:
        print(msg)


def now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_input_df(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {path}")
    return pd.read_json(path, lines=True)


def append_jsonl_row(output_path: Path, row: dict) -> None:
    with output_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_processed_urls(output_path: Path) -> set[str]:
    if not output_path.exists():
        return set()

    processed_urls: set[str] = set()
    with output_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                url = obj.get("url")
                if isinstance(url, str) and url:
                    processed_urls.add(url)
            except json.JSONDecodeError:
                continue

    return processed_urls


def to_jsonable(value):
    if value is None:
        return None

    # numpy scalar -> python scalar
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            value = value.item()
        except Exception:
            pass

    if isinstance(value, float) and pd.isna(value):
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}

    if isinstance(value, list):
        return [to_jsonable(v) for v in value]

    return str(value)


def sanitize_row_dict(row: pd.Series) -> dict:
    return {str(k): to_jsonable(v) for k, v in row.to_dict().items()}


def extract_final_chat_message(agent_result) -> str:
    """
    Пытаемся достать последнее текстовое сообщение модели из result["messages"].
    Это purely debug-поле.
    """
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
    print("=== OUTPUT JSONL SCHEMA (stage 4) ===")
    print("Root level: same fields as stage 3 output + one new field:")
    print("stage4: object | null")
    print()
    print("stage4 = null")
    print("  -> when stage 3 crawl failed or cleaned_html is missing/empty")
    print()
    print("stage4 object schema:")
    print("  success: bool")
    print("  error_message: str")
    print("  model_name: str")
    print("  prompt_version: str")
    print("  processed_at_utc: str")
    print("  input_html_length: int")
    print("  llm_input_html_length: int")
    print("  html_was_truncated: bool")
    print("  agent_final_message: str")
    print("  assessment: object | null")
    print()
    print("stage4.assessment schema:")
    print("  is_relevant: bool")
    print("  can_extract_markdown: bool")
    print("  reason: str")
    print("  document_kind: 'postmortem' | 'incident_report' | 'status_update' | 'irrelevant' | 'unknown'")
    print("  contains_main_text: bool")
    print("  contains_incident_information: bool")
    print("  has_timeline: bool")
    print("  has_root_cause: bool")
    print("  has_impact_description: bool")
    print("  has_resolution_or_mitigation: bool")
    print("  has_action_items_or_lessons_learned: bool")
    print("  language: str | null")
    print("  confidence: float")
    print("=" * 80)


def prepare_html_for_llm(cleaned_html: str) -> tuple[str, bool, int, int]:
    original_len = len(cleaned_html)

    if MAX_HTML_CHARS_FOR_LLM is None:
        return cleaned_html, False, original_len, original_len

    truncated = cleaned_html[:MAX_HTML_CHARS_FOR_LLM]
    was_truncated = len(truncated) < original_len
    return truncated, was_truncated, original_len, len(truncated)


# =============================================================================
# PYDANTIC SCHEMAS
# =============================================================================
class Stage4Assessment(BaseModel):
    is_relevant: bool = Field(
        description="Whether the HTML contains a relevant incident-related document worth keeping in the dataset."
    )
    can_extract_markdown: bool = Field(
        description="Whether the HTML can be converted into a clean and meaningful markdown document."
    )
    reason: str = Field(
        description="Short explanation in English describing why the document is relevant or not relevant."
    )
    document_kind: Literal[
        "postmortem",
        "incident_report",
        "status_update",
        "irrelevant",
        "unknown",
    ] = Field(
        description="Best matching document kind within the incident/postmortem domain."
    )
    contains_main_text: bool = Field(
        description="Whether the page contains substantial main textual content rather than mostly boilerplate or navigation."
    )
    contains_incident_information: bool = Field(
        description="Whether the document contains outage, incident, degradation, root cause, impact, or remediation information."
    )
    has_timeline: bool = Field(
        description="Whether the document contains a timeline of events."
    )
    has_root_cause: bool = Field(
        description="Whether the document describes a root cause or probable cause."
    )
    has_impact_description: bool = Field(
        description="Whether the document explains customer impact, system impact, or scope of the incident."
    )
    has_resolution_or_mitigation: bool = Field(
        description="Whether the document describes mitigation, resolution, or recovery actions."
    )
    has_action_items_or_lessons_learned: bool = Field(
        description="Whether the document includes follow-up actions, prevention steps, or lessons learned."
    )
    language: Optional[str] = Field(
        default=None,
        description="Language of the main content, for example 'en'."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Model confidence in the assessment."
    )


class Stage4Result(BaseModel):
    success: bool
    error_message: str
    model_name: str
    prompt_version: str
    processed_at_utc: str
    input_html_length: int
    llm_input_html_length: int
    html_was_truncated: bool
    agent_final_message: str
    assessment: Optional[Stage4Assessment] = None


# =============================================================================
# AGENT + TOOL
# =============================================================================
submission_buffer: dict = {"assessment": None}


@tool(args_schema=Stage4Assessment)
def submit_stage4_assessment_to_system(
    is_relevant: bool,
    can_extract_markdown: bool,
    reason: str,
    document_kind: str,
    contains_main_text: bool,
    contains_incident_information: bool,
    has_timeline: bool,
    has_root_cause: bool,
    has_impact_description: bool,
    has_resolution_or_mitigation: bool,
    has_action_items_or_lessons_learned: bool,
    language: Optional[str] = None,
    confidence: float = 0.0,
) -> str:
    """
    You MUST use this tool to submit the final stage4 assessment to the system.
    """
    submission_buffer["assessment"] = Stage4Assessment(
        is_relevant=is_relevant,
        can_extract_markdown=can_extract_markdown,
        reason=reason,
        document_kind=document_kind,
        contains_main_text=contains_main_text,
        contains_incident_information=contains_incident_information,
        has_timeline=has_timeline,
        has_root_cause=has_root_cause,
        has_impact_description=has_impact_description,
        has_resolution_or_mitigation=has_resolution_or_mitigation,
        has_action_items_or_lessons_learned=has_action_items_or_lessons_learned,
        language=language,
        confidence=confidence,
    )

    return (
        "SYSTEM SUCCESS: The stage4 assessment has been saved to the system. "
        "Your task is complete. Now write a very short acknowledgement in chat. "
        "STRICTLY DO NOT repeat the assessment fields, HTML analysis, or reasoning in chat."
    )


SYSTEM_PROMPT = """
You are an incident-document assessment agent.

You will receive metadata and cleaned HTML from a candidate page that is expected to belong to the postmortem / incident-report domain.
Your job is to assess whether this HTML is useful for the dataset and whether it can later be converted into a clean markdown document.

ALLOWED INTERNAL REASONING:
- You may think privately, including inside <think></think> tags if the model uses them.
- Do not expose your reasoning to the user in normal chat output.

STRICT WORKFLOW:
1. Analyze the provided cleaned HTML carefully.
2. Decide whether the page is relevant for the incident/postmortem dataset.
3. Decide whether the HTML can be converted into a clean markdown document.
4. Fill EVERY field required by the tool exactly once.
5. Call `submit_stage4_assessment_to_system`.
6. After the tool confirms success, write only a very short acknowledgement in chat.

IMPORTANT DEFINITIONS:
- is_relevant=True only if the page contains a meaningful incident-related document worth keeping.
- can_extract_markdown=True only if the HTML contains enough coherent main content to be transformed into a clean article/document markdown.
- reason MUST be in English.
- document_kind must be one of:
  - postmortem
  - incident_report
  - status_update
  - irrelevant
  - unknown

GUIDANCE:
- Prefer postmortem when the page is a retrospective analysis with cause, impact, timeline, remediation, lessons, or action items.
- Prefer incident_report when the page describes an outage/incident in a useful way but is less complete than a full postmortem.
- Prefer status_update when it is mostly a status/incident update with limited retrospective depth.
- Use irrelevant when the page is noise, navigation, broken content, boilerplate, login wall, or otherwise not useful.
- Use unknown only when the content is too ambiguous to classify confidently.
- If unsure, lower confidence.
- Do not invent facts that are not visible in the HTML.
""".strip()


PROMPT_TEMPLATE = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        (
            "human",
            """Assess the following candidate incident document.

Metadata:
- service_name: {name}
- source_url: {url}
- source_description: {description}
- cleaned_html_length: {cleaned_html_length}

Notes:
- The source_description is only a weak hint and may be incomplete.
- Base your judgment primarily on the cleaned HTML.
- Return the `reason` in English.

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
        tools=[submit_stage4_assessment_to_system],
    )


# =============================================================================
# STAGE4 LOGIC
# =============================================================================
def build_stage4_null_reason(row: dict) -> bool:
    crawl_success = bool(row.get("crawl_success", False))
    cleaned_html = row.get("cleaned_html", "")
    return (not crawl_success) or (not isinstance(cleaned_html, str)) or (not cleaned_html.strip())


def run_stage4_for_row(agent, row: dict) -> Stage4Result:
    submission_buffer["assessment"] = None

    cleaned_html = row["cleaned_html"]
    html_for_llm, was_truncated, original_len, llm_len = prepare_html_for_llm(cleaned_html)

    messages = PROMPT_TEMPLATE.invoke(
        {
            "name": row.get("name", ""),
            "url": row.get("url", ""),
            "description": row.get("description", ""),
            "cleaned_html_length": original_len,
            "cleaned_html": html_for_llm,
        }
    ).messages

    try:
        result = agent.invoke({"messages": messages})
        final_chat_message = extract_final_chat_message(result)
        assessment = submission_buffer.get("assessment")

        if assessment is None:
            return Stage4Result(
                success=False,
                error_message="The model did not submit a structured assessment tool call.",
                model_name=LLM_MODEL,
                prompt_version=PROMPT_VERSION,
                processed_at_utc=now_iso_utc(),
                input_html_length=original_len,
                llm_input_html_length=llm_len,
                html_was_truncated=was_truncated,
                agent_final_message=final_chat_message,
                assessment=None,
            )

        return Stage4Result(
            success=True,
            error_message="",
            model_name=LLM_MODEL,
            prompt_version=PROMPT_VERSION,
            processed_at_utc=now_iso_utc(),
            input_html_length=original_len,
            llm_input_html_length=llm_len,
            html_was_truncated=was_truncated,
            agent_final_message=final_chat_message,
            assessment=assessment,
        )

    except Exception as e:
        return Stage4Result(
            success=False,
            error_message=f"{type(e).__name__}: {e}",
            model_name=LLM_MODEL,
            prompt_version=PROMPT_VERSION,
            processed_at_utc=now_iso_utc(),
            input_html_length=original_len,
            llm_input_html_length=llm_len,
            html_was_truncated=was_truncated,
            agent_final_message="",
            assessment=None,
        )


# =============================================================================
# MAIN
# =============================================================================
async def main():
    ensure_parent_dir(OUTPUT_FILE)
    print_output_schema()

    df = load_input_df(INPUT_FILE)

    if LIMIT_ROWS is not None:
        df = df.head(LIMIT_ROWS).copy()

    debug_print(f"[INFO] Всего строк во входном файле: {len(df)}")

    processed_urls = set()
    if RESUME_FROM_OUTPUT:
        processed_urls = load_processed_urls(OUTPUT_FILE)
        debug_print(f"[INFO] Уже обработано URL в output: {len(processed_urls)}")

    model = build_model()
    agent = build_agent(model)

    total = len(df)
    llm_success_count = 0
    llm_fail_count = 0
    stage3_null_count = 0
    skipped_count = 0
    relevant_count = 0
    irrelevant_count = 0
    kind_counter = Counter()

    for i, row in df.iterrows():
        row_dict = sanitize_row_dict(row)
        url = row_dict.get("url", "")

        if RESUME_FROM_OUTPUT and isinstance(url, str) and url in processed_urls:
            skipped_count += 1
            debug_print(f"[SKIP] [{i+1}/{total}] Уже есть в output: {url}")
            continue

        debug_print("\n" + "=" * 80)
        debug_print(f"[START] [{i+1}/{total}] {row_dict.get('name', '')}")
        debug_print(f"[URL] {url}")

        output_row = dict(row_dict)

        # Если stage3 провалился — stage4 = null
        if build_stage4_null_reason(row_dict):
            output_row["stage4"] = None
            append_jsonl_row(OUTPUT_FILE, output_row)
            stage3_null_count += 1
            debug_print("[SKIP][STAGE3] stage4=null, потому что stage3 crawl неуспешен или cleaned_html пустой")
            continue

        stage4_result = run_stage4_for_row(agent, row_dict)
        output_row["stage4"] = stage4_result.model_dump()

        append_jsonl_row(OUTPUT_FILE, output_row)

        if stage4_result.success and stage4_result.assessment is not None:
            llm_success_count += 1
            assessment = stage4_result.assessment

            if assessment.is_relevant:
                relevant_count += 1
            else:
                irrelevant_count += 1

            kind_counter[assessment.document_kind] += 1

            debug_print(
                "[OK][STAGE4] "
                f"relevant={assessment.is_relevant}, "
                f"kind={assessment.document_kind}, "
                f"markdown={assessment.can_extract_markdown}, "
                f"confidence={assessment.confidence:.2f}"
            )
        else:
            llm_fail_count += 1
            debug_print(f"[FAIL][STAGE4] {stage4_result.error_message}")

    debug_print("\n" + "=" * 80)
    debug_print("=== ГОТОВО ===")
    debug_print(f"LLM success: {llm_success_count}")
    debug_print(f"LLM fail: {llm_fail_count}")
    debug_print(f"stage4 = null из-за stage3: {stage3_null_count}")
    debug_print(f"Пропущено по resume: {skipped_count}")
    debug_print(f"Relevant: {relevant_count}")
    debug_print(f"Irrelevant: {irrelevant_count}")
    debug_print(f"Document kinds: {dict(kind_counter)}")
    debug_print(f"Файл: {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())