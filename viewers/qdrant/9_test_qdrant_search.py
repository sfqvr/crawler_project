import argparse
import os
from typing import Any, Iterable

from dotenv import load_dotenv
from openai import OpenAI
from qdrant_client import QdrantClient, models


# =============================================================================
# CONFIG
# =============================================================================
load_dotenv()

# OpenAI-compatible endpoint for embeddings
EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", os.getenv("OPENAI_MODEL", "text-embedding-3-small"))
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Qdrant
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


def parse_csv(values: str | None) -> list[str]:
    if not values:
        return []
    return [item.strip() for item in values.split(",") if item.strip()]


def build_openai_client() -> OpenAI:
    base_url = require_env("OPENAI_BASE_URL", OPENAI_BASE_URL)
    api_key = require_env("OPENAI_API_KEY", OPENAI_API_KEY)
    return OpenAI(api_key=api_key, base_url=base_url)


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


def embed_query(client: OpenAI, text: str) -> list[float]:
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )
    return response.data[0].embedding


def build_keyword_filter(field: str, values: list[str]) -> models.Filter | None:
    if not values:
        return None

    return models.Filter(
        must=[
            models.FieldCondition(
                key=field,
                match=models.MatchAny(any=values),
            )
        ]
    )


def build_text_filter(field: str, text: str, any_mode: bool = False) -> models.Filter | None:
    text = text.strip()
    if not text:
        return None

    if any_mode:
        match_obj = models.MatchTextAny(text_any=text)
    else:
        match_obj = models.MatchText(text=text)

    return models.Filter(
        must=[
            models.FieldCondition(
                key=field,
                match=match_obj,
            )
        ]
    )


def merge_filters(*filters: models.Filter | None) -> models.Filter | None:
    conditions = []
    for flt in filters:
        if not flt:
            continue
        if flt.must:
            conditions.extend(flt.must)
        if flt.must_not:
            conditions.extend(flt.must_not)
        if flt.should:
            conditions.extend(flt.should)

    if not conditions:
        return None

    return models.Filter(must=conditions)


def get_points_from_query_response(response) -> list[Any]:
    if response is None:
        return []
    if hasattr(response, "points"):
        return response.points
    if isinstance(response, list):
        return response
    return []


def get_points_from_scroll_response(response) -> list[Any]:
    if response is None:
        return []
    if isinstance(response, tuple) and len(response) >= 1:
        return response[0]
    if isinstance(response, list):
        return response
    return []


def format_payload(payload: dict | None) -> dict:
    payload = payload or {}
    return {
        "url": payload.get("url"),
        "name": payload.get("name"),
        "company": payload.get("company"),
        "date": payload.get("date"),
        "document_kind": payload.get("document_kind"),
        "short_description": payload.get("short_description"),
        "incident_categories": payload.get("incident_categories"),
        "tech_stack": payload.get("tech_stack"),
        "infrastructure": payload.get("infrastructure"),
        "key_terms": payload.get("key_terms"),
    }


def print_hits(title: str, hits: list[Any], show_score: bool = True) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)

    if not hits:
        print("No results.")
        return

    for idx, hit in enumerate(hits, start=1):
        point_id = getattr(hit, "id", None)
        score = getattr(hit, "score", None)
        payload = format_payload(getattr(hit, "payload", {}) or {})

        print(f"[{idx}] id={point_id}")
        if show_score and score is not None:
            print(f"    score: {score}")
        print(f"    name: {payload.get('name')}")
        print(f"    company: {payload.get('company')}")
        print(f"    date: {payload.get('date')}")
        print(f"    kind: {payload.get('document_kind')}")
        print(f"    url: {payload.get('url')}")
        print(f"    incident_categories: {payload.get('incident_categories')}")
        print(f"    tech_stack: {payload.get('tech_stack')}")
        print(f"    infrastructure: {payload.get('infrastructure')}")
        print(f"    key_terms: {payload.get('key_terms')}")
        print(f"    short_description: {payload.get('short_description')}")
        print("-" * 80)


# =============================================================================
# SEARCH METHODS
# =============================================================================
def semantic_search(
    qdrant_client: QdrantClient,
    openai_client: OpenAI,
    query_text: str,
    limit: int = 5,
    query_filter: models.Filter | None = None,
):
    vector = embed_query(openai_client, query_text)
    response = qdrant_client.query_points(
        collection_name=QDRANT_COLLECTION,
        query=vector,
        query_filter=query_filter,
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )
    return get_points_from_query_response(response)


def keyword_filter_only_search(
    qdrant_client: QdrantClient,
    field: str,
    values: list[str],
    limit: int = 5,
):
    flt = build_keyword_filter(field, values)
    response = qdrant_client.scroll(
        collection_name=QDRANT_COLLECTION,
        scroll_filter=flt,
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )
    return get_points_from_scroll_response(response)


def text_filter_only_search(
    qdrant_client: QdrantClient,
    field: str,
    text: str,
    limit: int = 5,
    any_mode: bool = False,
):
    flt = build_text_filter(field, text, any_mode=any_mode)
    response = qdrant_client.scroll(
        collection_name=QDRANT_COLLECTION,
        scroll_filter=flt,
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )
    return get_points_from_scroll_response(response)


def semantic_search_with_keyword_filter(
    qdrant_client: QdrantClient,
    openai_client: OpenAI,
    query_text: str,
    field: str,
    values: list[str],
    limit: int = 5,
):
    flt = build_keyword_filter(field, values)
    return semantic_search(
        qdrant_client=qdrant_client,
        openai_client=openai_client,
        query_text=query_text,
        limit=limit,
        query_filter=flt,
    )


def semantic_search_with_text_filter(
    qdrant_client: QdrantClient,
    openai_client: OpenAI,
    query_text: str,
    field: str,
    text: str,
    limit: int = 5,
    any_mode: bool = False,
):
    flt = build_text_filter(field, text, any_mode=any_mode)
    return semantic_search(
        qdrant_client=qdrant_client,
        openai_client=openai_client,
        query_text=query_text,
        limit=limit,
        query_filter=flt,
    )


# =============================================================================
# DEMO SUITE
# =============================================================================
def run_demo_suite(
    qdrant_client: QdrantClient,
    openai_client: OpenAI,
    query_text: str,
    limit: int,
):
    print(f"\nCollection: {QDRANT_COLLECTION}")
    print(f"Embedding model: {EMBEDDING_MODEL}")
    print(f"Query: {query_text}")

    hits = semantic_search(qdrant_client, openai_client, query_text, limit=limit)
    print_hits("1) Semantic search", hits)

    hits = semantic_search_with_keyword_filter(
        qdrant_client,
        openai_client,
        query_text=query_text,
        field="incident_categories",
        values=["Database"],
        limit=limit,
    )
    print_hits('2) Semantic + keyword filter: incident_categories contains "Database"', hits)

    hits = semantic_search_with_keyword_filter(
        qdrant_client,
        openai_client,
        query_text=query_text,
        field="infrastructure",
        values=["Cloud"],
        limit=limit,
    )
    print_hits('3) Semantic + keyword filter: infrastructure contains "Cloud"', hits)

    hits = keyword_filter_only_search(
        qdrant_client,
        field="incident_categories",
        values=["Network", "Database"],
        limit=limit,
    )
    print_hits('4) Keyword-only search: incident_categories in ["Network", "Database"]', hits, show_score=False)

    hits = text_filter_only_search(
        qdrant_client,
        field="tech_stack_text",
        text="postgres kafka rabbitmq",
        limit=limit,
        any_mode=True,
    )
    print_hits('5) Text-only search: tech_stack_text matches ANY of "postgres kafka rabbitmq"', hits, show_score=False)

    hits = text_filter_only_search(
        qdrant_client,
        field="key_terms_text",
        text="route leak",
        limit=limit,
        any_mode=False,
    )
    print_hits('6) Text-only search: key_terms_text matches ALL terms in "route leak"', hits, show_score=False)

    hits = semantic_search_with_text_filter(
        qdrant_client,
        openai_client,
        query_text=query_text,
        field="key_terms_text",
        text="route leak bgp",
        limit=limit,
        any_mode=True,
    )
    print_hits('7) Semantic + text filter: key_terms_text matches ANY of "route leak bgp"', hits)


# =============================================================================
# MAIN
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="Test different Qdrant search methods")
    parser.add_argument(
        "--mode",
        choices=[
            "all",
            "semantic",
            "semantic_keyword",
            "semantic_text",
            "keyword_only",
            "text_only",
        ],
        default="all",
        help="Which search mode to run",
    )
    parser.add_argument(
        "--query",
        type=str,
        default="database outage caused by postgres xid wraparound and service errors",
        help="Semantic query text",
    )
    parser.add_argument(
        "--field",
        type=str,
        default="incident_categories",
        help="Payload field for keyword/text filters",
    )
    parser.add_argument(
        "--values",
        type=str,
        default="Database",
        help="Comma-separated values for keyword filters",
    )
    parser.add_argument(
        "--text",
        type=str,
        default="postgres",
        help="Text query for text filters",
    )
    parser.add_argument(
        "--any-mode",
        action="store_true",
        help="Use MatchTextAny instead of MatchText for text filters",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="How many hits to return",
    )
    args = parser.parse_args()

    qdrant_client = build_qdrant_client()
    openai_client = build_openai_client()

    if args.mode == "all":
        run_demo_suite(
            qdrant_client=qdrant_client,
            openai_client=openai_client,
            query_text=args.query,
            limit=args.limit,
        )
        return

    if args.mode == "semantic":
        hits = semantic_search(
            qdrant_client=qdrant_client,
            openai_client=openai_client,
            query_text=args.query,
            limit=args.limit,
        )
        print_hits("Semantic search", hits)
        return

    if args.mode == "semantic_keyword":
        hits = semantic_search_with_keyword_filter(
            qdrant_client=qdrant_client,
            openai_client=openai_client,
            query_text=args.query,
            field=args.field,
            values=parse_csv(args.values),
            limit=args.limit,
        )
        print_hits(f"Semantic + keyword filter on {args.field}", hits)
        return

    if args.mode == "semantic_text":
        hits = semantic_search_with_text_filter(
            qdrant_client=qdrant_client,
            openai_client=openai_client,
            query_text=args.query,
            field=args.field,
            text=args.text,
            limit=args.limit,
            any_mode=args.any_mode,
        )
        print_hits(f"Semantic + text filter on {args.field}", hits)
        return

    if args.mode == "keyword_only":
        hits = keyword_filter_only_search(
            qdrant_client=qdrant_client,
            field=args.field,
            values=parse_csv(args.values),
            limit=args.limit,
        )
        print_hits(f"Keyword-only search on {args.field}", hits, show_score=False)
        return

    if args.mode == "text_only":
        hits = text_filter_only_search(
            qdrant_client=qdrant_client,
            field=args.field,
            text=args.text,
            limit=args.limit,
            any_mode=args.any_mode,
        )
        print_hits(f"Text-only search on {args.field}", hits, show_score=False)
        return


if __name__ == "__main__":
    main()