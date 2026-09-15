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

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, Sequence, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage

from sard.agent.util import extract_json_object, pick_allowed
from sard.config.model_router import provider_name_for_model, route_chat_factory
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
        return self.circuit_breaker or _DEFAULT_BREAKER

    def invoke(
        self,
        use_case: str,
        system_prompt: str,
        user_text: str,
    ) -> AgentModelResponse:
        """Run one semantic chat call through primary-then-fallbacks."""
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

        candidates = self._candidates()
        selected: dict[str, Any] = {}

        def call(candidate: ModelCandidate) -> str:
            model = self.chat_model_factory(candidate.model_id, settings)
            response = model.invoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=user_text)]
            )
            content = _content_to_text(getattr(response, "content", ""))
            if not content.strip():
                raise FallbackClassifiedError(
                    FailureCategory.MALFORMED_OUTPUT, "Model returned empty content."
                )
            selected["model_id"] = candidate.model_id
            selected["provider"] = provider_name_for_model(candidate.model_id)
            selected["degraded"] = candidate.degraded
            return content

        try:
            text, events = run_with_fallback(
                use_case_key,
                candidates,
                call,
                max_retries_per_candidate=self.max_retries_per_candidate,
                circuit_breaker=self._breaker,
                sleep_fn=self.sleep_fn,
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
    ) -> tuple[Optional[dict], AgentModelResponse]:
        """Call the model and return ``(parsed_json | None, response)``.

        Retry-then-switch on invalid structured output: each candidate gets
        up to ``max_structured_attempts`` parse attempts before advancing to
        the next candidate, so a primary returning transport-successful but
        non-JSON output can no longer starve the fallbacks. Total
        transport-successful attempts are bounded by
        ``max_structured_attempts * num_candidates``; an optional outer
        ``deadline`` (a :class:`Deadline` or absolute monotonic float) stops
        new attempts once exhausted. Returns ``None`` (never raises) so
        callers can degrade deterministically (verification stays
        advisory-only).
        """
        from sard.agent.deadline import coerce_deadline

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
        try:
            per_candidate = max(0, int(self.max_structured_attempts))
        except (TypeError, ValueError):
            per_candidate = 2
        total_budget = per_candidate * len(candidates)
        if total_budget <= 0:
            return None, AgentModelResponse(
                success=False,
                use_case=use_case_key,
                error_message="استجابة النموذج غير صالحة بصيغة JSON.",
                failure_category=FailureCategory.MALFORMED_OUTPUT,
            )

        dl = coerce_deadline(deadline) if deadline is not None else None

        def call(candidate: ModelCandidate) -> str:
            model = self.chat_model_factory(candidate.model_id, settings)
            response = model.invoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=user_text)]
            )
            content = _content_to_text(getattr(response, "content", ""))
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
        attempts_done = 0
        for candidate in candidates:
            if attempts_done >= total_budget:
                break
            if dl is not None and dl.reserve_remaining() <= 0:
                break
            structured_left = per_candidate
            while structured_left > 0 and attempts_done < total_budget:
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
                    break
                attempts_done += 1
                structured_left -= 1
                all_events.extend(events)
                parsed = extract_json_object(text)
                if parsed is None:
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