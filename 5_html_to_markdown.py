import asyncio
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
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
storage.client.fget_object(
    bucket_name=INPUT_FOLDER_NAME,
    object_name=f"{INPUT_FILENAMES_PREFIX}.jsonl",
    file_path=f"{INPUT_FOLDER_NAME}/{INPUT_FILENAMES_PREFIX}_stage4.jsonl",
)

INPUT_FILE = Path(f"{INPUT_FOLDER_NAME}/{INPUT_FILENAMES_PREFIX}_stage4.jsonl")
OUTPUT_FILE = Path(f"{INPUT_FOLDER_NAME}/{INPUT_FILENAMES_PREFIX}_stage5.jsonl")

# INPUT_FILE = Path("parsed_danluu/danluu_postmortems_stage4.jsonl")
# OUTPUT_FILE = Path("parsed_danluu/danluu_postmortems_stage5.jsonl")

LIMIT_ROWS: Optional[int] = None
RESUME_FROM_OUTPUT = False
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


def print_output_schema() -> None:
    print("=" * 80)
    print("=== OUTPUT JSONL SCHEMA (stage 5) ===")
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
    stage5_success_count = 0
    stage5_fail_count = 0
    stage5_null_count = 0
    skipped_count = 0
    candidate_count = 0
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

        if not is_stage5_candidate(row_dict):
            output_row["stage5"] = None
            append_jsonl_row(OUTPUT_FILE, output_row)
            stage5_null_count += 1
            debug_print("[SKIP][STAGE5] stage5=null, запись не проходит фильтры stage5")
            continue

        candidate_count += 1
        assessment = get_stage4_assessment(row_dict) or {}
        kind_counter[assessment.get("document_kind", "unknown")] += 1

        stage5_result = run_stage5_for_row(agent, row_dict)
        output_row["stage5"] = stage5_result.model_dump()

        append_jsonl_row(OUTPUT_FILE, output_row)

        if stage5_result.success:
            stage5_success_count += 1
            debug_print(
                "[OK][STAGE5] "
                f"kind={assessment.get('document_kind')}, "
                f"markdown_length={stage5_result.markdown_length}"
            )
        else:
            stage5_fail_count += 1
            debug_print(f"[FAIL][STAGE5] {stage5_result.error_message}")


    storage.client.fput_object(
        bucket_name=INPUT_FOLDER_NAME,
        object_name=f"{INPUT_FILENAMES_PREFIX}_stage5.jsonl",
        file_path=OUTPUT_FILE,
    )

    debug_print("\n" + "=" * 80)
    debug_print("=== ГОТОВО ===")
    debug_print(f"Кандидатов для stage5: {candidate_count}")
    debug_print(f"Stage5 success: {stage5_success_count}")
    debug_print(f"Stage5 fail: {stage5_fail_count}")
    debug_print(f"stage5 = null из-за фильтров: {stage5_null_count}")
    debug_print(f"Пропущено по resume: {skipped_count}")
    debug_print(f"Document kinds among candidates: {dict(kind_counter)}")
    debug_print(f"Файл: {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())