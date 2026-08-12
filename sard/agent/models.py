"""Centralized, provider-neutral semantic model service for agent nodes.

The only place ``sard/agent`` talks to a chat model.  Nodes never import
NVIDIA SDK, OpenAI, Anthropic, or ``sard.config.models`` directly — they call
:meth:`AgentModelService.invoke` / :meth:`AgentModelService.invoke_json`.

Resolved model IDs and primary/fallback order come from
``get_rag_settings().chat_route``; concrete models are built lazily via
``sard.config.rag.build_chat_model`` (or an injected factory so offline tests
can supply fakes).  Never retries auth/dimension-mismatch failures, reusing
Step 3's fallback policy instead of duplicating it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, Sequence, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage

from sard.agent.util import extract_json_object, pick_allowed
from sard.config.rag import RAGSettings, build_chat_model
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
    degraded: bool = False
    use_case: str = ""
    events: list[FallbackEvent] = field(default_factory=list)
    failure_category: Optional[FailureCategory] = None
    error_message: str = ""


@dataclass
class AgentModelService:
    settings: Optional[RAGSettings] = None
    chat_model_factory: Callable[..., Any] = build_chat_model
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
        kwargs = {}
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
    ) -> tuple[Optional[dict], AgentModelResponse]:
        """Call the model and return ``(parsed_json | None, response)``.

        Retries parsing up to ``max_structured_attempts`` times on invalid
        structured output, bounded by the route's candidate/retry machinery.
        Returns ``None`` (never raises) so callers can degrade deterministically.
        """
        attempt = 0
        final_response: AgentModelResponse = AgentModelResponse(
            success=False, use_case=use_case
        )
        while attempt < self.max_structured_attempts:
            attempt += 1
            response = self.invoke(use_case, system_prompt, user_text)
            final_response = response
            if not response.success:
                break
            parsed = extract_json_object(response.text)
            if parsed is None:
                final_response = AgentModelResponse(
                    success=False,
                    use_case=response.use_case,
                    model_used=response.model_used,
                    degraded=response.degraded,
                    events=response.events,
                    failure_category=FailureCategory.MALFORMED_OUTPUT,
                    error_message="استجابة النموذج غير صالحة بصيغة JSON.",
                )
                continue
            allowed = tuple(allowed_keys)
            if allowed:
                parsed = pick_allowed(parsed, allowed)
            return parsed, final_response
        return None, final_response


_DEFAULT_BREAKER = CircuitBreaker()