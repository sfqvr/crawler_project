from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from dotenv import load_dotenv
from openai import OpenAI
from qdrant_client import QdrantClient, models


load_dotenv()


# =============================================================================
# DATA MODELS
# =============================================================================
@dataclass
class KeywordFilterSpec:
    field: str
    values: list[str]


@dataclass
class TextFilterSpec:
    field: str
    text: str
    any_mode: bool = False


@dataclass
class SearchHit:
    point_id: str
    payload: dict[str, Any]
    score: Optional[float] = None

    @property
    def url(self) -> Optional[str]:
        value = self.payload.get("url")
        return str(value) if value is not None else None

    @property
    def company(self) -> Optional[str]:
        value = self.payload.get("company")
        return str(value) if value is not None else None

    @property
    def document_kind(self) -> Optional[str]:
        value = self.payload.get("document_kind")
        return str(value) if value is not None else None

    @property
    def short_description(self) -> Optional[str]:
        value = self.payload.get("short_description")
        return str(value) if value is not None else None


@dataclass
class SearchResponse:
    mode: str
    hits: list[SearchHit] = field(default_factory=list)
    filter_applied: bool = False
    limit: int = 10
    query_text: Optional[str] = None


# =============================================================================
# API
# =============================================================================
class QdrantSearchAPI:
    """
    Universal search API for your incident documents collection.

    Supports:
    - semantic search
    - semantic search + payload filters
    - keyword-only payload search
    - text-only payload search
    - general hybrid-style search(query_text + filters)

    Environment variables:
    - OPENAI_BASE_URL
    - OPENAI_API_KEY
    - OPENAI_EMBEDDING_MODEL (fallback: OPENAI_MODEL)
    - QDRANT_COLLECTION
    - QDRANT_URL
    - QDRANT_API_KEY
    - QDRANT_LOCAL_MODE
    - QDRANT_LOCAL_PATH
    """

    def __init__(
        self,
        *,
        collection_name: Optional[str] = None,
        embedding_model: Optional[str] = None,
        openai_base_url: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        qdrant_url: Optional[str] = None,
        qdrant_api_key: Optional[str] = None,
        qdrant_local_mode: Optional[bool] = None,
        qdrant_local_path: Optional[str] = None,
        debug: bool = False,
    ) -> None:
        self.debug = debug

        self.collection_name = collection_name or os.getenv("QDRANT_COLLECTION", "incident_documents")
        self.embedding_model = embedding_model or os.getenv(
            "OPENAI_EMBEDDING_MODEL",
            os.getenv("OPENAI_MODEL", "text-embedding-3-small"),
        )

        self.openai_base_url = openai_base_url or os.getenv("OPENAI_BASE_URL")
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY")

        self.qdrant_url = qdrant_url or os.getenv("QDRANT_URL")
        self.qdrant_api_key = qdrant_api_key or os.getenv("QDRANT_API_KEY")

        if qdrant_local_mode is None:
            self.qdrant_local_mode = os.getenv("QDRANT_LOCAL_MODE", "false").lower() == "true"
        else:
            self.qdrant_local_mode = qdrant_local_mode

        self.qdrant_local_path = qdrant_local_path or os.getenv("QDRANT_LOCAL_PATH", "").strip()

        self._openai_client: Optional[OpenAI] = None
        self._qdrant_client: Optional[QdrantClient] = None

    # -------------------------------------------------------------------------
    # constructors / clients
    # -------------------------------------------------------------------------
    @classmethod
    def from_env(cls, *, debug: bool = False) -> "QdrantSearchAPI":
        return cls(debug=debug)

    def _debug(self, msg: str) -> None:
        if self.debug:
            print(msg)

    def _require(self, name: str, value: Optional[str]) -> str:
        if value is None or not value.strip():
            raise ValueError(f"Environment variable {name} is required.")
        return value.strip()

    @property
    def openai_client(self) -> OpenAI:
        if self._openai_client is None:
            base_url = self._require("OPENAI_BASE_URL", self.openai_base_url)
            api_key = self._require("OPENAI_API_KEY", self.openai_api_key)
            self._openai_client = OpenAI(api_key=api_key, base_url=base_url)
        return self._openai_client

    @property
    def qdrant_client(self) -> QdrantClient:
        if self._qdrant_client is None:
            if self.qdrant_local_mode:
                local_path = self.qdrant_local_path or ":memory:"
                if local_path == ":memory:":
                    self._debug("[INFO] Using Qdrant local mode in memory")
                    self._qdrant_client = QdrantClient(location=":memory:")
                else:
                    self._debug(f"[INFO] Using Qdrant local mode on disk: {local_path}")
                    self._qdrant_client = QdrantClient(path=local_path)
            else:
                url = self._require("QDRANT_URL", self.qdrant_url)
                self._debug(f"[INFO] Using Qdrant server mode: {url}")
                if self.qdrant_api_key:
                    self._qdrant_client = QdrantClient(url=url, api_key=self.qdrant_api_key)
                else:
                    self._qdrant_client = QdrantClient(url=url)

        return self._qdrant_client

    # -------------------------------------------------------------------------
    # embeddings
    # -------------------------------------------------------------------------
    def embed_query(self, text: str) -> list[float]:
        text = text.strip()
        if not text:
            raise ValueError("Query text must not be empty.")

        response = self.openai_client.embeddings.create(
            model=self.embedding_model,
            input=text,
        )
        return response.data[0].embedding

    # -------------------------------------------------------------------------
    # filter builders
    # -------------------------------------------------------------------------
    @staticmethod
    def keyword_filter(field: str, values: Sequence[str]) -> KeywordFilterSpec:
        cleaned = [str(v).strip() for v in values if str(v).strip()]
        return KeywordFilterSpec(field=field, values=cleaned)

    @staticmethod
    def _normalize_text_filter_value(field: str, text: str) -> str:
        normalized = text.strip()
        if field in {"tech_stack_text", "key_terms_text"}:
            normalized = normalized.lower()
        return normalized

    @staticmethod
    def text_filter(field: str, text: str, *, any_mode: bool = False) -> TextFilterSpec:
        normalized_text = QdrantSearchAPI._normalize_text_filter_value(field, text)
        return TextFilterSpec(field=field, text=normalized_text, any_mode=any_mode)

    def _build_keyword_condition(self, spec: KeywordFilterSpec) -> Optional[models.FieldCondition]:
        if not spec.values:
            return None

        return models.FieldCondition(
            key=spec.field,
            match=models.MatchAny(any=spec.values),
        )

    def _build_text_condition(self, spec: TextFilterSpec) -> Optional[models.FieldCondition]:
        if not spec.text:
            return None

        if spec.any_mode:
            match_obj = models.MatchTextAny(text_any=spec.text)
        else:
            match_obj = models.MatchText(text=spec.text)

        return models.FieldCondition(
            key=spec.field,
            match=match_obj,
        )

    def build_filter(
        self,
        *,
        keyword_filters: Optional[Sequence[KeywordFilterSpec]] = None,
        text_filters: Optional[Sequence[TextFilterSpec]] = None,
    ) -> Optional[models.Filter]:
        conditions: list[models.Condition] = []

        for spec in keyword_filters or []:
            condition = self._build_keyword_condition(spec)
            if condition is not None:
                conditions.append(condition)

        for spec in text_filters or []:
            condition = self._build_text_condition(spec)
            if condition is not None:
                conditions.append(condition)

        if not conditions:
            return None

        return models.Filter(must=conditions)

    # -------------------------------------------------------------------------
    # result formatting
    # -------------------------------------------------------------------------
    def _point_to_hit(self, point: Any) -> SearchHit:
        point_id = getattr(point, "id", None)
        payload = getattr(point, "payload", {}) or {}
        score = getattr(point, "score", None)

        return SearchHit(
            point_id=str(point_id),
            payload=dict(payload),
            score=score,
        )

    def _extract_query_points_hits(self, response: Any) -> list[SearchHit]:
        if response is None:
            return []

        points = getattr(response, "points", None)
        if points is None and isinstance(response, list):
            points = response
        if points is None:
            return []

        return [self._point_to_hit(point) for point in points]

    def _extract_scroll_hits(self, response: Any) -> list[SearchHit]:
        if response is None:
            return []

        if isinstance(response, tuple) and len(response) >= 1:
            points = response[0]
        elif isinstance(response, list):
            points = response
        else:
            points = []

        return [self._point_to_hit(point) for point in points]

    def hit_to_brief_dict(self, hit: SearchHit) -> dict[str, Any]:
        payload = hit.payload
        return {
            "point_id": hit.point_id,
            "url": payload.get("url"),
            "name": payload.get("name"),
            "company": payload.get("company"),
            "date": payload.get("date"),
            "document_kind": payload.get("document_kind"),
            "short_description": payload.get("short_description"),
            "incident_categories": payload.get("incident_categories", []),
            "infrastructure": payload.get("infrastructure", []),
            "tech_stack": payload.get("tech_stack", []),
            "score": hit.score,
        }

    # -------------------------------------------------------------------------
    # search methods
    # -------------------------------------------------------------------------
    def semantic_search(
        self,
        query_text: str,
        *,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        keyword_filters: Optional[Sequence[KeywordFilterSpec]] = None,
        text_filters: Optional[Sequence[TextFilterSpec]] = None,
    ) -> SearchResponse:
        vector = self.embed_query(query_text)
        query_filter = self.build_filter(
            keyword_filters=keyword_filters,
            text_filters=text_filters,
        )

        response = self.qdrant_client.query_points(
            collection_name=self.collection_name,
            query=vector,
            query_filter=query_filter,
            limit=limit,
            score_threshold=score_threshold,
            with_payload=True,
            with_vectors=False,
        )

        hits = self._extract_query_points_hits(response)
        return SearchResponse(
            mode="semantic",
            hits=hits,
            filter_applied=query_filter is not None,
            limit=limit,
            query_text=query_text,
        )

    def keyword_search(
        self,
        *,
        keyword_filters: Sequence[KeywordFilterSpec],
        limit: int = 10,
    ) -> SearchResponse:
        query_filter = self.build_filter(keyword_filters=keyword_filters)

        response = self.qdrant_client.scroll(
            collection_name=self.collection_name,
            scroll_filter=query_filter,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )

        hits = self._extract_scroll_hits(response)
        return SearchResponse(
            mode="keyword",
            hits=hits,
            filter_applied=query_filter is not None,
            limit=limit,
            query_text=None,
        )

    def text_search(
        self,
        *,
        text_filters: Sequence[TextFilterSpec],
        limit: int = 10,
    ) -> SearchResponse:
        query_filter = self.build_filter(text_filters=text_filters)

        response = self.qdrant_client.scroll(
            collection_name=self.collection_name,
            scroll_filter=query_filter,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )

        hits = self._extract_scroll_hits(response)
        return SearchResponse(
            mode="text",
            hits=hits,
            filter_applied=query_filter is not None,
            limit=limit,
            query_text=None,
        )

    def hybrid_search(
        self,
        query_text: str,
        *,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        keyword_filters: Optional[Sequence[KeywordFilterSpec]] = None,
        text_filters: Optional[Sequence[TextFilterSpec]] = None,
    ) -> SearchResponse:
        return self.semantic_search(
            query_text=query_text,
            limit=limit,
            score_threshold=score_threshold,
            keyword_filters=keyword_filters,
            text_filters=text_filters,
        )

    def search(
        self,
        *,
        query_text: Optional[str] = None,
        limit: int = 10,
        score_threshold: Optional[float] = None,
        keyword_filters: Optional[Sequence[KeywordFilterSpec]] = None,
        text_filters: Optional[Sequence[TextFilterSpec]] = None,
    ) -> SearchResponse:
        """
        Universal entry point.

        Behavior:
        - if query_text is provided -> semantic/hybrid search
        - otherwise -> filter-only search via scroll
        """
        has_query = bool(query_text and query_text.strip())
        has_keyword_filters = bool(keyword_filters)
        has_text_filters = bool(text_filters)

        if has_query:
            return self.semantic_search(
                query_text=query_text.strip(),
                limit=limit,
                score_threshold=score_threshold,
                keyword_filters=keyword_filters,
                text_filters=text_filters,
            )

        if has_keyword_filters and has_text_filters:
            query_filter = self.build_filter(
                keyword_filters=keyword_filters,
                text_filters=text_filters,
            )
            response = self.qdrant_client.scroll(
                collection_name=self.collection_name,
                scroll_filter=query_filter,
                limit=limit,
                with_payload=True,
                with_vectors=False,
            )
            hits = self._extract_scroll_hits(response)
            return SearchResponse(
                mode="filter_only",
                hits=hits,
                filter_applied=True,
                limit=limit,
            )

        if has_keyword_filters:
            return self.keyword_search(
                keyword_filters=keyword_filters,
                limit=limit,
            )

        if has_text_filters:
            return self.text_search(
                text_filters=text_filters,
                limit=limit,
            )

        response = self.qdrant_client.scroll(
            collection_name=self.collection_name,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        hits = self._extract_scroll_hits(response)
        return SearchResponse(
            mode="browse",
            hits=hits,
            filter_applied=False,
            limit=limit,
        )

    # -------------------------------------------------------------------------
    # convenience methods
    # -------------------------------------------------------------------------
    def get_by_point_id(self, point_id: str) -> Optional[SearchHit]:
        point_id = str(point_id).strip()
        if not point_id:
            return None

        response = self.qdrant_client.retrieve(
            collection_name=self.collection_name,
            ids=[point_id],
            with_payload=True,
            with_vectors=False,
        )

        if not response:
            return None

        return self._point_to_hit(response[0])

    def get_by_url(self, url: str) -> Optional[SearchHit]:
        url = url.strip()
        if not url:
            return None

        result = self.keyword_search(
            keyword_filters=[self.keyword_filter("url", [url])],
            limit=1,
        )
        return result.hits[0] if result.hits else None

    def get_by_company(self, company: str, *, limit: int = 10) -> SearchResponse:
        return self.keyword_search(
            keyword_filters=[self.keyword_filter("company", [company])],
            limit=limit,
        )

    def get_postmortems(
        self,
        *,
        query_text: Optional[str] = None,
        limit: int = 10,
    ) -> SearchResponse:
        return self.search(
            query_text=query_text,
            limit=limit,
            keyword_filters=[self.keyword_filter("document_kind", ["postmortem"])],
        )

    def search_database_incidents(
        self,
        query_text: str,
        *,
        limit: int = 10,
    ) -> SearchResponse:
        return self.hybrid_search(
            query_text=query_text,
            limit=limit,
            keyword_filters=[self.keyword_filter("incident_categories", ["Database"])],
        )

    def search_network_incidents(
        self,
        query_text: str,
        *,
        limit: int = 10,
    ) -> SearchResponse:
        return self.hybrid_search(
            query_text=query_text,
            limit=limit,
            keyword_filters=[self.keyword_filter("incident_categories", ["Network"])],
        )


# =============================================================================
# OPTIONAL DEMO
# =============================================================================
if __name__ == "__main__":
    api = QdrantSearchAPI.from_env(debug=True)

    result = api.hybrid_search(
        query_text="postgres xid wraparound outage",
        limit=5,
        keyword_filters=[
            api.keyword_filter("incident_categories", ["Database"]),
        ],
        text_filters=[
            api.text_filter("tech_stack_text", "postgres", any_mode=False),
        ],
    )

    print(f"mode={result.mode}, hits={len(result.hits)}")
    for idx, hit in enumerate(result.hits, start=1):
        print("-" * 80)
        print(f"[{idx}] score={hit.score}")
        print(f"url={hit.url}")
        print(f"company={hit.company}")
        print(f"kind={hit.document_kind}")
        print(f"short_description={hit.short_description}")