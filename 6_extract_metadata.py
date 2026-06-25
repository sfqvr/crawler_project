import asyncio
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

import pandas as pd
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain.chat_models import init_chat_model
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.prompts import ChatPromptTemplate


# =============================================================================
# CONFIG
# =============================================================================
load_dotenv()

INPUT_FILE = Path("parsed_danluu/danluu_postmortems_stage5.jsonl")
OUTPUT_FILE = Path("parsed_danluu/danluu_postmortems_stage6.jsonl")

LIMIT_ROWS: Optional[int] = None
RESUME_FROM_OUTPUT = False
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
    print("=== OUTPUT JSONL SCHEMA (stage 6) ===")
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
    candidate_count = 0
    stage6_success_count = 0
    stage6_fail_count = 0
    stage6_null_count = 0
    skipped_count = 0
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

        if not is_stage6_candidate(row_dict):
            output_row["stage6"] = None
            append_jsonl_row(OUTPUT_FILE, output_row)
            stage6_null_count += 1
            debug_print("[SKIP][STAGE6] stage6=null, запись не проходит фильтры stage6")
            continue

        candidate_count += 1
        assessment = get_stage4_assessment(row_dict) or {}
        kind = assessment.get("document_kind", "unknown")
        kind_counter[kind] += 1

        stage6_result = run_stage6_for_row(agent, row_dict)
        output_row["stage6"] = stage6_result.model_dump()

        append_jsonl_row(OUTPUT_FILE, output_row)

        if stage6_result.success and stage6_result.extraction is not None:
            extraction = stage6_result.extraction
            stage6_success_count += 1
            debug_print(
                "[OK][STAGE6] "
                f"kind={kind}, "
                f"company={extraction.company}, "
                f"date={extraction.date}, "
                f"confidence={extraction.confidence:.2f}"
            )
        else:
            stage6_fail_count += 1
            debug_print(f"[FAIL][STAGE6] {stage6_result.error_message}")

    debug_print("\n" + "=" * 80)
    debug_print("=== ГОТОВО ===")
    debug_print(f"Кандидатов для stage6: {candidate_count}")
    debug_print(f"Stage6 success: {stage6_success_count}")
    debug_print(f"Stage6 fail: {stage6_fail_count}")
    debug_print(f"stage6 = null из-за фильтров: {stage6_null_count}")
    debug_print(f"Пропущено по resume: {skipped_count}")
    debug_print(f"Document kinds among candidates: {dict(kind_counter)}")
    debug_print(f"Файл: {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())