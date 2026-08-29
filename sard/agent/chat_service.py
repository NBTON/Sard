"""Provider-neutral chat service — the boundary between the UI and models.

Implements the cultural assistant with Isnād provenance planning, hybrid retrieval,
and centralized artifact orchestration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from sard.agent.capability_routing import (
    Capability,
    StructuredIntent,
    classify_intent,
)
from sard.agent.cultural_router import (
    CULTURAL_SYSTEM_PROMPT,
    CulturalQueryResult,
    CulturalRouter,
    RetrievalDecision,
)
from sard.agent.util import sanitize_cultural_output
from sard.config.models import ModelConfigError, get_chat_model, get_model_settings
from sard.outputs.orchestrator import (
    ArtifactOrchestrator,
    ArtifactRequest,
    ArtifactResult,
    get_artifact_orchestrator,
)
from sard.schemas.isnad import IsnadChain, PlannerResult

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = CULTURAL_SYSTEM_PROMPT


@dataclass(frozen=True)
class ChatResult:
    """Provider-agnostic result returned to the UI layer.

    Only ``ok``, ``text``, ``error_message``, optional ``decision``, and citations are exposed.
    """

    ok: bool
    text: str = ""
    error_message: str = ""
    decision: Optional[Any] = None
    citations: list[dict[str, str]] = field(default_factory=list)
    planner_result: Optional[PlannerResult] = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)


class ChatService:
    """Provider-neutral chat service with isnād provenance planning & RAG grounding.

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
        planner: Optional[Any] = None,
        orchestrator: Optional[ArtifactOrchestrator] = None,
    ):
        self._injected_model = chat_model
        self.router = router or CulturalRouter()
        self.orchestrator = orchestrator or get_artifact_orchestrator()
        if planner is not None:
            self.planner = planner
        else:
            from sard.planner.pipeline import IsnadPlanner

            self.planner = IsnadPlanner()

    def _get_model(self) -> BaseChatModel:
        if self._injected_model is not None:
            return self._injected_model
        return get_chat_model()

    def _invoke_llm_str(self, sys_p: str, user_p: str) -> str:
        """Invoke configured LLM with prompt strings."""
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

    def ask_isnad(
        self,
        user_query: str,
        session_id: Optional[str] = None,
        mock_multimodal_files: Optional[dict] = None,
        status_callback: Optional[Callable[[str, str], None]] = None,
        lang: str = "ar",
    ) -> PlannerResult:
        """Run the isnād provenance planner to verify claims before generating."""
        return self.planner.plan_and_execute(
            query=user_query,
            session_id=session_id,
            mock_multimodal_files=mock_multimodal_files,
            llm_invoke_fn=self._invoke_llm_str if (self._injected_model is not None or self._can_load_model()) else None,
            status_callback=status_callback,
            lang=lang,
        )

    def _can_load_model(self) -> bool:
        try:
            import os
            settings = get_model_settings()
            if settings.provider == "nvidia":
                return bool(os.environ.get("NVIDIA_API_KEY", "").strip())
            elif settings.provider == "openrouter":
                return bool(os.environ.get("OPENROUTER_API_KEY", "").strip())
            return True
        except Exception:
            return False

    def ask_cultural(
        self,
        user_query: str,
        mock_multimodal_files: Optional[dict] = None,
    ) -> CulturalQueryResult:
        """Run the hybrid cultural router and synthesize an answer grounded in RAG/Web/Multimodal sources."""
        return self.router.answer_query(
            user_query,
            llm_invoke_fn=self._invoke_llm_str if (self._injected_model is not None or self._can_load_model()) else None,
            mock_multimodal_files=mock_multimodal_files,
        )

    def ask(
        self,
        user_query: str,
        messages: Optional[list[dict]] = None,
        attachments: Optional[Sequence[Any]] = None,
        use_hybrid_retrieval: bool = False,
        session_id: Optional[str] = None,
        mock_multimodal_files: Optional[dict] = None,
        status_callback: Optional[Callable[[str, str], None]] = None,
    ) -> ChatResult:
        """Send a user query (or full conversation messages) to the configured assistant.

        Never raises — configuration errors and unexpected failures are
        captured and returned as a sanitized :class:`ChatResult`.
        """
        if not user_query or not user_query.strip():
            return ChatResult(
                ok=False,
                error_message="الرجاء إدخال سؤال قبل الإرسال.",
            )

        # 1. Intent & Modality Classification
        intent = classify_intent(user_query, messages=messages, attachments=attachments)
        artifacts: list[dict[str, Any]] = []

        # Hybrid retrieval path via Isnād Planner & Agentic Cultural Tools
        if use_hybrid_retrieval:
            citations: list[dict[str, str]] = []
            text_resp = ""
            decision = None
            plan_res = None

            # 2. Run Retrieval & Provenance Planning
            try:
                plan_res = self.ask_isnad(
                    user_query=user_query,
                    session_id=session_id,
                    mock_multimodal_files=mock_multimodal_files,
                    status_callback=status_callback,
                )
                for ev in plan_res.visible_sources:
                    citations.append({
                        "id": ev.source_id,
                        "title": f"{ev.origin} ({ev.region})",
                        "url": ev.url_or_doc_id or "",
                        "origin": ev.origin,
                        "source_type": ev.source_type,
                    })

                text_resp = sanitize_cultural_output(plan_res.answer_ar or plan_res.answer_en or "")
                decision = plan_res.chain.decision
            except Exception as exc:
                logger.warning("Isnād planner execution encountered exception: %s. Falling back to cultural router.", exc)
                cultural_res = self.ask_cultural(user_query, mock_multimodal_files=mock_multimodal_files)
                text_resp = sanitize_cultural_output(cultural_res.answer_text)
                decision = cultural_res.decision
                citations = cultural_res.citations

            # 3. Artifact Orchestration
            # Check if an artifact is explicitly requested or triggered by capability
            if intent.explicit_artifact_request or intent.domain_capability in (
                Capability.PRESENTATION_DECK,
                Capability.RECIPE_CARD,
                Capability.CALENDAR_SYNC,
                Capability.GREETING_CARD,
                Capability.ETIQUETTE_SIMULATOR,
                Capability.ORAL_HISTORY,
            ):
                if status_callback:
                    status_callback("generating_artifacts", f"جارٍ إعداد وتوليد المخرجات المطلوبة ({', '.join(intent.requested_formats)})...")

                try:
                    generated_artifacts = self.orchestrator.orchestrate_from_intent(
                        intent=intent,
                        raw_text=text_resp,
                        sources=tuple(citations),
                    )
                    for art_res in generated_artifacts:
                        artifacts.append(art_res.to_dict())
                except Exception as art_exc:
                    logger.exception("Artifact generation failed: %s", art_exc)
                    # Add failed artifact result to public contract
                    for fmt in intent.requested_formats:
                        if fmt != "text":
                            failed_res = ArtifactResult(
                                id=f"art-{session_id or 'failed'}",
                                kind="document",
                                format=fmt,
                                title=f"مخرج ثقافي: {intent.extracted_topic}",
                                filename=f"sard-{fmt}",
                                mime_type="application/octet-stream",
                                size_bytes=0,
                                status="failed",
                                download_url=None,
                                error=f"تعذر توليد ملف {fmt.upper()} حالياً. الرجاء إعادة المحاولة لاحقاً.",
                            )
                            artifacts.append(failed_res.to_dict())

            return ChatResult(
                ok=True,
                text=text_resp,
                decision=decision,
                citations=citations,
                planner_result=plan_res,
                artifacts=artifacts,
            )

        # Direct conversation path
        try:
            model = self._get_model()
        except ModelConfigError as exc:
            logger.warning("Chat model configuration error: %s", exc)
            return ChatResult(ok=False, error_message=str(exc))

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

            return ChatResult(ok=True, text=sanitize_cultural_output(text))

        except Exception as exc:
            logger.exception("Unexpected error while invoking the chat model: %s", exc)
            return ChatResult(
                ok=False,
                error_message=(
                    "حدث خطأ غير متوقع أثناء الاتصال بنموذج الدردشة. "
                    "الرجاء المحاولة مرة أخرى لاحقًا."
                ),
            )


def current_status_label() -> str:
    """Human-readable "<provider> / <model>" label for the UI status area."""
    try:
        settings = get_model_settings()
        return f"{settings.provider} / {settings.model_name}"
    except ModelConfigError:
        return "غير مُعدّ بعد (not configured)"
