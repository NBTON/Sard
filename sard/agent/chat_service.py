"""Provider-neutral chat service — the boundary between the UI and models.

Implements the cultural assistant with hybrid retrieval (rag_search + parallel_search/extract).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from sard.agent.cultural_router import (
    CULTURAL_SYSTEM_PROMPT,
    CulturalQueryResult,
    CulturalRouter,
    RetrievalDecision,
)
from sard.config.models import ModelConfigError, get_chat_model, get_model_settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = CULTURAL_SYSTEM_PROMPT


@dataclass(frozen=True)
class ChatResult:
    """Provider-agnostic result returned to the UI layer.

    Only ``ok``, ``text``, ``error_message``, and optional ``decision`` are exposed.
    """

    ok: bool
    text: str = ""
    error_message: str = ""
    decision: Optional[RetrievalDecision] = None
    citations: list[dict[str, str]] = field(default_factory=list)


class ChatService:
    """Provider-neutral chat service with hybrid cultural search & RAG grounding.

    A LangChain chat model can be injected directly (used by tests to avoid
    any network access or API key). When omitted, the service lazily builds
    one from environment configuration via the central model factory on each
    call, so switching ``MODEL_PROVIDER``/``MODEL_NAME`` takes effect without
    restarting long-lived state.
    """

    def __init__(
        self,
        chat_model: Optional[BaseChatModel] = None,
        router: Optional[CulturalRouter] = None,
    ):
        self._injected_model = chat_model
        self.router = router or CulturalRouter()

    def _get_model(self) -> BaseChatModel:
        if self._injected_model is not None:
            return self._injected_model
        return get_chat_model()

    def ask_cultural(self, user_query: str) -> CulturalQueryResult:
        """Run the hybrid cultural router and synthesize an answer grounded in RAG/Web sources."""
        def _invoke_llm(sys_p: str, user_p: str) -> str:
            if self._injected_model is not None:
                model = self._injected_model
                resp = model.invoke([SystemMessage(content=sys_p), HumanMessage(content=user_p)])
                content = getattr(resp, "content", "")
                return str(content) if not isinstance(content, str) else content
            try:
                model = self._get_model()
                resp = model.invoke([SystemMessage(content=sys_p), HumanMessage(content=user_p)])
                content = getattr(resp, "content", "")
                return str(content) if not isinstance(content, str) else content
            except Exception as exc:
                logger.debug("Chat model invocation failed (%s); using deterministic synthesis.", exc)
                return ""

        return self.router.answer_query(
            user_query,
            llm_invoke_fn=_invoke_llm if self._injected_model is not None else None,
        )


    def ask(
        self,
        user_query: str,
        messages: Optional[list[dict]] = None,
        use_hybrid_retrieval: bool = False,
    ) -> ChatResult:

        """Send a user query (or full conversation messages) to the configured chat model and return the reply.

        Never raises — configuration errors and unexpected failures are
        captured and returned as a sanitized :class:`ChatResult`.
        """
        if not user_query or not user_query.strip():
            return ChatResult(
                ok=False,
                error_message="الرجاء إدخال سؤال قبل الإرسال.",
            )

        try:
            model = self._get_model()
        except ModelConfigError as exc:
            logger.warning("Chat model configuration error: %s", exc)
            return ChatResult(ok=False, error_message=str(exc))

        # When an explicit model is injected (tests/custom), use direct LangChain invoke
        if self._injected_model is not None:
            try:
                lc_messages: list[BaseMessage] = [SystemMessage(content=_SYSTEM_PROMPT)]
                if messages:
                    for m in messages[:-1]:
                        role = m.get("role")
                        content = m.get("content", "").strip()
                        if not content:
                            continue
                        if role == "user":
                            lc_messages.append(HumanMessage(content=content))
                        elif role == "assistant":
                            lc_messages.append(AIMessage(content=content))
                lc_messages.append(HumanMessage(content=user_query))
                response = model.invoke(lc_messages)
                text = getattr(response, "content", "")
                if not isinstance(text, str):
                    text = str(text)
                return ChatResult(ok=True, text=text)
            except Exception:
                logger.exception("Unexpected error while invoking injected chat model")
                return ChatResult(
                    ok=False,
                    error_message=(
                        "حدث خطأ غير متوقع أثناء الاتصال بنموذج الدردشة. "
                        "الرجاء المحاولة مرة أخرى لاحقًا."
                    ),
                )

        # Hybrid retrieval path for standard chat calls
        if use_hybrid_retrieval:
            cultural_res = self.ask_cultural(user_query)
            return ChatResult(
                ok=True,
                text=cultural_res.answer_text,
                decision=cultural_res.decision,
                citations=cultural_res.citations,
            )

        try:
            # Direct conversation path
            lc_messages: list[BaseMessage] = [SystemMessage(content=_SYSTEM_PROMPT)]
            if messages:
                for m in messages[:-1]:
                    role = m.get("role")
                    content = m.get("content", "").strip()
                    if not content:
                        continue
                    if role == "user":
                        lc_messages.append(HumanMessage(content=content))
                    elif role == "assistant":
                        lc_messages.append(AIMessage(content=content))
            lc_messages.append(HumanMessage(content=user_query))

            response = model.invoke(lc_messages)
            text = getattr(response, "content", "")
            if not isinstance(text, str):
                text = str(text)

            return ChatResult(ok=True, text=text)


        except Exception:
            logger.exception("Unexpected error while invoking the chat model")
            return ChatResult(
                ok=False,
                error_message=(
                    "حدث خطأ غير متوقع أثناء الاتصال بنموذج الدردشة. "
                    "الرجاء المحاولة مرة أخرى لاحقًا."
                ),
            )


def current_status_label() -> str:
    """Human-readable "<provider> / <model>" label for the UI status area.

    Never raises; returns a friendly placeholder if configuration is
    missing or invalid so the UI can render a status area unconditionally.
    """
    try:
        settings = get_model_settings()
        return f"{settings.provider} / {settings.model_name}"
    except ModelConfigError:
        return "غير مُعدّ بعد (not configured)"
