"""Frozen, presentation-safe contracts for the Sard application boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Optional

from sard.outputs.schemas import Itinerary


_SAFE_RUN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
MAX_QUERY_LENGTH = 4_000
MAX_PREFERENCES = 24
MAX_PREFERENCE_LENGTH = 200


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

    def __post_init__(self) -> None:
        query = self.query.strip() if isinstance(self.query, str) else ""
        if not query:
            raise ValueError("query must be nonblank")
        if len(query) > MAX_QUERY_LENGTH:
            raise ValueError("query is too long")
        if not isinstance(self.run_id, str) or not _SAFE_RUN_RE.fullmatch(self.run_id):
            raise ValueError("run_id must be a safe ASCII identifier")
        if len(self.preferences) > MAX_PREFERENCES:
            raise ValueError("too many preferences")
        normalized_preferences: list[str] = []
        for preference in self.preferences:
            if not isinstance(preference, str):
                raise ValueError("preferences must be strings")
            normalized = " ".join(preference.split())
            if not normalized or len(normalized) > MAX_PREFERENCE_LENGTH:
                raise ValueError("preference must be nonblank and bounded")
            normalized_preferences.append(normalized)
        normalized_dates = _validate_dates(self.trip_dates)
        mode = self.execution_mode
        if isinstance(mode, str):
            mode = UIExecutionMode(mode)
        if not isinstance(mode, UIExecutionMode):
            raise ValueError("unknown execution mode")
        object.__setattr__(self, "query", query)
        object.__setattr__(self, "preferences", tuple(normalized_preferences))
        object.__setattr__(self, "trip_dates", normalized_dates)
        object.__setattr__(self, "execution_mode", mode)
        object.__setattr__(self, "render_artifacts", bool(self.render_artifacts))


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

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not _SAFE_RUN_RE.fullmatch(self.run_id):
            raise ValueError("run_id must be a safe ASCII identifier")
        object.__setattr__(self, "dates", _validate_dates(self.dates))
        object.__setattr__(self, "preview", bool(self.preview))


def _validate_dates(values: tuple[date, ...]) -> tuple[date, ...]:
    if not isinstance(values, tuple):
        raise ValueError("dates must be a tuple")
    for value in values:
        if not isinstance(value, date) or isinstance(value, datetime):
            raise ValueError("dates must contain date values")
    return values
