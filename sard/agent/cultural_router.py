"""Hybrid Cultural Retrieval Router & Answer Synthesis for Sard.

Implements the cultural grounding pipeline:
- Always executes ``rag_search`` first.
- Evaluates hard routing rules (freshness, relevance score threshold, out-of-corpus coverage, conflict).
- Invokes ``parallel_search`` and optional ``parallel_extract`` within capped budgets (max 2 search, max 1 extract).
- Synthesizes culturally grounded, respectful answers with citations ([RAG: ...] and [Web: ...]).
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
    "You are a cultural guide. Ground every answer in retrieved sources.\n"
    "1) Call rag_search.\n"
    "2) If retrieval is weak, stale, or the question is time-sensitive, call parallel_search with a precise objective such as: "
    "“Find how Qatari business greeting etiquette works in Doha offices in 2026, including handshake, names, and coffee service. Prefer Qatari/Gulf sources.”\n"
    "3) Optionally parallel_extract the best URLs.\n"
    "4) Synthesize a short, practical answer with citations.\n"
    "5) If still unsure, say so.\n\n"
    "Answer Quality & Cultural Grounding Rules:\n"
    "- Name the community, region, and context. Do not flatten 'Arab', 'Asian', or 'African' into one custom.\n"
    "- Distinguish religious requirements vs cultural customs vs modern urban practices.\n"
    "- Flag disagreement across sources. Prefer local voices over Western explainers.\n"
    "- If the user is asking how to behave (guest etiquette, gifts, greetings, Ramadan, weddings), give do / don't with the reason, not trivia.\n"
    "- Cite every factual claim: use [RAG: filename] for local knowledge base documents and [Web: url] for web sources. If sources conflict, show both.\n"
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
    citations: list[dict[str, str]] = field(default_factory=list)
    latency_ms: float = 0.0


class CulturalRouter:
    """Hybrid Cultural Router enforcing hard rules A through E."""

    def __init__(
        self,
        rag_search_fn: Callable[[str, int], list[dict[str, Any]]] = rag_search,
        parallel_search_fn: Callable[..., list[dict[str, Any]]] = parallel_search,
        parallel_extract_fn: Callable[..., list[dict[str, Any]]] = parallel_extract,
    ):
        self.rag_search = rag_search_fn
        self.parallel_search = parallel_search_fn
        self.parallel_extract = parallel_extract_fn

    def route_and_retrieve(
        self,
        user_query: str,
        max_search_calls: int = 2,
        max_extract_calls: int = 1,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], RetrievalDecision]:
        """Executes retrieval according to Hard Rules A through E.

        ROUTING (hard rules):
        A. Always run rag_search first for cultural, historical, religious, etiquette, language, and identity questions.
        B. Use Parallel Search when ANY of these are true:
           - RAG top score is low (< 0.65) or chunks are off-topic
           - Query needs freshness: festivals this year, current events, venue hours, recent social change, “now / today / this year”
           - User asks about a culture, city, or practice not covered in the corpus
           - RAG sources conflict and you need a tie-break from reputable live sources
        C. Use parallel_extract only after search, and only for the 1–3 URLs that actually contain the answer.
        D. If RAG is high-confidence AND the question is stable cultural knowledge (proverbs, classical etiquette, documented ritual), do NOT search the web.
        E. Never answer a cultural claim from model memory alone when RAG and search both fail.
        """
        decision = RetrievalDecision(query=user_query)
        q_norm = user_query.strip().lower()

        # Check freshness
        is_fresh = bool(_FRESHNESS_PATTERN.search(q_norm))
        decision.is_time_sensitive = is_fresh

        # Check if topic is known in local corpus
        is_in_corpus = any(k in q_norm for k in _CORPUS_KEYWORDS)
        decision.is_in_corpus_topic = is_in_corpus

        # Step A: Always run rag_search first
        rag_results = self.rag_search(user_query, k=6)
        decision.rag_candidate_count = len(rag_results)
        top_score = rag_results[0]["score"] if rag_results else 0.0
        decision.rag_top_score = top_score

        # Check whether web search is required
        needs_web_search = False
        reasons = []

        if is_fresh:
            needs_web_search = True
            reasons.append("Query is time-sensitive/freshness required (Rule B: Freshness)")

        if top_score < RAG_HIGH_CONFIDENCE_THRESHOLD:
            needs_web_search = True
            reasons.append(f"RAG confidence ({top_score:.2f}) < {RAG_HIGH_CONFIDENCE_THRESHOLD:.2f} (Rule B: Low RAG Score)")

        if not is_in_corpus and not (top_score >= 0.85 and len(rag_results) >= 2):
            needs_web_search = True
            reasons.append("Topic not sufficiently covered in local corpus (Rule B: Out-of-corpus)")

        # Rule D: If RAG is high-confidence AND question is stable cultural knowledge, do NOT search the web
        if not is_fresh and is_in_corpus and (top_score >= RAG_HIGH_CONFIDENCE_THRESHOLD or (top_score >= 0.50 and len(rag_results) >= 1)):
            needs_web_search = False
            reasons = ["RAG is high-confidence on stable in-corpus cultural knowledge (Rule D)"]


        web_results: list[dict[str, Any]] = []
        extracted_results: list[dict[str, Any]] = []

        if needs_web_search and max_search_calls > 0:
            decision.web_search_triggered = True
            decision.web_search_reason = "; ".join(reasons)

            objective = self._generate_search_objective(user_query)
            keyword_queries = self._generate_search_queries(user_query)

            try:
                web_results = self.parallel_search(
                    objective=objective,
                    search_queries=keyword_queries,
                    max_results=8,
                )
                decision.web_search_count = 1

                # If first search returned few results and budget allows, refine query (capped at max 2)
                if len(web_results) < 2 and max_search_calls >= 2:
                    alt_queries = [q + " تقاليد عادات" for q in keyword_queries[:2]]
                    web_results_2 = self.parallel_search(
                        objective=objective,
                        search_queries=alt_queries,
                        max_results=8,
                    )
                    decision.web_search_count = 2
                    # Merge unique URLs
                    seen_urls = {r.get("url") for r in web_results}
                    for item in web_results_2:
                        if item.get("url") not in seen_urls:
                            web_results.append(item)
                            seen_urls.add(item.get("url"))

            except Exception as search_exc:
                logger.warning("Parallel search failed (%s); degrading gracefully.", search_exc)
                decision.web_unavailable_warning = True

            # Step C: Use parallel_extract only after search, and only for 1-3 URLs with thin excerpts
            if web_results and max_extract_calls > 0:
                top_urls = []
                for item in web_results[:3]:
                    url = item.get("url")
                    excerpts = item.get("excerpts") or []
                    total_len = sum(len(e) for e in excerpts)
                    # If excerpts are thin (< 150 chars) or specific detail needed, extract
                    if url and total_len < 250:
                        top_urls.append(url)

                if top_urls:
                    try:
                        decision.web_extract_triggered = True
                        extracted_results = self.parallel_extract(
                            urls=top_urls[:3],
                            objective=objective,
                        )
                        decision.web_extract_count = len(extracted_results)
                    except Exception as extract_exc:
                        logger.warning("Parallel extract failed (%s); continuing with search excerpts.", extract_exc)

        return rag_results, web_results, extracted_results, decision

    def answer_query(
        self,
        user_query: str,
        llm_invoke_fn: Optional[Callable[[str, str], str]] = None,
    ) -> CulturalQueryResult:
        """Full retrieve-then-generate pipeline adhering to cultural answer quality."""
        t0 = time.monotonic()
        rag_res, web_res, ext_res, decision = self.route_and_retrieve(user_query)

        # Build context for synthesis
        context_blocks = []
        citations_list = []

        # RAG items
        for r in rag_res:
            meta = r.get("metadata", {})
            cit_id = meta.get("citation_id") or meta.get("source_url") or r.get("source") or "corpus"
            # Standardize filename/source label
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

        # Handle Case E: Both RAG and Search returned no evidence
        if not rag_res and not web_res:
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
                citations=[],
                latency_ms=latency_ms,
            )

        # Synthesize answer using model or structured fallback generator
        if llm_invoke_fn is not None:
            user_prompt = (
                f"User Question: {user_query}\n\n"
                f"Retrieved Evidence:\n{full_context}\n\n"
                "Provide an accurate, respectful, and culturally grounded answer. "
                "Include [RAG: ...] and [Web: ...] citations directly following factual assertions. "
                "If the user is asking about behavior/etiquette, structure clearly with do's and don'ts and the underlying cultural reason."
            )
            try:
                answer_text = llm_invoke_fn(CULTURAL_SYSTEM_PROMPT, user_prompt)
            except Exception as exc:
                logger.error("LLM synthesis failed: %s; using deterministic synthesis.", exc)
                answer_text = self._synthesize_grounded_answer(user_query, rag_res, web_res, ext_res)
        else:
            answer_text = self._synthesize_grounded_answer(user_query, rag_res, web_res, ext_res)

        latency_ms = (time.monotonic() - t0) * 1000
        return CulturalQueryResult(
            answer_text=answer_text,
            decision=decision,
            rag_sources=rag_res,
            web_sources=web_res,
            extracted_sources=ext_res,
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
    ) -> str:
        """Deterministic grounded synthesis when LLM is offline/mocked."""
        is_arabic = bool(re.search(r"[\u0600-\u06FF]", query))

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
