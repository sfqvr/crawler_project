import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from qdrant_client import QdrantClient, models


# =============================================================================
# CONFIG
# =============================================================================
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parents[1]
INPUT_FILE = PROJECT_ROOT / "parsed_danluu" / "danluu_postmortems_qdrant_ready.jsonl"

QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "incident_documents")
QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")

QDRANT_LOCAL_MODE = os.getenv("QDRANT_LOCAL_MODE", "false").lower() == "true"
QDRANT_LOCAL_PATH = os.getenv("QDRANT_LOCAL_PATH", "").strip()

DEBUG = True


# =============================================================================
# HELPERS
# =============================================================================
def debug_print(msg: str) -> None:
    if DEBUG:
        print(msg)


def require_env(name: str, value: str | None) -> str:
    if value is None or not value.strip():
        raise ValueError(f"Environment variable {name} is required.")
    return value.strip()


def safe_str(value: Any) -> str:
    return "" if value is None else str(value)


def normalize_text(text: str) -> str:
    return safe_str(text).lower().strip()


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9_./+-]+", normalize_text(text))


def shorten(text: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", safe_str(text)).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


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
                print(f"[WARN] Failed to parse line {line_no}: {e}")
    return rows


def build_qdrant_client() -> QdrantClient:
    if QDRANT_LOCAL_MODE:
        local_path = QDRANT_LOCAL_PATH or ":memory:"
        if local_path == ":memory:":
            debug_print("[INFO] Using Qdrant local mode in memory")
            return QdrantClient(location=":memory:")
        debug_print(f"[INFO] Using Qdrant local mode on disk: {local_path}")
        return QdrantClient(path=local_path)

    url = require_env("QDRANT_URL", QDRANT_URL)
    if QDRANT_API_KEY:
        debug_print(f"[INFO] Using Qdrant server: {url}")
        return QdrantClient(url=url, api_key=QDRANT_API_KEY)

    debug_print(f"[INFO] Using Qdrant server: {url}")
    return QdrantClient(url=url)


def get_field_text(row: dict, field: str) -> str:
    value = row.get(field)
    if isinstance(value, list):
        return " ".join(safe_str(x) for x in value)
    return safe_str(value)


def local_field_stats(rows: list[dict], field: str, query: str) -> dict:
    query_norm = normalize_text(query)
    query_tokens = tokenize(query)

    exact_substring_matches = []
    all_tokens_matches = []
    any_tokens_matches = []

    for idx, row in enumerate(rows):
        field_text = get_field_text(row, field)
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

    return {
        "query_tokens": query_tokens,
        "exact_substring_matches": exact_substring_matches,
        "all_tokens_matches": all_tokens_matches,
        "any_tokens_matches": any_tokens_matches,
    }


def qdrant_text_search(
    qdrant_client: QdrantClient,
    field: str,
    query: str,
    limit: int,
    any_mode: bool,
):
    if any_mode:
        match_obj = models.MatchTextAny(text_any=query)
    else:
        match_obj = models.MatchText(text=query)

    response = qdrant_client.scroll(
        collection_name=QDRANT_COLLECTION,
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key=field,
                    match=match_obj,
                )
            ]
        ),
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )

    if isinstance(response, tuple):
        return response[0]
    return response


def print_local_examples(title: str, field: str, items: list[dict], limit: int) -> None:
    print(f"\n=== {title} ===")
    if not items:
        print("No matches.")
        return

    for item in items[:limit]:
        print("-" * 80)
        print(f"row_index: {item['row_index']} | line_no: {item['line_no']}")
        print(f"name: {item['name']}")
        print(f"url: {item['url']}")
        print(f"{field}: {shorten(item['field_value'])}")


def print_qdrant_hits(title: str, field: str, hits: list[Any], limit: int) -> None:
    print(f"\n=== {title} ===")
    if not hits:
        print("No Qdrant results.")
        return

    for idx, hit in enumerate(hits[:limit], start=1):
        payload = getattr(hit, "payload", {}) or {}
        print("-" * 80)
        print(f"[{idx}] id={getattr(hit, 'id', None)}")
        print(f"name: {payload.get('name')}")
        print(f"url: {payload.get('url')}")
        print(f"{field}: {shorten(payload.get(field, ''))}")


def inspect_case(
    rows: list[dict],
    qdrant_client: QdrantClient,
    field: str,
    query: str,
    limit: int,
) -> None:
    stats = local_field_stats(rows, field, query)

    qdrant_match_text_hits = qdrant_text_search(
        qdrant_client=qdrant_client,
        field=field,
        query=query,
        limit=limit,
        any_mode=False,
    )

    qdrant_match_text_any_hits = qdrant_text_search(
        qdrant_client=qdrant_client,
        field=field,
        query=query,
        limit=limit,
        any_mode=True,
    )

    print("\n" + "=" * 80)
    print(f"FIELD: {field}")
    print(f"QUERY: {query!r}")
    print(f"TOKENS: {stats['query_tokens']}")
    print(f"Всего строк: {len(rows)}")
    print()
    print(f"Local exact substring matches: {len(stats['exact_substring_matches'])}")
    print(f"Local all tokens present:      {len(stats['all_tokens_matches'])}")
    print(f"Local any token present:       {len(stats['any_tokens_matches'])}")
    print(f"Qdrant MatchText results:      {len(qdrant_match_text_hits)}")
    print(f"Qdrant MatchTextAny results:   {len(qdrant_match_text_any_hits)}")
    print("=" * 80)

    print_local_examples("LOCAL EXACT SUBSTRING EXAMPLES", field, stats["exact_substring_matches"], limit)
    print_local_examples("LOCAL ALL TOKENS EXAMPLES", field, stats["all_tokens_matches"], limit)
    print_local_examples("LOCAL ANY TOKEN EXAMPLES", field, stats["any_tokens_matches"], limit)

    print_qdrant_hits("QDRANT MatchText EXAMPLES", field, qdrant_match_text_hits, limit)
    print_qdrant_hits("QDRANT MatchTextAny EXAMPLES", field, qdrant_match_text_any_hits, limit)


def run_default_suite(rows: list[dict], qdrant_client: QdrantClient, limit: int) -> None:
    cases = [
        # single-token sanity checks
        ("tech_stack_text", "postgres"),
        ("tech_stack_text", "kafka"),
        ("tech_stack_text", "rabbitmq"),
        ("key_terms_text", "bgp"),
        ("key_terms_text", "leak"),

        # phrase-like cases that should exist somewhere
        ("key_terms_text", "data leak"),
        ("key_terms_text", "memory leak"),
        ("key_terms_text", "route reflectors"),

        # previous problematic multi-token cases
        ("tech_stack_text", "postgres kafka rabbitmq"),
        ("key_terms_text", "route leak"),
        ("key_terms_text", "route leak bgp"),
    ]

    for field, query in cases:
        inspect_case(rows, qdrant_client, field, query, limit)
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Debug Qdrant text search by comparing dataset-level matches with Qdrant results"
    )
    parser.add_argument(
        "--field",
        type=str,
        default=None,
        help="Field to inspect, e.g. tech_stack_text or key_terms_text",
    )
    parser.add_argument(
        "--query",
        type=str,
        default=None,
        help="Query string to inspect",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="How many examples to print",
    )
    parser.add_argument(
        "--default-suite",
        action="store_true",
        help="Run a built-in suite of text-search sanity checks",
    )
    args = parser.parse_args()

    if not INPUT_FILE.exists():
        print(f"Ошибка: файл не найден: {INPUT_FILE}")
        return

    rows = load_jsonl(INPUT_FILE)
    qdrant_client = build_qdrant_client()

    if args.default_suite:
        run_default_suite(rows, qdrant_client, args.limit)
        return

    if not args.field or not args.query:
        print("Нужно либо указать --default-suite, либо передать и --field, и --query")
        return

    inspect_case(rows, qdrant_client, args.field, args.query, args.limit)


if __name__ == "__main__":
    main()