"""Central, typed fallback policy for every model-dependent RAG operation.

Instead of scattering ``try/except`` blocks across embeddings, reranking,
query-rewriting, and generation, every one of those call sites goes through
:func:`run_with_fallback`, which:

- Tries an ordered list of :class:`ModelCandidate` (primary, then
  fallbacks), each with a small bounded retry budget.
- Classifies failures into a fixed :class:`FailureCategory` set.
- Never retries authentication failures or deterministic schema errors.
- Trips a simple in-session :class:`CircuitBreaker` per (use_case, model,
  endpoint) so a failing endpoint isn't hammered repeatedly.
- Records a :class:`FallbackEvent` for every attempt — observable, but
  never containing secrets or raw request/response payloads.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class FailureCategory(str, Enum):
    AUTHENTICATION = "authentication_failure"
    MODEL_UNAVAILABLE = "model_unavailable"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    INVALID_REQUEST = "invalid_request"
    CONTEXT_LENGTH = "context_length_failure"
    MALFORMED_OUTPUT = "malformed_structured_output"
    EMBEDDING_DIMENSION_MISMATCH = "embedding_dimension_mismatch"
    ZVEC_SCHEMA_MISMATCH = "zvec_schema_mismatch"
    ZVEC_UNAVAILABLE = "zvec_collection_unavailable"
    UNKNOWN = "unknown"


# Failures that must never be retried: retrying them wastes latency/quota
# for an outcome that cannot change without external intervention.
NON_RETRYABLE_CATEGORIES = frozenset(
    {
        FailureCategory.AUTHENTICATION,
        FailureCategory.ZVEC_SCHEMA_MISMATCH,
        FailureCategory.EMBEDDING_DIMENSION_MISMATCH,
    }
)


def classify_exception(exc: BaseException) -> FailureCategory:
    """Best-effort classification of a raised exception into a failure category.

    Deliberately conservative: falls back to UNKNOWN rather than guessing
    incorrectly. Inspects only the exception type name and message text —
    never logs the full exception (which might embed request headers).
    """
    if isinstance(exc, FallbackClassifiedError):
        return exc.category

    text = f"{type(exc).__name__} {exc}".lower()

    if any(k in text for k in ("401", "403", "unauthorized", "forbidden", "invalid api key", "authentication")):
        return FailureCategory.AUTHENTICATION
    if any(k in text for k in ("404", "model not found", "no such model", "unknown model")):
        return FailureCategory.MODEL_UNAVAILABLE
    if any(k in text for k in ("429", "rate limit", "too many requests")):
        return FailureCategory.RATE_LIMIT
    if any(k in text for k in ("timeout", "timed out", "deadline exceeded")):
        return FailureCategory.TIMEOUT
    if any(k in text for k in ("context length", "too many tokens", "maximum context")):
        return FailureCategory.CONTEXT_LENGTH
    if any(k in text for k in ("400", "bad request", "invalid request")):
        return FailureCategory.INVALID_REQUEST
    if any(k in text for k in ("500", "502", "503", "504", "service unavailable", "bad gateway")):
        return FailureCategory.MODEL_UNAVAILABLE
    return FailureCategory.UNKNOWN


class FallbackClassifiedError(Exception):
    """Raise this when a call site already knows the precise failure category
    (e.g. a schema validator), so :func:`classify_exception` doesn't have to
    guess from a message string."""

    def __init__(self, category: FailureCategory, message: str):
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class ModelCandidate:
    """One model/endpoint option in a use case's fallback route."""

    model_id: str
    endpoint_type: str  # "hosted" | "self_hosted"
    label: str  # "primary" | "fallback_1" | "fallback_2" | ...
    degraded: bool = False  # True if selecting this candidate degrades quality


@dataclass
class FallbackEvent:
    """One observable attempt record. Never contains secrets or payloads."""

    use_case: str
    requested_model: str
    resolved_model: str
    endpoint_type: str
    attempt: int
    failure_category: Optional[FailureCategory]
    selected_fallback: Optional[str]
    quality_degraded: bool
    latency_ms: float
    outcome: str  # "success" | "failure" | "skipped_circuit_open" | "exhausted"


class CircuitBreaker:
    """A minimal in-session circuit breaker keyed by (use_case, model, endpoint).

    Opens after ``failure_threshold`` consecutive failures and stays open
    for ``cooldown_seconds``, after which a single trial call is allowed
    (half-open) to decide whether to close it again.
    """

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 30.0):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}

    @staticmethod
    def _key(use_case: str, model_id: str, endpoint_type: str) -> str:
        return f"{use_case}::{model_id}::{endpoint_type}"

    def is_open(self, use_case: str, model_id: str, endpoint_type: str) -> bool:
        key = self._key(use_case, model_id, endpoint_type)
        opened_at = self._opened_at.get(key)
        if opened_at is None:
            return False
        if time.monotonic() - opened_at >= self.cooldown_seconds:
            # Half-open: allow one attempt through.
            return False
        return True

    def record_success(self, use_case: str, model_id: str, endpoint_type: str) -> None:
        key = self._key(use_case, model_id, endpoint_type)
        self._failures.pop(key, None)
        self._opened_at.pop(key, None)

    def record_failure(self, use_case: str, model_id: str, endpoint_type: str) -> None:
        key = self._key(use_case, model_id, endpoint_type)
        count = self._failures.get(key, 0) + 1
        self._failures[key] = count
        if count >= self.failure_threshold:
            self._opened_at[key] = time.monotonic()

    def reset(self) -> None:
        self._failures.clear()
        self._opened_at.clear()


# A process-wide default breaker. Tests/services may construct their own
# CircuitBreaker() for isolation instead of relying on this singleton.
default_circuit_breaker = CircuitBreaker()


class AllCandidatesFailedError(Exception):
    """Raised when every candidate (and every retry) in a route is exhausted."""

    def __init__(self, use_case: str, events: list[FallbackEvent]):
        self.use_case = use_case
        self.events = events
        super().__init__(
            f"All model candidates failed for use case '{use_case}' "
            f"after {len(events)} attempt(s)."
        )


def run_with_fallback(
    use_case: str,
    candidates: list[ModelCandidate],
    call: Callable[[ModelCandidate], T],
    max_retries_per_candidate: int = 2,
    backoff_base_seconds: float = 0.05,
    circuit_breaker: Optional[CircuitBreaker] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    deadline: object = None,
    cancel_event: Optional[threading.Event] = None,
    reserve_s: float = 0.0,
    per_attempt_cap_s: float = 30.0,
    overall_deadline_s: Optional[float] = None,
    deadline_monotonic: object = None,
) -> tuple[T, list[FallbackEvent]]:
    """Try each candidate in order, with bounded retries, recording events.

    Hierarchical-deadline hardening (workstream E, additive):

    - ``deadline`` accepts a :class:`sard.agent.deadline.Deadline` or a
      float absolute ``monotonic_end`` (compat shim); legacy
      ``deadline_monotonic`` float is honored when ``deadline`` is None.
    - No new candidate/retry starts once ``remaining - reserve <= 0``.
    - Each attempt is budgeted ``min(per_attempt_cap_s,
      remaining / attempts_left)`` and executed in a daemon thread so a
      hanging provider call cannot blow the outer budget (``3x2x30``
      chains are capped by the outer deadline, default
      ``SARD_RAG_OVERALL_DEADLINE=12s``).
    - ``cancel_event`` (threading.Event) aborts between attempts and
      abandons in-flight attempts; cancellation propagates as typed
      ``DeadlineCancelledError``, never as a model failure.

    Returns ``(result, events)`` on success. Raises
    :class:`AllCandidatesFailedError` (carrying all events) if every
    candidate is exhausted, or ``DeadlineCancelledError`` on cancel.
    """
    from sard.agent.deadline import (  # lazy: deadline module is leaf-level
        DeadlineCancelledError,
        DeadlineTimeoutError,
        coerce_deadline,
        deadline_from_timeout,
        rag_overall_deadline_s,
    )

    breaker = circuit_breaker or default_circuit_breaker
    events: list[FallbackEvent] = []
    requested_model = candidates[0].model_id if candidates else "unknown"

    # Resolve effective deadline (explicit wins; else legacy float; else
    # implicit outer cap from SARD_RAG_OVERALL_DEADLINE so unbounded
    # 3-candidates x 2-retries x 30s chains cannot run 180s).
    dl = coerce_deadline(deadline, reserve_s=reserve_s, cancel_event=cancel_event)
    if dl is None and deadline_monotonic is not None:
        dl = coerce_deadline(deadline_monotonic, reserve_s=reserve_s, cancel_event=cancel_event)
    if cancel_event is None and dl is not None:
        cancel_event = dl.cancel_event
    if dl is None:
        try:
            implicit_s = overall_deadline_s if overall_deadline_s is not None else rag_overall_deadline_s()
        except Exception:
            implicit_s = 12.0
        if implicit_s and implicit_s > 0:
            dl = deadline_from_timeout(implicit_s, label=f"rag:{use_case}")
    try:
        total_attempts = max(1, len(candidates) * max(1, int(max_retries_per_candidate)))
    except Exception:
        total_attempts = max(1, len(candidates) * 2)

    def _cancelled() -> bool:
        try:
            return bool(cancel_event is not None and cancel_event.is_set())
        except Exception:
            return False

    def _fail_event(candidate: ModelCandidate, attempt: int, category: FailureCategory, latency_ms: float, outcome: str = "failure") -> FallbackEvent:
        return FallbackEvent(
            use_case=use_case,
            requested_model=requested_model,
            resolved_model=candidate.model_id,
            endpoint_type=candidate.endpoint_type,
            attempt=attempt,
            failure_category=category,
            selected_fallback=candidate.label,
            quality_degraded=candidate.degraded,
            latency_ms=latency_ms,
            outcome=outcome,
        )

    def _run_attempt(candidate: ModelCandidate, budget_s: float):
        """Run call() in a daemon thread; abandon (don't join) on budget/cancel."""
        box: dict = {}
        done = threading.Event()

        def _target() -> None:
            try:
                box["result"] = call(candidate)
            except BaseException as exc:  # capture incl. Cancelled for classification
                box["error"] = exc
            finally:
                done.set()

        worker = threading.Thread(target=_target, name=f"sard-fb-{use_case}", daemon=True)
        worker.start()
        start = time.monotonic()
        poll = 0.02
        while not done.is_set():
            if _cancelled():
                raise DeadlineCancelledError(f"cancelled during '{use_case}'", stage=use_case)
            elapsed = time.monotonic() - start
            if elapsed >= budget_s:
                raise DeadlineTimeoutError(
                    f"deadline exceeded during '{use_case}' attempt (budget {budget_s:.2f}s)",
                    stage=use_case,
                )
            # Also respect outer reserve while in-flight: abandon early so the
            # reserve is preserved even if this attempt's budget was generous.
            if dl is not None and dl.reserve_remaining() <= 0:
                raise DeadlineTimeoutError(
                    f"deadline exceeded during '{use_case}' (reserve preserved)",
                    stage=use_case,
                )
            done.wait(timeout=min(poll, max(0.005, budget_s - elapsed)))
        if "error" in box:
            raise box["error"]
        return box.get("result")

    attempts_done = 0
    for candidate in candidates:
        if _cancelled():
            raise DeadlineCancelledError(f"cancelled before '{candidate.label}'", stage=use_case)
        if dl is not None and dl.reserve_remaining() <= 0:
            events.append(_fail_event(candidate, 0, FailureCategory.TIMEOUT, 0.0))
            break  # stop new candidates; reserve preserved
        if breaker.is_open(use_case, candidate.model_id, candidate.endpoint_type):
            events.append(
                FallbackEvent(
                    use_case=use_case,
                    requested_model=requested_model,
                    resolved_model=candidate.model_id,
                    endpoint_type=candidate.endpoint_type,
                    attempt=0,
                    failure_category=FailureCategory.MODEL_UNAVAILABLE,
                    selected_fallback=candidate.label,
                    quality_degraded=candidate.degraded,
                    latency_ms=0.0,
                    outcome="skipped_circuit_open",
                )
            )
            continue

        attempt = 0
        while attempt < max_retries_per_candidate:
            if _cancelled():
                raise DeadlineCancelledError(f"cancelled during '{candidate.label}'", stage=use_case)
            if dl is not None and dl.reserve_remaining() <= 0:
                events.append(_fail_event(candidate, attempt + 1, FailureCategory.TIMEOUT, 0.0))
                break  # stop retries; reserve preserved
            attempt += 1
            attempts_left = max(1, total_attempts - attempts_done)
            if dl is not None:
                try:
                    budget_s = dl.time_left_for_attempt(attempts_left, cap_s=per_attempt_cap_s)
                except Exception:
                    budget_s = per_attempt_cap_s
                # Shrunk per-attempt budget: never start doomed work.
                if budget_s <= 0.01:
                    events.append(_fail_event(candidate, attempt, FailureCategory.TIMEOUT, 0.0))
                    attempts_done += 1
                    break
            else:
                budget_s = per_attempt_cap_s
            start = time.monotonic()
            try:
                if dl is not None:
                    result = _run_attempt(candidate, max(0.05, float(budget_s)))
                else:
                    result = call(candidate)
            except DeadlineCancelledError:
                raise
            except DeadlineTimeoutError as exc:
                latency_ms = (time.monotonic() - start) * 1000
                events.append(_fail_event(candidate, attempt, FailureCategory.TIMEOUT, latency_ms))
                breaker.record_failure(use_case, candidate.model_id, candidate.endpoint_type)
                logger.warning(
                    "RAG fallback: use_case=%s model=%s attempt=%d category=%s (deadline)",
                    use_case, candidate.model_id, attempt, FailureCategory.TIMEOUT.value,
                )
                attempts_done += 1
                break  # budget gone: move to next candidate, preserve reserve
            except Exception as exc:  # noqa: BLE001 - central classification point
                latency_ms = (time.monotonic() - start) * 1000
                # A nested typed timeout is an attempt failure, not a crash.
                if isinstance(exc, DeadlineTimeoutError):
                    category = FailureCategory.TIMEOUT
                else:
                    category = classify_exception(exc)
                # Cancellation from nested stages must propagate, never be
                # recorded as an ordinary model failure.
                if isinstance(exc, DeadlineCancelledError) or _cancelled():
                    raise DeadlineCancelledError(str(exc) or "cancelled", stage=use_case) from exc
                events.append(
                    FallbackEvent(
                        use_case=use_case,
                        requested_model=requested_model,
                        resolved_model=candidate.model_id,
                        endpoint_type=candidate.endpoint_type,
                        attempt=attempt,
                        failure_category=category,
                        selected_fallback=candidate.label,
                        quality_degraded=candidate.degraded,
                        latency_ms=latency_ms,
                        outcome="failure",
                    )
                )
                breaker.record_failure(use_case, candidate.model_id, candidate.endpoint_type)
                logger.warning(
                    "RAG fallback: use_case=%s model=%s attempt=%d category=%s",
                    use_case,
                    candidate.model_id,
                    attempt,
                    category.value,
                )
                attempts_done += 1
                if category in NON_RETRYABLE_CATEGORIES:
                    break  # move to next candidate immediately, no retry
                if attempt < max_retries_per_candidate:
                    # Shrink backoff to the remaining reserve-preserving budget
                    # and keep it interruptible; never oversleep the deadline.
                    raw_backoff = backoff_base_seconds * (2 ** (attempt - 1))
                    if dl is not None:
                        sleep_for = max(0.0, min(raw_backoff, dl.reserve_remaining()))
                    else:
                        sleep_for = raw_backoff
                    if sleep_for > 0:
                        try:
                            sleep_fn(sleep_for)
                        except Exception:
                            pass
                    if _cancelled():
                        raise DeadlineCancelledError(f"cancelled during backoff '{use_case}'", stage=use_case)
                continue
            else:
                latency_ms = (time.monotonic() - start) * 1000
                attempts_done += 1
                breaker.record_success(use_case, candidate.model_id, candidate.endpoint_type)
                events.append(
                    FallbackEvent(
                        use_case=use_case,
                        requested_model=requested_model,
                        resolved_model=candidate.model_id,
                        endpoint_type=candidate.endpoint_type,
                        attempt=attempt,
                        failure_category=None,
                        selected_fallback=candidate.label,
                        quality_degraded=candidate.degraded,
                        latency_ms=latency_ms,
                        outcome="success",
                    )
                )
                return result, events

    raise AllCandidatesFailedError(use_case, events)
