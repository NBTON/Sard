"""Typed data contracts shared across the RAG pipeline.

Every RAG module (ingestion, retrieval, reranking, answer generation)
communicates through these dataclasses instead of raw dicts, so the
LangGraph ``retrieve`` node planned for a later step can consume the same
types without adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# --------------------------------------------------------------------------
# Ingestion-time document/chunk model
# --------------------------------------------------------------------------


class SourceFileType(str, Enum):
    PDF = "pdf"
    HTML = "html"
    MARKDOWN = "markdown"
    TEXT = "text"


@dataclass
class DocumentMetadata:
    """Required, verifiable provenance for one ingested source document."""

    source_name: str
    source_url: str
    title: str
    topic: str
    document_id: str
    language: str = "ar"
    publication_date: Optional[str] = None
    file_type: SourceFileType = SourceFileType.TEXT
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedDocument:
    """A single source document after parsing + normalization, pre-chunking."""

    document_id: str
    metadata: DocumentMetadata
    original_text: str
    normalized_text: str
    document_hash: str
    sections: list["ParsedSection"] = field(default_factory=list)


@dataclass
class ParsedSection:
    """An optional structural unit (heading + body) inside a document."""

    heading: Optional[str]
    text: str
    page_number: Optional[int] = None


@dataclass
class Chunk:
    """A retrieval unit with full citation metadata, ready to embed."""

    chunk_id: str
    document_id: str
    citation_id: str
    content: str
    content_hash: str
    title: str
    source_name: str
    source_url: str
    topic: str
    language: str
    publication_date: Optional[str]
    schema_version: str
    ingestion_version: str
    page_number: Optional[int] = None
    section_heading: Optional[str] = None
    extraction_method: str = "text"  # "text" or "vlm"
    vlm_model: Optional[str] = None
    vlm_confidence: Optional[float] = None
    vlm_needs_review: bool = False
    extra_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EmbeddedChunk:
    """A chunk plus its resolved dense embedding, ready for Zvec insertion."""

    chunk: Chunk
    dense_embedding: list[float]
    embedding_model: str
    embedding_dimension: int


# --------------------------------------------------------------------------
# Retrieval-time model
# --------------------------------------------------------------------------


@dataclass
class RewrittenQuery:
    """Structured output of the query-rewrite step."""

    original_question: str
    normalized_question: str
    search_variants: list[str]
    entities: list[str] = field(default_factory=list)
    topic_filter: Optional[str] = None
    exact_phrases: list[str] = field(default_factory=list)
    rewrite_succeeded: bool = False
    model_used: Optional[str] = None


@dataclass
class RetrievalFilters:
    """User- or system-supplied metadata filters for retrieval."""

    topic: Optional[str] = None
    source_name: Optional[str] = None
    language: Optional[str] = None
    publication_date: Optional[str] = None


class ScoreType(str, Enum):
    """Explicit score type classification to prevent uncalibrated cross-scale comparisons."""

    DENSE = "dense"
    FTS = "fts"
    RRF = "rrf"
    RERANK = "rerank"
    CALIBRATED_CONFIDENCE = "calibrated_confidence"
    LEXICAL = "lexical"
    WEB = "web"


@dataclass
class RetrievedCandidate:
    """One candidate chunk plus per-channel scoring/rank bookkeeping."""

    chunk_id: str
    document_id: str
    citation_id: str
    content: str
    title: str
    source_name: str
    source_url: str
    topic: str
    language: str
    publication_date: Optional[str]
    page_number: Optional[int]
    content_hash: str = ""
    dense_score: Optional[float] = None
    dense_rank: Optional[int] = None
    fts_score: Optional[float] = None
    fts_rank: Optional[int] = None
    fused_score: Optional[float] = None
    fused_rank: Optional[int] = None
    rerank_score: Optional[float] = None
    rerank_rank: Optional[int] = None
    confidence_score: Optional[float] = None
    score_type: Optional[str] = None
    is_relevant: bool = True
    region: Optional[str] = None
    extra_metadata: dict[str, Any] = field(default_factory=dict)


class RetrievalMode(str, Enum):
    """The retrieval channel(s) actually used to answer a request."""

    HYBRID = "hybrid"  # dense + fts fused
    DENSE_ONLY = "dense_only"
    FTS_ONLY_EMERGENCY = "fts_only_emergency"
    UNAVAILABLE = "unavailable"


@dataclass
class RetrievalResult:
    """Everything the answer step (and evaluation) needs about retrieval."""

    query: str
    rewritten: Optional[RewrittenQuery]
    dense_candidates: list[RetrievedCandidate]
    fts_candidates: list[RetrievedCandidate]
    fused_candidates: list[RetrievedCandidate]
    reranked_candidates: list[RetrievedCandidate]
    mode: RetrievalMode
    reranker_used: str  # "nvidia" | "rrf_fallback" | "dense_fallback" | "fts_fallback"
    fallback_events: list[Any] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    is_relevant: bool = True
    relevance_decision: str = "relevant"
    top_confidence: float = 0.0


# --------------------------------------------------------------------------
# Answer-time model
# --------------------------------------------------------------------------


@dataclass
class Citation:
    citation_id: str
    title: str
    source_name: str
    source_url: str
    chunk_id: str


@dataclass
class AnswerResult:
    question: str
    answer_text: str
    citations: list[Citation]
    generation_mode: str  # "generative" | "extractive_fallback"
    model_used: Optional[str]
    warnings: list[str] = field(default_factory=list)


@dataclass
class RAGAnswer:
    """The full, provider-independent result of ``RAGService.answer(...)``.

    This is the ONLY object the Streamlit UI (or, later, a LangGraph
    ``retrieve``/``answer`` node) should need to render a cited RAG answer
    end to end.
    """

    question: str
    rewritten_queries: list[str]
    dense_candidates: list[RetrievedCandidate]
    fts_candidates: list[RetrievedCandidate]
    fused_candidates: list[RetrievedCandidate]
    selected_context: list[RetrievedCandidate]
    answer_text: str
    citations: list[Citation]
    model_route: dict[str, Optional[str]]
    fallback_events: list[Any]
    retrieval_mode: str
    reranker_used: str
    timings_ms: dict[str, float]
    warnings: list[str] = field(default_factory=list)
