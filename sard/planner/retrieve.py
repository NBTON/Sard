"""Grounded Retrieval Layer for Sard's Isnād Planner.

Executes retrieval before drafting:
1. Curated RAG corpus first.
2. Parallel Search second, strictly for dated/public authoritative sources.
3. Multimodal extraction & visual inspection if media files or images are attached.

Transforms every retrieved document chunk, search hit, or extracted media trace
into an immutable L0 Evidence record with a durable source_id.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from sard.agent.tools.cultural_tools import parallel_search, rag_search
from sard.agent.tools.multimodal_tools import (
    MultimodalExtractedItem,
    extract_multimodal_context,
)
from sard.memory.l0_evidence import L0EvidenceStore
from sard.schemas.isnad import Evidence, Region, SourceType

logger = logging.getLogger("sard.planner.retrieve")

_FRESHNESS_RE = re.compile(
    r"(2025|2026|2027|اليوم|الآن|الان|حالي|جديد|مواعيد|تذاكر|مهرجان|فعاليات|"
    r"today|now|current|latest|upcoming|schedule|tickets|festival|events)",
    re.IGNORECASE,
)

# Unified web-trigger freshness set (mirrors cultural_router Rule B:
# year tags + Arabic/English freshness markers + schedule/ticket/festival).
# Kept local (not imported) so planner stays decoupled from the agent router,
# but semantics match: fresh queries always trigger web even when RAG hits.
_UNIFIED_FRESHNESS_RE = re.compile(
    r"(2025|2026|2027|هذا العام|هذه السنة|هذا الأسبوع|هذا الاسبوع|الأسبوع|الاسبوع|"
    r"أسبوع|اسبوع|اليوم|الآن|الان|غداً|غدا|حالياً|حاليا|حالي|جديد|مواعيد|ساعات العمل|"
    r"تذاكر|مهرجان|موسم|فعالية|فعاليات|this year|this week|now|today|tomorrow|"
    r"schedule|hours|event|festival|ticket|week|current|latest|upcoming)",
    re.IGNORECASE,
)

_RAG_CONFIDENCE_THRESHOLD = 0.65

# Request-aware depth budgets: depth selects how much to REQUEST (not how
# much to fetch-then-truncate). rag_k goes to rag_search, web_max goes to
# parallel_search/fanout (which caps per-provider requests), so simple never
# over-fetches and deep actually reaches providers.
_DEPTH_REQUEST_BUDGETS: Dict[str, Dict[str, int]] = {
    "simple": {"rag_k": 3, "web_max": 3},
    "normal": {"rag_k": 5, "web_max": 5},
    "deep": {"rag_k": 8, "web_max": 8},
}


def is_time_sensitive_query(query: str) -> bool:
    """Freshness-aware router: current-event questions need live sources."""
    text = query or ""
    return bool(_FRESHNESS_RE.search(text) or _UNIFIED_FRESHNESS_RE.search(text))


def infer_search_depth(query: str, explicit: Optional[str] = None) -> str:
    """Request-aware depth: explicit wins, else derived from the request.

    Short factoid queries -> ``simple``; time-sensitive or long/complex
    queries -> ``deep``; everything else -> ``normal``. Always returns one of
    ``simple``/``normal``/``deep``.
    """
    if explicit is not None:
        norm = (explicit or "").strip().lower()
        if norm in _DEPTH_REQUEST_BUDGETS:
            return norm
    text = (query or "").strip()
    if is_time_sensitive_query(text):
        return "deep"
    if len(text) < 40:
        return "simple"
    if len(text) > 200:
        return "deep"
    return "normal"


def _rag_top_score(rag_hits: List[Dict[str, Any]]) -> Optional[float]:
    """Best calibrated score in RAG hits, or None when hits carry no scores."""
    best: Optional[float] = None
    for hit in rag_hits or ():
        if not isinstance(hit, dict):
            continue
        raw = hit.get("score", hit.get("confidence_score"))
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if best is None or value > best:
            best = value
    return best


def should_trigger_web_search(query: str, rag_hits: List[Dict[str, Any]]) -> Tuple[bool, str]:
    """Unified web-trigger semantics (matches cultural_router Rule B).

    Web is warranted when the query needs freshness, when RAG is empty, or
    when RAG confidence is low (top score < 0.65). Returns (trigger, reason).
    """
    if is_time_sensitive_query(query):
        return True, "query requires freshness or live schedule"
    if not rag_hits:
        return True, "topic outside local corpus (no RAG hits)"
    top = _rag_top_score(rag_hits)
    if top is not None and top < _RAG_CONFIDENCE_THRESHOLD:
        return True, f"low RAG confidence ({top:.2f} < {_RAG_CONFIDENCE_THRESHOLD})"
    return False, ""


def _classify_source_type(url_or_name: str, origin: str) -> SourceType:
    """Classify the source type based on domain, title, or authority name."""
    s = f"{url_or_name} {origin}".lower()
    if any(k in s for k in ["moc.gov.sa", "heritage.moc", "culinary.moc", "museums.moc", "وزارة الثقافة", "هيئة التراث", "هيئة فنون الطهي", "دارة الملك عبدالعزيز", "دار الملك"]):
        return "ministry"
    if any(k in s for k in ["museum", "متحف", "متحف الوطني", "إثراء", "ithra", "قصر المربع", "قصر المصمك"]):
        return "museum"
    if any(k in s for k in ["saudipedia", "spa.gov.sa", "unesco.org", "وكالة الأنباء السعودية"]):
        return "news"
    if any(k in s for k in ["وثيقة", "مخطوطة", "تاريخ نجد", "تاريخ الحجاز", "عنوان المجد", "معجم"]):
        return "dated_text"
    if any(k in s for k in ["رواية شفهية", "شهادة حية", "مقابلة", "حرفي", "صانع تقليدي"]):
        return "oral_account"
    if any(k in s for k in ["@photo", "@image", "صورة", "مرفق", "upload", "user"]):
        return "user_upload"
    return "unknown"


def _infer_region_from_text(text: str) -> Region:
    """Infer region from text contents."""
    t_lower = text.lower()
    if any(k in t_lower for k in ["نجد", "رياض", "درعية", "طريف", "سدير", "قصيم", "وشم", "أثل", "najd"]):
        return "najd"
    if any(k in t_lower for k in ["حجاز", "جدة", "مكة", "مدينة", "علا", "رواشين", "منجور", "hijaz"]):
        return "hijaz"
    if any(k in t_lower for k in ["عسير", "أبها", "قط عسيري", "رجال ألمع", "asir"]):
        return "asir"
    if any(k in t_lower for k in ["شرقية", "أحساء", "احساء", "قطيف", "تاروت", "eastern"]):
        return "eastern"
    if any(k in t_lower for k in ["حائل", "تبوك", "جوف", "عرعر", "north"]):
        return "north"
    if any(k in t_lower for k in ["نجران", "جازان", "south"]):
        return "south"
    if any(k in t_lower for k in ["سعودي", "المملكة", "سعودية", "وطني", "سدو", "قهوة", "تأسيس", "all", "national"]):
        return "national"
    return "unknown"


class GroundedRetriever:
    """Orchestrates retrieval and converts all findings into L0 Evidence."""

    def __init__(
        self,
        l0_store: L0EvidenceStore,
        rag_search_fn: Callable[[str, int], List[Dict[str, Any]]] = rag_search,
        parallel_search_fn: Callable[..., List[Dict[str, Any]]] = parallel_search,
        multimodal_extract_fn: Callable[..., List[MultimodalExtractedItem]] = extract_multimodal_context,
    ):
        self.l0 = l0_store
        self.rag_search = rag_search_fn
        self.parallel_search = parallel_search_fn
        self.multimodal_extract = multimodal_extract_fn

    def retrieve(
        self,
        query: str,
        target_region: Optional[Region] = None,
        mock_multimodal_files: Optional[Dict[str, Any]] = None,
        allow_web_search: bool = True,
        uploaded_files: Optional[Dict[str, Any]] = None,
        deadline: Optional[Any] = None,
        deadline_monotonic: Optional[Any] = None,
        cancel_event: Optional[Any] = None,
        depth: Optional[str] = None,
    ) -> Tuple[List[Evidence], List[str]]:
        """Retrieve evidence across RAG, Web, and Multimodal extractors.

        Request-aware depth (``simple``/``normal``/``deep``, inferred from
        the request when omitted) selects how much to REQUEST from each
        channel (``rag_k``/``web_max``) so depth actually reaches providers
        instead of fetching-then-truncating. Web trigger semantics match
        ``cultural_router`` Rule B (fresh OR empty OR low-confidence RAG).
        Local/web hits are cross-deduplicated by canonical URL without
        dropping distinct content (same URL + distinct body is kept).
        """
        from sard.agent.deadline import DeadlineCancelledError as _Cancelled
        from sard.agent.deadline import coerce_deadline as _coerce

        dl = _coerce(deadline, cancel_event=cancel_event, label="retrieve")
        if dl is None and deadline_monotonic is not None:
            dl = _coerce(deadline_monotonic, cancel_event=cancel_event, label="retrieve")
        if dl is not None and cancel_event is None:
            cancel_event = dl.cancel_event

        def _gate(stage: str) -> None:
            if cancel_event is not None:
                try:
                    if cancel_event.is_set():
                        raise _Cancelled(f"cancelled at retrieval stage '{stage}'", stage=stage)
                except _Cancelled:
                    raise
                except Exception as exc_flag:
                    logger.debug("Retrieve cancel flag read skipped (%s).", type(exc_flag).__name__)
            if dl is not None:
                dl.check(stage)

        evidence_list: List[Evidence] = []
        retrieval_logs: List[str] = []

        # 1. Multimodal media inspection (real uploaded paths go to the real
        # extractor, never served from mock canned text).
        _gate("retrieve:multimodal")
        media_items = self.multimodal_extract(
            query,
            mock_files=mock_multimodal_files,
            uploaded_files=uploaded_files,
        )
        for m in media_items:
            method = getattr(m, "extraction_method", "core") or "core"
            unavailable = method in ("capability_unavailable", "provider_error") and not (m.extracted_text or "").strip()
            if unavailable:
                # Explicit provider-unavailable diagnostic: never cite as verified provenance.
                retrieval_logs.append(
                    f"المرفق {m.filename} تعذر تحليله: مزود المعالجة ({method}) غير متوفر — لم يُحتسب كمصدر موثق."
                )
                continue
            origin = f"المرفق البصري/المعرف ({m.filename})"
            excerpt = m.description or m.extracted_text or f"ملف مرئي من نوع {m.file_type}: {m.filename}"
            ev = self.l0.store_evidence(
                excerpt=excerpt,
                origin=origin,
                region=target_region or "unknown",
                source_type="user_upload",
                url_or_doc_id=m.filename,
                raw_data=getattr(m, "__dict__", {}),
                prefix="media",
            )
            evidence_list.append(ev)
            retrieval_logs.append(f"تم فحص المرفق {m.filename} وتوثيقه بسند {ev.source_id}")

        # Request-aware depth: how much to REQUEST from each channel.
        depth_norm = infer_search_depth(query, depth)
        request_budget = _DEPTH_REQUEST_BUDGETS.get(depth_norm, _DEPTH_REQUEST_BUDGETS["normal"])
        rag_k = int(request_budget["rag_k"])
        web_max = int(request_budget["web_max"])

        # 2. Curated RAG Search (request exactly rag_k; no fetch-then-truncate)
        _gate("retrieve:rag")
        rag_hits = self.rag_search(query, rag_k)
        for h in rag_hits:
            meta = h.get("metadata") or {}
            text = h.get("chunk") or h.get("text") or h.get("content") or ""
            title = h.get("title") or meta.get("title") or ""
            doc_id = h.get("citation_id") or h.get("doc_id") or meta.get("citation_id") or meta.get("chunk_id") or ""
            meta_reg = meta.get("region") or meta.get("region_code") or ""
            region = _infer_region_from_text(f"{title} {text} {meta_reg}") or target_region or "national"
            origin = meta.get("source_name") or h.get("source") or title or "وزارة الثقافة - سجل التراث الثقافي"
            stype = _classify_source_type(doc_id, origin)
            if stype == "unknown":
                stype = "ministry"  # Curated RAG is trusted

            ev = self.l0.store_evidence(
                excerpt=text,
                origin=origin,
                region=region,
                source_type=stype,
                date_or_period=meta.get("publication_date") or h.get("date_or_period") or "تراث موثق",
                url_or_doc_id=doc_id,
                raw_data=h,
                prefix="rag",
            )
            evidence_list.append(ev)
            retrieval_logs.append(f"تم استرجاع وثيقة RAG: {origin} [{region}] -> {ev.source_id}")

        # 3. Web search — unified trigger (cultural_router Rule B): fresh OR
        # empty OR low-confidence RAG. Depth-aware web_max reaches providers
        # (no fetch-then-truncate); cross-dedup by canonical URL keeps
        # distinct content even when URLs collide.
        trigger_web, trigger_reason = should_trigger_web_search(query, rag_hits)
        # Workstream E: never start the slow web leg on a doomed budget —
        # preserve the reserve for terminal SSE work instead.
        if dl is not None:
            try:
                dl.check("retrieve:web")
            except Exception:
                allow_web_search = False
        if allow_web_search and trigger_web:
            try:
                _gate("retrieve:web")
                web_hits = self.parallel_search(
                    objective=query,
                    search_queries=[query],
                    max_results=web_max,
                )
                try:
                    from sard.rag.search_providers import (
                        canonicalize_url as _canon,
                    )
                    from sard.rag.search_providers import (
                        is_duplicate_of_seen as _is_dup,
                    )
                except Exception:
                    _canon = None  # type: ignore[assignment]
                    _is_dup = None  # type: ignore[assignment]
                # Canonical URLs + bodies already kept from local RAG evidence.
                seen_canonicals: Dict[str, List[str]] = {}
                for h in rag_hits:
                    try:
                        meta = h.get("metadata") or {}
                        raw_url = (
                            h.get("url")
                            or h.get("source_url")
                            or meta.get("source_url")
                            or h.get("citation_id")
                            or h.get("doc_id")
                            or ""
                        )
                        body = h.get("chunk") or h.get("text") or h.get("content") or ""
                        canon = _canon(raw_url) if _canon is not None else (raw_url or "").strip().lower()
                        if canon:
                            seen_canonicals.setdefault(canon, []).append(body or "")
                    except Exception as exc_seen:
                        logger.debug("RAG seen-canonical skipped (%s).", type(exc_seen).__name__)
                        continue
                kept_web = 0
                for wh in web_hits:
                    title = wh.get("title", "")
                    content = wh.get("content", "")
                    url = wh.get("url", "")
                    try:
                        canon = _canon(url) if _canon is not None else (url or "").strip().lower()
                    except Exception as exc_canon:
                        logger.debug("Web canonical skipped (%s).", type(exc_canon).__name__)
                        canon = (url or "").strip().lower()
                    if canon and _is_dup is not None:
                        try:
                            if _is_dup(canon, content or "", "", seen_canonicals):
                                retrieval_logs.append(f"تم تجاهل مصدر ويب مكرر: {url}")
                                continue
                        except Exception as exc_dup:
                            logger.debug("Web cross-dedup skipped (%s).", type(exc_dup).__name__)
                    region = _infer_region_from_text(f"{title} {content}") or target_region or "unknown"
                    # Pre-synthesis gate: live web records obey the same
                    # entity/region/mandate policy as curated evidence. No
                    # disallowed record may reach answer synthesis.
                    try:
                        from sard.rag.relevance import filter_relevant_evidence as _gate

                        gate_candidate = {
                            "title": title,
                            "chunk": content,
                            "metadata": {
                                "topic": "",
                                "sector": "",
                                "region": "" if region == "unknown" else str(region),
                                "region_code": "" if region == "unknown" else str(region),
                            },
                        }
                        if not _gate(query, [gate_candidate]):
                            retrieval_logs.append(f"مصدر ويب مرفوض لبوابة الصلة: {title or url}")
                            continue
                    except Exception as exc:
                        logger.debug("Web relevance gate skipped (%s)", type(exc).__name__)
                    origin = title or url
                    stype = _classify_source_type(url, origin)

                    ev = self.l0.store_evidence(
                        excerpt=content,
                        origin=origin,
                        region=region,
                        source_type=stype,
                        date_or_period=wh.get("published_date"),
                        url_or_doc_id=url,
                        raw_data=wh,
                        prefix="web",
                    )
                    evidence_list.append(ev)
                    retrieval_logs.append(f"تم استرجاع مصدر ويب: {origin} -> {ev.source_id}")
                    if canon:
                        seen_canonicals.setdefault(canon, []).append(content or "")
                    kept_web += 1
                if trigger_reason:
                    retrieval_logs.append(f"سبب البحث الويب ({depth_norm}): {trigger_reason}")
            except _Cancelled:
                # Typed cancellation (gate or provider) propagates — never
                # swallowed as an ordinary web failure, never degraded.
                raise
            except Exception as exc:
                # A late-arriving cancel flag still converts to typed
                # cancellation instead of a quiet warning + continue.
                try:
                    if cancel_event is not None and cancel_event.is_set():
                        raise _Cancelled(
                            "cancelled during web retrieval", stage="retrieve:web"
                        ) from exc
                except _Cancelled:
                    raise
                except Exception as exc_flag:
                    logger.debug("Retrieve web cancel probe skipped (%s).", type(exc_flag).__name__)
                logger.warning("Parallel search skipped or failed: %s", type(exc).__name__)

        return evidence_list, retrieval_logs
