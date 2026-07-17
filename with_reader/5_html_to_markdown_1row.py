import asyncio
import json
import os
import pathlib
import sys
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain.tools import tool
from langchain_core.prompts import ChatPromptTemplate
from openai import OpenAI
from pydantic import BaseModel, Field

from minio_client import MinIOStorage, INPUT_FILENAMES_PREFIX

load_dotenv(dotenv_path=pathlib.Path(__file__).resolve().parent / ".env")

# =============================================================================
# CONFIG
# =============================================================================
INPUT_FOLDER_NAME = "parsed_jimmyl02"

storage = MinIOStorage()

DEBUG = True

OPENAI_MODEL = os.getenv("OPENAI_MODEL")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

STAGE5_BACKEND = os.getenv("STAGE5_BACKEND", "reader").strip().lower()

# READER backend (local LM Studio)
READER_MODEL = os.getenv("READER_MODEL", OPENAI_MODEL or "reader-lm-1.5b")
READER_BASE_URL = os.getenv("READER_BASE_URL", OPENAI_BASE_URL)
READER_API_KEY = os.getenv("READER_API_KEY", OPENAI_API_KEY or "lm-studio")

# AGENT backend (remote API: DeepSeek / OpenRouter / ...)
AGENT_MODEL = os.getenv("AGENT_MODEL", OPENAI_MODEL or "gpt-4o-mini")
AGENT_BASE_URL = os.getenv("AGENT_BASE_URL", OPENAI_BASE_URL)
AGENT_API_KEY = os.getenv("AGENT_API_KEY", OPENAI_API_KEY)
AGENT_PROVIDER = "openai"

LLM_MODEL = AGENT_MODEL if STAGE5_BACKEND == "agent" else READER_MODEL

MAX_HTML_CHARS_FOR_LLM: Optional[int] = None if os.getenv("MAX_HTML_CHARS_FOR_LLM") is None else int(
    os.getenv("MAX_HTML_CHARS_FOR_LLM"))

PROMPT_VERSION = "stage5_v1"

ALLOWED_DOCUMENT_KINDS = {"postmortem", "incident_report", "status_update"}

READER_V2_MODELS = {"readerlm-v2", "reader-lm-v2"}


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
    print("  backend: str")
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


from bs4 import BeautifulSoup


def pre_clean_html(html: str) -> str:
    """Проверенная очистка из comparev5.py — корректно работает с Cloudflare/OpenAI/Discord."""
    if not html:
        return ""
    try:
        soup = BeautifulSoup(html, "lxml")

        # 1) СНАЧАЛА удаляем шум по ТОЧНЫМ селекторам (не по подстрокам!)
        noise_selectors = [
            "script", "style", "noscript", "svg", "iframe", "img", "picture",
            "figure", "form", "button", "template",
            ".ads", ".advert", ".banner", "#comments", ".comments",
            "nav", "footer", "header",
            ".sidebar", ".related", ".share", ".social", ".breadcrumbs",
            ".breadcrumb", ".pagination", ".dropdown-container",
            ".mobile-menu", ".post-extras", ".post-email", ".newsletter",
        ]
        for tag in soup(noise_selectors):
            tag.decompose()

        # 2) чистим атрибуты (экономим токены), оставляем только табличные
        for tag in soup.find_all(True):
            tag.attrs = {k: v for k, v in tag.attrs.items()
                         if k in ("colspan", "rowspan")}

        # 3) ПОТОМ берём основной контейнер (широкий, надёжный порядок)
        main = (
                soup.select_one("main")
                or soup.select_one("article")
                or soup.select_one(".article-content")
                or soup.select_one("#updates")  # OpenAI status
                or soup.select_one(".article")
                or soup.select_one(".content")
                or soup.select_one("#content")
                or soup.body
        )
        return str(main or soup)
    except Exception:
        return html


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
    backend: str
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
# BACKEND: AGENT (tool-call, DeepSeek-style) — fallback
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


AGENT_SYSTEM_PROMPT = """
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

AGENT_PROMPT_TEMPLATE = ChatPromptTemplate.from_messages(
    [
        ("system", AGENT_SYSTEM_PROMPT),
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


def build_agent():
    model = init_chat_model(
        model=AGENT_MODEL,
        model_provider=AGENT_PROVIDER,
        base_url=AGENT_BASE_URL,
        api_key=AGENT_API_KEY,
        temperature=0.0,
    )
    return create_agent(model=model, tools=[submit_stage5_markdown_to_system])


def run_agent_backend(row: dict, html_for_llm: str) -> tuple[str, str]:
    """Возвращает (markdown, agent_final_message). Бросает исключение при ошибке."""
    submission_buffer["markdown_content"] = None
    assessment = get_stage4_assessment(row) or {}

    agent = build_agent()
    messages = AGENT_PROMPT_TEMPLATE.invoke(
        {
            "name": row.get("name", ""),
            "url": row.get("url", ""),
            "description": row.get("description", ""),
            "document_kind": assessment.get("document_kind", "unknown"),
            "cleaned_html_length": len(row.get("cleaned_html", "")),
            "cleaned_html": html_for_llm,
        }
    ).messages

    result = agent.invoke({"messages": messages})
    final_chat_message = extract_final_chat_message(result)
    markdown = submission_buffer.get("markdown_content")

    if markdown is None:
        raise RuntimeError("The model did not submit a markdown tool call.")

    return str(markdown), final_chat_message


# =============================================================================
# BACKEND: READER (reader-lm / readerlm-v2) — default
# =============================================================================
READER_V2_INSTRUCTION = (
    "Convert the following HTML into clean markdown. "
    "Keep only the main document content, drop navigation, boilerplate and UI noise."
)


def build_reader_client() -> OpenAI:
    return OpenAI(base_url=READER_BASE_URL, api_key=READER_API_KEY)


def run_reader_backend(html_for_llm: str) -> tuple[str, str]:
    client = build_reader_client()

    # pre-clean перед моделью
    cleaned = pre_clean_html(html_for_llm)
    debug_print(f"[READER][PRECLEAN] {len(html_for_llm)} -> {len(cleaned)} символов")

    is_v2 = READER_MODEL.strip().lower() in READER_V2_MODELS
    content = f"{READER_V2_INSTRUCTION}\n\n{cleaned}" if is_v2 else cleaned

    debug_print(f"[READER][INPUT] len={len(content)}, first_300={content[:300].replace(chr(10), ' ')}")

    resp = client.chat.completions.create(
        model=READER_MODEL,
        messages=[{"role": "user", "content": content}],
        temperature=0.1,
        timeout=750,
        max_tokens=20000,
    )
    markdown = (resp.choices[0].message.content or "").strip()
    finish = resp.choices[0].finish_reason
    debug_print(f"[READER][RECEIVED] len={len(markdown)} finish={finish}")

    if not markdown:
        raise RuntimeError("Reader model returned empty markdown.")
    return markdown, "reader conversion complete"


# =============================================================================
# STAGE5 LOGIC (backend-agnostic)
# =============================================================================
def run_stage5_for_row(row: dict) -> Stage5Result:
    cleaned_html = row["cleaned_html"]
    html_for_llm, was_truncated, original_len, llm_len = prepare_html_for_llm(cleaned_html)

    base = dict(
        backend=STAGE5_BACKEND,
        model_name=LLM_MODEL,
        prompt_version=PROMPT_VERSION,
        processed_at_utc=now_iso_utc(),
        input_html_length=original_len,
        llm_input_html_length=llm_len,
        html_was_truncated=was_truncated,
    )

    try:
        if STAGE5_BACKEND == "agent":
            markdown, final_message = run_agent_backend(row, html_for_llm)
        else:  # "reader" (default)
            markdown, final_message = run_reader_backend(html_for_llm)

        markdown = markdown.strip()
        if not markdown:
            raise RuntimeError("The model produced an empty markdown document.")

        return Stage5Result(
            success=True,
            error_message="",
            agent_final_message=final_message,
            markdown_length=len(markdown),
            markdown_content=markdown,
            **base,
        )

    except Exception as e:
        return Stage5Result(
            success=False,
            error_message=f"{type(e).__name__}: {e}",
            agent_final_message="",
            markdown_length=0,
            markdown_content="",
            **base,
        )


# =============================================================================
# MAIN
# =============================================================================
async def main():
    # Поддержка stdin (pipe) для обхода лимита длины командной строки Windows
    if len(sys.argv) >= 2 and sys.argv[1] != '-':
        row_dict: dict = json.loads(sys.argv[1])
    else:
        row_dict: dict = json.loads(sys.stdin.read())

    if "url" not in row_dict:
        print("Error: input JSON must contain a 'url' field")
        sys.exit(1)

    print_output_schema()

    url = row_dict.get("url", "")

    debug_print("\n" + "=" * 80)
    debug_print(f"[START] {row_dict.get('name', '')}")
    debug_print(f"[URL] {url}")
    debug_print(f"[BACKEND] {STAGE5_BACKEND} | [MODEL] {LLM_MODEL}")

    output_row = dict(row_dict)
    output_row["cleaned_html"] = storage.get_html('raw-data', INPUT_FILENAMES_PREFIX, url)
    output_row["markdown_content"] = storage.get_markdown('raw-data', INPUT_FILENAMES_PREFIX, url)

    # --- Фильтр кандидатов stage5 (оставлен закомментированным намеренно) ---
    # if not is_stage5_candidate(output_row):
    #     output_row["stage5"] = None
    #     storage.append_json('silver-data', INPUT_FILENAMES_PREFIX, output_row)
    #     debug_print("[SKIP][STAGE5] stage5=null, запись не проходит фильтры stage5")
    #     debug_print("\n" + "=" * 80)
    #     debug_print("=== ГОТОВО ===")
    #     return

    if not isinstance(output_row.get("cleaned_html"), str) or not output_row["cleaned_html"].strip():
        output_row["stage5"] = None
        storage.append_json('silver-data', INPUT_FILENAMES_PREFIX, output_row)
        debug_print("[SKIP][STAGE5] stage5=null, cleaned_html отсутствует или пуст")
        debug_print("\n" + "=" * 80)
        debug_print("=== ГОТОВО ===")
        return

    assessment = get_stage4_assessment(output_row) or {}
    debug_print(f"[CANDIDATE][STAGE5] document_kind={assessment.get('document_kind', 'unknown')}")

    stage5_result = run_stage5_for_row(output_row)
    output_row["stage5"] = stage5_result.model_dump()

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

    storage.append_json('silver-data', INPUT_FILENAMES_PREFIX, output_row)
    storage.append_html(
        "raw-data",
        INPUT_FILENAMES_PREFIX,
        output_row["url"],
        output_row["cleaned_html"],
    )

    generated_md = (output_row.get("stage5") or {}).get("markdown_content") or ""
    old_md = output_row.get("markdown_content") or ""
    md_to_save = generated_md or old_md

    storage.append_markdown(
        "raw-data",
        INPUT_FILENAMES_PREFIX,
        output_row["url"],
        md_to_save,
    )
    debug_print(
        f"[SAVE][STAGE5] md_to_save_len={len(md_to_save)} "
        f"(generated={len(generated_md)}, old={len(old_md)})"
    )

    # удаляем большие объекты из выходной строки
    output_row["cleaned_html"] = ""
    output_row["markdown_content"] = ""
    print("RESULT_JSON:" + json.dumps(output_row, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
