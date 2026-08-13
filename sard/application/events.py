"""Strict adapter from Step 5 ``SafeEvent`` records to UI progress events."""

from __future__ import annotations

import math
import re
from typing import Optional

from sard.agent.events import (
    EVENT_CITATION_COVERAGE_CALCULATED,
    EVENT_COMPLETED,
    EVENT_DEGRADED,
    EVENT_FAILED,
    EVENT_GRAPH_COMPLETED,
    EVENT_MODEL_FALLBACK_ACTIVATED,
    EVENT_RETRIED,
    EVENT_RETRIEVAL_MODE_CHANGED,
    EVENT_STARTED,
    EVENT_WAITING,
    SAFE_EVENT_KINDS,
    SafeEvent,
    sanitize_text,
)
from sard.agent.graph import NODE_NAMES
from sard.application.contracts import UIProgressEvent, UIProgressState, UIStage


_DEGRADED_RETRIEVAL_MODES = frozenset(
    {"hybrid_fused", "dense_only", "full_text_only", "unavailable"}
)
_WINDOWS_PATH_RE = re.compile(r"(?i)(?:[a-z]:[\\/]|\\\\)[^\s]+")
_POSIX_PATH_RE = re.compile(
    r"(?<![\w:/])/(?:home|tmp|var|Users|workspace|mnt|output)(?:/[^\s]*)?"
)
_FILE_URI_RE = re.compile(r"(?i)file://[^\s]+")
_INTERNAL_LINE_RE = re.compile(
    r"(?i)(chain[- ]of[- ]thought|system prompt|developer prompt|raw (?:request|response|payload)|traceback)"
)


def sanitize_ui_text(value: object, *, limit: int = 320) -> str:
    """Re-scrub display text and remove internal-only path/reasoning markers."""

    if not isinstance(value, str):
        return ""
    safe_parts: list[str] = []
    for line in value.splitlines() or [value]:
        if _INTERNAL_LINE_RE.search(line):
            continue
        line = _FILE_URI_RE.sub("[REDACTED]", line)
        line = _WINDOWS_PATH_RE.sub("[REDACTED]", line)
        line = _POSIX_PATH_RE.sub("[REDACTED]", line)
        cleaned = sanitize_text(line, limit=limit)
        if cleaned:
            safe_parts.append(cleaned)
    result = " ".join(safe_parts).strip()
    if len(result) > limit:
        result = result[: limit - 3] + "..."
    return result


def _finite_optional(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _nonnegative_int(value: object) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _progress_state(event: SafeEvent, outcome: Optional[str]) -> UIProgressState:
    if event.kind == EVENT_WAITING:
        return UIProgressState.WAITING
    if event.kind == EVENT_STARTED:
        return UIProgressState.ACTIVE
    if event.kind in {EVENT_COMPLETED, EVENT_CITATION_COVERAGE_CALCULATED}:
        return UIProgressState.COMPLETED
    if event.kind == EVENT_RETRIED:
        return UIProgressState.RETRIED
    if event.kind in {EVENT_DEGRADED, EVENT_MODEL_FALLBACK_ACTIVATED}:
        return UIProgressState.DEGRADED
    if event.kind == EVENT_FAILED:
        return UIProgressState.FAILED
    if event.kind == EVENT_RETRIEVAL_MODE_CHANGED:
        return (
            UIProgressState.DEGRADED
            if event.status in _DEGRADED_RETRIEVAL_MODES or event.degraded
            else UIProgressState.COMPLETED
        )
    if event.kind == EVENT_GRAPH_COMPLETED:
        resolved = outcome or event.status
        if resolved == "partial":
            return UIProgressState.PARTIALLY_COMPLETED
        if resolved == "failed":
            return UIProgressState.FAILED
        if resolved == "completed":
            return UIProgressState.COMPLETED
        raise ValueError("graph_completed event has an unknown outcome")
    raise ValueError("unknown event kind")


def adapt_safe_event(
    event: SafeEvent,
    *,
    sequence: int,
    expected_run_id: str,
    graph_outcome: Optional[str] = None,
    simulated: bool = False,
) -> UIProgressEvent:
    """Project only the fixed SafeEvent allowlist; arbitrary mappings fail closed."""

    if type(event) is not SafeEvent:
        raise TypeError("only SafeEvent records may cross the UI boundary")
    if event.kind not in SAFE_EVENT_KINDS:
        raise ValueError("unknown event kind")
    if event.run != expected_run_id:
        raise ValueError("event run_id does not match the active run")
    node = "understand" if event.kind == EVENT_WAITING and event.node == "pipeline" else event.node
    if node not in NODE_NAMES:
        raise ValueError("unknown graph node")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise ValueError("sequence must be a nonnegative integer")
    coverage = _finite_optional(event.coverage)
    if coverage is not None and not 0.0 <= coverage <= 1.0:
        coverage = None
    duration = _finite_optional(event.duration_ms)
    if duration is not None and duration < 0:
        duration = None
    return UIProgressEvent(
        sequence=sequence,
        run_id=expected_run_id,
        stage=UIStage(node),
        state=_progress_state(event, graph_outcome),
        event_kind=event.kind,
        timestamp=sanitize_ui_text(event.timestamp, limit=48),
        summary=sanitize_ui_text(event.summary),
        duration_ms=duration,
        source_count=_nonnegative_int(event.source_count),
        coverage=coverage,
        retry_count=_nonnegative_int(event.retry),
        degraded=bool(event.degraded),
        simulated=bool(simulated),
    )
