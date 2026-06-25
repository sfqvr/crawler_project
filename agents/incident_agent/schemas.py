from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional

from langchain.agents import AgentState
from langchain.tools import ToolRuntime
from pydantic import BaseModel, ConfigDict, Field, field_serializer
from typing_extensions import NotRequired


IncidentCategory = Literal[
    "Database",
    "Network",
    "Security/DDoS",
    "Hardware",
    "Application",
    "Configuration",
    "Deployment",
    "Capacity",
    "Performance/Latency",
    "Data Integrity/Corruption",
    "Human Error/Operational",
    "Authentication/Authorization",
    "Third-party dependency",
    "Unknown",
]

InfrastructureKind = Literal[
    "Cloud",
    "Bare-metal",
    "Network Provider",
    "CDN",
    "DNS",
    "Kubernetes",
    "Datacenter",
    "Third-party service",
    "Message Broker/Queue",
    "Serverless",
    "Virtualization/Hypervisor",
    "Unknown",
]

DocumentKind = Literal[
    "postmortem",
    "incident_report",
    "status_update",
    "unknown",
]


@dataclass
class IncidentAgentContext:
    user_id: str
    thread_id: str


class IncidentAgentState(AgentState):
    current_report_id: NotRequired[Optional[str]]
    recent_report_ids: NotRequired[list[str]]
    last_report_title: NotRequired[Optional[str]]
    last_search_query: NotRequired[Optional[str]]
    last_search_filters: NotRequired[dict[str, Any]]
    last_shortlist: NotRequired[list[dict[str, Any]]]


class RuntimeInjectedInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    runtime: ToolRuntime

    @field_serializer("runtime")
    def _serialize_runtime(self, value: ToolRuntime | None) -> Any:
        # Не даем Pydantic пытаться сериализовать runtime/context/store
        return None


class SearchIncidentsInput(RuntimeInjectedInput):
    query_text: Optional[str] = Field(
        default=None,
        description="Natural-language semantic query. Prefer using this for the main retrieval signal.",
    )
    document_kinds: list[DocumentKind] = Field(
        default_factory=list,
        description="Optional normalized document kinds to filter by.",
    )
    incident_categories: list[IncidentCategory] = Field(
        default_factory=list,
        description="Optional normalized incident categories. Use canonical enum values only.",
    )
    infrastructure: list[InfrastructureKind] = Field(
        default_factory=list,
        description="Optional normalized infrastructure categories. Use canonical enum values only.",
    )
    company: Optional[str] = Field(
        default=None,
        description="Optional exact company filter.",
    )
    tech_stack_text: Optional[str] = Field(
        default=None,
        description="Optional text refinement for tech_stack_text. Use cautiously because text search may be strict.",
    )
    key_terms_text: Optional[str] = Field(
        default=None,
        description="Optional text refinement for key_terms_text. Use cautiously because text search may be strict.",
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Maximum number of brief hits to return.",
    )


class GetIncidentDetailsInput(BaseModel):
    point_id: Optional[str] = Field(
        default=None,
        description="Qdrant point_id. Prefer this when available.",
    )
    url: Optional[str] = Field(
        default=None,
        description="Document URL. Use when point_id is not available.",
    )
    include_markdown: bool = Field(
        default=False,
        description="Whether to include markdown_content in the result.",
    )
    markdown_char_limit: int = Field(
        default=4000,
        ge=500,
        le=20000,
        description="Maximum number of markdown characters to include if include_markdown=True.",
    )

class GetIncidentMarkdownChunkInput(BaseModel):
    point_id: Optional[str] = Field(
        default=None,
        description="Qdrant point_id. Prefer this when available.",
    )
    url: Optional[str] = Field(
        default=None,
        description="Document URL. Use when point_id is not available.",
    )
    start_char: int = Field(
        default=0,
        ge=0,
        description="Inclusive start offset in markdown_content.",
    )
    end_char: int = Field(
        default=2000,
        ge=1,
        description="Exclusive end offset in markdown_content.",
    )

class SearchIncidentsHydeInput(RuntimeInjectedInput):
    company: Optional[str] = Field(
        default=None,
        description=(
            "Optional company name for the hypothetical incident document. "
            "Used only as part of the semantic pseudo-document, not as an exact payload filter."
        ),
    )
    short_description: Optional[str] = Field(
        default=None,
        description="Short hypothetical incident summary. This is usually the most important field.",
    )
    symptoms: Optional[str] = Field(
        default=None,
        description="Hypothetical symptoms, impact, or observable behavior.",
    )
    root_cause: Optional[str] = Field(
        default=None,
        description=(
            "Hypothetical root cause. Use cautiously. Leave empty if you are not confident."
        ),
    )
    resolution: Optional[str] = Field(
        default=None,
        description=(
            "Hypothetical mitigation or resolution. Use cautiously. Leave empty if unknown."
        ),
    )
    lessons_learned: Optional[str] = Field(
        default=None,
        description=(
            "Hypothetical lessons learned or governance takeaway. Leave empty if unknown."
        ),
    )
    document_kinds: list[DocumentKind] = Field(
        default_factory=list,
        description="Optional normalized document kinds to filter by.",
    )
    incident_categories: list[IncidentCategory] = Field(
        default_factory=list,
        description="Optional normalized incident categories. Use canonical enum values only.",
    )
    infrastructure: list[InfrastructureKind] = Field(
        default_factory=list,
        description="Optional normalized infrastructure categories. Use canonical enum values only.",
    )
    company_filter: Optional[str] = Field(
        default=None,
        description=(
            "Optional exact company filter in payload. "
            "Use this only when you truly want incidents from a specific company."
        ),
    )
    tech_stack_text: Optional[str] = Field(
        default=None,
        description="Optional text refinement for tech_stack_text. Use cautiously because text search may be strict.",
    )
    key_terms_text: Optional[str] = Field(
        default=None,
        description="Optional text refinement for key_terms_text. Use cautiously because text search may be strict.",
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Maximum number of brief hits to return.",
    )

class SimilarIncidentReference(BaseModel):
    point_id: str = Field(description="Qdrant point_id of the similar incident.")
    url: str = Field(description="Source URL of the similar incident.")
    company: Optional[str] = Field(default=None, description="Company name if known.")
    name: Optional[str] = Field(default=None, description="Document title if known.")
    why_relevant: str = Field(description="Why this incident is relevant to the user's question.")


class SaveFinalReportInput(RuntimeInjectedInput):
    title: Optional[str] = Field(
        default=None,
        description="Short title for the saved report. If omitted, derive it from the summary.",
    )
    summary: str = Field(description="Short grounded summary answering the user's request.")
    likely_patterns: list[str] = Field(default_factory=list)
    similar_incidents: list[SimilarIncidentReference] = Field(default_factory=list)
    recommended_checks: list[str] = Field(default_factory=list)
    possible_mitigations: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    references: list[str] = Field(
        default_factory=list,
        description="Usually source URLs or brief source identifiers.",
    )