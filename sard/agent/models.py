"""Centralized, provider-neutral semantic model service for agent nodes.

The only place ``sard/agent`` talks to a chat model.  Nodes never import
NVIDIA SDK, OpenAI, Anthropic, or ``sard.config.models`` directly — they call
:meth:`AgentModelService.invoke` / :meth:`AgentModelService.invoke_json`.

Resolved model IDs and primary/fallback order come from
``get_rag_settings().chat_route``; concrete models are built lazily via
:func:`sard.config.model_router.route_chat_factory`, which routes
OpenRouter-style IDs through the OpenRouter provider and every other ID
through the existing NVIDIA factory.  Never retries auth/dimension-mismatch
failures, reusing Step 3's fallback policy instead of duplicating it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, Sequence, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage

from sard.agent.util import extract_json_object, pick_allowed
from sard.config.model_router import provider_name_for_model, route_chat_factory
# Single-source reasoning-channel reader (thinking-mandatory endpoints put
# the answer in message.reasoning_content with empty content).
from sard.config.model_router import _reasoning_channel_text as _router_reasoning_text
from sard.config.rag import RAGSettings
from sard.rag.fallbacks import (
    AllCandidatesFailedError,
    CircuitBreaker,
    FallbackClassifiedError,
    FallbackEvent,
    FailureCategory,
    ModelCandidate,
    run_with_fallback,
)

T = TypeVar("T")

logger = logging.getLogger(__name__)


class ChatModelLike(Protocol):
    def invoke(self, messages: list[Any], **kwargs: Any) -> Any: ...


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


@dataclass
class AgentModelResponse:
    success: bool
    text: str = ""
    model_used: Optional[str] = None
    provider_used: Optional[str] = None
    degraded: bool = False
    use_case: str = ""
    events: list[FallbackEvent] = field(default_factory=list)
    failure_category: Optional[FailureCategory] = None
    error_message: str = ""


@dataclass
class AgentModelService:
    settings: Optional[RAGSettings] = None
    chat_model_factory: Callable[..., Any] = route_chat_factory
    circuit_breaker: Optional[CircuitBreaker] = None
    max_retries_per_candidate: int = 1
    max_structured_attempts: int = 2
    sleep_fn: Callable[[float], None] = time.sleep
    _owned_breaker: Optional[CircuitBreaker] = field(default=None, repr=False, compare=False)

    def _resolved_settings(self) -> Optional[RAGSettings]:
        if self.settings is not None:
            return self.settings
        try:
            from sard.config.rag import get_rag_settings

            return get_rag_settings()
        except Exception:
            return None

    def _candidates(self) -> list[ModelCandidate]:
        route = self._resolved_settings().chat_route
        base = self._resolved_settings().chat_base_url
        endpoint_type = "self_hosted" if base else "hosted"
        candidates = [
            ModelCandidate(model_id=route.primary, endpoint_type=endpoint_type, label="primary")
        ]
        for index, fallback in enumerate(route.fallbacks, start=1):
            candidates.append(
                ModelCandidate(
                    model_id=fallback,
                    endpoint_type=endpoint_type,
                    label=f"fallback_{index}",
                    degraded=True,
                )
            )
        return candidates

    @property
    def _breaker(self) -> CircuitBreaker:
        # Per-service breaker (never the process-global singleton by default)
        # so unrelated services/tests never share coherent-namespace buckets.
        # An explicitly injected circuit_breaker still wins (tests, routers).
        if self.circuit_breaker is not None:
            return self.circuit_breaker
        if self._owned_breaker is None:
            self._owned_breaker = CircuitBreaker()
        return self._owned_breaker

    def _coerce_call_deadline(
        self,
        deadline: Any = None,
        deadline_monotonic: Any = None,
        cancel_event: Any = None,
        label: str = "",
    ) -> Any:
        """Coerce hierarchical Deadline/cancel into one Deadline (or None).

        Accepts a Deadline, an absolute monotonic float, or None; the
        explicit ``deadline`` wins, legacy ``deadline_monotonic`` is honored
        when ``deadline`` is None. The caller's cancel_event is attached when
        the Deadline lacks one.
        """
        from sard.agent.deadline import coerce_deadline

        dl = None
        try:
            if deadline is not None:
                dl = coerce_deadline(deadline, cancel_event=cancel_event, label=label)
            elif deadline_monotonic is not None:
                dl = coerce_deadline(deadline_monotonic, cancel_event=cancel_event, label=label)
            elif cancel_event is not None:
                dl = coerce_deadline(None, cancel_event=cancel_event, label=label)
        except Exception as exc_coerce:
            logger.debug("Deadline coerce skipped (%s).", type(exc_coerce).__name__)
            dl = None
        if dl is None and cancel_event is not None:
            # Cancel-only callers still get cancellation between attempts via
            # run_with_fallback's cancel_event path.
            return None
        return dl

    def invoke(
        self,
        use_case: str,
        system_prompt: str,
        user_text: str,
        deadline: Any = None,
        deadline_monotonic: Any = None,
        cancel_event: Any = None,
        reserve_s: float = 0.0,
    ) -> AgentModelResponse:
        """Run one semantic chat call through primary-then-fallbacks.

        Hierarchical-deadline aware: ``deadline`` (Deadline or absolute
        monotonic float), legacy ``deadline_monotonic``, and ``cancel_event``
        are propagated to :func:`run_with_fallback`; no new candidate starts
        once ``remaining - reserve <= 0`` so the outer reserve is preserved.

        Cancellation is NEVER converted to a failure response: a set
        ``cancel_event`` (or a cancelled Deadline) raises typed
        :class:`DeadlineCancelledError` so graph/server layers keep
        cancellation distinguishable from timeout and never degrade or
        continue it as an ordinary model failure.
        """
        settings = self._resolved_settings()
        use_case_key = f"agent_{use_case}"
        if settings is None:
            return AgentModelResponse(
                success=False,
                use_case=use_case_key,
                error_message="تكوين النموذج غير متاح.",
                failure_category=FailureCategory.MODEL_UNAVAILABLE,
            )
        if not user_text or not user_text.strip():
            return AgentModelResponse(
                success=False,
                use_case=use_case_key,
                error_message="الطلب فارغ.",
                failure_category=FailureCategory.MALFORMED_OUTPUT,
            )

        dl = self._coerce_call_deadline(deadline, deadline_monotonic, cancel_event, label=use_case_key)
        if cancel_event is None and dl is not None:
            try:
                cancel_event = getattr(dl, "cancel_event", None)
            except Exception as exc_dl:
                logger.debug("Deadline cancel_event read skipped (%s).", type(exc_dl).__name__)
        if dl is not None:
            try:
                if dl.reserve_remaining() <= 0:
                    return AgentModelResponse(
                        success=False,
                        use_case=use_case_key,
                        events=[],
                        failure_category=FailureCategory.TIMEOUT,
                        error_message="تعذّر الوصول إلى نماذج التوليد المكوّنة.",
                    )
            except Exception as exc_budget:
                logger.debug("Deadline budget check skipped (%s).", type(exc_budget).__name__)

        candidates = self._candidates()
        selected: dict[str, Any] = {}

        def call(candidate: ModelCandidate) -> str:
            model = self.chat_model_factory(candidate.model_id, settings)
            response = model.invoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=user_text)]
            )
            content = _content_to_text(getattr(response, "content", ""))
            if not content.strip():
                content = _router_reasoning_text(response)
            if not content.strip():
                raise FallbackClassifiedError(
                    FailureCategory.MALFORMED_OUTPUT, "Model returned empty content."
                )
            selected["model_id"] = candidate.model_id
            selected["provider"] = provider_name_for_model(candidate.model_id)
            selected["degraded"] = candidate.degraded
            return content

        # Typed cancellation propagates (never a TIMEOUT response): the
        # graph guard converts it to a non-retryable cancelled node failure
        # so server layers stay distinguishable from timeout.
        try:
            text, events = run_with_fallback(
                use_case_key,
                candidates,
                call,
                max_retries_per_candidate=self.max_retries_per_candidate,
                circuit_breaker=self._breaker,
                sleep_fn=self.sleep_fn,
                deadline=dl,
                cancel_event=cancel_event,
                reserve_s=float(reserve_s or 0.0),
            )
        except AllCandidatesFailedError as exc:
            last = exc.events[-1].failure_category if exc.events else None
            return AgentModelResponse(
                success=False,
                use_case=use_case_key,
                events=exc.events,
                failure_category=last or FailureCategory.MODEL_UNAVAILABLE,
                error_message="تعذّر الوصول إلى نماذج التوليد المكوّنة.",
            )

        return AgentModelResponse(
            success=True,
            text=text,
            model_used=selected.get("model_id"),
            provider_used=selected.get("provider"),
            degraded=bool(selected.get("degraded")),
            use_case=use_case_key,
            events=events,
        )

    def invoke_json(
        self,
        use_case: str,
        system_prompt: str,
        user_text: str,
        allowed_keys: Sequence[str] = (),
        user_label: str = "",
        deadline: Any = None,
        deadline_monotonic: Any = None,
        cancel_event: Any = None,
        reserve_s: float = 0.0,
    ) -> tuple[Optional[dict], AgentModelResponse]:
        """Call the model and return ``(parsed_json | None, response)``.

        Invalid JSON advances IMMEDIATELY: a transport-successful but
        non-JSON response is a per-candidate terminal verdict — the same
        candidate is NEVER re-invoked for a parse retry. The next eligible
        candidate is tried instead, so one malformed primary cannot starve
        the fallbacks or burn quota on doomed re-asks.
        ``max_structured_attempts`` is accepted for signature compatibility
        and ignored. Transient transport failures still retry within
        ``max_retries_per_candidate`` via :func:`run_with_fallback`.

        An optional outer ``deadline`` (a :class:`Deadline` or absolute
        monotonic float), legacy ``deadline_monotonic``, and ``cancel_event``
        stop new attempts once exhausted (remaining-time budget enforced,
        reserve preserved). Model failures return ``None`` (never raise) so
        callers degrade deterministically; CANCELLATION raises typed
        :class:`DeadlineCancelledError` so graph/server layers keep it
        distinguishable from timeout and never continue it as an ordinary
        degraded failure.
        """
        settings = self._resolved_settings()
        use_case_key = f"agent_{use_case}"
        if settings is None:
            return None, AgentModelResponse(
                success=False,
                use_case=use_case_key,
                error_message="تكوين النموذج غير متاح.",
                failure_category=FailureCategory.MODEL_UNAVAILABLE,
            )
        if not user_text or not user_text.strip():
            return None, AgentModelResponse(
                success=False,
                use_case=use_case_key,
                error_message="الطلب فارغ.",
                failure_category=FailureCategory.MALFORMED_OUTPUT,
            )

        candidates = self._candidates()
        if not candidates:
            return None, AgentModelResponse(
                success=False,
                use_case=use_case_key,
                error_message="تعذّر الوصول إلى نماذج التوليد المكوّنة.",
                failure_category=FailureCategory.MODEL_UNAVAILABLE,
            )
        dl = self._coerce_call_deadline(deadline, deadline_monotonic, cancel_event, label=use_case_key)
        if cancel_event is None and dl is not None:
            try:
                cancel_event = getattr(dl, "cancel_event", None)
            except Exception as exc_dl2:
                logger.debug("Deadline cancel_event read skipped (%s).", type(exc_dl2).__name__)

        def call(candidate: ModelCandidate) -> str:
            model = self.chat_model_factory(candidate.model_id, settings)
            response = model.invoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=user_text)]
            )
            content = _content_to_text(getattr(response, "content", ""))
            if not content.strip():
                content = _router_reasoning_text(response)
            if not content.strip():
                raise FallbackClassifiedError(
                    FailureCategory.MALFORMED_OUTPUT, "Model returned empty content."
                )
            return content

        all_events: list[FallbackEvent] = []
        final_response = AgentModelResponse(
            success=False,
            use_case=use_case_key,
            failure_category=FailureCategory.MALFORMED_OUTPUT,
            error_message="استجابة النموذج غير صالحة بصيغة JSON.",
        )
        try:
            from sard.agent.deadline import DeadlineCancelledError as _DC2
        except Exception as exc_import:
            logger.debug("Deadline import skipped (%s).", type(exc_import).__name__)
            _DC2 = None  # type: ignore[assignment]

        def _raise_if_cancelled() -> None:
            flagged = False
            try:
                flagged = bool(cancel_event is not None and cancel_event.is_set())
            except Exception as exc_flag:
                logger.debug("Cancel flag read skipped (%s).", type(exc_flag).__name__)
            if flagged:
                if _DC2 is not None:
                    raise _DC2(f"cancelled during '{use_case_key}'", stage=use_case_key)
                raise TimeoutError(f"cancelled during '{use_case_key}'")

        for candidate in candidates:
            _raise_if_cancelled()
            if dl is not None and dl.reserve_remaining() <= 0:
                break
            try:
                text, events = run_with_fallback(
                    use_case_key,
                    [candidate],
                    call,
                    max_retries_per_candidate=self.max_retries_per_candidate,
                    circuit_breaker=self._breaker,
                    sleep_fn=self.sleep_fn,
                    deadline=dl,
                    cancel_event=cancel_event,
                    reserve_s=float(reserve_s or 0.0),
                )
            except AllCandidatesFailedError as exc:
                all_events.extend(exc.events)
                last = exc.events[-1].failure_category if exc.events else None
                final_response = AgentModelResponse(
                    success=False,
                    use_case=use_case_key,
                    events=list(all_events),
                    failure_category=last or FailureCategory.MODEL_UNAVAILABLE,
                    error_message="تعذّر الوصول إلى نماذج التوليد المكوّنة.",
                )
                # Transport failure on THIS candidate: advance to the next
                # candidate (continue the for loop — never stop the route).
                continue
            # Typed cancellation propagates (never a failure response):
            # run_with_fallback raises DeadlineCancelledError, which must
            # reach the graph guard, not be absorbed here.
            all_events.extend(events)
            parsed = extract_json_object(text)
            if parsed is None:
                # Malformed JSON is terminal for THIS candidate: record and
                # advance to the next candidate immediately — never re-invoke.
                final_response = AgentModelResponse(
                    success=False,
                    use_case=use_case_key,
                    model_used=candidate.model_id,
                    provider_used=provider_name_for_model(candidate.model_id),
                    degraded=bool(candidate.degraded),
                    events=list(all_events),
                    failure_category=FailureCategory.MALFORMED_OUTPUT,
                    error_message="استجابة النموذج غير صالحة بصيغة JSON.",
                )
                continue
            allowed = tuple(allowed_keys)
            if allowed:
                parsed = pick_allowed(parsed, allowed)
            return parsed, AgentModelResponse(
                success=True,
                text=text,
                model_used=candidate.model_id,
                provider_used=provider_name_for_model(candidate.model_id),
                degraded=bool(candidate.degraded),
                use_case=use_case_key,
                events=list(all_events),
            )
        return None, final_response


_DEFAULT_BREAKER = CircuitBreaker()