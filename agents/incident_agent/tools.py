from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from uuid import uuid4

from langchain.messages import ToolMessage
from langchain.tools import ToolRuntime, tool
from langgraph.types import Command
from pydantic import BaseModel

from search.qdrant_search_api import QdrantSearchAPI

from .schemas import (
    GetIncidentDetailsInput,
    GetIncidentMarkdownChunkInput,
    SaveFinalReportInput,
    SearchIncidentsHydeInput,
    SearchIncidentsInput,
)

logger = logging.getLogger("incident_agent.tools")


def _to_json_safe(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump()

    if isinstance(value, dict):
        return {k: _to_json_safe(v) for k, v in value.items()}

    if isinstance(value, list):
        return [_to_json_safe(v) for v in value]

    return value


def _truncate(text: str, limit: int = 100) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _derive_report_title(summary: str) -> str:
    cleaned = " ".join((summary or "").split())
    if not cleaned:
        return "Incident analysis report"
    return _truncate(cleaned, 80)

def _build_hyde_embedding_text(
    *,
    company: str | None = None,
    short_description: str | None = None,
    symptoms: str | None = None,
    root_cause: str | None = None,
    resolution: str | None = None,
    lessons_learned: str | None = None,
) -> str:
    parts = [
        (company or "").strip(),
        (short_description or "").strip(),
        (symptoms or "").strip(),
        (root_cause or "").strip(),
        (resolution or "").strip(),
        (lessons_learned or "").strip(),
    ]
    parts = [part for part in parts if part]
    return "\n\n".join(parts)

def _state_get(runtime: ToolRuntime, key: str, default: Any = None) -> Any:
    state = getattr(runtime, "state", None) or {}
    if hasattr(state, "get"):
        return state.get(key, default)
    return default


def get_report_namespace(user_id: str) -> tuple[str, ...]:
    return ("incident_agent", "reports", user_id)


def load_saved_report(store: Any, user_id: str, report_id: str) -> dict[str, Any] | None:
    item = store.get(get_report_namespace(user_id), report_id)
    if item is None:
        return None
    value = getattr(item, "value", None)
    if value is None:
        return None
    return _to_json_safe(value)


@lru_cache(maxsize=1)
def get_search_api() -> QdrantSearchAPI:
    return QdrantSearchAPI.from_env(debug=False)


def _log_event(event: str, **payload: Any) -> None:
    try:
        logger.info("%s | %s", event, json.dumps(_to_json_safe(payload), ensure_ascii=False, default=str))
    except Exception:
        logger.info("%s | %r", event, payload)


@tool
def research_update(note: str) -> str:
    """
    Send a short user-visible research update about your progress.
    Use it to briefly explain what you are checking, why you changed strategy,
    or what you found so far.
    Do not dump long hidden reasoning or chain-of-thought.
    Keep it concise, practical, and action-oriented.
    """
    cleaned = (note or "").strip()
    _log_event("research_update", note=cleaned)
    return "Research update recorded. Proceed to the next action."


@tool(args_schema=SearchIncidentsInput)
def search_incidents(
    query_text: str | None = None,
    document_kinds: list[str] | None = None,
    incident_categories: list[str] | None = None,
    infrastructure: list[str] | None = None,
    company: str | None = None,
    tech_stack_text: str | None = None,
    key_terms_text: str | None = None,
    limit: int = 5,
    runtime: ToolRuntime | None = None,
) -> Command:
    """
    Search incident documents using semantic query, enum filters, and optional text refinements.
    Prefer broad semantic search first, then add filters cautiously.
    """
    if runtime is None:
        raise RuntimeError("search_incidents expected ToolRuntime but did not receive it.")

    api = get_search_api()

    keyword_filters = []
    text_filters = []

    if document_kinds:
        keyword_filters.append(api.keyword_filter("document_kind", document_kinds))
    if incident_categories:
        keyword_filters.append(api.keyword_filter("incident_categories", incident_categories))
    if infrastructure:
        keyword_filters.append(api.keyword_filter("infrastructure", infrastructure))
    if company and company.strip():
        keyword_filters.append(api.keyword_filter("company", [company.strip()]))

    if tech_stack_text and tech_stack_text.strip():
        text_filters.append(api.text_filter("tech_stack_text", tech_stack_text, any_mode=False))
    if key_terms_text and key_terms_text.strip():
        text_filters.append(api.text_filter("key_terms_text", key_terms_text, any_mode=False))

    filters_snapshot = {
        "document_kinds": document_kinds or [],
        "incident_categories": incident_categories or [],
        "infrastructure": infrastructure or [],
        "company": company.strip() if company and company.strip() else None,
        "tech_stack_text": tech_stack_text.strip() if tech_stack_text and tech_stack_text.strip() else None,
        "key_terms_text": key_terms_text.strip() if key_terms_text and key_terms_text.strip() else None,
        "limit": limit,
    }

    _log_event(
        "tool_called",
        tool_name="search_incidents",
        tool_call_id=runtime.tool_call_id,
        query_text=query_text,
        **filters_snapshot,
    )

    result = api.search(
        query_text=query_text,
        limit=limit,
        keyword_filters=keyword_filters or None,
        text_filters=text_filters or None,
    )

    brief_hits = [api.hit_to_brief_dict(hit) for hit in result.hits]

    payload = {
        "mode": result.mode,
        "query_text": result.query_text,
        "filter_applied": result.filter_applied,
        "hit_count": len(result.hits),
        "hits": brief_hits,
    }
    payload_text = json.dumps(payload, ensure_ascii=False, indent=2)

    _log_event(
        "tool_result",
        tool_name="search_incidents",
        tool_call_id=runtime.tool_call_id,
        mode=result.mode,
        hit_count=len(result.hits),
        point_ids=[hit["point_id"] for hit in brief_hits],
    )

    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=payload_text,
                    tool_call_id=runtime.tool_call_id,
                )
            ],
            "last_search_query": query_text,
            "last_search_filters": filters_snapshot,
            "last_shortlist": brief_hits,
        }
    )

@tool(args_schema=SearchIncidentsHydeInput)
def search_incidents_hyde(
    company: str | None = None,
    short_description: str | None = None,
    symptoms: str | None = None,
    root_cause: str | None = None,
    resolution: str | None = None,
    lessons_learned: str | None = None,
    document_kinds: list[str] | None = None,
    incident_categories: list[str] | None = None,
    infrastructure: list[str] | None = None,
    company_filter: str | None = None,
    tech_stack_text: str | None = None,
    key_terms_text: str | None = None,
    limit: int = 5,
    runtime: ToolRuntime | None = None,
) -> Command:
    """
    Search incidents using a structured hypothetical incident document (HyDE-style).
    This is useful when the user describes a scenario or pattern rather than a precise keyword query.
    """
    if runtime is None:
        raise RuntimeError("search_incidents_hyde expected ToolRuntime but did not receive it.")

    api = get_search_api()

    hyde_query_text = _build_hyde_embedding_text(
        company=company,
        short_description=short_description,
        symptoms=symptoms,
        root_cause=root_cause,
        resolution=resolution,
        lessons_learned=lessons_learned,
    )

    if not hyde_query_text.strip():
        payload = {
            "mode": "semantic_hyde",
            "error": "At least one HyDE text field must be provided.",
            "hyde_query_text": "",
            "filter_applied": False,
            "hit_count": 0,
            "hits": [],
        }
        payload_text = json.dumps(payload, ensure_ascii=False, indent=2)

        _log_event(
            "tool_result",
            tool_name="search_incidents_hyde",
            tool_call_id=runtime.tool_call_id,
            error="empty_hyde_query",
        )

        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=payload_text,
                        tool_call_id=runtime.tool_call_id,
                    )
                ]
            }
        )

    keyword_filters = []
    text_filters = []

    if document_kinds:
        keyword_filters.append(api.keyword_filter("document_kind", document_kinds))
    if incident_categories:
        keyword_filters.append(api.keyword_filter("incident_categories", incident_categories))
    if infrastructure:
        keyword_filters.append(api.keyword_filter("infrastructure", infrastructure))
    if company_filter and company_filter.strip():
        keyword_filters.append(api.keyword_filter("company", [company_filter.strip()]))

    if tech_stack_text and tech_stack_text.strip():
        text_filters.append(api.text_filter("tech_stack_text", tech_stack_text, any_mode=False))
    if key_terms_text and key_terms_text.strip():
        text_filters.append(api.text_filter("key_terms_text", key_terms_text, any_mode=False))

    filters_snapshot = {
        "search_strategy": "hyde",
        "document_kinds": document_kinds or [],
        "incident_categories": incident_categories or [],
        "infrastructure": infrastructure or [],
        "company_filter": company_filter.strip() if company_filter and company_filter.strip() else None,
        "tech_stack_text": tech_stack_text.strip() if tech_stack_text and tech_stack_text.strip() else None,
        "key_terms_text": key_terms_text.strip() if key_terms_text and key_terms_text.strip() else None,
        "limit": limit,
    }

    _log_event(
        "tool_called",
        tool_name="search_incidents_hyde",
        tool_call_id=runtime.tool_call_id,
        hyde_query_text=hyde_query_text,
        company=company,
        short_description=short_description,
        symptoms=symptoms,
        root_cause=root_cause,
        resolution=resolution,
        lessons_learned=lessons_learned,
        **filters_snapshot,
    )

    result = api.search(
        query_text=hyde_query_text,
        limit=limit,
        keyword_filters=keyword_filters or None,
        text_filters=text_filters or None,
    )

    brief_hits = [api.hit_to_brief_dict(hit) for hit in result.hits]

    payload = {
        "mode": "semantic_hyde",
        "hyde_query_text": hyde_query_text,
        "filter_applied": result.filter_applied,
        "hit_count": len(result.hits),
        "hits": brief_hits,
    }
    payload_text = json.dumps(payload, ensure_ascii=False, indent=2)

    _log_event(
        "tool_result",
        tool_name="search_incidents_hyde",
        tool_call_id=runtime.tool_call_id,
        mode="semantic_hyde",
        hit_count=len(result.hits),
        point_ids=[hit["point_id"] for hit in brief_hits],
    )

    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=payload_text,
                    tool_call_id=runtime.tool_call_id,
                )
            ],
            "last_search_query": hyde_query_text,
            "last_search_filters": filters_snapshot,
            "last_shortlist": brief_hits,
        }
    )

@tool(args_schema=GetIncidentDetailsInput)
def get_incident_details(
    point_id: str | None = None,
    url: str | None = None,
    include_markdown: bool = False,
    markdown_char_limit: int = 4000,
) -> str:
    """
    Retrieve detailed information for a specific incident document by point_id or URL.
    Prefer point_id when available.

    If include_markdown=True, returns only the first markdown preview chunk.
    Use get_incident_markdown_chunk to read later ranges of the markdown.
    """
    api = get_search_api()

    _log_event(
        "tool_called",
        tool_name="get_incident_details",
        point_id=point_id,
        url=url,
        include_markdown=include_markdown,
        markdown_char_limit=markdown_char_limit,
    )

    hit = None
    if point_id and str(point_id).strip():
        hit = api.get_by_point_id(point_id)
    elif url and str(url).strip():
        hit = api.get_by_url(url)

    if hit is None:
        payload = {
            "found": False,
            "error": "Document not found.",
            "point_id": point_id,
            "url": url,
        }
        _log_event("tool_result", tool_name="get_incident_details", found=False)
        return json.dumps(payload, ensure_ascii=False, indent=2)

    p = hit.payload
    markdown_content = p.get("markdown_content")

    markdown_available = isinstance(markdown_content, str) and len(markdown_content) > 0
    markdown_total_length = len(markdown_content) if isinstance(markdown_content, str) else 0

    markdown_out = None
    markdown_truncated = False
    markdown_preview_start_char = None
    markdown_preview_end_char = None
    markdown_has_more = False
    markdown_next_start_char = None

    if include_markdown and markdown_available:
        markdown_preview_start_char = 0
        markdown_preview_end_char = min(markdown_char_limit, markdown_total_length)
        markdown_out = markdown_content[markdown_preview_start_char:markdown_preview_end_char]
        markdown_truncated = markdown_preview_end_char < markdown_total_length
        markdown_has_more = markdown_truncated
        markdown_next_start_char = markdown_preview_end_char if markdown_has_more else None

    payload = {
        "found": True,
        "point_id": hit.point_id,
        "url": p.get("url"),
        "name": p.get("name"),
        "description": p.get("description"),
        "company": p.get("company"),
        "date": p.get("date"),
        "document_kind": p.get("document_kind"),
        "short_description": p.get("short_description"),
        "incident_categories": p.get("incident_categories", []),
        "tech_stack": p.get("tech_stack", []),
        "infrastructure": p.get("infrastructure", []),
        "key_terms": p.get("key_terms", []),
        "symptoms": p.get("symptoms"),
        "root_cause": p.get("root_cause"),
        "resolution": p.get("resolution"),
        "lessons_learned": p.get("lessons_learned"),
        "stage6_confidence": p.get("stage6_confidence"),
        "markdown_available": markdown_available,
        "markdown_total_length": markdown_total_length,
        "markdown_included": include_markdown,
        "markdown_preview_start_char": markdown_preview_start_char,
        "markdown_preview_end_char": markdown_preview_end_char,
        "markdown_truncated": markdown_truncated,
        "markdown_has_more": markdown_has_more,
        "markdown_next_start_char": markdown_next_start_char,
        "markdown_content": markdown_out,
    }

    _log_event(
        "tool_result",
        tool_name="get_incident_details",
        found=True,
        point_id=hit.point_id,
        url=p.get("url"),
        markdown_available=markdown_available,
        markdown_total_length=markdown_total_length,
        markdown_truncated=markdown_truncated,
    )

    return json.dumps(payload, ensure_ascii=False, indent=2)

@tool(args_schema=GetIncidentMarkdownChunkInput)
def get_incident_markdown_chunk(
    point_id: str | None = None,
    url: str | None = None,
    start_char: int = 0,
    end_char: int = 2000,
) -> str:
    """
    Retrieve a specific markdown_content character range for a document.
    Use this to continue reading beyond the initial preview returned by get_incident_details.
    """
    api = get_search_api()

    _log_event(
        "tool_called",
        tool_name="get_incident_markdown_chunk",
        point_id=point_id,
        url=url,
        start_char=start_char,
        end_char=end_char,
    )

    hit = None
    if point_id and str(point_id).strip():
        hit = api.get_by_point_id(point_id)
    elif url and str(url).strip():
        hit = api.get_by_url(url)

    if hit is None:
        payload = {
            "found": False,
            "error": "Document not found.",
            "point_id": point_id,
            "url": url,
            "start_char": start_char,
            "end_char": end_char,
        }
        _log_event("tool_result", tool_name="get_incident_markdown_chunk", found=False)
        return json.dumps(payload, ensure_ascii=False, indent=2)

    p = hit.payload
    markdown_content = p.get("markdown_content")

    if not isinstance(markdown_content, str) or not markdown_content:
        payload = {
            "found": True,
            "point_id": hit.point_id,
            "url": p.get("url"),
            "markdown_available": False,
            "error": "markdown_content is missing or empty.",
            "start_char": start_char,
            "end_char": end_char,
        }
        _log_event(
            "tool_result",
            tool_name="get_incident_markdown_chunk",
            found=True,
            markdown_available=False,
            point_id=hit.point_id,
            url=p.get("url"),
        )
        return json.dumps(payload, ensure_ascii=False, indent=2)

    total_length = len(markdown_content)
    safe_start = max(0, start_char)
    safe_end = max(safe_start + 1, end_char)

    actual_start = min(safe_start, total_length)
    actual_end = min(safe_end, total_length)

    chunk_text = markdown_content[actual_start:actual_end]
    has_more = actual_end < total_length
    next_start_char = actual_end if has_more else None

    payload = {
        "found": True,
        "point_id": hit.point_id,
        "url": p.get("url"),
        "name": p.get("name"),
        "markdown_available": True,
        "markdown_total_length": total_length,
        "requested_start_char": start_char,
        "requested_end_char": end_char,
        "actual_start_char": actual_start,
        "actual_end_char": actual_end,
        "chunk_char_count": len(chunk_text),
        "has_more": has_more,
        "next_start_char": next_start_char,
        "chunk_text": chunk_text,
    }

    _log_event(
        "tool_result",
        tool_name="get_incident_markdown_chunk",
        found=True,
        point_id=hit.point_id,
        url=p.get("url"),
        actual_start_char=actual_start,
        actual_end_char=actual_end,
        has_more=has_more,
    )

    return json.dumps(payload, ensure_ascii=False, indent=2)

@tool(args_schema=SaveFinalReportInput)
def save_final_report(
    summary: str,
    title: str | None = None,
    likely_patterns: list[str] | None = None,
    similar_incidents: list[Any] | None = None,
    recommended_checks: list[str] | None = None,
    possible_mitigations: list[str] | None = None,
    caveats: list[str] | None = None,
    references: list[str] | None = None,
    runtime: ToolRuntime | None = None,
) -> Command:
    """
    Save a structured final report as a persistent artifact and return only a short acknowledgement.
    Use this when a structured saved document is more useful than a plain chat reply.
    """
    if runtime is None:
        raise RuntimeError("save_final_report expected ToolRuntime but did not receive it.")

    safe_payload = _to_json_safe(
        {
            "title": title.strip() if title and title.strip() else None,
            "summary": summary,
            "likely_patterns": likely_patterns or [],
            "similar_incidents": similar_incidents or [],
            "recommended_checks": recommended_checks or [],
            "possible_mitigations": possible_mitigations or [],
            "caveats": caveats or [],
            "references": references or [],
        }
    )

    if getattr(runtime, "store", None) is None:
        _log_event("report_save_failed", reason="store_unavailable", tool_call_id=runtime.tool_call_id)
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content="Report could not be saved because the artifact store is unavailable.",
                        tool_call_id=runtime.tool_call_id,
                    )
                ]
            }
        )

    user_id = getattr(getattr(runtime, "context", None), "user_id", "local_user")
    thread_id = getattr(getattr(runtime, "context", None), "thread_id", "unknown_thread")

    report_id = f"rep_{uuid4().hex[:12]}"
    report_title = safe_payload["title"] or _derive_report_title(summary)

    report = {
        "report_id": report_id,
        "title": report_title,
        "kind": "incident_analysis_report",
        "user_id": user_id,
        "thread_id": thread_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_search_context": {
            "last_search_query": _state_get(runtime, "last_search_query"),
            "last_search_filters": _state_get(runtime, "last_search_filters", {}),
            "last_shortlist": _state_get(runtime, "last_shortlist", []),
        },
        **safe_payload,
    }

    namespace = get_report_namespace(user_id)
    runtime.store.put(namespace, report_id, report)

    existing_report_ids = list(_state_get(runtime, "recent_report_ids", []) or [])
    recent_report_ids = [report_id, *[rid for rid in existing_report_ids if rid != report_id]][:10]

    _log_event(
        "report_saved",
        tool_name="save_final_report",
        tool_call_id=runtime.tool_call_id,
        report_id=report_id,
        title=report_title,
        user_id=user_id,
        thread_id=thread_id,
        namespace=namespace,
    )

    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=(
                        f"Report saved as {report_id}. "
                        "Do not repeat the full saved report unless the user explicitly asks for it."
                    ),
                    tool_call_id=runtime.tool_call_id,
                )
            ],
            "current_report_id": report_id,
            "recent_report_ids": recent_report_ids,
            "last_report_title": report_title,
        }
    )