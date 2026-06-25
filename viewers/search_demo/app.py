import json
import sys
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from search.qdrant_search_api import QdrantSearchAPI  # noqa: E402


QDRANT_READY_FILE = PROJECT_ROOT / "parsed_danluu" / "danluu_postmortems_qdrant_ready.jsonl"
DEFAULT_LIMIT = 10
DEFAULT_PORT = 8511


app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)

search_api = QdrantSearchAPI.from_env(debug=False)


def safe_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows

    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                obj["_line_no"] = line_no
                rows.append(obj)
            except json.JSONDecodeError:
                continue
    return rows


def collect_filter_options(rows: list[dict]) -> dict[str, list[str]]:
    document_kinds = set()
    incident_categories = set()
    infrastructures = set()
    companies = set()

    for row in rows:
        kind = safe_str(row.get("document_kind"))
        if kind:
            document_kinds.add(kind)

        company = safe_str(row.get("company"))
        if company:
            companies.add(company)

        for item in row.get("incident_categories", []) or []:
            item = safe_str(item)
            if item:
                incident_categories.add(item)

        for item in row.get("infrastructure", []) or []:
            item = safe_str(item)
            if item:
                infrastructures.add(item)

    return {
        "document_kinds": sorted(document_kinds),
        "incident_categories": sorted(incident_categories),
        "infrastructures": sorted(infrastructures),
        "companies": sorted(companies),
    }


ALL_ROWS = load_jsonl(QDRANT_READY_FILE)
FILTER_OPTIONS = collect_filter_options(ALL_ROWS)


def normalize_multivalue(values: list[str]) -> list[str]:
    result = []
    seen = set()

    for raw in values:
        if not raw:
            continue

        for part in str(raw).split(","):
            item = part.strip()
            if not item:
                continue
            if item not in seen:
                seen.add(item)
                result.append(item)

    return result


def get_multi_arg(name: str) -> list[str]:
    return normalize_multivalue(request.args.getlist(name))


def get_bool_arg(name: str) -> bool:
    value = request.args.get(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def get_int_arg(name: str, default: int) -> int:
    raw = request.args.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        return max(1, min(value, 100))
    except ValueError:
        return default


def get_float_arg(name: str) -> float | None:
    raw = request.args.get(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def build_search_context() -> dict:
    query_text = request.args.get("query", "").strip()
    limit = get_int_arg("limit", DEFAULT_LIMIT)
    score_threshold = get_float_arg("score_threshold")

    document_kinds = get_multi_arg("document_kind")
    incident_categories = get_multi_arg("incident_categories")
    infrastructures = get_multi_arg("infrastructure")
    companies = get_multi_arg("company")

    tech_stack_text = request.args.get("tech_stack_text", "").strip()
    tech_stack_any = get_bool_arg("tech_stack_any")

    key_terms_text = request.args.get("key_terms_text", "").strip()
    key_terms_any = get_bool_arg("key_terms_any")

    keyword_filters = []
    text_filters = []

    if document_kinds:
        keyword_filters.append(search_api.keyword_filter("document_kind", document_kinds))
    if incident_categories:
        keyword_filters.append(search_api.keyword_filter("incident_categories", incident_categories))
    if infrastructures:
        keyword_filters.append(search_api.keyword_filter("infrastructure", infrastructures))
    if companies:
        keyword_filters.append(search_api.keyword_filter("company", companies))

    if tech_stack_text:
        text_filters.append(
            search_api.text_filter(
                "tech_stack_text",
                tech_stack_text,
                any_mode=tech_stack_any,
            )
        )

    if key_terms_text:
        text_filters.append(
            search_api.text_filter(
                "key_terms_text",
                key_terms_text,
                any_mode=key_terms_any,
            )
        )

    result = search_api.search(
        query_text=query_text or None,
        limit=limit,
        score_threshold=score_threshold,
        keyword_filters=keyword_filters or None,
        text_filters=text_filters or None,
    )

    return {
        "query_text": query_text,
        "limit": limit,
        "score_threshold": score_threshold,
        "document_kinds": document_kinds,
        "incident_categories": incident_categories,
        "infrastructures": infrastructures,
        "companies": companies,
        "tech_stack_text": tech_stack_text,
        "tech_stack_any": tech_stack_any,
        "key_terms_text": key_terms_text,
        "key_terms_any": key_terms_any,
        "result": result,
    }


@app.route("/")
def index():
    context = build_search_context()
    return render_template(
        "index.html",
        filter_options=FILTER_OPTIONS,
        total_dataset_rows=len(ALL_ROWS),
        qdrant_collection=search_api.collection_name,
        embedding_model=search_api.embedding_model,
        **context,
    )


@app.route("/api/search")
def api_search():
    context = build_search_context()
    result = context["result"]

    payload = {
        "mode": result.mode,
        "limit": result.limit,
        "filter_applied": result.filter_applied,
        "query_text": result.query_text,
        "returned_hits": len(result.hits),
        "hits": [
            {
                "point_id": hit.point_id,
                "score": hit.score,
                "payload": hit.payload,
            }
            for hit in result.hits
        ],
    }
    return jsonify(payload)


if __name__ == "__main__":
    app.run(debug=True, port=DEFAULT_PORT)