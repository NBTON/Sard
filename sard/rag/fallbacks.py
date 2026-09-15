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
# INVALID_REQUEST (400/422), CONTEXT_LENGTH (incl. 413/input-too-long) and
# MALFORMED_OUTPUT (invalid JSON) advance immediately to the next candidate.
NON_RETRYABLE_CATEGORIES = frozenset(
    {
        FailureCategory.AUTHENTICATION,
        FailureCategory.ZVEC_SCHEMA_MISMATCH,
        FailureCategory.EMBEDDING_DIMENSION_MISMATCH,
        FailureCategory.INVALID_REQUEST,
        FailureCategory.CONTEXT_LENGTH,
        FailureCategory.MALFORMED_OUTPUT,
    }
)


# 5xx / transient server markers. Only MODEL_UNAVAILABLE failures whose
# message carries one of these markers is treated as transient (retryable);
# a 404-style MODEL_UNAVAILABLE advances immediately like a non-retryable.
_TRANSIENT_5XX_MARKERS = (
    "500",
    "501",
    "502",
    "503",
    "504",
    "505",
    "507",
    "508",
    "509",
    "511",
    "529",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "internal server error",
    "server error",
    "temporarily unavailable",
    "overloaded",
    "overload",
    "try again",
)


def is_transient_failure(category: FailureCategory, exc: BaseException) -> bool:
    """True when a same-candidate retry could plausibly succeed.

    Bounded transient policy (shared with sard.config.model_router):
    TIMEOUT and RATE_LIMIT (429) always retry; MODEL_UNAVAILABLE retries
    only for distinct 5xx/transient-server markers (never for 404-style
    unknown-model). Every other category — auth, invalid request,
    context-length/413, dimension mismatch, malformed JSON, schema —
    advances immediately to the next candidate.
    """
    if category in (FailureCategory.TIMEOUT, FailureCategory.RATE_LIMIT):
        return True
    if category is FailureCategory.MODEL_UNAVAILABLE:
        try:
            text = f"{type(exc).__name__} {exc}".lower()
        except Exception:
            return False
        return any(marker in text for marker in _TRANSIENT_5XX_MARKERS)
    return False


def classify_exception(exc: BaseException) -> FailureCategory:
    """Best-effort classification of a raised exception into a failure category.

    Deliberately conservative: falls back to UNKNOWN rather than guessing
    incorrectly. Inspects only the exception type name and message text —
    never logs the full exception (which might embed request headers).
    """
    if isinstance(exc, FallbackClassifiedError):
        return exc.category

    text = f"{type(exc).__name__} {exc}".lower()

    if any(
        k in text
        for k in (
            "401",
            "403",
            "unauthorized",
            "forbidden",
            "invalid api key",
            "invalid_api_key",
            "authentication",
            "permission denied",
            "access denied",
        )
    ):
        return FailureCategory.AUTHENTICATION
    if any(
        k in text
        for k in (
            "429",
            "rate limit",
            "rate_limit",
            "rate-limited",
            "too many requests",
            "quota exceeded",
            "quota_exceeded",
        )
    ):
        return FailureCategory.RATE_LIMIT
    if any(
        k in text
        for k in (
            "timeout",
            "timed out",
            "timedout",
            "deadline exceeded",
            "readtimeout",
            "connecttimeout",
            "read timed out",
        )
    ):
        return FailureCategory.TIMEOUT
    if any(
        k in text
        for k in (
            "context length",
            "context_length",
            "context window",
            "context-window",
            "too many tokens",
            "maximum context",
            "max tokens",
            "token limit",
            "input too long",
            "input_too_long",
            "prompt too long",
            "request too large",
            "payload too large",
            "content too long",
            "too large",
            "413",
        )
    ):
        return FailureCategory.CONTEXT_LENGTH
    if "dimension" in text:
        return FailureCategory.EMBEDDING_DIMENSION_MISMATCH
    if any(
        k in text
        for k in (
            "jsondecode",
            "json decode",
            "invalid json",
            "not json",
            "expecting value",
            "unterminated",
            "malformed",
        )
    ):
        return FailureCategory.MALFORMED_OUTPUT
    # "json" alone (type name JSONDecodeError/JSONError) implies malformed
    # structured output; checked after the specific phrases above.
    if "json" in text and ("decode" in text or "parse" in text or "error" in text):
        return FailureCategory.MALFORMED_OUTPUT
    if any(
        k in text
        for k in (
            "400",
            "422",
            "bad request",
            "invalid request",
            "invalid parameter",
            "invalid_parameter",
            "validation failed",
            "validation error",
        )
    ):
        return FailureCategory.INVALID_REQUEST
    if any(k in text for k in ("404", "model not found", "no such model", "unknown model")):
        return FailureCategory.MODEL_UNAVAILABLE
    if any(k in text for k in _TRANSIENT_5XX_MARKERS):
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
    for ``cooldown_seconds``. After cooldown, exactly ONE concurrent caller
    may claim the half-open trial via :meth:`try_acquire_probe` (atomic
    single-flight gate); every other concurrent caller keeps seeing
    :meth:`is_open` as True and must skip. The probe holder's
    :meth:`record_success` / :meth:`record_failure` outcome closes or
    re-opens the circuit and releases the gate.

    Thread-safe: all state transitions hold an internal ``RLock`` so
    concurrent model legs (router executor threads, graph nodes) observe a
    coherent view. Keys are coherently namespaced and normalized
    (``use_case`` stripped, ``model_id`` stripped, ``endpoint`` lowered) so
    ``Hosted``/``hosted`` or padded IDs never split one logical endpoint
    into two breaker buckets.

    Call-site contract (used by ``run_with_fallback`` and ``ModelRouter``)::

        if breaker.is_open(use_case, model, endpoint):
            if not breaker.try_acquire_probe(use_case, model, endpoint):
                continue  # skip: open, or another thread holds the probe
            # ... this thread is THE probe; record_success/failure settles it
    """

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 30.0):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}

        self._half_open_probe: dict[str, float] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _key(use_case: str, model_id: str, endpoint_type: str) -> str:
        use_case_norm = (use_case or "").strip()
        model_norm = (model_id or "").strip()
        endpoint_norm = (endpoint_type or "").strip().lower()
        return f"{use_case_norm}::{model_norm}::{endpoint_norm}"

    def is_open(self, use_case: str, model_id: str, endpoint_type: str) -> bool:
        """True while the circuit is open (including post-cooldown).

        Post-cooldown the circuit still reports open until a probe settles
        it: exactly one caller wins :meth:`try_acquire_probe`, the rest must
        skip. This is what prevents concurrent-probe stampedes.
        """
        key = self._key(use_case, model_id, endpoint_type)
        with self._lock:
            return key in self._opened_at

    def try_acquire_probe(self, use_case: str, model_id: str, endpoint_type: str) -> bool:
        """Atomically claim the single half-open trial. Thread-safe.

        Returns True exactly once per open episode (post-cooldown): the
        caller becomes THE probe and must settle via ``record_success`` /
        ``record_failure``. Returns False when the circuit is closed (no
        probe needed — proceed on the normal path), when cooldown has not
        elapsed, or when another thread already holds the probe. A probe
        held longer than ``cooldown_seconds`` is treated as stale (holder
        died without settling) and may be re-claimed.
        """
        key = self._key(use_case, model_id, endpoint_type)
        now = time.monotonic()
        with self._lock:
            opened_at = self._opened_at.get(key)
            if opened_at is None:
                return False  # closed: normal path, no probe needed
            if now - opened_at < self.cooldown_seconds:
                return False  # still cooling down
            claimed_at = self._half_open_probe.get(key)
            if claimed_at is not None and now - claimed_at < self.cooldown_seconds:
                return False  # live probe already in flight: skip
            self._half_open_probe[key] = now
            return True

    def record_success(self, use_case: str, model_id: str, endpoint_type: str) -> None:
        key = self._key(use_case, model_id, endpoint_type)
        with self._lock:
            self._failures.pop(key, None)
            self._opened_at.pop(key, None)
            self._half_open_probe.pop(key, None)

    def record_failure(self, use_case: str, model_id: str, endpoint_type: str) -> None:
        key = self._key(use_case, model_id, endpoint_type)
        with self._lock:
            count = self._failures.get(key, 0) + 1
            self._failures[key] = count
            if count >= self.failure_threshold:
                self._opened_at[key] = time.monotonic()
            if key in self._opened_at:
                # A settling probe re-opens with a fresh cooldown; either way
                # the in-flight marker is consumed by this outcome.
                self._half_open_probe.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._opened_at.clear()
            self._half_open_probe.clear()


# A process-wide default breaker. Tests/services may construct their own
# CircuitBreaker() for isolation instead of relying on this singleton.
default_circuit_breaker = CircuitBreaker()


def breaker_allows_call(
    breaker: "CircuitBreaker", use_case: str, model_id: str, endpoint_type: str
) -> tuple[bool, bool]:
    """Decide whether a call may proceed: ``(allowed, is_probe)``.

    Closed circuit → ``(True, False)``. Open circuit → exactly one concurrent
    caller wins the half-open probe (``(True, True)``) via the atomic
    :meth:`CircuitBreaker.try_acquire_probe`; every other concurrent caller
    gets ``(False, False)`` and must record ``skipped_circuit_open`` without
    touching the provider (no stampede). A lost race (circuit settled between
    the check and the claim) is re-checked so a now-closed circuit still
    proceeds on the normal path.
    """
    if not breaker.is_open(use_case, model_id, endpoint_type):
        return True, False
    if breaker.try_acquire_probe(use_case, model_id, endpoint_type):
        return True, True
    if not breaker.is_open(use_case, model_id, endpoint_type):
        return True, False
    return False, False


def reserve_aware_attempt_budget(dl: object, attempts_left: int, cap_s: float) -> float:
    """Per-attempt budget from reserve-protected remaining time.

    Uses ``reserve_remaining()`` — never ``remaining()`` — so per-attempt
    budgets can never consume the terminal reserve: with R seconds of
    unreserved budget and N attempts left, each attempt gets at most R/N
    (capped by ``cap_s``). Returns 0.0 when no unreserved budget remains so
    callers treat the attempt as doomed and stop before starting new work.
    """
    try:
        left = max(1, int(attempts_left))  # type: ignore[arg-type]
    except Exception:
        left = 1
    try:
        reserve_left = float(dl.reserve_remaining())  # type: ignore[attr-defined]
    except Exception as exc_reserve:
        logger.debug("Reserve read skipped (%s); falling back to legacy share.", type(exc_reserve).__name__)
        try:
            return max(0.0, float(dl.time_left_for_attempt(left, cap_s=cap_s)))  # type: ignore[attr-defined]
        except Exception as exc_share:
            logger.debug("Legacy attempt share skipped (%s).", type(exc_share).__name__)
            try:
                return max(0.0, float(cap_s))
            except Exception:
                return 30.0
    if reserve_left <= 0:
        return 0.0
    try:
        cap = float(cap_s)
    except Exception:
        cap = 30.0
    return max(0.0, min(cap, reserve_left / left))


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
      reserve_remaining() / attempts_left)`` — reserve-protected, never
      ``remaining() / attempts_left`` — and executed in a daemon thread so
      a hanging provider call cannot blow the outer budget (``3x2x30``
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
        # Half-open single flight: exactly one concurrent caller may hold
        # the probe; every other caller (or a still-cooling circuit) skips
        # without touching the provider — no probe stampede.
        allowed, is_probe = breaker_allows_call(
            breaker, use_case, candidate.model_id, candidate.endpoint_type
        )
        if not allowed:
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
                # Reserve-protected share of UNRESERVED time: the terminal
                # reserve is never offered to provider attempts.
                budget_s = reserve_aware_attempt_budget(dl, attempts_left, per_attempt_cap_s)
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
            except DeadlineTimeoutError:
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
                # Bounded transient-only retry: 429 / timeout / distinct 5xx
                # retry within budget; every other category (invalid request,
                # context-length/413, malformed JSON, unknown-model 404,
                # unknown) advances immediately to preserve latency/quota.
                if not is_transient_failure(category, exc):
                    break  # non-transient: advance immediately, no retry
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
                        except Exception as exc_sleep:
                            logger.debug("Fallback backoff sleep interrupted (%s).", type(exc_sleep).__name__)
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
