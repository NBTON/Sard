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

_TRANSIENT_5XX_MARKERS = ("500", "502", "503", "504", "service unavailable", "bad gateway", "overloaded")

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


def _is_transient(category: FailureCategory, exc: BaseException) -> bool:
    if category in (FailureCategory.TIMEOUT, FailureCategory.RATE_LIMIT):
        return True
    if category is FailureCategory.MODEL_UNAVAILABLE:
        text = f"{type(exc).__name__} {exc}".lower()
        return any(marker in text for marker in _TRANSIENT_5XX_MARKERS)
    return False


def provider_name_for_model(model_id: str) -> str:
    """Return the provider name for a model ID (pure function, no I/O).

    Canonical OpenRouter IDs are vendor/name namespaced (contain ``/``);
    bare NVIDIA NIM IDs delegate to the existing ChatNVIDIA factory.
    """
    from sard.config.routing_table import OPENROUTER_CANDIDATES

    if not model_id:
        return "nvidia"
    if model_id in OPENROUTER_CANDIDATES or "/" in model_id:
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
            except Exception:
                timeout = timeout_s
            return self.build_chat_for_model(model_id, timeout)

        return _factory

    # -- core execution ------------------------------------------------------
    def _legs(self, task: TaskClass, candidates: Optional[Sequence[RouteCandidate]]) -> list[RouteCandidate]:
        if candidates is not None:
            return list(candidates)
        try:
            return get_route(task)
        except Exception:
            return []

    def _attempt_timeout(self, leg: RouteCandidate, deadline_monotonic: Optional[float], legs_left: int) -> float:
        if deadline_monotonic is None:
            return max(_MIN_TIMEOUT_S, float(leg.deadline_s))
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            return _MIN_TIMEOUT_S
        share = remaining / max(1, legs_left)
        return max(_MIN_TIMEOUT_S, min(float(leg.deadline_s), share))

    def _invoke_once(self, model: Any, messages: Any, timeout_s: float) -> str:
        future = _SHARED_EXECUTOR.submit(model.invoke, messages)
        try:
            response = future.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise TimeoutError(f"Model invocation timed out after {timeout_s:.2f}s.") from exc
        text = _content_to_text(getattr(response, "content", ""))
        if not text.strip():
            raise FallbackClassifiedError(FailureCategory.MALFORMED_OUTPUT, "Model returned empty content.")
        return text

    def invoke_chat(
        self,
        task: TaskClass,
        messages: Any,
        deadline_monotonic: Optional[float] = None,
        candidates: Optional[Sequence[RouteCandidate]] = None,
    ) -> RouterResult:
        """Run one chat call through the task's ordered legs with receipts."""
        legs = self._legs(task, candidates)
        if not legs:
            return self._deterministic(task, "No validated model route for this task class yet.")

        breaker = self._breaker_for(task)
        receipts: list[RouteReceipt] = []
        legs_left = len(legs)

        for leg in legs:
            use_case = self._breaker_use_case(task, leg.provider)
            endpoint = "openrouter" if leg.provider == "openrouter" else "hosted"
            remaining_ms = (
                None if deadline_monotonic is None else (deadline_monotonic - time.monotonic()) * 1000.0
            )
            if breaker.is_open(use_case, leg.model_id, endpoint):
                receipts.append(
                    RouteReceipt(task.value, leg.provider, leg.model_id, 0, FailureCategory.MODEL_UNAVAILABLE.value,
                                 "skipped_circuit_open", 0.0, remaining_ms, True, 0)
                )
                legs_left -= 1
                continue

            timeout_s = self._attempt_timeout(leg, deadline_monotonic, legs_left)
            attempts = 0
            leg_done = False
            last_category: FailureCategory = FailureCategory.MODEL_UNAVAILABLE
            while attempts < 2 and not leg_done:
                attempts += 1
                attempt_started = time.monotonic()
                try:
                    model = self.build_chat(leg.provider, leg.model_id, timeout_s)
                    text = self._invoke_once(model, messages, timeout_s)
                except Exception as exc:  # noqa: BLE001 - central classification point
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
                        timeout_s = self._attempt_timeout(leg, deadline_monotonic, legs_left)
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
    ) -> tuple[Optional[dict], RouterResult]:
        """Invoke then parse JSON: 1 parse retry on the same leg, then next leg.

        Returns ``(parsed | None, result)`` and never raises; per-leg
        ``parse_attempts`` are recorded on that leg's receipt.
        """
        from sard.agent.util import extract_json_object

        legs = self._legs(task, candidates)
        if not legs:
            return None, self._deterministic(task, "No validated model route for this task class yet.")

        breaker = self._breaker_for(task)
        receipts: list[RouteReceipt] = []
        legs_left = len(legs)
        final_result = RouterResult(False, task_class=task.value)

        for leg in legs:
            use_case = self._breaker_use_case(task, leg.provider)
            endpoint = "openrouter" if leg.provider == "openrouter" else "hosted"
            remaining_ms = (
                None if deadline_monotonic is None else (deadline_monotonic - time.monotonic()) * 1000.0
            )
            if breaker.is_open(use_case, leg.model_id, endpoint):
                receipts.append(
                    RouteReceipt(task.value, leg.provider, leg.model_id, 0, FailureCategory.MODEL_UNAVAILABLE.value,
                                 "skipped_circuit_open", 0.0, remaining_ms, True, 0)
                )
                legs_left -= 1
                continue

            timeout_s = self._attempt_timeout(leg, deadline_monotonic, legs_left)
            parse_attempts = 0
            transport_attempts = 0
            leg_text: Optional[str] = None
            leg_failed_transport = False
            while parse_attempts < max_parse_attempts:
                parse_attempts += 1
                attempt_started = time.monotonic()
                try:
                    # (Re)invoke for every parse attempt: the retry asks the
                    # same leg once more before moving to the next model.
                    transport_attempts += 1
                    model = self.build_chat(leg.provider, leg.model_id, timeout_s)
                    leg_text = self._invoke_once(model, messages, timeout_s)
                except Exception as exc:  # noqa: BLE001 - central classification point
                    latency_ms = (time.monotonic() - attempt_started) * 1000.0
                    category = classify_exception(exc)
                    retryable = category not in NON_RETRYABLE_CATEGORIES and _is_transient(category, exc)
                    if retryable and transport_attempts < 2 and parse_attempts < max_parse_attempts:
                        breaker.record_failure(use_case, leg.model_id, endpoint)
                        try:
                            self._sleep_fn(_TRANSIENT_RETRY_BACKOFF_S)
                        except Exception as exc_sleep:
                            logger.debug("Router backoff sleep interrupted (%s).", type(exc_sleep).__name__)
                        timeout_s = self._attempt_timeout(leg, deadline_monotonic, legs_left)
                        parse_attempts -= 1  # transport retry does not consume the parse budget
                        leg_text = None
                        continue
                    breaker.record_failure(use_case, leg.model_id, endpoint)
                    receipts.append(
                        RouteReceipt(task.value, leg.provider, leg.model_id, transport_attempts,
                                     category.value, "failure", latency_ms, remaining_ms, False, parse_attempts)
                    )
                    leg_failed_transport = True
                    break
                latency_ms = (time.monotonic() - attempt_started) * 1000.0
                parsed = extract_json_object(leg_text or "")
                if parsed is not None:
                    breaker.record_success(use_case, leg.model_id, endpoint)
                    receipts.append(
                        RouteReceipt(task.value, leg.provider, leg.model_id, transport_attempts, None,
                                     "success", latency_ms, remaining_ms, False, parse_attempts)
                    )
                    return parsed, RouterResult(True, leg_text or "", leg.provider, leg.model_id,
                                               task.value, receipts, parse_attempts=parse_attempts)
                logger.warning("Model route invalid JSON: task=%s provider=%s parse_attempt=%d",
                               task.value, leg.provider, parse_attempts)
                timeout_s = self._attempt_timeout(leg, deadline_monotonic, legs_left)
                leg_text = None
            if leg_failed_transport:
                legs_left -= 1
                continue
            breaker.record_failure(use_case, leg.model_id, endpoint)
            receipts.append(
                RouteReceipt(task.value, leg.provider, leg.model_id, transport_attempts,
                             FailureCategory.MALFORMED_OUTPUT.value, "failure", 0.0,
                             remaining_ms, False, parse_attempts)
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
    ) -> RouterResult:
        """String-prompt convenience wrapper around :meth:`invoke_chat`."""
        from langchain_core.messages import HumanMessage, SystemMessage

        return self.invoke_chat(
            task,
            [SystemMessage(content=system_prompt), HumanMessage(content=user_text)],
            deadline_monotonic=deadline_monotonic,
            candidates=candidates,
        )

    def invoke_embeddings(
        self,
        task: TaskClass,
        texts: Sequence[str],
        deadline_monotonic: Optional[float] = None,
        candidates: Optional[Sequence[RouteCandidate]] = None,
    ) -> RouterResult:
        """Embed via the task's NVIDIA primary ONLY (never mix dimensions).

        The separate-collection rule (routing_table) guarantees a single leg;
        any extra legs are ignored so a fallback vector space can never leak
        into the primary collection.
        """
        legs = self._legs(task, candidates)
        primary = [leg for leg in legs if leg.provider == "nvidia"][:1]
        if task not in (TaskClass.EMBED_TEXT, TaskClass.EMBED_MULTIMODAL) or not primary:
            return self._deterministic(task, "No validated embedding route for this task class yet.")
        leg = primary[0]
        use_case = self._breaker_use_case(task, leg.provider)
        endpoint = "hosted"
        breaker = self._breaker_for(task)
        remaining_ms = None if deadline_monotonic is None else (deadline_monotonic - time.monotonic()) * 1000.0
        if breaker.is_open(use_case, leg.model_id, endpoint):
            receipt = RouteReceipt(task.value, leg.provider, leg.model_id, 0,
                                   FailureCategory.MODEL_UNAVAILABLE.value, "skipped_circuit_open",
                                   0.0, remaining_ms, True, 0)
            return RouterResult(False, task_class=task.value, receipts=[receipt],
                                failure_category=FailureCategory.MODEL_UNAVAILABLE)
        timeout_s = self._attempt_timeout(leg, deadline_monotonic, 1)
        provider = self._providers.get(leg.provider)
        if provider is None:
            return self._deterministic(task, "Embedding provider unavailable.")
        attempts = 0
        while attempts < 2:
            attempts += 1
            started = time.monotonic()
            try:
                model = provider.build_embeddings(leg.model_id, timeout_s)
                future = _SHARED_EXECUTOR.submit(model.embed_documents, list(texts))
                vectors = future.result(timeout=timeout_s)
            except concurrent.futures.TimeoutError:
                category = FailureCategory.TIMEOUT
                latency_ms = (time.monotonic() - started) * 1000.0
                if attempts < 2:
                    breaker.record_failure(use_case, leg.model_id, endpoint)
                    continue
                breaker.record_failure(use_case, leg.model_id, endpoint)
                receipt = RouteReceipt(task.value, leg.provider, leg.model_id, attempts, category.value,
                                       "failure", latency_ms, remaining_ms, False, 0)
                return RouterResult(False, task_class=task.value, receipts=[receipt], failure_category=category)
            except Exception as exc:  # noqa: BLE001 - central classification point
                category = classify_exception(exc)
                latency_ms = (time.monotonic() - started) * 1000.0
                retryable = category not in NON_RETRYABLE_CATEGORIES and _is_transient(category, exc)
                if retryable and attempts < 2:
                    breaker.record_failure(use_case, leg.model_id, endpoint)
                    continue
                breaker.record_failure(use_case, leg.model_id, endpoint)
                receipt = RouteReceipt(task.value, leg.provider, leg.model_id, attempts, category.value,
                                       "failure", latency_ms, remaining_ms, False, 0)
                return RouterResult(False, task_class=task.value, receipts=[receipt], failure_category=category)
            else:
                latency_ms = (time.monotonic() - started) * 1000.0
                breaker.record_success(use_case, leg.model_id, endpoint)
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
