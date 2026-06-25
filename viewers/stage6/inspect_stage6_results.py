import argparse
import json
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parents[1]
INPUT_FILE = PROJECT_ROOT / "parsed_danluu" / "danluu_postmortems_stage6.jsonl"


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


def get_stage6(row: dict) -> dict | None:
    stage6 = row.get("stage6")
    return stage6 if isinstance(stage6, dict) else None


def get_extraction(row: dict) -> dict | None:
    stage6 = get_stage6(row)
    if not stage6:
        return None
    extraction = stage6.get("extraction")
    return extraction if isinstance(extraction, dict) else None


def print_summary(rows: list[dict]) -> None:
    total = len(rows)
    stage6_null = 0
    stage6_success = 0
    stage6_fail = 0

    kind_counter = Counter()
    category_counter = Counter()
    infra_counter = Counter()
    company_counter = Counter()

    for row in rows:
        stage6 = get_stage6(row)
        extraction = get_extraction(row)

        if stage6 is None:
            stage6_null += 1
            continue

        if stage6.get("success") is True:
            stage6_success += 1
        else:
            stage6_fail += 1

        if extraction:
            company = extraction.get("company")
            if company:
                company_counter[company] += 1

            metadata_filters = extraction.get("metadata_filters", {}) or {}
            for kind in metadata_filters.get("incident_categories", []) or []:
                category_counter[kind] += 1
            for infra in metadata_filters.get("infrastructure", []) or []:
                infra_counter[infra] += 1

        stage4 = row.get("stage4") or {}
        assessment = stage4.get("assessment") if isinstance(stage4, dict) else None
        if isinstance(assessment, dict):
            doc_kind = assessment.get("document_kind")
            if doc_kind:
                kind_counter[doc_kind] += 1

    print("=" * 80)
    print("=== STAGE 6 SUMMARY ===")
    print(f"Всего строк: {total}")
    print(f"stage6 = null: {stage6_null}")
    print(f"stage6.success = True: {stage6_success}")
    print(f"stage6.success = False: {stage6_fail}")
    print()

    print("Document kinds:")
    for k, v in kind_counter.items():
        print(f"  {k}: {v}")
    print()

    print("Top companies:")
    for k, v in company_counter.most_common(10):
        print(f"  {k}: {v}")
    print()

    print("Top incident categories:")
    for k, v in category_counter.most_common(15):
        print(f"  {k}: {v}")
    print()

    print("Top infrastructure tags:")
    for k, v in infra_counter.most_common(15):
        print(f"  {k}: {v}")
    print("=" * 80)


def print_row(row: dict, index: int) -> None:
    stage6 = get_stage6(row)
    extraction = get_extraction(row)

    print("-" * 80)
    print(f"row_index: {index} | line_no: {row.get('_line_no')}")
    print(f"name: {row.get('name')}")
    print(f"url: {row.get('url')}")
    print(f"description: {row.get('description')}")
    print()

    if stage6 is None:
        print("stage6: null")
        return

    print(f"stage6.success: {stage6.get('success')}")
    print(f"stage6.error_message: {stage6.get('error_message')}")
    print(f"stage6.model_name: {stage6.get('model_name')}")
    print(f"stage6.prompt_version: {stage6.get('prompt_version')}")
    print(f"stage6.processed_at_utc: {stage6.get('processed_at_utc')}")
    print(f"stage6.input_markdown_length: {stage6.get('input_markdown_length')}")
    print(f"stage6.llm_input_markdown_length: {stage6.get('llm_input_markdown_length')}")
    print(f"stage6.markdown_was_truncated: {stage6.get('markdown_was_truncated')}")
    print()

    if extraction is None:
        print("stage6.extraction: null")
        return

    print("=== EXTRACTION ===")
    print(f"company: {extraction.get('company')}")
    print(f"date: {extraction.get('date')}")
    print(f"short_description: {extraction.get('short_description')}")
    print(f"confidence: {extraction.get('confidence')}")
    print()

    metadata_filters = extraction.get("metadata_filters", {}) or {}
    print("metadata_filters:")
    print(f"  incident_categories: {metadata_filters.get('incident_categories', [])}")
    print(f"  tech_stack: {metadata_filters.get('tech_stack', [])}")
    print(f"  infrastructure: {metadata_filters.get('infrastructure', [])}")
    print(f"  key_terms: {metadata_filters.get('key_terms', [])}")
    print()

    searchable_text = extraction.get("searchable_text", {}) or {}
    print("searchable_text:")
    print(f"  symptoms: {searchable_text.get('symptoms', '')}")
    print(f"  root_cause: {searchable_text.get('root_cause', '')}")
    print(f"  resolution: {searchable_text.get('resolution', '')}")
    print(f"  lessons_learned: {searchable_text.get('lessons_learned', '')}")


def main():
    parser = argparse.ArgumentParser(
        description="Просмотр результатов stage6 metadata extraction"
    )
    parser.add_argument(
        "--index",
        type=int,
        default=None,
        help="Показать конкретную строку по индексу"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Сколько первых записей показать"
    )
    parser.add_argument(
        "--only-success",
        action="store_true",
        help="Показывать только записи с stage6.success == True"
    )
    args = parser.parse_args()

    if not INPUT_FILE.exists():
        print(f"Ошибка: файл не найден: {INPUT_FILE}")
        return

    rows = load_jsonl(INPUT_FILE)
    print_summary(rows)

    filtered_rows_with_idx = []
    for idx, row in enumerate(rows):
        stage6 = get_stage6(row)

        if args.only_success:
            if not stage6 or stage6.get("success") is not True:
                continue

        filtered_rows_with_idx.append((idx, row))

    if args.index is not None:
        if args.index < 0 or args.index >= len(rows):
            print(f"Ошибка: индекс {args.index} вне диапазона 0..{len(rows)-1}")
            return
        print_row(rows[args.index], args.index)
        return

    print("=== EXAMPLES ===")
    for idx, row in filtered_rows_with_idx[:args.limit]:
        print_row(row, idx)


if __name__ == "__main__":
    main()