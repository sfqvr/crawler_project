from __future__ import annotations

import json
import logging
import os
from typing import Any
from uuid import uuid4

from langchain_core.messages import BaseMessage

from .agent import build_agent, configure_logging, get_store
from .schemas import IncidentAgentContext
from .tools import load_saved_report

logger = logging.getLogger("incident_agent.runner")


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


def main() -> None:
    configure_logging()
    agent = build_agent()
    store = get_store()

    user_id = os.getenv("INCIDENT_AGENT_USER_ID", "local_user")
    thread_id = os.getenv("INCIDENT_AGENT_THREAD_ID") or uuid4().hex

    last_seen_report_id: str | None = None

    print("Incident agent started.")
    print(f"user_id={user_id}")
    print(f"thread_id={thread_id}")
    print("Type 'exit' to quit.\n")

    while True:
        user_input = input("You> ").strip()
        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            break

        logger.info(
            "turn_started | %s",
            json.dumps(
                {
                    "user_id": user_id,
                    "thread_id": thread_id,
                    "user_input": user_input,
                },
                ensure_ascii=False,
            ),
        )

        result = agent.invoke(
            {"messages": [{"role": "user", "content": user_input}]},
            {"configurable": {"thread_id": thread_id}},
            context=IncidentAgentContext(user_id=user_id, thread_id=thread_id),
        )

        returned_messages = result["messages"]
        assistant_message = returned_messages[-1]
        assistant_text = _message_to_text(assistant_message).strip()

        current_report_id = result.get("current_report_id")
        saved_report = None

        if current_report_id and current_report_id != last_seen_report_id:
            saved_report = load_saved_report(store, user_id, current_report_id)
            last_seen_report_id = current_report_id

        logger.info(
            "turn_completed | %s",
            json.dumps(
                {
                    "user_id": user_id,
                    "thread_id": thread_id,
                    "assistant_text_preview": assistant_text[:300],
                    "current_report_id": current_report_id,
                    "recent_report_ids": result.get("recent_report_ids", []),
                },
                ensure_ascii=False,
            ),
        )

        if saved_report:
            print("\n[Saved report artifact]")
            print(json.dumps(saved_report, ensure_ascii=False, indent=2))

        print("\nAssistant>")
        print(assistant_text or "(empty assistant message)")
        print()


if __name__ == "__main__":
    main()