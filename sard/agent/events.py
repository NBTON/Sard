"""Safe, sanitized observability events for the agent pipeline.

Every event emitted by graph nodes is a :class:`SafeEvent` whose fields are
bounded and scrubbed: no prompts, raw provider payloads, secrets, auth
headers, internal exceptions or chain-of-thought ever cross this boundary.
Failure kinds are fixed by :class:`FailureKind` so routing and retry policy
stay deterministic.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

EVENT_WAITING = "waiting"
EVENT_STARTED = "started"
EVENT_COMPLETED = "completed"
EVENT_RETRIED = "retried"
EVENT_DEGRADED = "degraded"
EVENT_FAILED = "failed"
EVENT_RETRIEVAL_MODE_CHANGED = "retrieval_mode_changed"
EVENT_MODEL_FALLBACK_ACTIVATED = "model_fallback_activated"
EVENT_CITATION_COVERAGE_CALCULATED = "citation_coverage_calculated"
EVENT_GRAPH_COMPLETED = "graph_completed"

SAFE_EVENT_KINDS = frozenset(
    {
        EVENT_WAITING,
        EVENT_STARTED,
        EVENT_COMPLETED,
        EVENT_RETRIED,
        EVENT_DEGRADED,
        EVENT_FAILED,
        EVENT_RETRIEVAL_MODE_CHANGED,
        EVENT_MODEL_FALLBACK_ACTIVATED,
        EVENT_CITATION_COVERAGE_CALCULATED,
        EVENT_GRAPH_COMPLETED,
    }
)


class FailureKind(str, Enum):
    """Fixed failure taxonomy used for routing and retry policy."""

    AUTH = "auth"
    MODEL_UNAVAILABLE = "model_unavailable"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    INVALID_STRUCTURED_OUTPUT = "invalid_structured_output"
    ZVEC_UNAVAILABLE = "zvec_unavailable"
    EMBEDDING_MISMATCH = "embedding_mismatch"
    NO_EVIDENCE = "no_evidence"
    RERANKING_UNAVAILABLE = "reranking_unavailable"
    VERIFICATION_EXHAUSTED = "verification_exhausted"
    RENDERING_VALIDATION = "rendering_validation"


NON_RETRYABLE_FAILURE_KINDS = frozenset(
    {
        FailureKind.AUTH,
        FailureKind.EMBEDDING_MISMATCH,
    }
)

_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|token|password|secret|credential)"
    r"(\s*[:=]\s*|\s+)\S+"
)
_BEARER_RE = re.compile(r"(?i)bearer\s+[^\s,;]+")
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_-]{24,}\b")
_HEADER_RE = re.compile(r"(?im)^(x-nvidia[^:]*|authorization|content-type)[^\n]*$")


def sanitize_text(text: str, limit: int = 320) -> str:
    """Scrub secrets/headers and cap length so events stay safe to surface."""
    if not text:
        return ""
    scrubbed = text
    scrubbed = _HEADER_RE.sub("", scrubbed)
    scrubbed = _BEARER_RE.sub("[REDACTED]", scrubbed)
    scrubbed = _SECRET_RE.sub(r"\1=[REDACTED]", scrubbed)
    scrubbed = _LONG_TOKEN_RE.sub("[REDACTED]", scrubbed)
    scrubbed = re.sub(r"\s+", " ", scrubbed).strip()
    if len(scrubbed) > limit:
        scrubbed = scrubbed[: limit - 3] + "..."
    return scrubbed or "[REDACTED]"


def safe_chain_message(exc: BaseException) -> str:
    """Turn any exception into a generic, log-safe message."""
    name = type(exc).__name__ if exc else "unknown"
    return f"تعذّر تنفيذ الخطوة بشكل غير متوقع ({name})."


@dataclass(frozen=True)
class SafeEvent:
    kind: str
    run: str
    node: str
    status: str
    timestamp: str
    duration_ms: Optional[float] = None
    summary: str = ""
    source_count: Optional[int] = None
    coverage: Optional[float] = None
    retry: Optional[int] = None
    degraded: bool = False

    def __post_init__(self) -> None:
        if self.kind not in SAFE_EVENT_KINDS:
            raise ValueError(f"Unknown safe event kind: {self.kind!r}")

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "run": self.run,
            "node": self.node,
            "status": self.status,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "summary": self.summary,
            "source_count": self.source_count,
            "coverage": self.coverage,
            "retry": self.retry,
            "degraded": self.degraded,
        }


@dataclass(frozen=True)
class SafeError:
    run: str
    node: str
    kind: str
    message: str
    retryable: bool
    happened_at: str


@dataclass(frozen=True)
class SafeFallbackEvent:
    use_case: str
    requested_model: str
    resolved_model: str
    attempt: int
    outcome: str
    degraded: bool
    failure_category: Optional[str] = None
    latency_ms: float = 0.0
    endpoint_type: Optional[str] = None
    selected_fallback: Optional[str] = None


def adapt_fallback_events(events) -> list[SafeFallbackEvent]:
    """Convert provider-neutral fallback records to safe graph metadata.

    The adapter intentionally copies model-routing metadata only. It never
    carries prompts, responses, credentials, or provider payloads across the
    graph boundary.
    """

    adapted: list[SafeFallbackEvent] = []
    for event in events or ():
        raw_failure = getattr(event, "failure_category", None)
        failure = getattr(raw_failure, "value", raw_failure)
        adapted.append(
            SafeFallbackEvent(
                use_case=str(getattr(event, "use_case", "") or ""),
                requested_model=str(getattr(event, "requested_model", "") or ""),
                resolved_model=str(getattr(event, "resolved_model", "") or ""),
                attempt=int(getattr(event, "attempt", 0) or 0),
                outcome=str(getattr(event, "outcome", "") or ""),
                degraded=bool(getattr(event, "quality_degraded", False)),
                failure_category=failure,
                latency_ms=float(getattr(event, "latency_ms", 0.0) or 0.0),
                endpoint_type=getattr(event, "endpoint_type", None),
                selected_fallback=getattr(event, "selected_fallback", None),
            )
        )
    return adapted


class GraphNodeError(Exception):
    """Raised by nodes when the failure kind is already known."""

    def __init__(self, kind: FailureKind, safe_message: str):
        super().__init__(safe_message)
        self.kind = kind
        self.safe_message = safe_message


def make_event(
    kind: str,
    run: str,
    node: str,
    status: str,
    summary: str = "",
    duration_ms: Optional[float] = None,
    source_count: Optional[int] = None,
    coverage: Optional[float] = None,
    retry: Optional[int] = None,
    degraded: bool = False,
) -> SafeEvent:
    return SafeEvent(
        kind=kind,
        run=run,
        node=node,
        status=status,
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        duration_ms=duration_ms,
        summary=sanitize_text(summary),
        source_count=source_count,
        coverage=coverage,
        retry=retry,
        degraded=degraded,
    )


def make_error(run: str, node: str, kind: FailureKind, message: str, retryable: bool) -> SafeError:
    return SafeError(
        run=run,
        node=node,
        kind=kind.value,
        message=sanitize_text(message, limit=280),
        retryable=retryable,
        happened_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
