"""Provider-neutral chat service — the boundary between the UI and models.

Implements the cultural assistant with Isnād provenance planning, hybrid retrieval,
and centralized artifact orchestration.
"""

from __future__ import annotations

import concurrent.futures
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from sard.agent.capability_routing import (
    classify_intent,
)
from sard.agent.cultural_router import (
    CULTURAL_SYSTEM_PROMPT,
    CULTURAL_SYSTEM_PROMPT_EN,
    CulturalQueryResult,
    CulturalRouter,
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
from sard.rag.relevance import (
    relevance_details,
    requires_medical_qualification,
    strong_product_grounding,
)
from sard.agent.proposals import CulturalProposalResult, build_cultural_proposal_result

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = CULTURAL_SYSTEM_PROMPT
_SYSTEM_PROMPT_EN = CULTURAL_SYSTEM_PROMPT_EN

# Persistent, bounded worker executor to prevent context-manager shutdown blocking traps
_SHARED_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="sard-chat"
)

_HISTORY_TURNS = ("user", "assistant")
_MAX_HISTORY_TURNS = 12


def extract_session_history_turns(
    messages: Optional[Sequence[dict]],
    current_query: str,
    session_id: Optional[str],
    limit: int = _MAX_HISTORY_TURNS,
) -> list[dict[str, str]]:
    """Return validated same-session history turns for prompt context.

    Only bounded, non-empty prior ``user``/``assistant`` turns are kept.
    Client-provided ``role="system"`` messages are NEVER promoted: the
    server-owned cultural system prompt stays first and authoritative.
    Without an explicit ``session_id``, history is omitted entirely to
    prevent cross-session leakage. The trailing duplicate of the current
    query is dropped so it is not repeated. History is prompt context —
    never current-turn evidence.
    """

    if not session_id or not messages:
        return []
    prior = list(messages)
    if prior and str(prior[-1].get("content", "")).strip() == (current_query or "").strip():
        prior = prior[:-1]
    turns: list[dict[str, str]] = []
    for item in prior[-limit:]:
        role = str(item.get("role", "")).strip().lower()
        content = str(item.get("content", "")).strip()
        if role in _HISTORY_TURNS and content:
            turns.append({"role": role, "content": content})
    return turns


def resolve_followup_retrieval_query(
    user_query: str,
    history_turns: list[dict[str, str]],
) -> str:
    """Resolve an elliptical same-session follow-up to a retrievable query.

    When the current query carries no recognized topic (e.g. ``وماذا عن
    طريقة تقديمها؟``), the most recent prior user turn supplies the
    antecedent for *retrieval scoping only*. Retrieved evidence is still
    validated as current-turn evidence; history itself is never cited.
    Queries that already carry a topic are returned unchanged.
    """

    from sard.rag.relevance import query_profile

    if query_profile(user_query).topics:
        return user_query
    for turn in reversed(history_turns):
        if turn["role"] == "user":
            antecedent = turn["content"].strip()
            if antecedent:
                return f"{antecedent}\n{user_query}"
    return user_query


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
    proposal_result: CulturalProposalResult = field(default_factory=CulturalProposalResult)

    @property
    def proposals(self):
        """Compatibility shorthand for consumers interested only in proposals."""
        return self.proposal_result.proposals


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
    ) -> PlannerResult:
        """Run the isnād provenance planner to verify claims before generating."""
        return self.planner.plan_and_execute(
            query=user_query,
            session_id=session_id,
            mock_multimodal_files=mock_multimodal_files,
            llm_invoke_fn=self._invoke_llm_str if (self._injected_model is not None or self._can_load_model()) else None,
            status_callback=status_callback,
            lang=lang,
            uploaded_files=uploaded_files,
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

    def _filter_planner_result(self, query: str, result: PlannerResult) -> PlannerResult:
        """Remove planner evidence that does not match the current entity/mandate.

        The planner owns the classification and provenance stages, but its
        retriever can be replaced by a test or external boundary, and live web
        hits bypass the curated index. A final agent-side check keeps an
        unrelated Fashion/Crafts or regional record from becoming a citation.
        When every requested topic retains supporting evidence, the already
        generated answer is kept and only the disallowed sources are trimmed;
        when a topic loses all support, the synthesis is cleared so no removed
        evidence survives in prose and the caller emits an explicit hedge.
        """

        from sard.rag.relevance import query_profile

        evidence = list(result.chain.evidence or [])
        if not evidence:
            return result

        candidates: list[dict[str, Any]] = []
        for ev in evidence:
            raw: dict[str, Any] = {}
            memory = getattr(self.planner, "memory", None)
            l0 = getattr(memory, "l0", None)
            try:
                raw = l0.get_raw_ref(ev.raw_ref) or {} if l0 is not None else {}
            except Exception as exc:
                logger.debug("Planner raw evidence lookup skipped: %s", type(exc).__name__)
            raw_meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
            candidates.append(
                {
                    "title": raw.get("title") or ev.origin,
                    "source": ev.origin,
                    "chunk": ev.excerpt,
                    "metadata": {
                        "source_name": ev.origin,
                        "source_url": raw_meta.get("source_url") or raw.get("source_url") or "",
                        "citation_id": raw_meta.get("citation_id") or raw.get("citation_id") or ev.source_id,
                        "chunk_id": raw_meta.get("chunk_id") or raw.get("chunk_id") or ev.source_id,
                        "topic": raw_meta.get("topic") or raw.get("topic") or "",
                        "sector": raw_meta.get("sector") or raw.get("sector") or "",
                        "region": ev.region,
                        "region_code": raw_meta.get("region_code") or raw.get("region_code") or "",
                    },
                }
            )

        decisions = [relevance_details(query, candidate) for candidate in candidates]
        keep = [ev for ev, decision in zip(evidence, decisions) if decision["accepted"]]
        if len(keep) == len(evidence):
            return result

        if not keep:
            chain = result.chain.model_copy(
                update={
                    "evidence": [],
                    "atoms": [],
                    "score": "low",
                    "decision": "ask",
                    "missing": ["No entity- and mandate-matched evidence was found for the current query."],
                }
            )
            return result.model_copy(
                update={"chain": chain, "answer_ar": None, "answer_en": None, "visible_sources": []}
            )

        # Per-topic coverage: every requested topic must retain at least one
        # supporting evidence record, otherwise claims from the removed
        # evidence may linger in the generated prose.
        required_topics = set(query_profile(query).topics)
        covered_topics: set[str] = set()
        for decision in decisions:
            if decision["accepted"]:
                covered_topics.update(decision.get("matched_topics") or [])
        if required_topics and not required_topics <= covered_topics:
            kept_ids = {ev.source_id for ev in keep}
            chain = result.chain.model_copy(
                update={
                    "evidence": keep,
                    "atoms": [atom for atom in result.chain.atoms if set(atom.source_ids) & kept_ids],
                    "score": "low",
                    "decision": "ask",
                    "missing": ["The synthesized answer was cleared because filtering removed evidence."],
                }
            )
            return result.model_copy(
                update={
                    "chain": chain,
                    # Never leave claims from removed evidence in a response. A
                    # subsequent caller emits an explicit uncertainty hedge.
                    "answer_ar": None,
                    "answer_en": None,
                    "visible_sources": [ev for ev in result.visible_sources if ev.source_id in kept_ids],
                }
            )

        kept_ids = {ev.source_id for ev in keep}
        chain = result.chain.model_copy(
            update={
                "evidence": keep,
                "atoms": [atom for atom in result.chain.atoms if set(atom.source_ids) & kept_ids],
            }
        )
        return result.model_copy(
            update={
                "chain": chain,
                "visible_sources": [ev for ev in result.visible_sources if ev.source_id in kept_ids],
            }
        )

    @staticmethod
    def _medical_note(lang: str) -> str:
        if lang == "en":
            return (
                "\n\n> Note: references to healing or therapeutic benefits describe reported local beliefs or uses, "
                "not medical evidence or a treatment claim."
            )
        return (
            "\n\n> تنبيه: ما يرد عن الاستشفاء أو الفوائد العلاجية يصف معتقدات أو استخدامات محلية محتملة، "
            "وليس دليلاً طبياً على علاج مرض."
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
        deadline_monotonic: Optional[float] = None,
        uploaded_files: Optional[dict] = None,
    ) -> ChatResult:
        """Route user query with Isnād provenance verification and artifact rendering."""
        # Check empty query early
        if not user_query or not user_query.strip():
            return ChatResult(
                ok=False,
                error_message="الرجاء إدخال سؤال قبل الإرسال." if (lang or "ar") == "ar" else "Please enter a question before sending.",
            )

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
            if fmt_name in ("pdf", "docx", "txt"):
                return "document"
            if fmt_name in ("pptx",):
                return "presentation"
            if fmt_name in ("ics",):
                return "calendar"
            if fmt_name in ("svg", "png"):
                return "image"
            return "document"

        def _maybe_orchestrate(text: str, sources: list[dict[str, str]]) -> list[dict[str, Any]]:
            """Centralized helper: render requested artifact formats or return structured failure."""
            local_artifacts: list[dict[str, Any]] = []
            target_fmts = getattr(intent, "target_formats", None) or getattr(intent, "requested_formats", ())
            if intent.explicit_artifact_request and target_fmts:
                from sard.agent.capability_routing import Capability

                proposal_capability = intent.domain_capability in {
                    Capability.RECIPE_CARD,
                    Capability.ARTISAN_CRAFT,
                    Capability.ETIQUETTE_SIMULATOR,
                }
                proposal_is_grounded = strong_product_grounding(user_query, sources)
                for fmt in target_fmts:
                    if fmt == "text":
                        continue
                    topic_str = getattr(intent, "canonical_topic", None) or getattr(intent, "extracted_topic", None) or user_query
                    if proposal_capability and not proposal_is_grounded:
                        local_artifacts.append(
                            ArtifactResult(
                                id=f"art-gated-{fmt}",
                                kind=_format_to_kind(fmt),
                                format=fmt,
                                title=f"مخرج ثقافي: {topic_str}",
                                filename=f"sard-{fmt}",
                                mime_type="application/octet-stream",
                                size_bytes=0,
                                status="failed",
                                download_url=None,
                                error="لم يُنشأ المخرج لأن الطلب يحتاج إلى شاهد ثقافي قوي ومطابق للكيان والقطاع.",
                                error_category="insufficient_evidence",
                            ).to_dict()
                        )
                        continue
                    # Map format to orchestrator call
                    art_req = ArtifactRequest(
                        format=fmt,
                        kind=_format_to_kind(fmt),
                        title=f"مخرج ثقافي: {topic_str}",
                        topic=topic_str,
                        region=intent.region or "المملكة العربية السعودية",
                        raw_text=text,
                        sources=tuple(sources) if sources else (),
                        metadata={
                            "session_id": session_id,
                            "intent": intent.to_dict() if hasattr(intent, "to_dict") else asdict(intent),
                            "locale": resolved_lang,
                            "evidence_gate": "strong" if proposal_is_grounded else "not_applicable",
                        },
                    )
                    try:
                        res = self.orchestrator.generate_artifact(art_req, deadline_monotonic=deadline_monotonic)
                        if res:
                            local_artifacts.append(res.to_dict())
                    except Exception as exc:
                        logger.error("Artifact orchestration failed for format '%s': %s", fmt, exc)
                        failed_res = ArtifactResult(
                            id=f"art-failed-{fmt}",
                            kind=_format_to_kind(fmt),
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
            return local_artifacts

        # Early check for unconfigured model applies ONLY to the direct-model
        # path below. The hybrid path (planner + deterministic synthesis) and
        # the G10 deterministic fast-path are designed to serve grounded
        # answers offline; failing them fast on model config would defeat the
        # zero-cold-start bundled corpus. The direct path has its own
        # ModelConfigError handling with identical messaging.
        if not use_hybrid_retrieval and self._injected_model is None:
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
            proposal_result = CulturalProposalResult()

            # 2. Run Retrieval & Provenance Planning. An elliptical
            # same-session follow-up (e.g. "وماذا عن طريقة تقديمها؟") carries
            # no topic of its own; the most recent prior user turn supplies
            # the antecedent for retrieval scoping only. Retrieved evidence is
            # still validated as current-turn evidence; history is never cited.
            history_turns = extract_session_history_turns(messages, user_query, session_id)
            retrieval_query = resolve_followup_retrieval_query(user_query, history_turns)
            try:
                plan_res = self.ask_isnad(
                    user_query=retrieval_query,
                    session_id=session_id,
                    mock_multimodal_files=mock_multimodal_files,
                    status_callback=status_callback,
                    lang=resolved_lang,
                    uploaded_files=uploaded_files,
                )
                plan_res = self._filter_planner_result(retrieval_query, plan_res)
                for ev in plan_res.visible_sources:
                    raw: dict[str, Any] = {}
                    memory = getattr(self.planner, "memory", None)
                    l0 = getattr(memory, "l0", None)
                    try:
                        raw = l0.get_raw_ref(ev.raw_ref) or {} if l0 is not None else {}
                    except Exception as exc:
                        logger.debug("Planner citation lookup skipped: %s", type(exc).__name__)
                    raw_meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
                    source_url = (
                        raw_meta.get("source_url")
                        or raw.get("source_url")
                        or (ev.url_or_doc_id if str(ev.url_or_doc_id or "").startswith(("http://", "https://")) else "")
                    )
                    citation_id = raw_meta.get("citation_id") or raw.get("citation_id") or ev.source_id
                    citations.append({
                        "id": citation_id,
                        "citation_id": citation_id,
                        "title": raw.get("title") or f"{ev.origin} ({ev.region})",
                        "url": source_url,
                        "source_url": source_url,
                        "origin": ev.origin,
                        "source_type": ev.source_type,
                        "chunk_id": raw_meta.get("chunk_id") or raw.get("chunk_id") or ev.source_id,
                        "source_id": ev.source_id,
                        "excerpt": (ev.excerpt or "")[:500],
                        "region": ev.region,
                        "topic": raw_meta.get("topic") or raw.get("topic") or "",
                        "sector": raw_meta.get("sector") or raw.get("sector") or "",
                        "score": raw.get("score") or raw_meta.get("confidence_score") or 0.0,
                    })

                # Choose answer language based on resolved locale
                if resolved_lang == "en":
                    text_resp = sanitize_cultural_output(plan_res.answer_en or plan_res.answer_ar or "")
                else:
                    text_resp = sanitize_cultural_output(plan_res.answer_ar or plan_res.answer_en or "")
                decision = plan_res.chain.decision
            except Exception as exc:
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
                cultural_res = self.ask_cultural(retrieval_query, mock_multimodal_files=mock_multimodal_files, lang=resolved_lang, uploaded_files=uploaded_files)
                text_resp = sanitize_cultural_output(cultural_res.answer_text)
                decision = cultural_res.decision
                citations = cultural_res.citations
                # planner_result stays None on fallback, but citations/text are preserved

            # Recommendations are derived only after current-turn factual
            # citations have been validated. Mandate records are added as
            # separate, inspectable citations only when a strong proposal is
            # actually visible.
            proposal_result, mandate_citations = build_cultural_proposal_result(user_query, citations)
            if mandate_citations:
                citations.extend(mandate_citations)

            # Empty output must be explicit hedge, not empty string
            if not text_resp or not text_resp.strip():
                text_resp = _empty_hedge(user_query)
                if decision is None:
                    decision = "hedge"

            if requires_medical_qualification(user_query):
                medical_note = self._medical_note(resolved_lang)
                if medical_note.strip() not in text_resp:
                    text_resp = f"{text_resp.rstrip()}{medical_note}"

            # 3. Artifact Orchestration — always via helper (BOTH paths)
            artifacts = _maybe_orchestrate(text_resp, citations)

            return ChatResult(
                ok=True,
                text=text_resp,
                decision=decision,
                citations=citations,
                planner_result=plan_res,
                artifacts=artifacts,
                proposal_result=proposal_result,
            )

        # Direct conversation path — MUST also support artifact intent
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
            # Restore only validated same-session history. It is prompt
            # context, never current-turn evidence; without a session ID,
            # client history is omitted to prevent cross-session leakage.
            for prior in extract_session_history_turns(messages, user_query, session_id):
                if prior["role"] == "user":
                    lc_messages.append(HumanMessage(content=prior["content"]))
                else:
                    lc_messages.append(AIMessage(content=prior["content"]))
            lc_messages.append(HumanMessage(content=user_query))

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

            if text and requires_medical_qualification(user_query):
                medical_note = self._medical_note(resolved_lang)
                if medical_note.strip() not in text:
                    text = f"{text.rstrip()}{medical_note}"

            artifacts = _maybe_orchestrate(text, []) if text else []
            return ChatResult(ok=True, text=text, artifacts=artifacts)

        except Exception as exc:
            logger.warning("Chat model direct invoke failed or timed out: %s", exc)
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
