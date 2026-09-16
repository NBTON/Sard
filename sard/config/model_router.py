"""Capability-aware model execution engine (workstream B).

:func:`get_route` (see :mod:`sard.config.routing_table`) decides the ordered
``(provider, model_id, deadline_s)`` legs; this module executes them:

- Per-candidate deadline: ``min(task budget, remaining / legs left)`` from
  an optional ``deadline_monotonic`` so fallbacks always keep a slice.
  Chat-family legs cap at 6s, rewrite at 8s, compose at 20s, embed at 30s
  (see ``TASK_BUDGET_S``).
- Exactly 1 transient retry per leg (timeout / 429 / 5xx only).
  Auth / dimension / schema failures skip immediately via the shared
  ``NON_RETRYABLE_CATEGORIES`` — never retried, never depended on twice.
- Per-router :class:`CircuitBreaker` instances (never the process-global
  singleton), keyed ``(task_class, provider, model, endpoint)``: threshold
  3, cooldown 30s (15s for FAST_CLASSIFY / QUERY_REWRITE).
- Telemetry receipts ``{task_class, provider, model, attempt,
  failure_category, outcome, latency_ms, deadline_remaining_ms,
  breaker_skipped, parse_attempts}``. They convert losslessly to the
  existing ``model_routes`` / ``fallback_events`` / ``timings`` channels
  (provider travels alongside ``model_used``); failure taxonomy reuses
  :mod:`sard.rag.fallbacks` with no new schema.
- ``RERANK`` / ``TTS`` (and any task with no legs) resolve to a
  deterministic fallback with an ``UNSUPPORTED`` receipt until validated.

No secrets, prompts, or payloads ever enter receipts or logs.
"""

from __future__ import annotations

import concurrent.futures
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from sard.rag.fallbacks import (
    NON_RETRYABLE_CATEGORIES,
    CircuitBreaker,
    FailureCategory,
    FallbackClassifiedError,
    FallbackEvent,
    classify_exception,
)
from sard.config.routing_table import (
    RouteCandidate,
    TaskClass,
    get_route,
)

logger = logging.getLogger(__name__)

FAST_TASKS = frozenset({TaskClass.FAST_CLASSIFY, TaskClass.QUERY_REWRITE})

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


def _coerce_router_deadline(
    deadline: Any = None,
    deadline_monotonic: Any = None,
    cancel_event: Any = None,
    label: str = "",
) -> tuple[Any, Any]:
    """Coerce hierarchical Deadline/cancel into (Deadline|None, cancel_event).

    ``deadline`` accepts a Deadline or absolute monotonic float and wins;
    legacy ``deadline_monotonic`` float is honored when ``deadline`` is None.
    A bare ``cancel_event`` without any deadline still returns
    ``(None, cancel_event)`` so callers get cancellation checks.
    """
    try:
        from sard.agent.deadline import coerce_deadline as _coerce
    except Exception:
        return None, cancel_event
    try:
        if deadline is not None:
            dl = _coerce(deadline, cancel_event=cancel_event, label=label)
        elif deadline_monotonic is not None:
            dl = _coerce(deadline_monotonic, cancel_event=cancel_event, label=label)
        elif cancel_event is not None:
            dl = None
        else:
            return None, None
    except Exception as exc_coerce:
        logger.debug("Router deadline coerce skipped (%s).", type(exc_coerce).__name__)
        return None, cancel_event
    if dl is not None and cancel_event is None:
        try:
            cancel_event = getattr(dl, "cancel_event", None)
        except Exception as exc_cancel:
            logger.debug("Router cancel read skipped (%s).", type(exc_cancel).__name__)
    return dl, cancel_event


def _cancel_requested(cancel_event: Any) -> bool:
    try:
        return bool(cancel_event is not None and cancel_event.is_set())
    except Exception as exc_flag:
        logger.debug("Router cancel flag read skipped (%s).", type(exc_flag).__name__)
        return False


def _remaining_ms(dl: Any) -> Optional[float]:
    if dl is None:
        return None
    try:
        return (float(dl.monotonic_end) - time.monotonic()) * 1000.0
    except Exception as exc_end:
        logger.debug("Router remaining-ms end read skipped (%s).", type(exc_end).__name__)
    try:
        return float(dl.remaining()) * 1000.0
    except Exception as exc_rem:
        logger.debug("Router remaining-ms read skipped (%s).", type(exc_rem).__name__)
        return None


def _reserve_exhausted(dl: Any) -> bool:
    if dl is None:
        return False
    try:
        return bool(dl.reserve_remaining() <= 0)
    except Exception as exc_reserve:
        logger.debug("Router reserve check skipped (%s).", type(exc_reserve).__name__)
        return False

_MIN_TIMEOUT_S = 0.05
_TRANSIENT_RETRY_BACKOFF_S = 0.05

_SHARED_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="sard-router")


@dataclass
class RouteReceipt:
    """One observable routing attempt. Never contains secrets or payloads."""

    task_class: str
    provider: str
    model: str
    attempt: int
    failure_category: Optional[str]
    outcome: str  # "success" | "failure" | "skipped_circuit_open" | "unsupported"
    latency_ms: float
    deadline_remaining_ms: Optional[float] = None
    breaker_skipped: bool = False
    parse_attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_class": self.task_class,
            "provider": self.provider,
            "model": self.model,
            "attempt": self.attempt,
            "failure_category": self.failure_category,
            "outcome": self.outcome,
            "latency_ms": round(self.latency_ms, 3),
            "deadline_remaining_ms": (
                None if self.deadline_remaining_ms is None else round(self.deadline_remaining_ms, 3)
            ),
            "breaker_skipped": self.breaker_skipped,
            "parse_attempts": self.parse_attempts,
        }


@dataclass
class RouterResult:
    """Outcome of one routed call, with provider carried alongside model_used."""

    success: bool
    text: str = ""
    provider: Optional[str] = None
    model_used: Optional[str] = None
    task_class: str = ""
    receipts: list[RouteReceipt] = field(default_factory=list)
    failure_category: Optional[FailureCategory] = None
    error_message: str = ""
    deterministic_fallback: bool = False
    parse_attempts: int = 0
    data: Any = None

    @property
    def model_routes(self) -> dict[str, Optional[str]]:
        """Existing channel shape: {task: model} (unchanged)."""
        return {self.task_class: self.model_used}

    @property
    def provider_routes(self) -> dict[str, Optional[str]]:
        """Provider alongside model_used: {task: provider}."""
        return {self.task_class: self.provider}

    def to_fallback_events(self, requested_model: Optional[str] = None) -> list[FallbackEvent]:
        """Convert receipts to the existing FallbackEvent channel (no new schema)."""
        events = []
        for receipt in self.receipts:
            raw = receipt.failure_category
            category: Optional[FailureCategory] = None
            if raw is not None:
                try:
                    category = FailureCategory(raw)
                except ValueError:
                    category = FailureCategory.UNKNOWN
            outcome = receipt.outcome
            if outcome == "unsupported":
                outcome = "exhausted"  # channel vocabulary has no "unsupported"
            events.append(
                FallbackEvent(
                    use_case=self.task_class,
                    requested_model=requested_model or (self.receipts[0].model if self.receipts else "unknown"),
                    resolved_model=receipt.model,
                    endpoint_type="openrouter" if receipt.provider == "openrouter" else "hosted",
                    attempt=receipt.attempt,
                    failure_category=category,
                    selected_fallback=None,
                    quality_degraded=False,
                    latency_ms=receipt.latency_ms,
                    outcome=outcome,
                )
            )
        return events

    def to_timings(self, total_ms: float) -> dict[str, float]:
        return {f"{self.task_class}_ms": round(total_ms, 3)}


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for piece in content:
            if isinstance(piece, str):
                parts.append(piece)
            elif isinstance(piece, dict) and isinstance(piece.get("text"), str):
                parts.append(piece["text"])
        return "\n".join(parts)
    return str(content)


def _reasoning_channel_text(response: Any) -> str:
    """Extract thinking-channel text from a chat response (never raises).

    Mirrors the bakeoff runner extraction: ``reasoning`` /
    ``reasoning_content`` on the message, ``additional_kwargs``, or
    ``response_metadata``, plus legacy ``choices[0].text``. Returns "" when
    no channel carries text.
    """
    try:
        holders: list[Any] = [response]
        for attr in ("additional_kwargs", "response_metadata"):
            try:
                value = getattr(response, attr, None)
            except Exception:
                value = None
            if isinstance(value, dict):
                holders.append(value)
        for holder in holders:
            if isinstance(holder, dict):
                for key in ("reasoning_content", "reasoning"):
                    value = holder.get(key)
                    if isinstance(value, str) and value.strip():
                        return value
                    if isinstance(value, list):
                        texts = [str(p.get("text", "")) for p in value if isinstance(p, dict)]
                        joined = " ".join(t for t in texts if t.strip())
                        if joined.strip():
                            return joined
            else:
                for key in ("reasoning_content", "reasoning"):
                    try:
                        value = getattr(holder, key, None)
                    except Exception:
                        value = None
                    if isinstance(value, str) and value.strip():
                        return value
        try:
            raw = getattr(response, "response_metadata", None) or {}
            choices = raw.get("choices") if isinstance(raw, dict) else None
            if choices:
                text = (choices[0] or {}).get("text", "")
                if isinstance(text, str) and text.strip():
                    return text
        except Exception:
            pass
    except Exception as exc_reason:
        logger.debug("Reasoning-channel read skipped (%s).", type(exc_reason).__name__)
    return ""


def _is_transient(category: FailureCategory, exc: BaseException) -> bool:
    # Aligned bounded transient policy: delegate to the shared fallback
    # helper so router and run_with_fallback retry exactly the same set
    # (429 / timeout / distinct 5xx). Nonretryable categories (auth,
    # invalid request, context-length/413, dimension mismatch, malformed
    # JSON) advance immediately in both paths.
    try:
        from sard.rag.fallbacks import is_transient_failure as _shared_transient
    except Exception as exc_import:
        logger.debug("Shared transient helper import skipped (%s).", type(exc_import).__name__)
        _shared_transient = None
    if _shared_transient is not None:
        try:
            return bool(_shared_transient(category, exc))
        except Exception as exc_transient:
            logger.debug("Shared transient check skipped (%s).", type(exc_transient).__name__)
    if category in (FailureCategory.TIMEOUT, FailureCategory.RATE_LIMIT):
        return True
    if category is FailureCategory.MODEL_UNAVAILABLE:
        try:
            text = f"{type(exc).__name__} {exc}".lower()
        except Exception as exc_text:
            logger.debug("Transient text read skipped (%s).", type(exc_text).__name__)
            return False
        return any(marker in text for marker in _TRANSIENT_5XX_MARKERS)
    return False


def provider_name_for_model(model_id: str) -> str:
    """Return the provider name for a model ID (pure function, no I/O).

    Membership in the catalog wins; known NVIDIA NIM slashed IDs
    (``meta/...``, ``nvidia/...``, ``mistralai/...`` legacy shapes) route
    to NVIDIA; remaining ``/``-namespaced IDs are OpenRouter; bare IDs
    delegate to the existing ChatNVIDIA factory.
    """
    from sard.config.routing_table import OPENROUTER_CANDIDATES

    if not model_id:
        return "nvidia"
    if model_id in OPENROUTER_CANDIDATES:
        return "openrouter"
    lowered = model_id.strip().lower()
    for prefix in ("meta/", "nvidia/", "mistralai/", "nv-"):
        if lowered.startswith(prefix):
            return "nvidia"
    if "/" in model_id:
        return "openrouter"
    return "nvidia"


class ModelRouter:
    """Execute TaskClass routes with deadlines, one transient retry, and receipts."""

    def __init__(
        self,
        providers: Optional[dict[str, Any]] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        fast_circuit_breaker: Optional[CircuitBreaker] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        if providers is None:
            from sard.config.providers.nvidia import NvidiaProvider
            from sard.config.providers.openrouter import OpenRouterProvider

            providers = {"openrouter": OpenRouterProvider(), "nvidia": NvidiaProvider()}
        # Per-router instances (never the process-global singleton).
        self._providers = providers
        self._breaker = circuit_breaker or CircuitBreaker(failure_threshold=3, cooldown_seconds=30.0)
        self._fast_breaker = fast_circuit_breaker or CircuitBreaker(failure_threshold=3, cooldown_seconds=15.0)
        self._sleep_fn = sleep_fn

    # -- provider selection -------------------------------------------------
    def provider_for(self, model_id: str) -> str:
        """Return the provider name for a model ID (pure function)."""
        return provider_name_for_model(model_id)

    def _breaker_for(self, task: TaskClass) -> CircuitBreaker:
        return self._fast_breaker if task in FAST_TASKS else self._breaker

    @staticmethod
    def _breaker_use_case(task: TaskClass, provider: str) -> str:
        # Key carries (task_class, provider, model, endpoint) via the shared
        # breaker's (use_case, model, endpoint) key format.
        return f"{task.value}:{provider}"

    def _breaker_allows(self, task: TaskClass, provider: str, model_id: str, endpoint: str) -> tuple[bool, bool]:
        """Atomic half-open gate: ``(allowed, is_probe)`` for one routing leg.

        Exactly one concurrent caller may hold the post-cooldown probe; all
        others get ``(False, False)`` and must skip without consulting the
        provider (no stampede). Never raises: on unexpected breaker errors
        the leg is allowed on the normal (non-probe) path.
        """
        try:
            from sard.rag.fallbacks import breaker_allows_call as _allows
        except Exception as exc_import:
            logger.debug("Breaker gate import skipped (%s).", type(exc_import).__name__)
            return True, False
        breaker = self._breaker_for(task)
        try:
            return _allows(breaker, self._breaker_use_case(task, provider), model_id, endpoint)
        except Exception as exc_gate:
            logger.debug("Breaker gate check skipped (%s).", type(exc_gate).__name__)
            return True, False

    def build_chat(self, provider_name: str, model_id: str, timeout_s: float) -> Any:
        provider = self._providers.get(provider_name)
        if provider is None:
            raise FallbackClassifiedError(
                FailureCategory.MODEL_UNAVAILABLE, f"Unknown provider '{provider_name}'."
            )
        return provider.build_chat(model_id, float(timeout_s))

    def build_chat_for_model(self, model_id: str, timeout_s: float = 6.0) -> Any:
        """Build a chat model via provider selection (legacy factory adapter)."""
        return self.build_chat(self.provider_for(model_id), model_id, timeout_s)

    def as_chat_factory(self, timeout_s: float = 6.0) -> Callable[[str, Any], Any]:
        """Return a ``(model_id, settings) -> model`` factory for legacy call sites."""

        def _factory(model_id: str, settings: Any = None) -> Any:
            timeout = timeout_s
            try:
                if settings is not None and getattr(settings, "request_timeout_seconds", None):
                    timeout = min(float(timeout_s), float(settings.request_timeout_seconds))
            except Exception as exc_timeout:
                logger.debug("Factory timeout read skipped (%s).", type(exc_timeout).__name__)
                timeout = timeout_s
            return self.build_chat_for_model(model_id, timeout)

        return _factory

    # -- core execution ------------------------------------------------------
    def _legs(self, task: TaskClass, candidates: Optional[Sequence[RouteCandidate]]) -> list[RouteCandidate]:
        if candidates is not None:
            return list(candidates)
        try:
            return get_route(task)
        except Exception as exc_route:
            logger.debug("Route lookup skipped (%s).", type(exc_route).__name__)
            return []

    def _deadline_end(self, dl: Any, deadline_monotonic: Optional[float]) -> Optional[float]:
        """Effective absolute monotonic end from a Deadline or legacy float."""
        if dl is not None:
            try:
                return float(dl.monotonic_end)
            except Exception as exc_end:
                logger.debug("Deadline end read skipped (%s).", type(exc_end).__name__)
            try:
                # Fallback: Deadline-like with remaining()
                return time.monotonic() + float(dl.remaining())
            except Exception as exc_rem:
                logger.debug("Deadline remaining read skipped (%s).", type(exc_rem).__name__)
                return None
        return deadline_monotonic

    def _attempt_timeout(self, leg: RouteCandidate, dl_or_end: Any, legs_left: int) -> float:
        """Per-attempt provider timeout from RESERVE-PROTECTED remaining time.

        When a ``Deadline`` is available the share is computed from
        ``reserve_remaining()`` — never from ``remaining()`` / raw
        ``monotonic_end`` — so the terminal reserve is excluded from every
        provider budget: ``min(leg.deadline_s, reserve_remaining /
        legs_left)``. Legacy absolute floats (no reserve info) split their
        remaining time as before. Returns ``_MIN_TIMEOUT_S`` for expired
        budgets; callers treat that as doomed and stop new legs.
        """
        if dl_or_end is None:
            return max(_MIN_TIMEOUT_S, float(leg.deadline_s))
        try:
            is_deadline = hasattr(dl_or_end, "reserve_remaining") or hasattr(dl_or_end, "monotonic_end")
        except Exception:
            is_deadline = False
        if is_deadline:
            try:
                from sard.rag.fallbacks import reserve_aware_attempt_budget as _reserve_budget
            except Exception as exc_import:
                logger.debug("Reserve budget helper import skipped (%s).", type(exc_import).__name__)
                _reserve_budget = None  # type: ignore[assignment]
            if _reserve_budget is not None:
                try:
                    return max(_MIN_TIMEOUT_S, float(_reserve_budget(dl_or_end, legs_left, float(leg.deadline_s))))
                except Exception as exc_budget:
                    logger.debug("Reserve budget compute skipped (%s).", type(exc_budget).__name__)
            try:
                reserve_left = float(dl_or_end.reserve_remaining())  # type: ignore[attr-defined]
            except Exception as exc_reserve:
                logger.debug("Attempt timeout reserve read skipped (%s).", type(exc_reserve).__name__)
                return max(_MIN_TIMEOUT_S, float(leg.deadline_s))
            if reserve_left <= 0:
                return _MIN_TIMEOUT_S
            return max(_MIN_TIMEOUT_S, min(float(leg.deadline_s), reserve_left / max(1, legs_left)))
        end: Optional[float] = None
        try:
            end = float(dl_or_end)
        except Exception as exc_float:
            logger.debug("Attempt timeout float read skipped (%s).", type(exc_float).__name__)
            return max(_MIN_TIMEOUT_S, float(leg.deadline_s))
        remaining = end - time.monotonic()
        if remaining <= 0:
            return _MIN_TIMEOUT_S
        share = remaining / max(1, legs_left)
        return max(_MIN_TIMEOUT_S, min(float(leg.deadline_s), share))

    def _invoke_once(
        self, model: Any, messages: Any, timeout_s: float, cancel_event: Any = None
    ) -> str:
        future = _SHARED_EXECUTOR.submit(model.invoke, messages)
        # Poll in short slices so cancel_event aborts promptly instead of
        # blocking for the full timeout.
        try:
            from sard.agent.deadline import DeadlineCancelledError as _DC
        except Exception as exc_import:
            logger.debug("Deadline import skipped (%s).", type(exc_import).__name__)
            _DC = None  # type: ignore[assignment]
        start = time.monotonic()
        remaining = max(_MIN_TIMEOUT_S, float(timeout_s))
        while True:
            if _cancel_requested(cancel_event):
                future.cancel()
                if _DC is not None:
                    raise _DC("cancelled during model invocation", stage="model_router")
                raise TimeoutError("cancelled during model invocation")
            slice_s = min(0.05, remaining)
            try:
                response = future.result(timeout=slice_s)
                break
            except concurrent.futures.TimeoutError:
                elapsed = time.monotonic() - start
                remaining = float(timeout_s) - elapsed
                if remaining <= 0:
                    future.cancel()
                    raise TimeoutError(f"Model invocation timed out after {timeout_s:.2f}s.")
                continue
        text = _content_to_text(getattr(response, "content", ""))
        if not text.strip():
            # Reasoning-channel reader (bakeoff §7.2): thinking-mandatory
            # endpoints (e.g. liquid/lfm) return the answer in
            # message.reasoning_content with empty content. LangChain
            # surfaces it via additional_kwargs/response_metadata, so probe
            # those before declaring the leg MALFORMED.
            text = _reasoning_channel_text(response)
        if not text.strip():
            raise FallbackClassifiedError(FailureCategory.MALFORMED_OUTPUT, "Model returned empty content.")
        return text

    def invoke_chat(
        self,
        task: TaskClass,
        messages: Any,
        deadline_monotonic: Optional[float] = None,
        candidates: Optional[Sequence[RouteCandidate]] = None,
        deadline: Any = None,
        cancel_event: Any = None,
    ) -> RouterResult:
        """Run one chat call through the task's ordered legs with receipts.

        Hierarchical-deadline aware: ``deadline`` (Deadline or absolute
        monotonic float) wins, legacy ``deadline_monotonic`` is honored when
        ``deadline`` is None, and ``cancel_event`` aborts between legs (typed
        ``DeadlineCancelledError`` propagates, never recorded as a model
        failure). No new leg starts once ``remaining - reserve <= 0``.
        """
        dl, cancel_event = _coerce_router_deadline(deadline, deadline_monotonic, cancel_event, label=task.value)
        end = self._deadline_end(dl, deadline_monotonic)
        try:
            from sard.agent.deadline import DeadlineCancelledError as _DCC
        except Exception:
            _DCC = None  # type: ignore[assignment]
        legs = self._legs(task, candidates)
        if not legs:
            return self._deterministic(task, "No validated model route for this task class yet.")
        if dl is not None and _reserve_exhausted(dl):
            return RouterResult(False, "", None, None, task.value, [], FailureCategory.TIMEOUT,
                                "تعذّر الوصول إلى نماذج التوليد المكوّنة.")

        breaker = self._breaker_for(task)
        receipts: list[RouteReceipt] = []
        legs_left = len(legs)

        for leg in legs:
            if _cancel_requested(cancel_event):
                if _DCC is not None:
                    raise _DCC(f"cancelled before leg '{leg.model_id}'", stage=task.value)
                break
            if dl is not None and _reserve_exhausted(dl):
                break
            use_case = self._breaker_use_case(task, leg.provider)
            endpoint = "openrouter" if leg.provider == "openrouter" else "hosted"
            remaining_ms = _remaining_ms(dl) if dl is not None else (
                None if end is None else (end - time.monotonic()) * 1000.0
            )
            # Half-open single flight: exactly one concurrent caller may hold
            # the probe; all others skip without touching the provider.
            allowed, _is_probe = self._breaker_allows(task, leg.provider, leg.model_id, endpoint)
            if not allowed:
                receipts.append(
                    RouteReceipt(task.value, leg.provider, leg.model_id, 0, FailureCategory.MODEL_UNAVAILABLE.value,
                                 "skipped_circuit_open", 0.0, remaining_ms, True, 0)
                )
                legs_left -= 1
                continue

            timeout_s = self._attempt_timeout(leg, dl if dl is not None else end, legs_left)
            attempts = 0
            leg_done = False
            last_category: FailureCategory = FailureCategory.MODEL_UNAVAILABLE
            while attempts < 2 and not leg_done:
                if _cancel_requested(cancel_event):
                    if _DCC is not None:
                        raise _DCC(f"cancelled during leg '{leg.model_id}'", stage=task.value)
                    break
                if dl is not None and _reserve_exhausted(dl):
                    break
                attempts += 1
                attempt_started = time.monotonic()
                try:
                    model = self.build_chat(leg.provider, leg.model_id, timeout_s)
                    text = self._invoke_once(model, messages, timeout_s, cancel_event)
                except Exception as exc:  # noqa: BLE001 - central classification point
                    if _DCC is not None and isinstance(exc, _DCC):
                        raise
                    if _cancel_requested(cancel_event):
                        if _DCC is not None:
                            raise _DCC(str(exc) or "cancelled", stage=task.value) from exc
                    latency_ms = (time.monotonic() - attempt_started) * 1000.0
                    category = classify_exception(exc)
                    retryable = category not in NON_RETRYABLE_CATEGORIES and _is_transient(category, exc)
                    if retryable and attempts < 2:
                        breaker.record_failure(use_case, leg.model_id, endpoint)
                        receipts.append(
                            RouteReceipt(task.value, leg.provider, leg.model_id, attempts, category.value,
                                         "failure", latency_ms, remaining_ms, False, 0)
                        )
                        logger.warning("Model route retry: task=%s provider=%s attempt=%d category=%s",
                                       task.value, leg.provider, attempts, category.value)
                        try:
                            self._sleep_fn(_TRANSIENT_RETRY_BACKOFF_S * (2 ** (attempts - 1)))
                        except Exception as exc_sleep:
                            logger.debug("Router backoff sleep interrupted (%s).", type(exc_sleep).__name__)
                        # Shrink the retry so the remaining legs keep a slice.
                        timeout_s = self._attempt_timeout(leg, dl if dl is not None else end, legs_left)
                        continue
                    breaker.record_failure(use_case, leg.model_id, endpoint)
                    receipts.append(
                        RouteReceipt(task.value, leg.provider, leg.model_id, attempts, category.value,
                                     "failure", latency_ms, remaining_ms, False, 0)
                    )
                    logger.warning("Model route failure: task=%s provider=%s category=%s",
                                   task.value, leg.provider, category.value)
                    last_category = category
                    break  # retry budget for this leg spent -> next leg
                else:
                    latency_ms = (time.monotonic() - attempt_started) * 1000.0
                    breaker.record_success(use_case, leg.model_id, endpoint)
                    receipts.append(
                        RouteReceipt(task.value, leg.provider, leg.model_id, attempts, None,
                                     "success", latency_ms, remaining_ms, False, 0)
                    )
                    return RouterResult(True, text, leg.provider, leg.model_id, task.value, receipts)
            legs_left -= 1

        last_category = FailureCategory.MODEL_UNAVAILABLE
        for receipt in reversed(receipts):
            if receipt.failure_category and receipt.outcome == "failure":
                try:
                    last_category = FailureCategory(receipt.failure_category)
                except ValueError:
                    last_category = FailureCategory.UNKNOWN
                break
        return RouterResult(False, "", None, None, task.value, receipts, last_category,
                            "تعذّر الوصول إلى نماذج التوليد المكوّنة.")

    def invoke_json(
        self,
        task: TaskClass,
        messages: Any,
        deadline_monotonic: Optional[float] = None,
        candidates: Optional[Sequence[RouteCandidate]] = None,
        max_parse_attempts: int = 2,
        deadline: Any = None,
        cancel_event: Any = None,
    ) -> tuple[Optional[dict], RouterResult]:
        """Invoke then parse JSON: invalid JSON advances IMMEDIATELY.

        Release contract: a transport-successful but non-JSON response is a
        per-leg terminal verdict (``MALFORMED_OUTPUT`` receipt) — the leg is
        NEVER re-invoked for a parse retry. The next eligible candidate is
        tried instead, so one malformed primary cannot starve the fallbacks
        or burn quota on doomed re-asks. ``max_parse_attempts`` is accepted
        for signature compatibility and ignored. Transient TRANSPORT
        failures (429 / timeout / distinct 5xx) still get exactly one retry
        on the same leg; nonretryable transports advance immediately.

        Returns ``(parsed | None, result)`` and never raises for model
        failures (cancellation propagates as typed ``DeadlineCancelledError``).
        Hierarchical ``deadline``/``cancel_event`` stop new legs/attempts once
        the remaining-time budget (reserve preserved) is exhausted.
        """
        from sard.agent.util import extract_json_object

        dl, cancel_event = _coerce_router_deadline(deadline, deadline_monotonic, cancel_event, label=task.value)
        end = self._deadline_end(dl, deadline_monotonic)
        try:
            from sard.agent.deadline import DeadlineCancelledError as _DCJ
        except Exception as exc_import:
            logger.debug("Deadline import skipped (%s).", type(exc_import).__name__)
            _DCJ = None  # type: ignore[assignment]

        legs = self._legs(task, candidates)
        if not legs:
            return None, self._deterministic(task, "No validated model route for this task class yet.")
        if dl is not None and _reserve_exhausted(dl):
            empty = RouterResult(False, "", None, None, task.value, [], FailureCategory.TIMEOUT,
                                 "استجابة النموذج غير صالحة بصيغة JSON.")
            return None, empty

        breaker = self._breaker_for(task)
        receipts: list[RouteReceipt] = []
        legs_left = len(legs)
        final_result = RouterResult(False, task_class=task.value)

        for leg in legs:
            if _cancel_requested(cancel_event):
                if _DCJ is not None:
                    raise _DCJ(f"cancelled before leg '{leg.model_id}'", stage=task.value)
                break
            if dl is not None and _reserve_exhausted(dl):
                break
            use_case = self._breaker_use_case(task, leg.provider)
            endpoint = "openrouter" if leg.provider == "openrouter" else "hosted"
            remaining_ms = _remaining_ms(dl) if dl is not None else (
                None if end is None else (end - time.monotonic()) * 1000.0
            )
            # Half-open single flight: exactly one concurrent caller may hold
            # the probe; all others skip without touching the provider.
            allowed, _is_probe = self._breaker_allows(task, leg.provider, leg.model_id, endpoint)
            if not allowed:
                receipts.append(
                    RouteReceipt(task.value, leg.provider, leg.model_id, 0, FailureCategory.MODEL_UNAVAILABLE.value,
                                 "skipped_circuit_open", 0.0, remaining_ms, True, 0)
                )
                legs_left -= 1
                continue

            timeout_s = self._attempt_timeout(leg, dl if dl is not None else end, legs_left)
            transport_attempts = 0
            leg_text: Optional[str] = None
            # One transport invocation (+1 same-leg retry for TRANSIENT
            # transport failures only), then exactly one parse. Malformed
            # JSON advances immediately — never re-invoked.
            while True:
                if _cancel_requested(cancel_event):
                    if _DCJ is not None:
                        raise _DCJ(f"cancelled during leg '{leg.model_id}'", stage=task.value)
                    break
                if dl is not None and _reserve_exhausted(dl):
                    break
                transport_attempts += 1
                attempt_started = time.monotonic()
                try:
                    model = self.build_chat(leg.provider, leg.model_id, timeout_s)
                    leg_text = self._invoke_once(model, messages, timeout_s, cancel_event)
                except Exception as exc:  # noqa: BLE001 - central classification point
                    if _DCJ is not None and isinstance(exc, _DCJ):
                        raise
                    if _cancel_requested(cancel_event):
                        if _DCJ is not None:
                            raise _DCJ(str(exc) or "cancelled", stage=task.value) from exc
                    latency_ms = (time.monotonic() - attempt_started) * 1000.0
                    category = classify_exception(exc)
                    retryable = category not in NON_RETRYABLE_CATEGORIES and _is_transient(category, exc)
                    if retryable and transport_attempts < 2:
                        breaker.record_failure(use_case, leg.model_id, endpoint)
                        try:
                            self._sleep_fn(_TRANSIENT_RETRY_BACKOFF_S)
                        except Exception as exc_sleep:
                            logger.debug("Router backoff sleep interrupted (%s).", type(exc_sleep).__name__)
                        timeout_s = self._attempt_timeout(leg, dl if dl is not None else end, legs_left)
                        leg_text = None
                        continue
                    breaker.record_failure(use_case, leg.model_id, endpoint)
                    receipts.append(
                        RouteReceipt(task.value, leg.provider, leg.model_id, transport_attempts,
                                     category.value, "failure", latency_ms, remaining_ms, False, 1)
                    )
                    leg_text = None
                    break
                break
            if leg_text is None:
                legs_left -= 1
                continue
            latency_ms = (time.monotonic() - attempt_started) * 1000.0
            parsed = extract_json_object(leg_text)
            if parsed is not None:
                breaker.record_success(use_case, leg.model_id, endpoint)
                receipts.append(
                    RouteReceipt(task.value, leg.provider, leg.model_id, transport_attempts, None,
                                 "success", latency_ms, remaining_ms, False, 1)
                )
                return parsed, RouterResult(True, leg_text or "", leg.provider, leg.model_id,
                                           task.value, receipts, parse_attempts=1)
            # Malformed JSON: terminal for THIS leg. Record and advance to
            # the next candidate immediately — never re-invoke this leg.
            logger.warning("Model route invalid JSON: task=%s provider=%s model=%s — advancing",
                           task.value, leg.provider, leg.model_id)
            breaker.record_failure(use_case, leg.model_id, endpoint)
            receipts.append(
                RouteReceipt(task.value, leg.provider, leg.model_id, transport_attempts,
                             FailureCategory.MALFORMED_OUTPUT.value, "failure", latency_ms,
                             remaining_ms, False, 1)
            )
            legs_left -= 1

        last_category = FailureCategory.MALFORMED_OUTPUT
        for receipt in reversed(receipts):
            if receipt.failure_category and receipt.outcome == "failure":
                try:
                    last_category = FailureCategory(receipt.failure_category)
                except ValueError:
                    last_category = FailureCategory.UNKNOWN
                break
        final_result = RouterResult(False, "", None, None, task.value, receipts, last_category,
                                    "استجابة النموذج غير صالحة بصيغة JSON.")
        return None, final_result

    def invoke_text(
        self,
        task: TaskClass,
        system_prompt: str,
        user_text: str,
        deadline_monotonic: Optional[float] = None,
        candidates: Optional[Sequence[RouteCandidate]] = None,
        deadline: Any = None,
        cancel_event: Any = None,
    ) -> RouterResult:
        """String-prompt convenience wrapper around :meth:`invoke_chat`."""
        from langchain_core.messages import HumanMessage, SystemMessage

        return self.invoke_chat(
            task,
            [SystemMessage(content=system_prompt), HumanMessage(content=user_text)],
            deadline_monotonic=deadline_monotonic,
            candidates=candidates,
            deadline=deadline,
            cancel_event=cancel_event,
        )

    def invoke_embeddings(
        self,
        task: TaskClass,
        texts: Sequence[str],
        deadline_monotonic: Optional[float] = None,
        candidates: Optional[Sequence[RouteCandidate]] = None,
        deadline: Any = None,
        cancel_event: Any = None,
    ) -> RouterResult:
        """Embed via the task's NVIDIA primary ONLY (never mix dimensions).

        The separate-collection rule (routing_table) guarantees a single leg;
        any extra legs are ignored so a fallback vector space can never leak
        into the primary collection. Hierarchical ``deadline``/``cancel_event``
        are honored: cancellation raises typed ``DeadlineCancelledError``
        (never recorded as a model failure) and no new attempt starts once
        the remaining-time budget (reserve preserved) is exhausted.
        """
        dl, cancel_event = _coerce_router_deadline(deadline, deadline_monotonic, cancel_event, label=task.value)
        end = self._deadline_end(dl, deadline_monotonic)
        try:
            from sard.agent.deadline import DeadlineCancelledError as _DCE
        except Exception:
            _DCE = None  # type: ignore[assignment]
        legs = self._legs(task, candidates)
        primary = [leg for leg in legs if leg.provider == "nvidia"][:1]
        if task not in (TaskClass.EMBED_TEXT, TaskClass.EMBED_MULTIMODAL) or not primary:
            return self._deterministic(task, "No validated embedding route for this task class yet.")
        if _cancel_requested(cancel_event):
            if _DCE is not None:
                raise _DCE("cancelled before embedding", stage=task.value)
            return RouterResult(False, task_class=task.value, receipts=[],
                                failure_category=FailureCategory.TIMEOUT)
        if dl is not None and _reserve_exhausted(dl):
            return RouterResult(False, task_class=task.value, receipts=[],
                                failure_category=FailureCategory.TIMEOUT)
        leg = primary[0]
        use_case = self._breaker_use_case(task, leg.provider)
        endpoint = "hosted"
        remaining_ms = _remaining_ms(dl) if dl is not None else (
            None if end is None else (end - time.monotonic()) * 1000.0
        )
        # Half-open single flight: a losing concurrent probe skips without
        # touching the provider.
        allowed, _is_probe = self._breaker_allows(task, leg.provider, leg.model_id, endpoint)
        if not allowed:
            receipt = RouteReceipt(task.value, leg.provider, leg.model_id, 0,
                                   FailureCategory.MODEL_UNAVAILABLE.value, "skipped_circuit_open",
                                   0.0, remaining_ms, True, 0)
            return RouterResult(False, task_class=task.value, receipts=[receipt],
                                failure_category=FailureCategory.MODEL_UNAVAILABLE)
        timeout_s = self._attempt_timeout(leg, dl if dl is not None else end, 1)
        provider = self._providers.get(leg.provider)
        if provider is None:
            return self._deterministic(task, "Embedding provider unavailable.")
        attempts = 0
        while attempts < 2:
            if _cancel_requested(cancel_event):
                if _DCE is not None:
                    raise _DCE("cancelled during embedding", stage=task.value)
                break
            if dl is not None and _reserve_exhausted(dl):
                receipt = RouteReceipt(task.value, leg.provider, leg.model_id, attempts,
                                       FailureCategory.TIMEOUT.value, "failure", 0.0,
                                       remaining_ms, False, 0)
                return RouterResult(False, task_class=task.value, receipts=[receipt],
                                    failure_category=FailureCategory.TIMEOUT)
            attempts += 1
            started = time.monotonic()
            try:
                model = provider.build_embeddings(leg.model_id, timeout_s)
                future = _SHARED_EXECUTOR.submit(model.embed_documents, list(texts))
                # Poll so cancel_event abandons promptly; shrink waits to the
                # remaining budget.
                vectors: Any = None
                elapsed = 0.0
                while True:
                    if _cancel_requested(cancel_event):
                        future.cancel()
                        if _DCE is not None:
                            raise _DCE("cancelled during embedding", stage=task.value)
                        raise TimeoutError("cancelled during embedding")
                    slice_s = min(0.05, max(0.005, float(timeout_s) - elapsed))
                    try:
                        vectors = future.result(timeout=slice_s)
                        break
                    except concurrent.futures.TimeoutError:
                        elapsed = time.monotonic() - started
                        if elapsed >= float(timeout_s):
                            future.cancel()
                            raise
                        if dl is not None and _reserve_exhausted(dl):
                            future.cancel()
                            raise TimeoutError("deadline exceeded during embedding (reserve preserved)")
                        continue
            except concurrent.futures.TimeoutError:
                category = FailureCategory.TIMEOUT
                latency_ms = (time.monotonic() - started) * 1000.0
                if attempts < 2:
                    self._breaker_for(task).record_failure(use_case, leg.model_id, endpoint)
                    timeout_s = self._attempt_timeout(leg, dl if dl is not None else end, 1)
                    continue
                self._breaker_for(task).record_failure(use_case, leg.model_id, endpoint)
                receipt = RouteReceipt(task.value, leg.provider, leg.model_id, attempts, category.value,
                                       "failure", latency_ms, remaining_ms, False, 0)
                return RouterResult(False, task_class=task.value, receipts=[receipt], failure_category=category)
            except Exception as exc:  # noqa: BLE001 - central classification point
                if _DCE is not None and isinstance(exc, _DCE):
                    raise
                if _cancel_requested(cancel_event):
                    if _DCE is not None:
                        raise _DCE(str(exc) or "cancelled", stage=task.value) from exc
                category = classify_exception(exc)
                latency_ms = (time.monotonic() - started) * 1000.0
                retryable = category not in NON_RETRYABLE_CATEGORIES and _is_transient(category, exc)
                if retryable and attempts < 2:
                    self._breaker_for(task).record_failure(use_case, leg.model_id, endpoint)
                    timeout_s = self._attempt_timeout(leg, dl if dl is not None else end, 1)
                    continue
                self._breaker_for(task).record_failure(use_case, leg.model_id, endpoint)
                receipt = RouteReceipt(task.value, leg.provider, leg.model_id, attempts, category.value,
                                       "failure", latency_ms, remaining_ms, False, 0)
                return RouterResult(False, task_class=task.value, receipts=[receipt], failure_category=category)
            else:
                latency_ms = (time.monotonic() - started) * 1000.0
                self._breaker_for(task).record_success(use_case, leg.model_id, endpoint)
                receipt = RouteReceipt(task.value, leg.provider, leg.model_id, attempts, None,
                                       "success", latency_ms, remaining_ms, False, 0)
                return RouterResult(True, "", leg.provider, leg.model_id, task.value, [receipt], data=vectors)
        return self._deterministic(task, "Embedding route exhausted.")  # pragma: no cover

    def _deterministic(self, task: TaskClass, reason: str) -> RouterResult:
        receipt = RouteReceipt(
            task_class=task.value if isinstance(task, TaskClass) else str(task),
            provider="deterministic",
            model="deterministic-fallback",
            attempt=0,
            failure_category=FailureCategory.MODEL_UNAVAILABLE.value,
            outcome="unsupported",
            latency_ms=0.0,
            deadline_remaining_ms=None,
            breaker_skipped=False,
            parse_attempts=0,
        )
        task_name = task.value if isinstance(task, TaskClass) else str(task)
        return RouterResult(
            success=False,
            task_class=task_name,
            receipts=[receipt],
            failure_category=FailureCategory.MODEL_UNAVAILABLE,
            error_message=reason,
            deterministic_fallback=True,
        )


def route_chat_factory(model_id: str, settings: Any = None, timeout_s: Optional[float] = None) -> Any:
    """Legacy ``(model_id, settings) -> model`` factory routing via providers.

    Canonical OpenRouter IDs (containing ``/``) build through
    :class:`OpenRouterProvider`; every other ID delegates to the existing
    NVIDIA factory unchanged, so current routes behave exactly as before.
    """
    router = ModelRouter()
    timeout = float(timeout_s) if timeout_s else 6.0
    try:
        if settings is not None and getattr(settings, "request_timeout_seconds", None):
            timeout = min(timeout, float(settings.request_timeout_seconds))
    except (TypeError, ValueError):
        timeout = float(timeout_s) if timeout_s else 6.0
    return router.build_chat_for_model(model_id, timeout)


__all__ = [
    "ModelRouter",
    "RouteReceipt",
    "RouterResult",
    "provider_name_for_model",
    "route_chat_factory",
    "FAST_TASKS",
]
