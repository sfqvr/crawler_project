from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from flask import Flask, request, send_from_directory
from langchain_core.messages import BaseMessage

from .agent import build_agent, configure_logging, get_store
from .schemas import IncidentAgentContext
from .tools import load_saved_report

logger = logging.getLogger("incident_agent.flask_app")

THREAD_REPORT_INDEX: dict[tuple[str, str], list[str]] = {}
LAST_SENT_REPORT_ID: dict[tuple[str, str], str] = {}
THREAD_UI_HISTORY: dict[tuple[str, str], list[dict[str, Any]]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "\n".join(parts)

    return str(content)


def _message_to_text(message: BaseMessage) -> str:
    return _content_to_text(message.content)


def _truncate(text: str, limit: int = 500) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _message_type(message: BaseMessage) -> str:
    return str(getattr(message, "type", "") or "").lower()


def _extract_saved_report_id(text: str) -> str | None:
    match = re.search(r"Report saved as (rep_[A-Za-z0-9]+)", text or "")
    if not match:
        return None
    return match.group(1)


def _extract_tool_trace(turn_messages: list[BaseMessage]) -> list[dict[str, Any]]:
    tool_calls_by_id: dict[str, dict[str, Any]] = {}
    traces: list[dict[str, Any]] = []

    for message in turn_messages:
        tool_calls = getattr(message, "tool_calls", None) or []
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue

            tool_call_id = tool_call.get("id") or tool_call.get("tool_call_id")
            if not tool_call_id:
                continue

            tool_calls_by_id[tool_call_id] = {
                "name": tool_call.get("name") or "tool",
                "args": tool_call.get("args") or {},
            }

    for message in turn_messages:
        if _message_type(message) != "tool":
            continue

        tool_call_id = getattr(message, "tool_call_id", None)
        meta = tool_calls_by_id.get(tool_call_id or "", {})

        traces.append(
            {
                "tool_call_id": tool_call_id,
                "name": getattr(message, "name", None) or meta.get("name") or "tool",
                "status": "done",
                "input": meta.get("args") or {},
                "result_preview": _truncate(_message_to_text(message), 800),
            }
        )

    return traces


def _build_artifact_preview(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "report_id": report.get("report_id"),
        "title": report.get("title"),
        "created_at_utc": report.get("created_at_utc"),
        "summary_excerpt": _truncate(report.get("summary", ""), 260),
        "likely_patterns_count": len(report.get("likely_patterns", []) or []),
        "similar_incidents_count": len(report.get("similar_incidents", []) or []),
        "recommended_checks_count": len(report.get("recommended_checks", []) or []),
        "possible_mitigations_count": len(report.get("possible_mitigations", []) or []),
    }


def _load_recent_artifacts(store: Any, user_id: str, thread_id: str) -> list[dict[str, Any]]:
    report_ids = THREAD_REPORT_INDEX.get((user_id, thread_id), [])
    items: list[dict[str, Any]] = []

    for report_id in report_ids:
        report = load_saved_report(store, user_id, report_id)
        if report:
            items.append(_build_artifact_preview(report))

    return items


def _slice_current_turn(messages: list[BaseMessage]) -> list[BaseMessage]:
    last_user_index = -1
    for i, message in enumerate(messages):
        if _message_type(message) in {"human", "user"}:
            last_user_index = i

    if last_user_index == -1:
        return messages

    return messages[last_user_index + 1 :]


def _reconstruct_ui_messages(
    agent: Any,
    store: Any,
    user_id: str,
    thread_id: str,
) -> list[dict[str, Any]]:
    try:
        snapshot = agent.get_state({"configurable": {"thread_id": thread_id}})
    except Exception:
        logger.exception(
            "failed_to_load_thread_state | user_id=%s | thread_id=%s",
            user_id,
            thread_id,
        )
        return []

    values = getattr(snapshot, "values", None) or {}
    raw_messages = values.get("messages", []) or []

    items: list[dict[str, Any]] = []
    current_turn: dict[str, Any] | None = None
    pending_tools: dict[str, dict[str, Any]] = {}

    def flush_turn() -> None:
        nonlocal current_turn, pending_tools

        if not current_turn:
            return

        user_message = current_turn.get("user")
        assistant_message = current_turn.get("assistant")

        if user_message:
            items.append(user_message)

        if assistant_message and (
            assistant_message.get("content")
            or assistant_message.get("tools")
            or assistant_message.get("artifactPreview")
        ):
            items.append(assistant_message)

        current_turn = None
        pending_tools = {}

    for raw_message in raw_messages:
        msg_type = _message_type(raw_message)
        text = _message_to_text(raw_message).strip()

        if msg_type in {"human", "user"}:
            flush_turn()
            current_turn = {
                "user": {
                    "id": uuid4().hex,
                    "role": "user",
                    "content": text,
                    "createdAt": None,
                },
                "assistant": {
                    "id": uuid4().hex,
                    "role": "assistant",
                    "content": "",
                    "createdAt": None,
                    "tools": [],
                    "artifactPreview": None,
                },
            }
            continue

        if current_turn is None:
            continue

        if msg_type == "ai":
            tool_calls = getattr(raw_message, "tool_calls", None) or []

            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue

                tool_call_id = tool_call.get("id") or tool_call.get("tool_call_id") or uuid4().hex
                trace = {
                    "tool_call_id": tool_call_id,
                    "name": tool_call.get("name") or "tool",
                    "status": "done",
                    "input": tool_call.get("args") or {},
                    "result_preview": "",
                }
                current_turn["assistant"]["tools"].append(trace)
                pending_tools[tool_call_id] = trace

            if text:
                current_turn["assistant"]["content"] = text

        elif msg_type == "tool":
            tool_call_id = getattr(raw_message, "tool_call_id", None) or uuid4().hex
            trace = pending_tools.get(tool_call_id)

            if trace is None:
                trace = {
                    "tool_call_id": tool_call_id,
                    "name": getattr(raw_message, "name", None) or "tool",
                    "status": "done",
                    "input": {},
                    "result_preview": "",
                }
                current_turn["assistant"]["tools"].append(trace)
                pending_tools[tool_call_id] = trace

            trace["result_preview"] = _truncate(text, 800)

            report_id = _extract_saved_report_id(text)
            if report_id:
                report = load_saved_report(store, user_id, report_id)
                if report:
                    current_turn["assistant"]["artifactPreview"] = _build_artifact_preview(report)

    flush_turn()
    return items


def create_app() -> Flask:
    configure_logging()
    agent = build_agent()
    store = get_store()

    app = Flask(__name__)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.post("/api/chat")
    def chat():
        payload = request.get_json(silent=True) or {}

        message = str(payload.get("message", "")).strip()
        user_id = str(payload.get("user_id") or "local_user").strip()
        thread_id = str(payload.get("thread_id") or uuid4().hex).strip()
        user_created_at = payload.get("created_at_utc") or _now_iso()

        if not message:
            return {"error": "message is required"}, 400

        logger.info(
            "api_chat_started | %s",
            json.dumps(
                {
                    "user_id": user_id,
                    "thread_id": thread_id,
                    "message_preview": _truncate(message, 300),
                },
                ensure_ascii=False,
            ),
        )

        result = agent.invoke(
            {"messages": [{"role": "user", "content": message}]},
            {"configurable": {"thread_id": thread_id}},
            context=IncidentAgentContext(user_id=user_id, thread_id=thread_id),
        )

        all_messages: list[BaseMessage] = result["messages"]
        turn_messages = _slice_current_turn(all_messages)

        assistant_text = ""
        if turn_messages:
            assistant_text = _message_to_text(turn_messages[-1]).strip()

        tool_trace = _extract_tool_trace(turn_messages)

        recent_report_ids = result.get("recent_report_ids", []) or []
        if recent_report_ids:
            THREAD_REPORT_INDEX[(user_id, thread_id)] = recent_report_ids

        current_report_id = result.get("current_report_id")
        artifact_preview = None

        if current_report_id:
            last_sent = LAST_SENT_REPORT_ID.get((user_id, thread_id))
            if current_report_id != last_sent:
                saved_report = load_saved_report(store, user_id, current_report_id)
                if saved_report:
                    artifact_preview = _build_artifact_preview(saved_report)
                    LAST_SENT_REPORT_ID[(user_id, thread_id)] = current_report_id

        assistant_created_at = _now_iso()

        ui_user_message = {
            "id": uuid4().hex,
            "role": "user",
            "content": message,
            "createdAt": user_created_at,
        }

        ui_assistant_message = {
            "id": uuid4().hex,
            "role": "assistant",
            "content": assistant_text or "(empty assistant message)",
            "createdAt": assistant_created_at,
            "tools": tool_trace,
            "artifactPreview": artifact_preview,
        }

        history_key = (user_id, thread_id)
        history_items = THREAD_UI_HISTORY.get(history_key, [])
        history_items.extend([ui_user_message, ui_assistant_message])
        THREAD_UI_HISTORY[history_key] = history_items

        response = {
            "user_id": user_id,
            "thread_id": thread_id,
            "assistant_message": ui_assistant_message["content"],
            "assistant_created_at_utc": assistant_created_at,
            "tool_trace": tool_trace,
            "artifact_preview": artifact_preview,
            "recent_artifacts": _load_recent_artifacts(store, user_id, thread_id),
        }

        logger.info(
            "api_chat_completed | %s",
            json.dumps(
                {
                    "user_id": user_id,
                    "thread_id": thread_id,
                    "tool_calls": len(tool_trace),
                    "current_report_id": current_report_id,
                },
                ensure_ascii=False,
            ),
        )

        return response

    @app.get("/api/threads/<thread_id>/messages")
    def list_thread_messages(thread_id: str):
        user_id = str(request.args.get("user_id") or "local_user").strip()
        history_key = (user_id, thread_id)

        items = THREAD_UI_HISTORY.get(history_key)
        if items is None:
            items = _reconstruct_ui_messages(agent, store, user_id, thread_id)
            THREAD_UI_HISTORY[history_key] = items

        return {"items": items}

    @app.get("/api/threads/<thread_id>/artifacts")
    def list_thread_artifacts(thread_id: str):
        user_id = str(request.args.get("user_id") or "local_user").strip()
        return {"items": _load_recent_artifacts(store, user_id, thread_id)}

    @app.get("/api/artifacts/<report_id>")
    def get_artifact(report_id: str):
        user_id = str(request.args.get("user_id") or "local_user").strip()

        report = load_saved_report(store, user_id, report_id)
        if not report:
            return {"error": "artifact not found"}, 404

        return report

    frontend_dist = os.getenv("FRONTEND_DIST")
    if frontend_dist:
        dist_path = Path(frontend_dist).resolve()

        @app.route("/", defaults={"path": ""})
        @app.route("/<path:path>")
        def serve_frontend(path: str):
            if not dist_path.exists():
                return {"error": f"FRONTEND_DIST does not exist: {dist_path}"}, 500

            file_path = dist_path / path
            if path and file_path.exists() and file_path.is_file():
                return send_from_directory(dist_path, path)

            index_path = dist_path / "index.html"
            if index_path.exists():
                return send_from_directory(dist_path, "index.html")

            return {"error": "frontend build not found"}, 404

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)