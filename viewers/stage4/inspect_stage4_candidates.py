import json
from pathlib import Path
from collections import Counter

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parents[1]

INPUT_FILE = PROJECT_ROOT / "parsed_danluu" / "danluu_postmortems_stage4.jsonl"

ALLOWED_DOCUMENT_KINDS = {"postmortem", "incident_report", "status_update"}


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


def get_stage4(row: dict) -> dict | None:
    stage4 = row.get("stage4")
    return stage4 if isinstance(stage4, dict) else None


def get_assessment(row: dict) -> dict | None:
    stage4 = get_stage4(row)
    if not stage4:
        return None
    assessment = stage4.get("assessment")
    return assessment if isinstance(assessment, dict) else None


def is_candidate(row: dict) -> bool:
    stage4 = get_stage4(row)
    assessment = get_assessment(row)

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

    return True


def is_strange_case(row: dict) -> bool:
    assessment = get_assessment(row)
    if not assessment:
        return False

    is_relevant = assessment.get("is_relevant")
    document_kind = assessment.get("document_kind")

    return is_relevant is False and document_kind != "irrelevant"


def main():
    if not INPUT_FILE.exists():
        print(f"Ошибка: файл не найден: {INPUT_FILE}")
        return

    rows = load_jsonl(INPUT_FILE)
    total = len(rows)

    stage4_null_count = 0
    stage4_success_count = 0
    candidate_count = 0

    document_kind_counter = Counter()
    candidate_kind_counter = Counter()
    strange_rows = []

    for idx, row in enumerate(rows):
        stage4 = get_stage4(row)
        assessment = get_assessment(row)

        if stage4 is None:
            stage4_null_count += 1
            continue

        if stage4.get("success") is True:
            stage4_success_count += 1

        if assessment:
            document_kind = assessment.get("document_kind")
            if document_kind is not None:
                document_kind_counter[document_kind] += 1

        if is_candidate(row):
            candidate_count += 1
            candidate_kind_counter[assessment.get("document_kind")] += 1

        if is_strange_case(row):
            strange_rows.append(
                {
                    "row_index": idx,
                    "line_no": row.get("_line_no"),
                    "name": row.get("name"),
                    "url": row.get("url"),
                    "document_kind": assessment.get("document_kind"),
                    "is_relevant": assessment.get("is_relevant"),
                    "can_extract_markdown": assessment.get("can_extract_markdown"),
                    "reason": assessment.get("reason"),
                    "confidence": assessment.get("confidence"),
                }
            )

    print("=" * 80)
    print("=== STAGE 4 SUMMARY ===")
    print(f"Всего строк: {total}")
    print(f"stage4 = null: {stage4_null_count}")
    print(f"stage4.success = True: {stage4_success_count}")
    print()

    print("=== КАНДИДАТЫ ДЛЯ ЭТАПА 5 ===")
    print("Условия:")
    print("  - stage4.success == True")
    print("  - is_relevant == True")
    print("  - can_extract_markdown == True")
    print(f"  - document_kind in {sorted(ALLOWED_DOCUMENT_KINDS)}")
    print(f"Итого подходящих записей: {candidate_count}")
    print("Разбивка по document_kind:")
    for kind, count in candidate_kind_counter.items():
        print(f"  {kind}: {count}")
    print()

    print("=== ВСЕ document_kind В stage4.assessment ===")
    for kind, count in document_kind_counter.items():
        print(f"  {kind}: {count}")
    print()

    print("=== СТРАННЫЕ СЛУЧАИ ===")
    print('Условие: is_relevant == False, но document_kind != "irrelevant"')
    print(f"Найдено: {len(strange_rows)}")

    if strange_rows:
        print()
        for item in strange_rows:
            print("-" * 80)
            print(f"row_index: {item['row_index']} | line_no: {item['line_no']}")
            print(f"name: {item['name']}")
            print(f"url: {item['url']}")
            print(f"document_kind: {item['document_kind']}")
            print(f"is_relevant: {item['is_relevant']}")
            print(f"can_extract_markdown: {item['can_extract_markdown']}")
            print(f"confidence: {item['confidence']}")
            print(f"reason: {item['reason']}")

    print("=" * 80)


if __name__ == "__main__":
    main()