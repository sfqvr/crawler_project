import json
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5


INPUT_FILE = Path("parsed_danluu/danluu_postmortems_stage6.jsonl")
OUTPUT_FILE = Path("parsed_danluu/danluu_postmortems_qdrant_ready.jsonl")


def load_jsonl(path: Path) -> list[dict]:
    rows = []
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
                print(f"[WARN] Не удалось распарсить строку {line_no}: {e}")
    return rows


def append_jsonl_row(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


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
    stage4 = row.get("stage4")
    stage5 = row.get("stage5")
    stage6 = row.get("stage6")

    if not isinstance(stage4, dict):
        return False
    if not isinstance(stage5, dict):
        return False
    if not isinstance(stage6, dict):
        return False

    if stage4.get("success") is not True:
        return False
    if stage5.get("success") is not True:
        return False
    if stage6.get("success") is not True:
        return False

    assessment = stage4.get("assessment")
    extraction = stage6.get("extraction")

    if not isinstance(assessment, dict):
        return False
    if not isinstance(extraction, dict):
        return False

    if assessment.get("is_relevant") is not True:
        return False

    markdown_content = stage5.get("markdown_content", "")
    if not isinstance(markdown_content, str) or not markdown_content.strip():
        return False

    return True


def transform_row(row: dict) -> dict:
    stage4 = row["stage4"]
    stage5 = row["stage5"]
    stage6 = row["stage6"]

    assessment = stage4["assessment"]
    extraction = stage6["extraction"]

    metadata_filters = extraction.get("metadata_filters", {}) or {}
    searchable_text = extraction.get("searchable_text", {}) or {}

    url = safe_str(row.get("url"))
    name = safe_str(row.get("name"))
    description = safe_str(row.get("description"))
    company = safe_str(extraction.get("company"))
    date = extraction.get("date")
    short_description = safe_str(extraction.get("short_description"))
    document_kind = safe_str(assessment.get("document_kind"))

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


def main():
    if not INPUT_FILE.exists():
        print(f"Ошибка: файл не найден: {INPUT_FILE}")
        return

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    if OUTPUT_FILE.exists():
        OUTPUT_FILE.unlink()

    rows = load_jsonl(INPUT_FILE)

    output_count = 0
    skipped_count = 0

    for row in rows:
        if not is_qdrant_candidate(row):
            skipped_count += 1
            continue

        transformed = transform_row(row)
        append_jsonl_row(OUTPUT_FILE, transformed)
        output_count += 1

    print("=" * 80)
    print("=== QDRANT DATASET PREPARATION COMPLETE ===")
    print(f"Input rows: {len(rows)}")
    print(f"Output rows: {output_count}")
    print(f"Filtered out: {skipped_count}")
    print(f"Output file: {OUTPUT_FILE}")
    print("=" * 80)

    print_output_schema()


if __name__ == "__main__":
    main()