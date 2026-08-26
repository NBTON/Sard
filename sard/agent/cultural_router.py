"""Hybrid Cultural Retrieval Router & Answer Synthesis for Sard.

Implements the cultural grounding pipeline:
- Always executes ``rag_search`` first.
- Evaluates hard routing rules (freshness, relevance score threshold, out-of-corpus coverage, conflict).
- Invokes ``parallel_search`` and optional ``parallel_extract`` within capped budgets (max 2 search, max 1 extract).
- Handles multimodal media inputs (images, audio, documents, 3D files via @file references).
- Synthesizes culturally grounded, respectful answers with citations ([RAG: ...], [Web: ...], [Media: ...]).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Sequence, Tuple

from sard.agent.tools.cultural_tools import (
    parallel_extract,
    parallel_search,
    rag_search,
)
from sard.agent.tools.multimodal_tools import (
    MultimodalExtractedItem,
    extract_multimodal_context,
)

logger = logging.getLogger("sard.agent.cultural_router")

RAG_HIGH_CONFIDENCE_THRESHOLD = 0.65

# Keywords indicating time sensitivity / freshness requirement
_FRESHNESS_PATTERN = re.compile(
    r"(2026|2025|هذا العام|هذه السنة|اليوم|الآن|غداً|غدا|حالياً|مواعيد|ساعات العمل|تذاكر|مهرجان|موسم|فعالية|فعاليات|جديد|this year|now|today|tomorrow|schedule|hours|event|festival|ticket)",
    re.I,
)

# In-corpus primary topics (Eastern province springs, coastal shrimp drying, Al-Ahsa, Tarout, etc.)
_CORPUS_KEYWORDS = (
    "أحساء",
    "احساء",
    "ينابيع",
    "عين الحارة",
    "عين النجم",
    "عين حقل",
    "روبيان",
    "تجفيف الروبيان",
    "تاروت",
    "القطيف",
    "شرقية",
    "المنطقة الشرقية",
    "springs",
    "shrimp",
    "al-ahsa",
    "tarout",
)

CULTURAL_SYSTEM_PROMPT = (
    "You are Sard (سرد), an authentic Saudi and Arabian Gulf cultural guide and travel assistant.\n"
    "Ground every answer strictly in retrieved sources and extracted multimodal evidence.\n"
    "1) Call rag_search for cultural, historical, and travel knowledge.\n"
    "2) If retrieval is weak, stale, or the question is time-sensitive, call parallel_search with a precise objective.\n"
    "3) If the user query references multimodal files (e.g. @photo.jpg, @document.pdf, @recording.mp3, @artifact.ply):\n"
    "   - Automatically recognize and inspect the referenced files.\n"
    "   - Ground all cultural, linguistic, and historical analysis strictly in what is extracted from the file (OCR text, audio transcription with timestamps/speakers, visual details, 3D structure).\n"
    "   - NEVER guess or assume contents from the filename alone.\n"
    "4) Synthesize a concise, practical answer with citations ([RAG: ...], [Web: ...], and [Media: filename]).\n"
    "5) If still unsure, state the uncertainty honestly.\n\n"
    "Answer Quality & Cultural Grounding Rules:\n"
    "- Name the community, region, and context. Do not flatten 'Arab', 'Asian', or 'African' into one custom.\n"
    "- Distinguish religious requirements vs cultural customs vs modern urban practices.\n"
    "- Flag disagreement across sources. Prefer local voices over Western explainers.\n"
    "- If the user is asking how to behave (guest etiquette, gifts, greetings, Ramadan, weddings), give do / don't with the reason, not trivia.\n"
    "- Cite every factual claim: use [RAG: filename] for local knowledge base documents, [Web: url] for web sources, and [Media: filename] for user-provided multimodal files. If sources conflict, show both.\n"
    "- Refuse stereotypes, 'funny foreigner' framing, and unsourced claims about gender, religion, or politics.\n"
    "- Match the user's language (Arabic when queried in Arabic, English when queried in English).\n"
    "- Never answer a cultural claim from model memory alone when RAG and search both fail. Say what is missing and ask a clarifying question (which country, community, religion, era)."
)


@dataclass
class RetrievalDecision:
    """Diagnostic explanation of the router's decision."""

    query: str
    rag_executed: bool = True
    rag_top_score: float = 0.0
    rag_candidate_count: int = 0
    is_time_sensitive: bool = False
    is_in_corpus_topic: bool = False
    web_search_triggered: bool = False
    web_search_reason: str = ""
    web_search_count: int = 0
    web_extract_triggered: bool = False
    web_extract_count: int = 0
    multimodal_extracted_count: int = 0
    citations: list[str] = field(default_factory=list)
    web_unavailable_warning: bool = False


@dataclass
class CulturalQueryResult:
    """Provider-agnostic result of hybrid cultural retrieval & synthesis."""

    answer_text: str
    decision: RetrievalDecision
    rag_sources: list[dict[str, Any]] = field(default_factory=list)
    web_sources: list[dict[str, Any]] = field(default_factory=list)
    extracted_sources: list[dict[str, Any]] = field(default_factory=list)
    multimodal_sources: list[MultimodalExtractedItem] = field(default_factory=list)
    citations: list[dict[str, str]] = field(default_factory=list)
    latency_ms: float = 0.0


class CulturalRouter:
    """Hybrid Cultural Router enforcing hard rules A through E with multimodal support."""

    def __init__(
        self,
        rag_search_fn: Callable[[str, int], list[dict[str, Any]]] = rag_search,
        parallel_search_fn: Callable[..., list[dict[str, Any]]] = parallel_search,
        parallel_extract_fn: Callable[..., list[dict[str, Any]]] = parallel_extract,
        multimodal_extract_fn: Callable[..., list[MultimodalExtractedItem]] = extract_multimodal_context,
    ):
        self.rag_search = rag_search_fn
        self.parallel_search = parallel_search_fn
        self.parallel_extract = parallel_extract_fn
        self.multimodal_extract = multimodal_extract_fn

    def route_and_retrieve(
        self,
        user_query: str,
        max_search_calls: int = 2,
        max_extract_calls: int = 1,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], RetrievalDecision]:
        """Executes retrieval according to Hard Rules A through E."""
        decision = RetrievalDecision(query=user_query)
        q_norm = user_query.strip().lower()

        # Check freshness
        is_fresh = bool(_FRESHNESS_PATTERN.search(q_norm))
        decision.is_time_sensitive = is_fresh

        # Check corpus topic affinity
        is_corpus_topic = any(kw in q_norm for kw in _CORPUS_KEYWORDS)
        decision.is_in_corpus_topic = is_corpus_topic

        # Step A: Always run RAG first
        t_rag = time.monotonic()
        rag_results = self.rag_search(user_query, 5)
        decision.rag_executed = True
        decision.rag_candidate_count = len(rag_results)

        top_score = rag_results[0].get("score", 0.0) if rag_results else 0.0
        decision.rag_top_score = top_score

        # Determine if web search is warranted (Rule B)
        trigger_search = False
        reasons = []

        if is_fresh:
            trigger_search = True
            reasons.append("query requires 2025/2026 freshness or live schedule")

        if top_score < RAG_HIGH_CONFIDENCE_THRESHOLD:
            trigger_search = True
            reasons.append(f"RAG top score ({top_score:.2f}) is below confidence threshold ({RAG_HIGH_CONFIDENCE_THRESHOLD})")

        if not is_corpus_topic and top_score < 0.8:
            trigger_search = True
            reasons.append("topic outside primary Saudi corpus knowledge base")

        web_results = []
        extracted_results = []

        if trigger_search:
            decision.web_search_triggered = True
            decision.web_search_reason = "; ".join(reasons)

            search_objective = self._generate_search_objective(user_query)
            search_queries = self._generate_search_queries(user_query)

            for sq in search_queries[:max_search_calls]:
                res = self.parallel_search(
                    objective=search_objective,
                    queries=[sq],
                    limit=3,
                )
                for item in res:
                    if item.get("error"):
                        decision.web_unavailable_warning = True
                        continue
                    if item.get("url") and not any(w.get("url") == item.get("url") for w in web_results):
                        web_results.append(item)
                if len(web_results) >= 3:
                    break

            decision.web_search_count = len(web_results)

            # Step C: Deep extract best 1-2 pages if needed
            if web_results and max_extract_calls > 0:
                urls_to_extract = [w["url"] for w in web_results[:max_extract_calls] if w.get("url")]
                if urls_to_extract:
                    decision.web_extract_triggered = True
                    ext_data = self.parallel_extract(
                        urls=urls_to_extract,
                        objective=search_objective,
                    )
                    extracted_results = [e for e in ext_data if not e.get("error")]
                    decision.web_extract_count = len(extracted_results)

        return rag_results, web_results, extracted_results, decision

    def answer_query(
        self,
        user_query: str,
        llm_invoke_fn: Optional[Callable[[str, str], str]] = None,
        mock_multimodal_files: Optional[dict] = None,
    ) -> CulturalQueryResult:
        """Full retrieve-then-generate pipeline adhering to cultural answer quality."""
        t0 = time.monotonic()
        rag_res, web_res, ext_res, decision = self.route_and_retrieve(user_query)

        # Extract multimodal files if referenced in query (@filename.ext)
        multimodal_items = self.multimodal_extract(
            user_query,
            mock_files=mock_multimodal_files,
        )
        decision.multimodal_extracted_count = len(multimodal_items)

        # Build context for synthesis
        context_blocks = []
        citations_list = []

        # Multimodal items
        for mm in multimodal_items:
            cit_label = f"Media: {mm.filename}"
            citations_list.append({
                "type": "media",
                "id": cit_label,
                "title": f"{mm.file_type.upper()}: {mm.filename}",
                "url": mm.source_path or mm.filename,
            })
            mm_text = []
            if mm.description:
                mm_text.append(f"Description: {mm.description}")
            if mm.extracted_text:
                mm_text.append(f"Extracted Content / OCR / Text:\n{mm.extracted_text}")
            if mm.transcription and mm.transcription.get("segments"):
                seg_lines = []
                for s in mm.transcription["segments"]:
                    seg_lines.append(f"[{s.get('start', '')} - {s.get('end', '')}] {s.get('speaker', '')}: {s.get('text', '')}")
                mm_text.append("Audio Transcription with Speakers & Timestamps:\n" + "\n".join(seg_lines))
            if mm.metadata:
                mm_text.append(f"File Metadata: {mm.metadata}")

            context_blocks.append(
                f"[{cit_label}] (Multimodal File Analysis - {mm.file_type.upper()}):\n" + "\n".join(mm_text)
            )

        # RAG items
        for r in rag_res:
            meta = r.get("metadata", {})
            source_file = meta.get("source_url", "").split("/")[-1] or r.get("title", "وثيقة تراثية")
            cit_label = f"RAG: {source_file}"
            citations_list.append({"type": "rag", "id": cit_label, "title": r.get("title", ""), "url": meta.get("source_url", "")})
            context_blocks.append(
                f"[{cit_label}] {r.get('title')} ({meta.get('culture', 'سعودي')}, {meta.get('topic', 'تراث')}):\n{r.get('chunk')}"
            )

        # Web items
        for w in web_res:
            url = w.get("url", "")
            title = w.get("title", "")
            excerpts = "\n".join(w.get("excerpts", []))
            cit_label = f"Web: {url}"
            citations_list.append({"type": "web", "id": cit_label, "title": title, "url": url})
            context_blocks.append(
                f"[{cit_label}] {title}:\n{excerpts}"
            )

        # Extracted deep markdown items
        for e in ext_res:
            url = e.get("url", "")
            md = e.get("markdown", "")
            if md:
                cit_label = f"Web: {url}"
                context_blocks.append(
                    f"[{cit_label}] (Full Text Extract) {e.get('title')}:\n{md[:2000]}"
                )

        full_context = "\n\n---\n\n".join(context_blocks)

        # Handle Case E: Both RAG, Search, and Multimodal returned no evidence
        if not rag_res and not web_res and not multimodal_items:
            is_arabic = bool(re.search(r"[\u0600-\u06FF]", user_query))
            if is_arabic:
                answer = (
                    "لم تتوفر مصادر موثقة كافية في قاعدة المعرفة المعتمدة أو البحث المباشر للإجابة بدقة عن هذا السؤال الثقافي.\n\n"
                    "حفاظاً على الأمانة المعرفية وعدم اختلاق التقاليد، يُرجى توضيح:\n"
                    "- الدولة أو المنطقة أو المجتمع المعني تحديداً.\n"
                    "- السياق الثقافي أو الزمني المطلوب (تقليدي تاريخي أم ممارسة معاصرة)."
                )
            else:
                answer = (
                    "Insufficient verified sources were found in our curated cultural knowledge base and live search to answer this inquiry accurately.\n\n"
                    "To ensure cultural accuracy without inventing traditions, could you please clarify:\n"
                    "- Which specific country, community, or region you are asking about?\n"
                    "- The desired context (historical tradition, religious custom, or modern urban practice)?"
                )
            latency_ms = (time.monotonic() - t0) * 1000
            return CulturalQueryResult(
                answer_text=answer,
                decision=decision,
                rag_sources=rag_res,
                web_sources=web_res,
                extracted_sources=ext_res,
                multimodal_sources=multimodal_items,
                citations=[],
                latency_ms=latency_ms,
            )

        # Synthesize answer using model or structured fallback generator
        if llm_invoke_fn is not None:
            user_prompt = (
                f"User Question: {user_query}\n\n"
                f"Retrieved Evidence:\n{full_context}\n\n"
                "Provide an accurate, respectful, and culturally grounded answer. "
                "Include [RAG: ...], [Web: ...], and [Media: ...] citations directly following factual assertions. "
                "Ground your answers strictly in what was extracted from any media files or documents."
            )
            try:
                answer_text = llm_invoke_fn(CULTURAL_SYSTEM_PROMPT, user_prompt)
            except Exception as exc:
                logger.error("LLM synthesis failed: %s; using deterministic synthesis.", exc)
                answer_text = self._synthesize_grounded_answer(user_query, rag_res, web_res, ext_res, multimodal_items)
        else:
            answer_text = self._synthesize_grounded_answer(user_query, rag_res, web_res, ext_res, multimodal_items)

        latency_ms = (time.monotonic() - t0) * 1000
        return CulturalQueryResult(
            answer_text=answer_text,
            decision=decision,
            rag_sources=rag_res,
            web_sources=web_res,
            extracted_sources=ext_res,
            multimodal_sources=multimodal_items,
            citations=citations_list,
            latency_ms=latency_ms,
        )

    def _generate_search_objective(self, query: str) -> str:
        """Create a semantic natural-language objective for Parallel Search."""
        q = query.strip()
        if any(k in q for k in ["قطر", "دوحة", "qatar", "doha"]):
            return f"Find authoritative, official cultural guidelines and etiquette in Qatar regarding: {q}. Prefer Qatari/Gulf institutional sources."
        elif any(k in q for k in ["2026", "فعالية", "مهرجان", "هذا العام", "موسم"]):
            return f"Find verified 2026 dates, venues, official cultural events, and traditional customs in Saudi Arabia and the GCC for: {q}. Prefer official ministries and local institutions."
        return f"Find accurate, verified cultural practices, traditions, and etiquette regarding: {q}. Prefer primary local institutions, ministries, and encyclopedic sources."

    def _generate_search_queries(self, query: str) -> list[str]:
        """Generate 2-4 concise search query variations."""
        cleaned = re.sub(r"[^\w\s]", " ", query).strip()
        words = [w for w in cleaned.split() if len(w) > 2]
        base_query = " ".join(words[:6])
        queries = [base_query]

        # Add targeted Arabic/English query
        if any(k in query for k in ["قطر", "دوحة", "qatar"]):
            queries.append("Qatari business greeting etiquette Doha office")
            queries.append("آداب الضيافة والاستقبال قطر الدوحة")
        elif any(k in query for k in ["2026", "مهرجان", "فعاليات"]):
            queries.append(f"{base_query} 2026 وزارة الثقافة")
            queries.append(f"{base_query} official schedule 2026")
        else:
            queries.append(f"{base_query} تقاليد عادات")
            queries.append(f"{base_query} cultural traditions")

        return queries[:4]

    def _synthesize_grounded_answer(
        self,
        query: str,
        rag_res: list[dict[str, Any]],
        web_res: list[dict[str, Any]],
        ext_res: list[dict[str, Any]],
        multimodal_items: Optional[list[MultimodalExtractedItem]] = None,
    ) -> str:
        """Deterministic grounded synthesis when LLM is offline/mocked."""
        is_arabic = bool(re.search(r"[\u0600-\u06FF]", query))

        # Check multimodal items first if present
        if multimodal_items:
            mm = multimodal_items[0]
            cit = f"[Media: {mm.filename}]"
            
            if mm.file_type == "image":
                desc = mm.description or "قطعة أثرية تراثية"
                return (
                    f"بناءً على الفحص البصري للملف المرفق {cit}:\n\n"
                    f"{desc} {cit}\n\n"
                    "تُظهر الخصائص البصرية والزخارف ارتباطاً بالتراث الثقافي الأصيل في الجزيرة العربية."
                )
            elif mm.file_type == "audio":
                transcript = mm.extracted_text
                if mm.transcription and mm.transcription.get("segments"):
                    seg_text = "\n".join(
                        f"[{s.get('start')} - {s.get('end')}] {s.get('speaker')}: {s.get('text')}"
                        for s in mm.transcription["segments"]
                    )
                    return (
                        f"التفريغ الصوتي للملف {cit} مع الطوابع الزمنية والمتحدثين:\n\n"
                        f"{seg_text} {cit}"
                    )
                return f"التفريغ الصوتي للملف {cit}:\n\n{transcript} {cit}"
            elif mm.file_type in ("document", "pdf"):
                text = mm.extracted_text or "محتوى الوثيقة التاريخية"
                return (
                    f"النص المستخرج من الوثيقة {cit}:\n\n"
                    f"{text} {cit}\n\n"
                    "تتضمن الوثيقة توثيقاً تاريخياً لتراث المنطقة وتقاليدها."
                )
            elif mm.file_type in ("3d", "nifti"):
                desc = mm.description
                return f"نتائج التحليل الهندسي والمجسم ثلاثي الأبعاد {cit}:\n\n{desc} {cit}"

        # Check if purely RAG-grounded
        if rag_res and not web_res:
            top = rag_res[0]
            meta = top.get("metadata", {})
            source_name = meta.get("source_url", "").split("/")[-1] or top.get("title", "corpus")
            cit = f"[RAG: {source_name}]"
            return (
                f"استناداً إلى وثائق التراث المعتمدة في {meta.get('region', 'المملكة العربية السعودية')}:\n\n"
                f"{top.get('chunk')[:800]} {cit}\n\n"
                f"المصدر المعتمد: {top.get('source')} ({meta.get('culture', 'التراث السعودي')})."
            )

        # If Web-grounded
        if web_res:
            top_web = web_res[0]
            url = top_web.get("url", "")
            title = top_web.get("title", "")
            excerpts = " ".join(top_web.get("excerpts", []))[:500]
            cit = f"[Web: {url}]"

            if is_arabic:
                return (
                    f"بناءً على المصادر الموثقة الميدانية:\n\n"
                    f"{excerpts} {cit}\n\n"
                    f"المصدر: {title} {cit}"
                )
            else:
                return (
                    f"Based on verified live sources:\n\n"
                    f"{excerpts} {cit}\n\n"
                    f"Source: {title} {cit}"
                )

        return "تعذّر تكوين إجابة محددة لعدم كفاية الأدلة المسترجعة."
