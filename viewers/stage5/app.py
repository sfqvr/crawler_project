from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request
import markdown as markdown_lib


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parents[1]
DEFAULT_INPUT_FILE = PROJECT_ROOT / "parsed_danluu" / "danluu_postmortems_stage5.jsonl"

INPUT_FILE = Path(os.getenv("STAGE5_JSONL_PATH", str(DEFAULT_INPUT_FILE)))

app = Flask(__name__, template_folder="templates", static_folder="static")


def safe_str(value: Any) -> str:
    return "" if value is None else str(value)


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                obj["_line_no"] = line_no
                rows.append(obj)
            except json.JSONDecodeError as e:
                rows.append(
                    {
                        "_line_no": line_no,
                        "_load_error": f"JSON decode error: {e}",
                    }
                )
    return rows


def get_stage4(row: dict) -> dict | None:
    value = row.get("stage4")
    return value if isinstance(value, dict) else None


def get_stage4_assessment(row: dict) -> dict | None:
    stage4 = get_stage4(row)
    if not stage4:
        return None
    value = stage4.get("assessment")
    return value if isinstance(value, dict) else None


def get_stage5(row: dict) -> dict | None:
    value = row.get("stage5")
    return value if isinstance(value, dict) else None


def classify_stage_status(stage_obj: dict | None) -> str:
    if stage_obj is None:
        return "null"

    success = stage_obj.get("success")
    if success is True:
        return "success"
    return "failed"


def render_markdown_to_html(text: str) -> str:
    if not text.strip():
        return ""
    return markdown_lib.markdown(
        text,
        extensions=["extra", "tables", "fenced_code", "sane_lists"],
    )


def build_search_text(row: dict) -> str:
    stage4_assessment = get_stage4_assessment(row) or {}
    stage5 = get_stage5(row) or {}

    parts = [
        safe_str(row.get("name")),
        safe_str(row.get("url")),
        safe_str(row.get("description")),
        safe_str(stage4_assessment.get("document_kind")),
        safe_str(stage4_assessment.get("reason")),
        safe_str(stage5.get("error_message")),
    ]
    return " ".join(parts).lower()


def normalize_record(row: dict, row_index: int) -> dict:
    stage4 = get_stage4(row)
    stage4_assessment = get_stage4_assessment(row)
    stage5 = get_stage5(row)

    cleaned_html = row.get("cleaned_html", "")
    if not isinstance(cleaned_html, str):
        cleaned_html = safe_str(cleaned_html)

    markdown_content = ""
    if stage5 and isinstance(stage5.get("markdown_content"), str):
        markdown_content = stage5["markdown_content"]

    document_kind = ""
    if stage4_assessment and isinstance(stage4_assessment.get("document_kind"), str):
        document_kind = stage4_assessment["document_kind"]

    stage4_status = classify_stage_status(stage4)
    stage5_status = classify_stage_status(stage5)

    normalized = {
        "row_index": row_index,
        "line_no": row.get("_line_no"),
        "name": safe_str(row.get("name")),
        "url": safe_str(row.get("url")),
        "description": safe_str(row.get("description")),
        "error": row.get("error"),
        "cleaned_html": cleaned_html,
        "cleaned_html_length": row.get("cleaned_html_length", len(cleaned_html)),
        "crawl_success": row.get("crawl_success"),
        "crawl_mode": row.get("crawl_mode"),
        "crawl_url_type": row.get("crawl_url_type"),
        "debug_status_code": row.get("debug_status_code"),
        "stage4": stage4,
        "stage4_assessment": stage4_assessment,
        "stage4_status": stage4_status,
        "stage4_relevant": (
            stage4_assessment.get("is_relevant") if stage4_assessment else None
        ),
        "stage4_can_extract_markdown": (
            stage4_assessment.get("can_extract_markdown") if stage4_assessment else None
        ),
        "stage5": stage5,
        "stage5_status": stage5_status,
        "stage5_success": (stage5.get("success") if stage5 else None),
        "stage5_error_message": (
            safe_str(stage5.get("error_message")) if stage5 else ""
        ),
        "document_kind": document_kind,
        "has_markdown": bool(markdown_content.strip()),
        "markdown_content": markdown_content,
        "markdown_length": len(markdown_content),
        "search_text": build_search_text(row),
        "raw_row": row,
    }
    return normalized


RAW_ROWS = load_jsonl(INPUT_FILE)
RECORDS = [normalize_record(row, idx) for idx, row in enumerate(RAW_ROWS)]


def get_all_kinds(records: list[dict]) -> list[str]:
    return sorted(
        {
            record["document_kind"]
            for record in records
            if isinstance(record.get("document_kind"), str) and record["document_kind"].strip()
        }
    )


ALL_KINDS = get_all_kinds(RECORDS)


def compute_meta(records: list[dict]) -> dict:
    stage4_counter = Counter(record["stage4_status"] for record in records)
    stage5_counter = Counter(record["stage5_status"] for record in records)
    kind_counter = Counter(
        record["document_kind"]
        for record in records
        if record["document_kind"]
    )

    return {
        "total_count": len(records),
        "all_kinds": ALL_KINDS,
        "stage4_status_counts": dict(stage4_counter),
        "stage5_status_counts": dict(stage5_counter),
        "document_kind_counts": dict(kind_counter),
    }


META = compute_meta(RECORDS)


def record_matches_filters(
    record: dict,
    *,
    search: str,
    only_with_markdown: bool,
    only_stage4_relevant: bool,
    stage4_status: str,
    stage5_status: str,
    selected_kinds: list[str],
    all_kinds: list[str],
) -> bool:
    if search:
        if search.lower().strip() not in record["search_text"]:
            return False

    if only_with_markdown and not record["has_markdown"]:
        return False

    if only_stage4_relevant and record["stage4_relevant"] is not True:
        return False

    if stage4_status != "all" and record["stage4_status"] != stage4_status:
        return False

    if stage5_status != "all" and record["stage5_status"] != stage5_status:
        return False

    selected_kinds = [kind for kind in selected_kinds if kind in all_kinds]

    if len(selected_kinds) == 0:
        # kind filter disabled
        pass
    elif len(selected_kinds) == len(all_kinds):
        # filter enabled, but pass only rows where kind exists
        if not record["document_kind"]:
            return False
    else:
        # partial selection
        if record["document_kind"] not in selected_kinds:
            return False

    return True


def serialize_sidebar_item(record: dict, filtered_index: int) -> dict:
    return {
        "filtered_index": filtered_index,
        "row_index": record["row_index"],
        "name": record["name"] or "Unnamed",
        "document_kind": record["document_kind"] or None,
        "stage4_status": record["stage4_status"],
        "stage5_status": record["stage5_status"],
        "stage4_relevant": record["stage4_relevant"],
        "has_markdown": record["has_markdown"],
        "markdown_length": record["markdown_length"],
        "url": record["url"],
    }


def serialize_current_record(record: dict, filtered_index: int) -> dict:
    quick_metadata = {
        "row_index": record["row_index"],
        "line_no": record["line_no"],
        "name": record["name"],
        "url": record["url"],
        "description": record["description"],
        "document_kind": record["document_kind"] or None,
        "stage4_status": record["stage4_status"],
        "stage4_relevant": record["stage4_relevant"],
        "stage4_can_extract_markdown": record["stage4_can_extract_markdown"],
        "stage5_status": record["stage5_status"],
        "stage5_success": record["stage5_success"],
        "has_markdown": record["has_markdown"],
        "markdown_length": record["markdown_length"],
        "cleaned_html_length": record["cleaned_html_length"],
        "crawl_success": record["crawl_success"],
        "crawl_mode": record["crawl_mode"],
        "crawl_url_type": record["crawl_url_type"],
        "debug_status_code": record["debug_status_code"],
        "stage5_error_message": record["stage5_error_message"],
    }

    return {
        "filtered_index": filtered_index,
        "row_index": record["row_index"],
        "line_no": record["line_no"],
        "name": record["name"],
        "url": record["url"],
        "description": record["description"],
        "document_kind": record["document_kind"] or None,
        "stage4_status": record["stage4_status"],
        "stage5_status": record["stage5_status"],
        "stage4_relevant": record["stage4_relevant"],
        "stage4_can_extract_markdown": record["stage4_can_extract_markdown"],
        "has_markdown": record["has_markdown"],
        "markdown_length": record["markdown_length"],
        "cleaned_html_length": record["cleaned_html_length"],
        "crawl_url_type": record["crawl_url_type"],
        "debug_status_code": record["debug_status_code"],
        "stage5_error_message": record["stage5_error_message"],
        "markdown_content": record["markdown_content"],
        "markdown_rendered_html": render_markdown_to_html(record["markdown_content"]),
        "cleaned_html": record["cleaned_html"],
        "quick_metadata_json": json.dumps(quick_metadata, ensure_ascii=False, indent=2),
        "full_row_json": json.dumps(record["raw_row"], ensure_ascii=False, indent=2),
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/meta")
def api_meta():
    return jsonify(META)


@app.post("/api/query")
def api_query():
    payload = request.get_json(silent=True) or {}

    search = safe_str(payload.get("search", "")).strip()
    only_with_markdown = bool(payload.get("only_with_markdown", False))
    only_stage4_relevant = bool(payload.get("only_stage4_relevant", False))
    stage4_status = safe_str(payload.get("stage4_status", "all")) or "all"
    stage5_status = safe_str(payload.get("stage5_status", "all")) or "all"
    selected_kinds = payload.get("document_kinds", [])
    selected_index = payload.get("selected_index", 0)

    if not isinstance(selected_kinds, list):
        selected_kinds = []

    try:
        selected_index = int(selected_index)
    except (TypeError, ValueError):
        selected_index = 0

    filtered_records = [
        record
        for record in RECORDS
        if record_matches_filters(
            record,
            search=search,
            only_with_markdown=only_with_markdown,
            only_stage4_relevant=only_stage4_relevant,
            stage4_status=stage4_status,
            stage5_status=stage5_status,
            selected_kinds=selected_kinds,
            all_kinds=ALL_KINDS,
        )
    ]

    filtered_total = len(filtered_records)

    if filtered_total == 0:
        current = None
        selected_index = 0
    else:
        selected_index = max(0, min(selected_index, filtered_total - 1))
        current = serialize_current_record(filtered_records[selected_index], selected_index)

    items = [
        serialize_sidebar_item(record, idx)
        for idx, record in enumerate(filtered_records)
    ]

    return jsonify(
        {
            "total_count": len(RECORDS),
            "filtered_total": filtered_total,
            "selected_index": selected_index,
            "items": items,
            "current": current,
        }
    )


if __name__ == "__main__":
    app.run(debug=True, port=8505)