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
        """Invoke configured LLM with prompt strings with fast timeout."""
        import concurrent.futures

        def _call(m):
            resp = m.invoke([SystemMessage(content=sys_p), HumanMessage(content=user_p)])
            content = getattr(resp, "content", "")
            return str(content) if not isinstance(content, str) else content

        if self._injected_model is not None:
            try:
                return _call(self._injected_model)
            except Exception as exc:
                logger.debug("Injected model failed (%s)", exc)
                return ""

        try:
            model = self._get_model()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_call, model)
                return future.result(timeout=6.0)
        except Exception as exc:
            logger.debug("Chat model invocation failed or timed out (%s); using deterministic synthesis.", exc)
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
            if settings.provider == "gemini":
                return bool(os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip())
            elif settings.provider == "openai":
                return bool(os.environ.get("OPENAI_API_KEY", "").strip())
            elif settings.provider == "anthropic":
                return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
            elif settings.provider == "nvidia":
                return bool(os.environ.get("NVIDIA_API_KEY", "").strip() or os.environ.get("NVIDIA_CHAT_BASE_URL", "").strip())
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

        # 1. Intent & Modality Classification (survives every fallback)
        intent = classify_intent(user_query, messages=messages, attachments=attachments)
        artifacts: list[dict[str, Any]] = []

        def _empty_hedge(query: str) -> str:
            q_norm = (query or "").lower().strip()
            if any(q_norm == g or q_norm.startswith(g + " ") for g in ["من أنت", "من انت", "عرفني بنفسك", "عرف بنفسك", "ما هو سرد", "مرحبا", "أهلا", "اهلا", "السلام عليكم", "صباح الخير", "مساء الخير", "هلا", "أهلاً", "hello", "hi", "who are you"]):
                return (
                    "أهلاً وسهلاً بك! 🇸🇦\n\n"
                    "أنا **سرد**، رفيقك الثقافي الذكي ومستشارك المعتمد لاستكشاف التراث والحضارة في المملكة العربية السعودية، "
                    "بمعارف موثقة مستندة إلى سجلات وهيئات **وزارة الثقافة السعودية** و**دارة الملك عبد العزيز**.\n\n"
                    "### 🏛️ كيف يمكنني مساعدتك اليوم؟\n"
                    "1. **المعارف والتراث الإقليمي**: استكشاف التراث والعمارة والأزياء والتقاليد عبر **مناطق المملكة الـ 13**.\n"
                    "2. **القطاعات الثقافية الـ 11**: التراث، فنون الطهي، الأزياء، الأدب، الموسيقى، العمارة، المتاحف، الفنون البصرية، المسرح، الأفلام، والمكتبات.\n"
                    "3. **المخرجات والأدوات التفاعلية**:\n"
                    "   - تصميم **عروض تقديمية (PowerPoint .pptx)** للإيجاز الثقافي.\n"
                    "   - إعداد **بطاقات الوصفات والحرف التراثية (PDF)**.\n"
                    "   - محاكاة **بروتوكولات الإتيكيت والضيافة والمجالس** ومخططات تدفقية.\n"
                    "   - فك شفرة **الأمثال واللهجات المحلية** وسرد قصصها.\n"
                    "   - توثيق **السير والتاريخ الشفوي العائلي** في كتيبات مصقولة.\n"
                    "   - مزامنة **المواسم الفلكية والمناسبات التراثية (.ics)**.\n\n"
                    "تفضل بطرح سؤالك أو اختر موضوعاً للبدء!"
                )
            # Explicit Arabic hedge, never empty string, never shrimp/Eastern/UNESCO canned article
            return (
                f"تعذّر توليد إجابة موثقة عن: \"{query[:120]}\" في الوقت الحالي.\n\n"
                "حفاظًا على الأمانة المعرفية، لا أقدّم توليفًا غير مُسنَد بلا مصادر.\n"
                "يمكنني مساعدتك في:\n"
                "- برامج ومسارات سياحية وتراثية مخصصة حسب المنطقة والمدة.\n"
                "- معلومات موثقة عن المواقع الأثرية والفنون والحرف اليدوية.\n"
                "- الفعاليات والمواسم الثقافية لوزارة الثقافة.\n\n"
                "يرجى توضيح المنطقة أو السياق المطلوب، أو إعادة المحاولة."
            )

        def _maybe_orchestrate(text_for_artifact: str, citations_for_artifact: list[dict[str, str]]) -> list[dict[str, Any]]:
            # Centralized artifact orchestration used in BOTH hybrid and direct paths.
            # Invariant: explicit artifact intent (requested_formats) always triggers orchestrator,
            # even when use_hybrid_retrieval is False or planner failed or model returned empty.
            # General PDF (SAUDI_CULTURAL_FACTUAL with pdf) must still render as generic document,
            # not as itinerary-only rendering – orchestrator handles kind via capability + format.
            needs_artifact = intent.explicit_artifact_request or intent.domain_capability in (
                Capability.PRESENTATION_DECK,
                Capability.RECIPE_CARD,
                Capability.CALENDAR_SYNC,
                Capability.GREETING_CARD,
                Capability.ETIQUETTE_SIMULATOR,
                Capability.ORAL_HISTORY,
            )
            # Also cover general artifact case: SAUDI_CULTURAL_FACTUAL with explicit format
            if not needs_artifact and intent.explicit_artifact_request:
                needs_artifact = True
            if not needs_artifact:
                return []
            if status_callback:
                try:
                    status_callback("generating_artifacts", f"جارٍ إعداد وتوليد المخرجات المطلوبة ({', '.join(intent.requested_formats)})...")
                except Exception:
                    pass
            local_artifacts: list[dict[str, Any]] = []
            try:
                generated = self.orchestrator.orchestrate_from_intent(
                    intent=intent,
                    raw_text=text_for_artifact or _empty_hedge(user_query),
                    sources=tuple(citations_for_artifact),
                )
                for art_res in generated:
                    local_artifacts.append(art_res.to_dict())
                # If orchestrator returned empty but intent requested formats, surface failed artifacts
                if not local_artifacts:
                    for fmt in intent.requested_formats:
                        if fmt != "text":
                            failed_res = ArtifactResult(
                                id=f"art-{session_id or 'failed'}-{fmt}",
                                kind="document",
                                format=fmt,
                                title=f"مخرج ثقافي: {intent.extracted_topic}",
                                filename=f"sard-{fmt}",
                                mime_type="application/octet-stream",
                                size_bytes=0,
                                status="failed",
                                download_url=None,
                                error=f"تعذر توليد ملف {fmt.upper()} حالياً. الرجاء إعادة المحاولة لاحقاً.",
                                error_category="orchestrator_empty",
                            )
                            local_artifacts.append(failed_res.to_dict())
            except Exception as art_exc:
                logger.exception("Artifact generation failed: %s", art_exc)
                for fmt in intent.requested_formats:
                    if fmt != "text":
                        failed_res = ArtifactResult(
                            id=f"art-{session_id or 'failed'}-{fmt}",
                            kind="document",
                            format=fmt,
                            title=f"مخرج ثقافي: {intent.extracted_topic}",
                            filename=f"sard-{fmt}",
                            mime_type="application/octet-stream",
                            size_bytes=0,
                            status="failed",
                            download_url=None,
                            error=f"تعذر توليد ملف {fmt.upper()} حالياً. الرجاء إعادة المحاولة لاحقاً.",
                            error_category="renderer_exception",
                        )
                        local_artifacts.append(failed_res.to_dict())
            return local_artifacts

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
                # planner_result stays None on fallback, but citations/text are preserved

            # Empty output must be explicit hedge, not empty string
            if not text_resp or not text_resp.strip():
                text_resp = _empty_hedge(user_query)
                if decision is None:
                    decision = "hedge"

            # 3. Artifact Orchestration — always via helper (BOTH paths)
            artifacts = _maybe_orchestrate(text_resp, citations)

            return ChatResult(
                ok=True,
                text=text_resp,
                decision=decision,
                citations=citations,
                planner_result=plan_res,
                artifacts=artifacts,
            )

        # Direct conversation path — MUST also support artifact intent
        try:
            model = self._get_model()
        except ModelConfigError as exc:
            logger.warning("Chat model configuration error: %s", exc)
            # Even on config error, if artifact requested, return failed artifact so SSE can surface it
            if intent.explicit_artifact_request:
                artifacts = _maybe_orchestrate(_empty_hedge(user_query), [])
            return ChatResult(ok=False, error_message=str(exc), artifacts=artifacts)

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

            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(model.invoke, lc_messages)
                response = future.result(timeout=6.0)
            text = getattr(response, "content", "")
            if not isinstance(text, str):
                text = str(text)
            text = sanitize_cultural_output(text)
            if not text or not text.strip():
                text = _empty_hedge(user_query)

            artifacts = _maybe_orchestrate(text, [])
            return ChatResult(ok=True, text=text, artifacts=artifacts)

        except Exception as exc:
            logger.warning("Chat model direct invoke failed or timed out: %s", exc)
            fallback_text = _empty_hedge(user_query)
            artifacts = _maybe_orchestrate(fallback_text, [])
            return ChatResult(ok=True, text=fallback_text, artifacts=artifacts)


def current_status_label() -> str:
    """Human-readable "<provider> / <model>" label for the UI status area."""
    try:
        settings = get_model_settings()
        return f"{settings.provider} / {settings.model_name}"
    except ModelConfigError:
        return "غير مُعدّ بعد (not configured)"
