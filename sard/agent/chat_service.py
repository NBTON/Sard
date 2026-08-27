"""Provider-neutral chat service — the boundary between the UI and models.

Implements the cultural assistant with Isnād provenance planning and hybrid retrieval.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from sard.agent.cultural_router import (
    CULTURAL_SYSTEM_PROMPT,
    CulturalQueryResult,
    CulturalRouter,
    RetrievalDecision,
)
from sard.agent.util import sanitize_cultural_output
from sard.config.models import ModelConfigError, get_chat_model, get_model_settings
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
    ):
        self._injected_model = chat_model
        self.router = router or CulturalRouter()
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
            get_model_settings()
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

        # Hybrid retrieval path via Isnād Planner & Agentic Cultural Tools
        if use_hybrid_retrieval:
            from sard.agent.capability_routing import Capability, classify_capability
            from sard.agent.tools.cultural_agentic_tools import (
                tool_advise_artisan_craft,
                tool_compile_oral_history_memoir,
                tool_conduct_verified_research,
                tool_create_greeting_card,
                tool_decode_dialect_or_proverb,
                tool_generate_presentation,
                tool_generate_recipe_or_craft_card,
                tool_simulate_etiquette_protocol,
                tool_sync_heritage_calendar,
            )

            cap = classify_capability(user_query)
            artifacts: list[dict[str, Any]] = []

            # Check for specialized tool triggers
            try:
                if cap == Capability.PRESENTATION_DECK:
                    if status_callback:
                        status_callback("generating_presentation", "جارٍ إعداد وتوليد شرائح العرض التقديمي الثقافي (PowerPoint)...")
                    pres_res = tool_generate_presentation(topic=user_query[:50], overview_text=user_query)
                    artifacts.append({
                        "type": "pptx",
                        "title": pres_res["title"],
                        "filename": pres_res["filename"],
                        "url": pres_res["download_url"],
                        "data": pres_res,
                    })
                elif cap == Capability.RECIPE_CARD:
                    if status_callback:
                        status_callback("generating_recipe", "جارٍ إعداد وتوليد بطاقة الطهي والحرفة التراثية المطبوعة (PDF)...")
                    rec_res = tool_generate_recipe_or_craft_card(item_name=user_query)
                    artifacts.append({
                        "type": "pdf",
                        "title": rec_res["title"],
                        "filename": rec_res["filename"],
                        "url": rec_res["download_url"],
                        "data": rec_res,
                    })
                elif cap == Capability.CALENDAR_SYNC:
                    if status_callback:
                        status_callback("syncing_calendar", "جارٍ استخراج وتوليد مواسم التقويم والمناسبات التراثية (.ics)...")
                    cal_res = tool_sync_heritage_calendar(query=user_query)
                    artifacts.append({
                        "type": "ics",
                        "title": "التقويم والمواسم التراثية السعودية",
                        "filename": cal_res["filename"],
                        "url": cal_res["download_url"],
                        "data": cal_res,
                    })
                elif cap == Capability.GREETING_CARD:
                    if status_callback:
                        status_callback("designing_card", "جارٍ تصميم بطاقة التهنئة التراثية ونظم الأبيات الشعرية...")
                    card_res = tool_create_greeting_card(custom_message=user_query)
                    artifacts.append({
                        "type": "card",
                        "title": card_res["title"],
                        "filename": card_res["filename"],
                        "url": card_res["download_url"],
                        "data": card_res,
                    })
                elif cap == Capability.ETIQUETTE_SIMULATOR:
                    if status_callback:
                        status_callback("simulating_etiquette", "جارٍ تشغيل محاكي الإتيكيت ورسم المخطط التدفقي...")
                    et_res = tool_simulate_etiquette_protocol(situation=user_query)
                    artifacts.append({
                        "type": "etiquette_flow",
                        "title": et_res["title"],
                        "filename": "etiquette-protocol.svg",
                        "url": "#",
                        "data": et_res,
                    })
                elif cap == Capability.DIALECT_PROVERB:
                    if status_callback:
                        status_callback("decoding_dialect", "جارٍ فك شفرة اللهجة واستخراج سالفة المثل وسياق استخدامه...")
                    dl_res = tool_decode_dialect_or_proverb(phrase_or_proverb=user_query)
                    artifacts.append({
                        "type": "dialect_lore",
                        "title": f"فك شفرة: {dl_res['proverb_title']}",
                        "filename": "dialect-lore.json",
                        "url": "#",
                        "data": dl_res,
                    })
                elif cap == Capability.ARTISAN_CRAFT:
                    if status_callback:
                        status_callback("consulting_craft", "جارٍ استخراج دليل أصالة الحرفة ومعايير كشف التقليد...")
                    craft_res = tool_advise_artisan_craft(craft_name=user_query)
                    artifacts.append({
                        "type": "artisan_craft",
                        "title": f"دليل أصالة: {craft_res['craft_name']}",
                        "filename": "craft-guide.json",
                        "url": "#",
                        "data": craft_res,
                    })
                elif cap == Capability.ORAL_HISTORY:
                    if status_callback:
                        status_callback("compiling_memoir", "جارٍ صياغة وتوثيق كتيب السيرة والتاريخ الشفوي (PDF)...")
                    mem_res = tool_compile_oral_history_memoir(
                        family_name=user_query[:30],
                        raw_notes=[{"topic": "جذور العائلة والنشأة الأولى", "content": user_query, "era": "الزمن الجميل"}],
                    )
                    artifacts.append({
                        "type": "pdf",
                        "title": mem_res["title"],
                        "filename": mem_res["filename"],
                        "url": mem_res["download_url"],
                        "data": mem_res,
                    })
                elif cap == Capability.VERIFIED_RESEARCH:
                    if status_callback:
                        status_callback("researching_citations", "جارٍ توثيق المراجع المعتمدة ومراجعة دارة الملك عبد العزيز وهيئة التراث...")
                    res_res = tool_conduct_verified_research(topic=user_query)
                    artifacts.append({
                        "type": "verified_research",
                        "title": f"توثيق معتمد: {user_query[:40]}",
                        "filename": "verified-research.json",
                        "url": "#",
                        "data": res_res,
                    })
            except Exception as tool_exc:
                logger.warning("Agentic tool auto-invocation notice: %s", tool_exc)

            try:
                plan_res = self.ask_isnad(
                    user_query=user_query,
                    session_id=session_id,
                    mock_multimodal_files=mock_multimodal_files,
                    status_callback=status_callback,
                )
                citations = []
                for ev in plan_res.visible_sources:
                    citations.append({
                        "id": ev.source_id,
                        "title": f"{ev.origin} ({ev.region})",
                        "url": ev.url_or_doc_id or "",
                        "origin": ev.origin,
                        "source_type": ev.source_type,
                    })

                text_resp = sanitize_cultural_output(plan_res.answer_ar or plan_res.answer_en or "")
                return ChatResult(
                    ok=True,
                    text=text_resp,
                    decision=plan_res.chain.decision,
                    citations=citations,
                    planner_result=plan_res,
                    artifacts=artifacts,
                )
            except Exception as exc:
                logger.warning("Isnād planner execution encountered exception: %s. Falling back to cultural router.", exc)
                cultural_res = self.ask_cultural(user_query, mock_multimodal_files=mock_multimodal_files)
                all_arts = artifacts + getattr(cultural_res, "artifacts", [])
                return ChatResult(
                    ok=True,
                    text=sanitize_cultural_output(cultural_res.answer_text),
                    decision=cultural_res.decision,
                    citations=cultural_res.citations,
                    artifacts=all_arts,
                )

        try:
            model = self._get_model()
        except ModelConfigError as exc:
            logger.warning("Chat model configuration error: %s", exc)
            return ChatResult(ok=False, error_message=str(exc))

        # Direct conversation path
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
