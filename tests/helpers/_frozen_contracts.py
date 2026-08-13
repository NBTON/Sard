"""Test-only replica of the frozen Step 7 public UI contracts.

This file is an exact, byte-for-byte mirror of the contract block in
``step-7-shared-contracts/index.md`` (kind: spec, section "Exact Step 7 public
contracts").  Agent A owns the real ``sard.application.contracts`` module,
which lands from another branch; until then, tests use this replica so they
stay executable.  Once the real module exists, ``tests.helpers.step7_contracts``
automatically prefers it over this replica.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional

from sard.outputs.schemas import Itinerary


class UIExecutionMode(str, Enum):
    LIVE = "live"
    CACHED_DEMO = "cached_demo"


class UIModeKind(str, Enum):
    LIVE = "live"
    DEGRADED_RETRIEVAL = "degraded_retrieval"
    MODEL_FALLBACK = "model_fallback"
    CACHED_DEMO = "cached_demo"
    UNAVAILABLE = "unavailable"


class UIStage(str, Enum):
    UNDERSTAND = "understand"
    PLAN = "plan"
    RETRIEVE = "retrieve"
    COMPOSE = "compose"
    VERIFY = "verify"
    RENDER = "render"


class UIProgressState(str, Enum):
    WAITING = "waiting"
    ACTIVE = "active"
    COMPLETED = "completed"
    RETRIED = "retried"
    DEGRADED = "degraded"
    FAILED = "failed"
    PARTIALLY_COMPLETED = "partially_completed"


@dataclass(frozen=True)
class UIRunRequest:
    query: str
    run_id: str
    trip_dates: tuple[date, ...] = ()
    preferences: tuple[str, ...] = ()
    execution_mode: UIExecutionMode = UIExecutionMode.LIVE
    render_artifacts: bool = True


@dataclass(frozen=True)
class UIProgressEvent:
    sequence: int
    run_id: str
    stage: UIStage
    state: UIProgressState
    event_kind: str
    timestamp: str
    summary: str = ""
    duration_ms: Optional[float] = None
    source_count: Optional[int] = None
    coverage: Optional[float] = None
    retry_count: Optional[int] = None
    degraded: bool = False
    simulated: bool = False


@dataclass(frozen=True)
class UIModelRoute:
    use_case: str
    resolved_model: str
    used_fallback: bool = False


@dataclass(frozen=True)
class UIModeStatus:
    kind: UIModeKind
    retrieval_mode: str
    model_fallback_used: bool
    execution_mode: UIExecutionMode
    model_routes: tuple[UIModelRoute, ...] = ()


@dataclass(frozen=True)
class UISourceView:
    citation_id: str
    title: str
    url: str
    page: Optional[int] = None
    section: Optional[str] = None
    publication_date: Optional[date] = None
    metadata_complete: bool = False
    citation_verified: bool = True


@dataclass(frozen=True)
class UIArtifactView:
    artifact_type: str
    display_label: str
    filename: str
    mime_type: str
    size_bytes: int
    checksum: Optional[str]
    creation_status: str
    warnings: tuple[str, ...] = ()
    error_category: Optional[str] = None
    download_bytes: Optional[bytes] = None


@dataclass(frozen=True)
class UIRunResult:
    run_id: str
    final_answer: str
    graph_outcome: str
    mode: UIModeStatus
    sources: tuple[UISourceView, ...]
    itinerary: Optional[Itinerary]
    artifacts: tuple[UIArtifactView, ...]
    progress_events: tuple[UIProgressEvent, ...]
    coverage_ratio: Optional[float] = None
    warnings: tuple[str, ...] = ()
    error_message: str = ""


@dataclass(frozen=True)
class CalendarAfterDateRequest:
    run_id: str
    dates: tuple[date, ...]
    preview: bool = False
