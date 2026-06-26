import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from uuid import NAMESPACE_URL, uuid5

from dotenv import load_dotenv
from openai import OpenAI
from qdrant_client import QdrantClient, models

from prefect import task



# =============================================================================
# CONFIG
# =============================================================================
load_dotenv()

INPUT_FILE = Path("parsed_danluu/danluu_postmortems_qdrant_ready.jsonl")

# OpenAI-compatible embeddings endpoint
EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", os.getenv("OPENAI_MODEL", "text-embedding-3-small"))
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Qdrant connection
QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "incident_documents")

QDRANT_LOCAL_MODE = os.getenv("QDRANT_LOCAL_MODE", "false").lower() == "true"
QDRANT_LOCAL_PATH = os.getenv("QDRANT_LOCAL_PATH", "").strip()

# Upload settings
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
UPSERT_BATCH_SIZE = int(os.getenv("UPSERT_BATCH_SIZE", "32"))
RECREATE_COLLECTION = os.getenv("RECREATE_COLLECTION", "false").lower() == "true"

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


def safe_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def chunked(items: list[Any], size: int) -> Iterable[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def normalize_date_to_rfc3339(date_value: Any) -> str | None:
    """
    Converts YYYY-MM-DD into RFC3339 UTC date-time for Qdrant datetime payload index.
    """
    text = safe_str(date_value)
    if not text:
        return None

    try:
        parsed = datetime.strptime(text, "%Y-%m-%d")
        return parsed.strftime("%Y-%m-%dT00:00:00Z")
    except ValueError:
        # If already in a richer format or unexpected, keep as-is only if it looks like datetime-ish.
        # Otherwise return None to avoid noisy invalid datetime payloads.
        if "T" in text or "Z" in text:
            return text
        return None


def build_embedding_text_from_row(row: dict) -> str:
    """
    Rebuild embedding text from semantic stage6 fields.
    """
    parts = [
        safe_str(row.get("company")),
        safe_str(row.get("short_description")),
        safe_str(row.get("symptoms")),
        safe_str(row.get("root_cause")),
        safe_str(row.get("resolution")),
        safe_str(row.get("lessons_learned")),
    ]
    parts = [part for part in parts if part]
    return "\n\n".join(parts)


def make_point_id(row: dict) -> str:
    existing = safe_str(row.get("qdrant_point_id"))
    if existing:
        return existing

    url = safe_str(row.get("url"))
    if url:
        return str(uuid5(NAMESPACE_URL, url))

    fallback = f"row-{row.get('_line_no', 'unknown')}"
    return str(uuid5(NAMESPACE_URL, fallback))


def build_payload(row: dict) -> dict:
    """
    Payload that will be stored in Qdrant.
    """
    tech_stack = row.get("tech_stack", [])
    if not isinstance(tech_stack, list):
        tech_stack = []

    key_terms = row.get("key_terms", [])
    if not isinstance(key_terms, list):
        key_terms = []

    incident_categories = row.get("incident_categories", [])
    if not isinstance(incident_categories, list):
        incident_categories = []

    infrastructure = row.get("infrastructure", [])
    if not isinstance(infrastructure, list):
        infrastructure = []

    payload = {
        "url": safe_str(row.get("url")),
        "name": safe_str(row.get("name")),
        "description": safe_str(row.get("description")),
        "company": safe_str(row.get("company")) or None,
        "date": normalize_date_to_rfc3339(row.get("date")),
        "document_kind": safe_str(row.get("document_kind")) or None,
        "short_description": safe_str(row.get("short_description")),
        "incident_categories": [safe_str(x) for x in incident_categories if safe_str(x)],
        "tech_stack": [safe_str(x) for x in tech_stack if safe_str(x)],
        "tech_stack_text": safe_str(row.get("tech_stack_text")),
        "infrastructure": [safe_str(x) for x in infrastructure if safe_str(x)],
        "key_terms": [safe_str(x) for x in key_terms if safe_str(x)],
        "key_terms_text": safe_str(row.get("key_terms_text")),
        "symptoms": safe_str(row.get("symptoms")),
        "root_cause": safe_str(row.get("root_cause")),
        "resolution": safe_str(row.get("resolution")),
        "lessons_learned": safe_str(row.get("lessons_learned")),
        "markdown_content": safe_str(row.get("markdown_content")),
        "embedding_text": build_embedding_text_from_row(row),
        "stage6_confidence": row.get("stage6_confidence"),
    }
    return payload


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
    debug_print(f"[INFO] Using remote/local server Qdrant at: {url}")

    if QDRANT_API_KEY:
        return QdrantClient(url=url, api_key=QDRANT_API_KEY)
    return QdrantClient(url=url)


def embed_texts(client: OpenAI, texts: list[str]) -> list[list[float]]:
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=texts,
    )
    return [item.embedding for item in response.data]


def ensure_collection(qdrant_client: QdrantClient, collection_name: str, vector_size: int) -> None:
    if RECREATE_COLLECTION and qdrant_client.collection_exists(collection_name):
        debug_print(f"[INFO] Recreating collection: {collection_name}")
        qdrant_client.delete_collection(collection_name=collection_name)

    if not qdrant_client.collection_exists(collection_name):
        debug_print(f"[INFO] Creating collection: {collection_name} (vector_size={vector_size})")
        qdrant_client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=vector_size,
                distance=models.Distance.COSINE,
            ),
        )
    else:
        debug_print(f"[INFO] Collection already exists: {collection_name}")


def create_payload_indexes(qdrant_client: QdrantClient, collection_name: str) -> None:
    index_plan = [
        ("url", models.PayloadSchemaType.KEYWORD),
        ("company", models.PayloadSchemaType.KEYWORD),
        ("document_kind", models.PayloadSchemaType.KEYWORD),
        ("incident_categories", models.PayloadSchemaType.KEYWORD),
        ("tech_stack", models.PayloadSchemaType.KEYWORD),
        ("infrastructure", models.PayloadSchemaType.KEYWORD),
        ("key_terms", models.PayloadSchemaType.KEYWORD),
        ("date", models.PayloadSchemaType.DATETIME),
        ("stage6_confidence", models.PayloadSchemaType.FLOAT),
    ]

    text_index_plan = [
        (
            "tech_stack_text",
            models.TextIndexParams(
                type=models.TextIndexType.TEXT,
                tokenizer=models.TokenizerType.WORD,
                lowercase=True,
                min_token_len=2,
                max_token_len=64,
                phrase_matching=False,
            ),
        ),
        (
            "key_terms_text",
            models.TextIndexParams(
                type=models.TextIndexType.TEXT,
                tokenizer=models.TokenizerType.WORD,
                lowercase=True,
                min_token_len=2,
                max_token_len=64,
                phrase_matching=False,
            ),
        ),
    ]

    for field_name, schema in index_plan:
        try:
            qdrant_client.create_payload_index(
                collection_name=collection_name,
                field_name=field_name,
                field_schema=schema,
                wait=True,
            )
            debug_print(f"[INDEX] {field_name} -> {schema}")
        except Exception as e:
            debug_print(f"[WARN] Failed to create payload index for {field_name}: {e}")

    for field_name, text_schema in text_index_plan:
        try:
            qdrant_client.create_payload_index(
                collection_name=collection_name,
                field_name=field_name,
                field_schema=text_schema,
                wait=True,
            )
            debug_print(f"[INDEX] {field_name} -> text")
        except Exception as e:
            debug_print(f"[WARN] Failed to create text index for {field_name}: {e}")


def print_payload_schema() -> None:
    schema = {
        "url": "keyword",
        "company": "keyword",
        "document_kind": "keyword",
        "incident_categories": "keyword (array)",
        "tech_stack": "keyword (array)",
        "tech_stack_text": "text",
        "infrastructure": "keyword (array)",
        "key_terms": "keyword (array)",
        "key_terms_text": "text",
        "date": "datetime (RFC3339)",
        "stage6_confidence": "float",
        "markdown_content": "stored payload only",
        "embedding_text": "stored payload only",
        "name": "stored payload only",
        "description": "stored payload only",
        "short_description": "stored payload only",
        "symptoms": "stored payload only",
        "root_cause": "stored payload only",
        "resolution": "stored payload only",
        "lessons_learned": "stored payload only",
    }

    print("=" * 80)
    print("=== QDRANT PAYLOAD / INDEX PLAN ===")
    for key, value in schema.items():
        print(f"{key}: {value}")
    print("=" * 80)


@task
def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Input file not found: {INPUT_FILE}")

    print_payload_schema()

    rows = load_jsonl(INPUT_FILE)
    if not rows:
        print("[INFO] No rows found in input file.")
        return

    debug_print(f"[INFO] Input rows: {len(rows)}")

    prepared = []
    for row in rows:
        embedding_text = build_embedding_text_from_row(row)
        if not embedding_text.strip():
            debug_print(f"[SKIP] Empty embedding text for URL: {row.get('url')}")
            continue

        prepared.append(
            {
                "id": make_point_id(row),
                "vector_text": embedding_text,
                "payload": build_payload(row),
            }
        )

    if not prepared:
        print("[INFO] No valid rows to upload.")
        return

    debug_print(f"[INFO] Rows ready for embedding/upload: {len(prepared)}")

    openai_client = build_openai_client()
    qdrant_client = build_qdrant_client()

    # Detect vector dimension from the first embedding
    first_embedding = embed_texts(openai_client, [prepared[0]["vector_text"]])[0]
    vector_size = len(first_embedding)
    debug_print(f"[INFO] Detected embedding dimension: {vector_size}")

    ensure_collection(qdrant_client, QDRANT_COLLECTION, vector_size)
    create_payload_indexes(qdrant_client, QDRANT_COLLECTION)

    uploaded_points = 0

    for batch in chunked(prepared, EMBEDDING_BATCH_SIZE):
        texts = [item["vector_text"] for item in batch]
        vectors = embed_texts(openai_client, texts)

        points = []
        for item, vector in zip(batch, vectors):
            points.append(
                models.PointStruct(
                    id=item["id"],
                    vector=vector,
                    payload=item["payload"],
                )
            )

        # Upsert batch
        for points_batch in chunked(points, UPSERT_BATCH_SIZE):
            qdrant_client.upsert(
                collection_name=QDRANT_COLLECTION,
                points=points_batch,
                wait=True,
            )
            uploaded_points += len(points_batch)
            debug_print(f"[UPSERT] Uploaded {uploaded_points}/{len(prepared)} points")

    print("=" * 80)
    print("=== QDRANT UPLOAD COMPLETE ===")
    print(f"Input rows: {len(rows)}")
    print(f"Uploaded points: {uploaded_points}")
    print(f"Collection: {QDRANT_COLLECTION}")
    print(f"Embedding model: {EMBEDDING_MODEL}")
    if QDRANT_LOCAL_MODE:
        print(f"Qdrant mode: local ({QDRANT_LOCAL_PATH or ':memory:'})")
    else:
        print(f"Qdrant mode: server ({QDRANT_URL})")
    print("=" * 80)


if __name__ == "__main__":
    main()