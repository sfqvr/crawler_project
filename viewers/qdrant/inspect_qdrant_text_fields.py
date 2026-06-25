import argparse
import json
import re
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parents[1]
INPUT_FILE = PROJECT_ROOT / "parsed_danluu" / "danluu_postmortems_qdrant_ready.jsonl"


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


def safe_str(value: Any) -> str:
    return "" if value is None else str(value)


def normalize_text(text: str) -> str:
    return safe_str(text).lower().strip()


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9_./+-]+", normalize_text(text))


def build_field_text(row: dict, field: str) -> str:
    value = row.get(field)

    if isinstance(value, list):
        return " ".join(safe_str(x) for x in value)

    return safe_str(value)


def shorten(text: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def inspect_field(rows: list[dict], field: str, query: str, limit: int = 10) -> None:
    query_norm = normalize_text(query)
    query_tokens = tokenize(query)

    exact_substring_matches = []
    all_tokens_matches = []
    any_tokens_matches = []

    for idx, row in enumerate(rows):
        field_text = build_field_text(row, field)
        field_norm = normalize_text(field_text)
        field_tokens = set(tokenize(field_text))

        has_exact_substring = query_norm in field_norm if query_norm else False
        has_all_tokens = bool(query_tokens) and all(token in field_tokens for token in query_tokens)
        has_any_tokens = bool(query_tokens) and any(token in field_tokens for token in query_tokens)

        item = {
            "row_index": idx,
            "line_no": row.get("_line_no"),
            "name": row.get("name"),
            "url": row.get("url"),
            "field_value": field_text,
        }

        if has_exact_substring:
            exact_substring_matches.append(item)
        if has_all_tokens:
            all_tokens_matches.append(item)
        if has_any_tokens:
            any_tokens_matches.append(item)

    print("=" * 80)
    print(f"FIELD: {field}")
    print(f"QUERY: {query!r}")
    print(f"TOKENS: {query_tokens}")
    print(f"Всего строк: {len(rows)}")
    print()
    print(f"Exact substring matches: {len(exact_substring_matches)}")
    print(f"All tokens present:      {len(all_tokens_matches)}")
    print(f"Any token present:       {len(any_tokens_matches)}")
    print("=" * 80)

    def print_examples(title: str, items: list[dict]) -> None:
        print(f"\n=== {title} ===")
        if not items:
            print("Нет совпадений.")
            return

        for item in items[:limit]:
            print("-" * 80)
            print(f"row_index: {item['row_index']} | line_no: {item['line_no']}")
            print(f"name: {item['name']}")
            print(f"url: {item['url']}")
            print(f"{field}: {shorten(item['field_value'])}")

    print_examples("EXACT SUBSTRING EXAMPLES", exact_substring_matches)
    print_examples("ALL TOKENS EXAMPLES", all_tokens_matches)
    print_examples("ANY TOKEN EXAMPLES", any_tokens_matches)


def run_default_suite(rows: list[dict], limit: int) -> None:
    checks = [
        ("tech_stack_text", "postgres kafka rabbitmq"),
        ("key_terms_text", "route leak"),
        ("key_terms_text", "route leak bgp"),
    ]

    for field, query in checks:
        inspect_field(rows, field=field, query=query, limit=limit)
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Проверка, встречаются ли слова/фразы в qdrant-ready JSONL полях"
    )
    parser.add_argument(
        "--field",
        type=str,
        default=None,
        help="Поле для проверки, например tech_stack_text или key_terms_text"
    )
    parser.add_argument(
        "--query",
        type=str,
        default=None,
        help="Поисковая строка"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Сколько примеров показать"
    )
    parser.add_argument(
        "--default-suite",
        action="store_true",
        help="Прогнать набор типовых проверок из тестового qdrant-search сценария"
    )
    args = parser.parse_args()

    if not INPUT_FILE.exists():
        print(f"Ошибка: файл не найден: {INPUT_FILE}")
        return

    rows = load_jsonl(INPUT_FILE)

    if args.default_suite:
        run_default_suite(rows, limit=args.limit)
        return

    if not args.field or not args.query:
        print("Нужно либо указать --default-suite, либо передать и --field, и --query")
        return

    inspect_field(rows, field=args.field, query=args.query, limit=args.limit)


if __name__ == "__main__":
    main()