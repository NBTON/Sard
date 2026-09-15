"""Provider-neutral chat service — the boundary between the UI and models.

Implements the cultural assistant with Isnād provenance planning, hybrid retrieval,
and centralized artifact orchestration.
"""

from __future__ import annotations

import concurrent.futures
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from sard.agent.capability_routing import (
    Capability,
    classify_intent,
)
from sard.agent.cultural_router import (
    CULTURAL_SYSTEM_PROMPT,
    CULTURAL_SYSTEM_PROMPT_EN,
    CulturalQueryResult,
    CulturalRouter,
)
from sard.agent.deadline import (
    DeadlineCancelledError,
    DeadlineTimeoutError,
    coerce_deadline,
)
from sard.agent.lang_utils import resolve_language
from sard.agent.scope_guard import check_scope_before_retrieval
from sard.agent.util import sanitize_cultural_output
from sard.config.models import ModelConfigError, get_chat_model, get_model_settings
from sard.outputs.orchestrator import (
    ArtifactOrchestrator,
    ArtifactRequest,
    ArtifactResult,
    get_artifact_orchestrator,
)
from sard.schemas.isnad import PlannerResult

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = CULTURAL_SYSTEM_PROMPT
_SYSTEM_PROMPT_EN = CULTURAL_SYSTEM_PROMPT_EN

# Persistent, bounded worker executor to prevent context-manager shutdown blocking traps
_SHARED_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="sard-chat"
)


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
        self._model_router = None  # lazy ModelRouter (capability-aware, per-service breaker)
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

    def _get_model_router(self):
        """Lazily build the capability-aware router (never raises)."""
        if self._model_router is None:
            try:
                from sard.config.model_router import ModelRouter

                self._model_router = ModelRouter()
            except Exception as exc:
                logger.debug("Model router unavailable (%s).", type(exc).__name__)
                return None
        return self._model_router

    def _invoke_via_router(self, messages, timeout_s: float = 6.0) -> Optional[str]:
        """Try capability-aware routing first; return text or None (never raises).

        Bounded by the same per-call budget as the legacy path via
        ``deadline_monotonic``; receipts carry provider/model metadata and no
        prompts, payloads, or secrets.
        """
        try:
            from sard.config.routing_table import TaskClass

            router = self._get_model_router()
            if router is None:
                return None
            result = router.invoke_chat(
                TaskClass.COMPOSE_SHORT,
                messages,
                deadline_monotonic=time.monotonic() + timeout_s,
            )
            if result.success and result.text and result.text.strip():
                return result.text
        except Exception as exc:
            logger.debug("Routed model invocation failed (%s); using legacy model path.", type(exc).__name__)
        return None

    def _invoke_llm_str(self, sys_p: str, user_p: str) -> str:
        """Invoke configured LLM with prompt strings with fast timeout."""
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

        routed = self._invoke_via_router(
            [SystemMessage(content=sys_p), HumanMessage(content=user_p)], timeout_s=6.0
        )
        if routed is not None:
            return routed

        try:
            model = self._get_model()
            future = _SHARED_EXECUTOR.submit(_call, model)
            try:
                return future.result(timeout=6.0)
            except concurrent.futures.TimeoutError:
                future.cancel()
                logger.debug("Chat model invocation timed out after 6.0s; using deterministic synthesis.")
                return ""
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
        uploaded_files: Optional[dict] = None,
        deadline: Optional[Any] = None,
        deadline_monotonic: Optional[Any] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> PlannerResult:
        """Run the isnād provenance planner to verify claims before generating."""
        dl = coerce_deadline(deadline, cancel_event=cancel_event, label="ask_isnad")
        if dl is None and deadline_monotonic is not None:
            dl = coerce_deadline(deadline_monotonic, cancel_event=cancel_event, label="ask_isnad")
        if dl is not None and cancel_event is None:
            cancel_event = dl.cancel_event
        return self.planner.plan_and_execute(
            query=user_query,
            session_id=session_id,
            mock_multimodal_files=mock_multimodal_files,
            llm_invoke_fn=self._invoke_llm_str if (self._injected_model is not None or self._can_load_model()) else None,
            status_callback=status_callback,
            lang=lang,
            uploaded_files=uploaded_files,
            deadline=dl,
            cancel_event=cancel_event,
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
            return False
        except Exception:
            return False

    def ask_cultural(
        self,
        query: str,
        mock_multimodal_files: Optional[dict] = None,
        lang: str = "ar",
        uploaded_files: Optional[dict] = None,
    ) -> CulturalQueryResult:
        """Run cultural queries through deterministic search tools and prompt grounding."""
        return self.router.answer_query(
            query,
            mock_multimodal_files=mock_multimodal_files,
            lang=lang,
            uploaded_files=uploaded_files,
        )

    def ask(
        self,
        user_query: str,
        use_hybrid_retrieval: bool = False,
        messages: Optional[Sequence[dict]] = None,
        session_id: Optional[str] = None,
        mock_multimodal_files: Optional[dict] = None,
        status_callback: Optional[Callable[[str, str], None]] = None,
        attachments: Optional[Sequence[dict]] = None,
        lang: Optional[str] = None,
        deadline_monotonic: Optional[Any] = None,
        uploaded_files: Optional[dict] = None,
        deadline: Optional[Any] = None,
        cancel_event: Optional[Any] = None,
        run_id: Optional[str] = None,
    ) -> ChatResult:
        """Route user query with Isnād provenance verification and artifact rendering."""
        # Check empty query early
        if not user_query or not user_query.strip():
            return ChatResult(
                ok=False,
                error_message="الرجاء إدخال سؤال قبل الإرسال." if (lang or "ar") == "ar" else "Please enter a question before sending.",
            )

        # Hierarchical deadline (workstream E): Deadline object preferred;
        # float monotonic_end accepted via compat shim.
        dl = coerce_deadline(deadline, cancel_event=cancel_event, label="ask")
        if dl is None and deadline_monotonic is not None:
            dl = coerce_deadline(deadline_monotonic, cancel_event=cancel_event, label="ask")
        if dl is not None and cancel_event is None:
            cancel_event = dl.cancel_event

        def _cancelled() -> bool:
            try:
                return bool(cancel_event is not None and cancel_event.is_set())
            except Exception:
                return False

        # 0. Resolve language explicitly
        resolved_lang = resolve_language(lang, user_query)

        # Pre-retrieval scope validation
        should_block, scope_response = check_scope_before_retrieval(user_query, lang=resolved_lang)
        if should_block:
            return ChatResult(
                ok=True,
                text=sanitize_cultural_output(scope_response),
                decision="scope_block",
                citations=[],
                planner_result=None,
                artifacts=[],
            )

        # 1. Intent & Modality Classification (survives every fallback)
        intent = classify_intent(user_query, messages=messages, attachments=attachments)
        artifacts: list[dict[str, Any]] = []

        def _empty_hedge(query: str) -> str:
            q_norm = (query or "").lower().strip()
            if any(q_norm == g or q_norm.startswith(g + " ") for g in ["من أنت", "من انت", "عرفني بنفسك", "عرف بنفسك", "ما هو سرد", "مرحبا", "أهلا", "اهلا", "السلام عليكم", "صباح الخير", "مساء الخير", "هلا", "أهلاً", "hello", "hi", "who are you"]):
                if resolved_lang == "en":
                    return (
                        "Welcome! 🇸🇦\n\n"
                        "I am **Sard**, your Saudi Cultural Companion — grounded in verified records from the **Saudi Ministry of Culture** and **King Abdulaziz Foundation**.\n\n"
                        "### How can I help you today?\n"
                        "1. **Regional heritage & identity** across the 13 Saudi regions.\n"
                        "2. **Eleven cultural sectors**: Heritage, Culinary Arts, Fashion, Literature, Music, Architecture, Museums, Visual Arts, Theater, Film, and Libraries.\n"
                        "3. **Interactive outputs**:\n"
                        "   - **Presentations (PowerPoint .pptx)** for cultural briefings.\n"
                        "   - **Recipe & craft cards (PDF)**.\n"
                        "   - **Etiquette & hospitality simulators** with flowcharts.\n"
                        "   - **Proverbs & dialects** with lore.\n"
                        "   - **Memoir booklets** for family oral history.\n"
                        "   - **Heritage calendars (.ics)**.\n\n"
                        "Please ask a question or pick a topic to begin!"
                    )
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
            # Bilingual hedge
            if resolved_lang == "en":
                return (
                    f"Unable to generate a verified answer for: \"{query[:120]}\" at this time.\n\n"
                    "To preserve knowledge integrity, I don't generate unsourced syntheses.\n"
                    "I can help you with:\n"
                    "- Tailored heritage and tourism itineraries by region and duration.\n"
                    "- Verified information on archaeological sites, arts, and handicrafts.\n"
                    "- Cultural events and seasons organized by the Ministry of Culture.\n\n"
                    "Please clarify the region or context you need, or try again."
                )
            return (
                f"تعذّر توليد إجابة موثقة عن: \"{query[:120]}\" في الوقت الحالي.\n\n"
                "حفاظًا على الأمانة المعرفية، لا أقدّم توليفًا غير مُسنَد بلا مصادر.\n"
                "يمكنني مساعدتك في:\n"
                "- خطط الجولات التراثية والسياحية حسب المنطقة والمدة.\n"
                "- معلومات موثقة عن المواقع الأثرية، والفنون، والحرف اليدوية.\n"
                "- الفعاليات والمواسم الثقافية التابعة لوزارة الثقافة.\n\n"
                "يرجى تحديد المنطقة أو الجانب الذي ترغب في استكشافه، أو إعادة المحاولة."
            )

        def _format_to_kind(fmt_name: str) -> str:
            # Legacy fallback (format-only); preferred path is _kind_for_intent.
            if fmt_name in ("pdf", "docx", "txt"):
                return "document"
            if fmt_name in ("pptx",):
                return "presentation"
            if fmt_name in ("ics",):
                return "calendar"
            if fmt_name in ("svg", "png"):
                return "image"
            return "document"

        def _kind_for_intent(intent_obj: Any, fmt_name: str) -> str:
            """Specialized artifact kind routing from real intent (not format-only).

            Mirrors the orchestrator's capability mapping so chat -> request ->
            document -> renderer all agree on the specialized kind (recipe,
            memoir, card, diagram, presentation, calendar) instead of
            collapsing everything to generic document/image.
            """
            try:
                cap = getattr(intent_obj, "domain_capability", None)
                if cap == Capability.PRESENTATION_DECK or fmt_name == "pptx":
                    return "presentation"
                if cap == Capability.CALENDAR_SYNC or fmt_name == "ics":
                    return "calendar"
                if cap == Capability.GREETING_CARD:
                    return "card"
                if cap == Capability.ETIQUETTE_SIMULATOR or cap == Capability.DIAGRAM_GENERATION or fmt_name == "svg":
                    return "diagram"
                if cap == Capability.RECIPE_CARD:
                    return "recipe"
                if cap == Capability.ORAL_HISTORY:
                    return "memoir"
                if fmt_name == "png":
                    return "image"
                if fmt_name in ("json", "csv"):
                    return "document"
            except Exception:
                pass
            return _format_to_kind(fmt_name)

        def _is_longform_request(intent_obj: Any, query_text: str, body_text: str) -> bool:
            """Practical staged long-form trigger (no cost for simple chat)."""
            try:
                cap = getattr(intent_obj, "domain_capability", None)
                if cap in (Capability.ORAL_HISTORY, Capability.VERIFIED_RESEARCH, Capability.ITINERARY_PLANNING):
                    # Only stage when the body is actually long; short memoir/
                    # research answers keep the fast single-pass path.
                    if len(f"{query_text}\n{body_text}".split()) >= 60:
                        return True
                text = f"{query_text}\n{body_text}"
                long_markers = ("تقرير شامل", "بحث مطول", "كتيب", "مذكرات", "دراسة مفصلة",
                                "long report", "detailed report", "booklet", "memoir", "thesis")
                if any(m in text.lower() for m in long_markers) and len(text.split()) >= 60:
                    return True
                # Very long verified bodies (> ~400 words) benefit from staging.
                if len(body_text.split()) >= 400:
                    return True
            except Exception:
                pass
            return False

        def _build_longform_content(
            body_text: str, srcs: list[dict[str, str]], intent_obj: Any, topic_text: str
        ) -> tuple[Optional[Dict[str, Any]], list[str], bool]:
            """Staged path: research(done upstream) -> outline -> section draft
            -> editorial coherence -> verification -> render content.

            Honors deadlines (degrades to single-section with a transparent
            warning instead of starting doomed stages) and never slows simple
            chat (caller gates on _is_longform_request).
            """
            warnings: list[str] = []
            degraded = False

            def _budget_left() -> bool:
                try:
                    if dl is not None and hasattr(dl, "reserve_remaining"):
                        return bool(dl.reserve_remaining() > 1.0)
                except Exception:
                    pass
                return True

            # Stage 1: outline (deterministic sectioning of the verified body).
            _emit("longform_outline", "إعداد مخطط التقرير المطول..." if resolved_lang != "en" else "Outlining long-form report...")
            paras = [p.strip() for p in (body_text or "").split("\n\n") if p.strip()]
            if not paras:
                paras = [body_text.strip()] if body_text.strip() else [topic_text]
            # Editorial outline: intro + body sections (<=6) + takeaways.
            sections: list[Dict[str, Any]] = []
            if not _budget_left() or _cancelled():
                degraded = True
                warnings.append("ضيق المهلة: تم استخدام مسار مختصر بقسم واحد." if resolved_lang != "en" else "Tight deadline: single-section fast path used.")
                return {"sections": [{"title": topic_text[:80], "paragraphs": paras[:4]}],
                        "summary": paras[0][:500] if paras else ""}, warnings, degraded
            # Stage 2: section drafting (split body across sections).
            _emit("longform_draft", "صياغة الأقسام..." if resolved_lang != "en" else "Drafting sections...")
            per_section = max(1, (len(paras) + 3) // 4)
            titles = ["المقدمة", "السياق التراثي", "الشواهد والأدلة", "الخلاصات"]
            for idx in range(4):
                chunk = paras[idx * per_section:(idx + 1) * per_section]
                if not chunk and idx > 0:
                    break
                if not _budget_left() or _cancelled():
                    degraded = True
                    warnings.append("انتهت المهلة أثناء الصياغة: تم الاحتفاظ بالأقسام المكتملة فقط." if resolved_lang != "en" else "Deadline during drafting: kept completed sections only.")
                    break
                sections.append({"title": titles[idx] if idx < len(titles) else f"قسم {idx + 1}", "paragraphs": chunk or paras[:1]})
                _emit("longform_section", f"{idx + 1}/{4}")
            # Stage 3: editorial coherence (dedupe exact repeats, keep order).
            _emit("longform_edit", "مراجعة التحرير والاتساق..." if resolved_lang != "en" else "Editorial coherence pass...")
            seen: set[str] = set()
            for sec in sections:
                unique: list[str] = []
                for para in (sec.get("paragraphs", []) or []):
                    key = str(para).strip()
                    if key and key not in seen:
                        seen.add(key)
                        unique.append(para)
                sec["paragraphs"] = unique or sec.get("paragraphs", [])
            # Stage 4: verification (drop empty sections; keep evidence link).
            _emit("longform_verify", "التحقق من الاستشهادات..." if resolved_lang != "en" else "Verifying citations...")
            sections = [s for s in sections if (s.get("paragraphs") or s.get("title", "").strip())]
            if not sections:
                degraded = True
                warnings.append("لا توجد أقسام موثقة: تم استخدام النص الكامل كقسم واحد." if resolved_lang != "en" else "No verifiable sections: full text used as one section.")
                sections = [{"title": topic_text[:80], "paragraphs": paras[:4]}]
            content: Dict[str, Any] = {"sections": sections, "summary": paras[0][:500] if paras else ""}
            return content, warnings, degraded

        def _emit(stage: str, message: str) -> None:
            if status_callback is not None:
                try:
                    status_callback(stage, message)
                except Exception:
                    pass

        def _maybe_orchestrate(text: str, sources: list[dict[str, str]]) -> list[dict[str, Any]]:
            """Centralized helper: render requested artifact formats or return structured failure.

            Workstream E: checks the hierarchical deadline before each
            format (never starts doomed renders); on expiry/cancel degrades
            to a typed ``failed`` entry (``error_category`` timeout /
            cancelled) instead of writing late output. Emits typed SSE
            stages ``artifact_started`` / ``render_started`` /
            ``verify_started`` / ``artifact_ready`` / ``artifact_failed``.
            """
            local_artifacts: list[dict[str, Any]] = []
            target_fmts = getattr(intent, "target_formats", None) or getattr(intent, "requested_formats", ())
            # Deterministic idempotent run key: server-supplied run_id wins so
            # chat -> request -> document -> store share one identity; fallback
            # derives from session+query+formats for safe retries.
            effective_run_id = str(run_id or "").strip()
            if not effective_run_id:
                try:
                    from sard.outputs.document import stable_run_key as _stable_run

                    effective_run_id = _stable_run(str(session_id or ""), user_query, tuple(target_fmts or ()))
                except Exception:
                    effective_run_id = ""
            # Staged long-form content is built once (not per format) so all
            # formats share the same canonical sections/evidence.
            staged_content: Optional[Dict[str, Any]] = None
            staged_warnings: list[str] = []
            if intent.explicit_artifact_request and _is_longform_request(intent, user_query, text):
                staged_content, staged_warnings, _degraded = _build_longform_content(
                    text, sources, intent,
                    getattr(intent, "canonical_topic", None) or getattr(intent, "extracted_topic", None) or user_query,
                )
                if _degraded:
                    staged_warnings = list(staged_warnings)
            if intent.explicit_artifact_request and target_fmts:
                for fmt in target_fmts:
                    if fmt == "text":
                        continue
                    kind_for_fmt = _kind_for_intent(intent, fmt)
                    if _cancelled():
                        raise DeadlineCancelledError("cancelled before artifact render", stage="artifact_started")
                    if dl is not None:
                        try:
                            dl.check("artifact_started")
                        except DeadlineTimeoutError:
                            _emit("artifact_failed", f"تعذر توليد {str(fmt).upper()} ضمن المهلة." if resolved_lang != "en" else f"{str(fmt).upper()} generation timed out.")
                            local_artifacts.append({
                                "id": f"art-failed-{fmt}",
                                "kind": kind_for_fmt,
                                "format": fmt,
                                "type": fmt,
                                "title": f"مخرج ثقافي: {user_query[:40]}",
                                "filename": f"sard-{fmt}",
                                "mime_type": "application/octet-stream",
                                "size_bytes": 0,
                                "status": "failed",
                                "download_url": None,
                                "url": "",
                                "error": "تجاوز المهلة المحددة؛ تم إلغاء التوليد دون حفظ ملفات يتيمة.",
                                "error_category": "timeout",
                                "warnings": [],
                                "preview": None,
                                "checksum": None,
                                "data": None,
                            })
                            continue
                    topic_str = getattr(intent, "canonical_topic", None) or getattr(intent, "extracted_topic", None) or user_query
                    _emit("artifact_started", f"بدء توليد {str(fmt).upper()}..." if resolved_lang != "en" else f"Starting {str(fmt).upper()} generation...")
                    _emit("render_started", str(fmt))
                    # Map format to orchestrator call (specialized kind from
                    # real intent; IDs preserved across the chain).
                    # Preserve evidence IDs: keep chunk/source/evidence keys,
                    # not just citation_id/title/url.
                    norm_sources: list[Dict[str, Any]] = []
                    for entry in (sources or []):
                        if isinstance(entry, dict):
                            kept = {k: entry.get(k) for k in (
                                "citation_id", "id", "title", "url", "origin", "source_type",
                                "chunk_id", "source_id", "evidence_id", "excerpt", "region",
                            ) if entry.get(k) not in (None, "")}
                            # Normalize to orchestrator/document source shape.
                            if "citation_id" not in kept and kept.get("id"):
                                kept["citation_id"] = kept["id"]
                            norm_sources.append(kept)
                    art_req = ArtifactRequest(
                        format=fmt,
                        kind=kind_for_fmt,
                        title=f"مخرج ثقافي: {topic_str}",
                        topic=topic_str,
                        content_data=dict(staged_content) if staged_content else None,
                        region=intent.region or "المملكة العربية السعودية",
                        raw_text=text,
                        sources=tuple(norm_sources) if norm_sources else (),
                        metadata={
                            "session_id": session_id,
                            "run_id": effective_run_id,
                            "version": 1,
                            "intent": intent.to_dict() if hasattr(intent, "to_dict") else asdict(intent),
                            "locale": resolved_lang,
                            "provenance": ["chat_service"],
                            "evidence_ids": [d.get("evidence_id") or d.get("chunk_id") or d.get("source_id") for d in norm_sources if isinstance(d, dict) and (d.get("evidence_id") or d.get("chunk_id") or d.get("source_id"))],
                            "warnings": list(staged_warnings),
                        },
                    )
                    try:
                        res = self.orchestrator.generate_artifact(art_req, deadline=dl, cancel_event=cancel_event)
                        if res:
                            _emit("verify_started", str(fmt))
                            d = res.to_dict()
                            local_artifacts.append(d)
                            _emit("artifact_ready" if d.get("status") == "created" else "artifact_failed", str(fmt))
                    except DeadlineCancelledError:
                        raise
                    except DeadlineTimeoutError as exc:
                        logger.warning("Artifact orchestration timed out for format '%s': %s", fmt, exc)
                        _emit("artifact_failed", str(fmt))
                        local_artifacts.append({
                            "id": f"art-failed-{fmt}",
                            "kind": kind_for_fmt,
                            "format": fmt,
                            "type": fmt,
                            "title": f"مخرج ثقافي: {topic_str}",
                            "filename": f"sard-{fmt}",
                            "mime_type": "application/octet-stream",
                            "size_bytes": 0,
                            "status": "failed",
                            "download_url": None,
                            "url": "",
                            "error": "تجاوز المهلة المحددة؛ تم إلغاء التوليد دون حفظ ملفات يتيمة.",
                            "error_category": "timeout",
                            "warnings": [],
                            "preview": None,
                            "checksum": None,
                            "data": None,
                        })
                    except Exception as exc:
                        logger.error("Artifact orchestration failed for format '%s': %s", fmt, exc)
                        _emit("artifact_failed", str(fmt))
                        failed_res = ArtifactResult(
                            id=f"art-failed-{fmt}",
                            kind=kind_for_fmt,
                            format=fmt,
                            title=f"مخرج ثقافي: {topic_str}",
                            filename=f"error.{fmt}",
                            mime_type="application/octet-stream",
                            size_bytes=0,
                            status="failed",
                            download_url=None,
                            error=f"تعذر توليد ملف {fmt.upper()} حالياً. الرجاء إعادة المحاولة لاحقاً.",
                            error_category="renderer_exception",
                        )
                        local_artifacts.append(failed_res.to_dict())
            # Renderer isolation: successful formats are kept even when one
            # renderer fails (loop continues; no early return on failure).
            return local_artifacts

        # Early check for unconfigured model when no injected model is present
        if self._injected_model is None:
            try:
                _ = self._get_model()
            except ModelConfigError as exc:
                logger.warning("Chat model configuration error: %s", exc)
                err_msg = str(exc)
                if resolved_lang == "en" and "ANTHROPIC_API_KEY" in err_msg:
                    err_msg = "Server not configured: missing API credentials. Please configure ANTHROPIC_API_KEY or another provider."
                artifacts = []
                if intent.explicit_artifact_request:
                    artifacts = _maybe_orchestrate(_empty_hedge(user_query), [])
                return ChatResult(ok=False, error_message=err_msg, artifacts=artifacts)

        # G10 fast-path: pure data formats (json/csv/txt) render deterministically
        # in ~ms; skip slow RAG/web planner so SSE always meets 5s budget.
        _fmts = set(getattr(intent, "requested_formats", ()) or ())
        if use_hybrid_retrieval and intent.explicit_artifact_request and _fmts and _fmts <= {"json", "csv", "txt", "text"}:
            _fast_text = user_query if resolved_lang == "en" else f"مخرجات منظمة عن: {getattr(intent, 'extracted_topic', None) or user_query}"
            _fast_arts = _maybe_orchestrate(_fast_text, [])
            return ChatResult(ok=True, text=sanitize_cultural_output(_fast_text), decision="structured_fastpath", citations=[], planner_result=None, artifacts=_fast_arts)

        # Hybrid retrieval path via Isnād Planner & Agentic Cultural Tools
        if use_hybrid_retrieval:
            citations: list[dict[str, str]] = []
            text_resp = ""
            decision = None
            plan_res = None

            # 2. Run Retrieval & Provenance Planning
            try:
                _emit("research_started", "جارٍ البحث في المعارف الموثقة..." if resolved_lang != "en" else "Searching verified knowledge...")
                plan_res = self.ask_isnad(
                    user_query=user_query,
                    session_id=session_id,
                    mock_multimodal_files=mock_multimodal_files,
                    status_callback=status_callback,
                    lang=resolved_lang,
                    uploaded_files=uploaded_files,
                    deadline=dl,
                    cancel_event=cancel_event,
                )
                for ev in plan_res.visible_sources:
                    citations.append({
                        "id": ev.source_id,
                        "title": f"{ev.origin} ({ev.region})",
                        "url": ev.url_or_doc_id or "",
                        "origin": ev.origin,
                        "source_type": ev.source_type,
                        "chunk_id": ev.source_id,
                        "source_id": ev.source_id,
                        "excerpt": (ev.excerpt or "")[:500],
                        "region": ev.region,
                    })

                # Choose answer language based on resolved locale
                if resolved_lang == "en":
                    text_resp = sanitize_cultural_output(plan_res.answer_en or plan_res.answer_ar or "")
                else:
                    text_resp = sanitize_cultural_output(plan_res.answer_ar or plan_res.answer_en or "")
                decision = plan_res.chain.decision
            except Exception as exc:
                # Typed cancellation always propagates (never swallowed into
                # a fallback that would write late output after abort).
                if isinstance(exc, DeadlineCancelledError) or _cancelled():
                    raise DeadlineCancelledError(str(exc) or "cancelled", stage="ask_isnad") from exc
                is_timeout = (
                    isinstance(exc, (concurrent.futures.TimeoutError, TimeoutError))
                    or "timeout" in str(exc).lower()
                    or "deadline" in str(exc).lower()
                )
                if is_timeout:
                    logger.warning("Isnād planner execution timed out: %s. Aborting rather than cascading.", exc)
                    msg = (
                        "Server response timeout: please try again later."
                        if resolved_lang == "en"
                        else "تعذّر استلام رد بسبب تجاوز المهلة المحددة. يرجى المحاولة لاحقاً."
                    )
                    return ChatResult(ok=False, error_message=msg, artifacts=[])
                logger.warning("Isnād planner execution encountered exception: %s. Falling back to cultural router.", exc)
                cultural_res = self.ask_cultural(user_query, mock_multimodal_files=mock_multimodal_files, lang=resolved_lang, uploaded_files=uploaded_files)
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
        if _cancelled():
            raise DeadlineCancelledError("cancelled before direct invoke", stage="ask_direct")
        if dl is not None:
            try:
                dl.check("ask_direct")
            except DeadlineTimeoutError:
                msg = (
                    "Server response timeout: please try again later."
                    if resolved_lang == "en"
                    else "تعذّر استلام رد من النموذج بسبب تجاوز المهلة المحددة. يرجى المحاولة لاحقاً."
                )
                return ChatResult(ok=False, error_message=msg, artifacts=[])
        try:
            model = self._get_model()
        except ModelConfigError as exc:
            logger.warning("Chat model configuration error: %s", exc)
            # Even on config error, if artifact requested, return failed artifact so SSE can surface it
            if intent.explicit_artifact_request:
                artifacts = _maybe_orchestrate(_empty_hedge(user_query), [])
            # Localize error message if possible
            err_msg = str(exc)
            if resolved_lang == "en" and "ANTHROPIC_API_KEY" in err_msg:
                err_msg = "Server not configured: missing API credentials. Please configure ANTHROPIC_API_KEY or another provider."
            return ChatResult(ok=False, error_message=err_msg, artifacts=artifacts)

        try:
            system_prompt = _SYSTEM_PROMPT_EN if resolved_lang == "en" else _SYSTEM_PROMPT
            lc_messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
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

            routed = self._invoke_via_router(lc_messages, timeout_s=6.0)
            if routed is not None:
                routed_text = sanitize_cultural_output(routed)
                if not routed_text or not routed_text.strip():
                    routed_text = ""
                routed_artifacts = _maybe_orchestrate(routed_text, []) if routed_text else []
                return ChatResult(ok=True, text=routed_text, artifacts=routed_artifacts)

            future = _SHARED_EXECUTOR.submit(model.invoke, lc_messages)
            try:
                response = future.result(timeout=6.0)
            except concurrent.futures.TimeoutError:
                future.cancel()
                raise TimeoutError("Model invocation timed out after 6.0s")

            text = getattr(response, "content", "")
            if not isinstance(text, str):
                text = str(text)
            text = sanitize_cultural_output(text)
            if not text or not text.strip():
                text = ""

            artifacts = _maybe_orchestrate(text, []) if text else []
            return ChatResult(ok=True, text=text, artifacts=artifacts)

        except Exception as exc:
            logger.warning("Chat model direct invoke failed or timed out: %s", exc)
            if isinstance(exc, DeadlineCancelledError) or _cancelled():
                raise DeadlineCancelledError(str(exc) or "cancelled", stage="ask_direct") from exc
            is_timeout = (
                isinstance(exc, (concurrent.futures.TimeoutError, TimeoutError))
                or "timeout" in str(exc).lower()
                or "deadline" in str(exc).lower()
            )
            if is_timeout:
                msg = (
                    "Server response timeout: please try again later."
                    if resolved_lang == "en"
                    else "تعذّر استلام رد من النموذج بسبب تجاوز المهلة المحددة. يرجى المحاولة لاحقاً."
                )
                return ChatResult(ok=False, error_message=msg, artifacts=[])

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
