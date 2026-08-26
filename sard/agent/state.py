"""Typed state contract for the Sard agent LangGraph pipeline.

The pipeline runs exactly one user request through
``understand -> plan -> retrieve -> compose -> verify -> render``.  All
cross-node data flows through :class:`GraphState`; nodes return partial dicts
that LangGraph merges, applying reducers on append-only fields so the graph
finishes as a typed state rather than crashing.

This module carries only types and the initial-state factory.  Providers are
never imported here, keeping the state provider-independent.
"""

from __future__ import annotations

import operator
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Optional, TypedDict

from sard.outputs.schemas import Itinerary


def merge_mapping(existing: Optional[dict], update: dict) -> dict:
    """Reducer that merges scalar-valued dicts, latest wins per key."""
    out = dict(existing or {})
    out.update(update or {})
    return out


class RAGMode(str, Enum):
    """Normalized retrieval mode, mapped from Step 3's modes."""

    HYBRID_RERANKED = "hybrid_reranked"
    HYBRID_FUSED = "hybrid_fused"
    DENSE_ONLY = "dense_only"
    FULL_TEXT_ONLY = "full_text_only"
    UNAVAILABLE = "unavailable"


class ClaimStatus(str, Enum):
    """Atomic-claim verdicts produced by the verification step."""

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"
    NON_FACTUAL = "non_factual"
    USER_PROVIDED = "user_provided"
    EXPLICITLY_UNCERTAIN = "explicitly_uncertain"


class GraphOutcome(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True)
class PlanTimeBlock:
    period: str
    activity_type: str


@dataclass(frozen=True)
class PlanDay:
    day_index: int
    focus: str
    time_blocks: tuple[PlanTimeBlock, ...] = ()


@dataclass(frozen=True)
class ItineraryPlan:
    """Provisional plan: activity *types*, evidence topics, open questions.

    Deliberately contains no unverified concrete facts — only shapes,
    topics and constraints the retrieval/compose steps may bind to evidence.
    """

    focus_summary: str
    days: tuple[PlanDay, ...] = ()
    activity_types: tuple[str, ...] = ()
    evidence_topics: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    provisional: bool = True


@dataclass(frozen=True)
class EvidenceItem:
    """One retrieved chunk adapted from a Step 3 ``RAGAnswer``."""

    citation_id: str
    chunk_id: str
    content: str
    title: str
    source_name: str
    source_url: str
    mode: str
    model_used: Optional[str] = None
    fallback_used: Optional[str] = None
    timing_ms: Optional[float] = None
    dense_score: Optional[float] = None
    fts_score: Optional[float] = None
    fused_score: Optional[float] = None
    rerank_score: Optional[float] = None
    dense_rank: Optional[int] = None
    fts_rank: Optional[int] = None
    fused_rank: Optional[int] = None
    rerank_rank: Optional[int] = None
    page_number: Optional[int] = None
    language: Optional[str] = None
    publication_date: Optional[str] = None
    section_heading: Optional[str] = None


@dataclass(frozen=True)
class ClaimRecord:
    claim_id: str
    text: str
    citation_ids: tuple[str, ...] = ()
    supporting_chunk_ids: tuple[str, ...] = ()
    status: ClaimStatus = ClaimStatus.UNSUPPORTED
    explanation: str = ""
    correction: str = ""


@dataclass(frozen=True)
class CoverageReport:
    total_claims: int
    external_claims: int
    covered_claims: int
    coverage_ratio: float
    uncovered_claim_ids: tuple[str, ...] = ()
    model_used: Optional[str] = None
    note: str = ""


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    verified_claim_ids: tuple[str, ...]
    unsupported_claim_ids: tuple[str, ...]
    feedback: str
    model_used: Optional[str] = None


@dataclass(frozen=True)
class VerificationRound:
    round_index: int
    passed: bool
    verified_claim_ids: tuple[str, ...]
    unsupported_claim_ids: tuple[str, ...]


@dataclass(frozen=True)
class RenderedArtifactInfo:
    filename: str
    path: str
    mime_type: str
    size_bytes: int
    warnings: tuple[str, ...] = ()
    artifact_type: str = ""
    display_label: str = ""
    absolute_path: str = ""
    checksum: Optional[str] = None
    creation_status: str = "created"
    error_category: Optional[str] = None


@dataclass(frozen=True)
class MultimodalItem:
    filename: str
    file_type: str  # image, audio, video, document, 3d, nifti
    extracted_text: str = ""
    description: str = ""
    source_path: Optional[str] = None
    extraction_method: str = "core"
    metadata: dict = field(default_factory=dict)


class GraphState(TypedDict, total=False):
    """Full pipeline state. Append-only fields use LangGraph reducers."""

    run_id: str
    original_request: str
    normalized_request: str
    request_language: str
    multimodal_inputs: list[MultimodalItem]

    intent: str
    destination: Optional[str]
    duration_days: Optional[int]
    travel_dates: list[str]
    audience: list[str]
    interests: list[str]
    timing: Optional[str]
    timing_constraints: list[str]
    accessibility_needs: list[str]
    budget: Optional[str]
    user_facts: list[str]
    missing_constraints: list[str]
    assumptions: list[str]
    understanding_degraded: bool

    plan: Optional[ItineraryPlan]

    retrieval_queries: Annotated[list[str], operator.add]
    retrieval_filters: dict
    evidence: list[EvidenceItem]
    retrieval_mode: str
    reranking_used: Optional[str]
    retrieval_warnings: list[str]

    draft: Optional[str]
    itinerary: Optional[Itinerary]
    sources: list

    atomic_claims: list[ClaimRecord]
    claim_citation_mapping: dict
    unsupported_claims: list[str]
    coverage: Optional[CoverageReport]
    verification_result: Optional[VerificationResult]
    verification_feedback: Annotated[list[str], operator.add]
    verification_history: Annotated[list[VerificationRound], operator.add]
    verification_exhausted: bool
    compose_retry_count: int
    compose_max_retries: int

    final_answer: Optional[str]

    output_root: Optional[str]
    render_checksums: bool
    caller_dates: list[str]
    preview_calendar: bool
    model_fallback_used: bool

    rendered_artifacts: Annotated[list[RenderedArtifactInfo], operator.add]

    model_routes: Annotated[dict, merge_mapping]
    timings: Annotated[dict, merge_mapping]
    fallback_events: Annotated[list, operator.add]
    errors: Annotated[list, operator.add]
    warnings: Annotated[list[str], operator.add]
    progress_events: Annotated[list, operator.add]
    node_failures: Annotated[list[str], operator.add]
    graph_outcome: str


def initial_state(
    request: str,
    run_id: Optional[str] = None,
    compose_max_retries: int = 2,
) -> dict:
    """Seed a fresh, fully defaulted state for a single run."""
    return {
        "run_id": run_id or f"run-{uuid.uuid4().hex[:12]}",
        "original_request": request,
        "normalized_request": request,
        "request_language": "ar",
        "multimodal_inputs": [],
        "intent": "travel_planning",
        "destination": None,
        "duration_days": None,
        "travel_dates": [],
        "audience": [],
        "interests": [],
        "timing": None,
        "timing_constraints": [],
        "accessibility_needs": [],
        "budget": None,
        "user_facts": [],
        "missing_constraints": [],
        "assumptions": [],
        "understanding_degraded": False,
        "plan": None,
        "retrieval_queries": [],
        "retrieval_filters": {},
        "evidence": [],
        "retrieval_mode": RAGMode.UNAVAILABLE.value,
        "reranking_used": None,
        "retrieval_warnings": [],
        "draft": None,
        "itinerary": None,
        "sources": [],
        "atomic_claims": [],
        "claim_citation_mapping": {},
        "unsupported_claims": [],
        "coverage": None,
        "verification_result": None,
        "verification_feedback": [],
        "verification_history": [],
        "verification_exhausted": False,
        "compose_retry_count": 0,
        "compose_max_retries": compose_max_retries,
        "final_answer": None,
        "output_root": None,
        "render_checksums": False,
        "caller_dates": [],
        "preview_calendar": False,
        "model_fallback_used": False,
        "rendered_artifacts": [],
        "model_routes": {},
        "timings": {},
        "fallback_events": [],
        "errors": [],
        "warnings": [],
        "progress_events": [],
        "node_failures": [],
        "graph_outcome": GraphOutcome.COMPLETED.value,
    }
