from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from .prompts import SYSTEM_PROMPT
from .schemas import IncidentAgentContext, IncidentAgentState

from .tools import (
    get_incident_details,
    get_incident_markdown_chunk,
    research_update,
    save_final_report,
    search_incidents,
    search_incidents_hyde,
)

load_dotenv()

LLM_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL")
LLM_API_KEY = os.getenv("OPENAI_API_KEY")
LLM_PROVIDER = "openai"

logger = logging.getLogger("incident_agent.agent")


def configure_logging(
    *,
    level: int = logging.INFO,
    log_dir: str = "logs",
    log_filename: str = "incident_agent.log",
) -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_path = Path(log_dir) / log_filename

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if not root_logger.handlers:
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)

        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(level)
        stream_handler.setFormatter(formatter)

        root_logger.addHandler(file_handler)
        root_logger.addHandler(stream_handler)


@lru_cache(maxsize=1)
def get_checkpointer() -> InMemorySaver:
    return InMemorySaver()


@lru_cache(maxsize=1)
def get_store() -> InMemoryStore:
    return InMemoryStore()


def build_model():
    return init_chat_model(
        model=LLM_MODEL,
        model_provider=LLM_PROVIDER,
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY,
        temperature=0.0,
    )


def build_agent():
    model = build_model()

    agent = create_agent(
        model=model,
        tools=[
            research_update,
            search_incidents,
            search_incidents_hyde,
            get_incident_details,
            get_incident_markdown_chunk,
            save_final_report,
        ],
        system_prompt=SYSTEM_PROMPT,
        state_schema=IncidentAgentState,
        context_schema=IncidentAgentContext,
        checkpointer=get_checkpointer(),
        store=get_store(),
    )

    logger.info(
        "agent_built | model=%s | checkpointer=%s | store=%s",
        LLM_MODEL,
        type(get_checkpointer()).__name__,
        type(get_store()).__name__,
    )
    return agent