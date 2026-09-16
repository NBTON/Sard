"""Retrieval node: ``retrieve``.

Calls ONLY the injected Step 3 ``RAGService`` provider-independent interface
(never Zvec or an SDK directly), passing the Arabic query plus supported
metadata filters.  Adapts the returned ``RAGAnswer`` into typed graph
evidence with stable citation IDs, per-channel scores, models, normalized
mode, fallbacks, timings and warnings.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Optional

from sard.agent.events import (
    EVENT_COMPLETED,
    EVENT_MODEL_FALLBACK_ACTIVATED,
    EVENT_RETRIEVAL_MODE_CHANGED,
    EVENT_STARTED,
    adapt_fallback_events,
    make_event,
)
from sard.agent.routing import normalize_retrieval_mode
from sard.agent.state import EvidenceItem, RAGMode
from sard.rag.schemas import RetrievedCandidate
from sard.rag.service import RAGService

logger = logging.getLogger(__name__)

# Unified web-trigger threshold: matches cultural_router Rule B and
# planner should_trigger_web_search (0.65). Web is warranted when the
# query needs freshness, when local evidence is empty, or when top
# local confidence is low.
_WEB_TRIGGER_THRESHOLD = 0.65
_WEB_MAX_RESULTS = 5


def _evidence_item(candidate: RetrievedCandidate, mode: str, model_used: Optional[str]) -> EvidenceItem:
    return EvidenceItem(
        citation_id=candidate.citation_id,
        chunk_id=candidate.chunk_id,
        content=candidate.content,
        title=candidate.title,
        source_name=candidate.source_name,
        source_url=candidate.source_url,
        mode=mode,
        model_used=model_used,
        fallback_used=None,
        dense_score=candidate.dense_score,
        fts_score=candidate.fts_score,
        fused_score=candidate.fused_score,
        rerank_score=candidate.rerank_score,
        dense_rank=candidate.dense_rank,
        fts_rank=candidate.fts_rank,
        fused_rank=candidate.fused_rank,
        rerank_rank=candidate.rerank_rank,
        page_number=candidate.page_number,
        language=candidate.language,
        publication_date=candidate.publication_date,
        section_heading=getattr(candidate, "section_heading", None),
    )


def _score_lookup(candidates: list[RetrievedCandidate]) -> dict[str, RetrievedCandidate]:
    return {candidate.citation_id: candidate for candidate in candidates}


def _top_local_confidence(evidence: list[EvidenceItem]) -> Optional[float]:
    """Best local confidence across rerank/fused/dense scores (None if none)."""
    best: Optional[float] = None
    for item in evidence:
        for value in (item.rerank_score, item.fused_score, item.fts_score, item.dense_score):
            if isinstance(value, (int, float)):
                best = float(value) if best is None else max(best, float(value))
    return best


def _web_trigger(query: str, evidence: list[EvidenceItem]) -> tuple[bool, str]:
    """Unified web-trigger semantics (cultural_router Rule B).

    Web is warranted when the query needs freshness, when local RAG is
    empty, or when top local confidence is low. Returns (trigger, reason).
    """
    try:
        from sard.planner.retrieve import is_time_sensitive_query as _is_fresh
    except Exception as exc_import:
        logger.debug("Freshness router import skipped (%s).", type(exc_import).__name__)
        _is_fresh = None  # type: ignore[assignment]
    try:
        if _is_fresh is not None and bool(_is_fresh(query)):
            return True, "query requires freshness or live schedule"
    except Exception as exc_fresh:
        logger.debug("Freshness check skipped (%s).", type(exc_fresh).__name__)
    if not evidence:
        return True, "topic outside local corpus (no RAG hits)"
    top = _top_local_confidence(evidence)
    if top is not None and top < _WEB_TRIGGER_THRESHOLD:
        return True, f"low RAG confidence ({top:.2f} < {_WEB_TRIGGER_THRESHOLD:.2f})"
    return False, ""


def _web_evidence_item(hit: Any, rank: int) -> Optional[EvidenceItem]:
    """Adapt one fanout SearchResult to a graph EvidenceItem (None if unusable).

    Web citation IDs are stable (``CIT-WEB-<sha1[:12]>``) and satisfy
    ``CITATION_ID_RE`` so downstream verify/render treat them like any
    other evidence. Items failing the verify provenance floor (valid
    http(s) URL, title, chunk id, >=20 char content) are dropped here so
    they can never become dangling citations.
    """
    try:
        url = (getattr(hit, "url", "") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            return None
        body = (getattr(hit, "content", "") or getattr(hit, "snippet", "") or "").strip()
        if len(body) < 20:
            return None
        title = (getattr(hit, "title", "") or "").strip() or (getattr(hit, "domain", "") or "").strip()
        if not title:
            return None
        base = (getattr(hit, "id", "") or "").strip()
        if not base:
            canon = getattr(hit, "canonical_url", "") or url
            base = hashlib.sha1(f"{canon}|{getattr(hit, 'content_hash', '')}".encode("utf-8")).hexdigest()
        citation_id = f"CIT-WEB-{base[:12].upper()}"
        source_name = title
        try:
            from sard.rag.search_providers import domain_of as _domain_of
            domain = _domain_of(url)
            if domain:
                source_name = domain
        except Exception as exc_domain:
            logger.debug("Web domain read skipped (%s).", type(exc_domain).__name__)
        return EvidenceItem(
            citation_id=citation_id,
            chunk_id=f"{citation_id}-c1",
            content=body,
            title=title,
            source_name=source_name,
            source_url=url,
            mode="web",
            model_used=None,
            fallback_used=None,
            fused_rank=rank,
            language=getattr(hit, "language", None) or None,
            publication_date=getattr(hit, "published_at", None) or None,
        )
    except Exception as exc_adapt:
        logger.debug("Web evidence adapt skipped (%s).", type(exc_adapt).__name__)
        return None


def _run_web_leg(
    query: str,
    evidence: list[EvidenceItem],
    deps: Any,
) -> tuple[list[EvidenceItem], list[str], dict[str, Any]]:
    """Budgeted Parallel -> Tavily -> Exa fanout for the graph retrieve node.

    Mirrors the legacy router/planner path: trigger on freshness/empty/low
    local confidence, cross-dedup against local evidence by canonical URL,
    apply the shared relevance gate, adapt survivors to EvidenceItems.
    Returns ``(web_evidence, warnings, info)`` where ``info`` carries
    trigger/telemetry flags. Never raises for provider failures;
    cancellation (typed ``DeadlineCancelledError``) propagates.
    """
    warnings: list[str] = []
    info: dict[str, Any] = {
        "web_search_triggered": False,
        "web_search_reason": "",
        "web_result_count": 0,
        "web_unavailable_warning": False,
    }
    trigger, reason = _web_trigger(query, evidence)
    if not trigger:
        return [], warnings, info
    info["web_search_triggered"] = True
    info["web_search_reason"] = reason

    deadline = getattr(deps, "deadline", None)
    cancel_event = getattr(deps, "cancel_event", None)
    if deadline is not None:
        try:
            deadline.check("retrieve:web")
        except Exception:
            # Doomed budget: preserve the reserve for terminal work.
            warnings.append("تعذّر البحث الحي ضمن المهلة؛ سيُبنى الرد على الدليل المحلي.")
            info["web_unavailable_warning"] = True
            return [], warnings, info

    t0 = time.monotonic()
    try:
        from sard.rag.search_providers import fanout_search as _fanout
    except Exception as exc_import:
        logger.debug("Web fanout import skipped (%s).", type(exc_import).__name__)
        warnings.append("البحث الحي غير متاح في هذه البيئة؛ سيُبنى الرد على الدليل المحلي.")
        info["web_unavailable_warning"] = True
        return [], warnings, info

    try:
        results, _telemetry, _flags = _fanout(
            query,
            [query],
            depth="normal",
            max_results=_WEB_MAX_RESULTS,
            deadline=deadline,
            cancel_event=cancel_event,
        )
    except Exception as exc_fanout:
        try:
            from sard.agent.deadline import DeadlineCancelledError as _Cancelled
        except Exception:
            _Cancelled = None  # type: ignore[assignment]
        if _Cancelled is not None and isinstance(exc_fanout, _Cancelled):
            raise
        # Honest degrade: providers failed, never fabricate.
        try:
            from sard.rag.search_providers import (
                resolve_exa_key as _exa,
                resolve_parallel_key as _par,
                resolve_tavily_key as _tav,
            )
            has_key = bool(_par() or _tav() or _exa())
        except Exception:
            import os as _os
            has_key = bool(
                _os.environ.get("PARALLEL_API_KEY", "").strip()
                or _os.environ.get("TAVILY_API_KEY", "").strip()
                or _os.environ.get("EXA_API_KEY", "").strip()
            )
        if not has_key:
            warnings.append("البحث الحي غير مهيأ (لا مفاتيح)؛ سيُبنى الرد على الدليل المحلي.")
        else:
            warnings.append("تعذّر البحث الحي مؤقتًا؛ سيُبنى الرد على الدليل المحلي.")
        info["web_unavailable_warning"] = True
        return [], warnings, info

    if not results:
        warnings.append("لم يعثر البحث الحي على نتائج؛ سيُبنى الرد على الدليل المحلي.")
        info["web_unavailable_warning"] = True
        return [], warnings, info

    try:
        from sard.rag.search_providers import canonicalize_url as _canon
        from sard.rag.search_providers import is_duplicate_of_seen as _is_dup
    except Exception as exc_seen_import:
        logger.debug("Web dedup import skipped (%s).", type(exc_seen_import).__name__)
        _canon = None  # type: ignore[assignment]
        _is_dup = None  # type: ignore[assignment]

    seen_canonicals: dict[str, list[str]] = {}
    for item in evidence:
        try:
            canon = _canon(item.source_url) if _canon is not None else (item.source_url or "").strip().lower()
            if canon:
                seen_canonicals.setdefault(canon, []).append(item.content or "")
        except Exception as exc_seen:
            logger.debug("Local seen-canonical skipped (%s).", type(exc_seen).__name__)
            continue

    candidates: list[dict[str, Any]] = []
    for hit in results:
        try:
            url = getattr(hit, "url", "") or ""
            canon = _canon(url) if _canon is not None else (url or "").strip().lower()
            if canon and _is_dup is not None:
                try:
                    if _is_dup(canon, getattr(hit, "content", "") or "", getattr(hit, "snippet", "") or "", seen_canonicals):
                        continue
                except Exception as exc_dup:
                    logger.debug("Web cross-dedup skipped (%s).", type(exc_dup).__name__)
            body = (getattr(hit, "content", "") or getattr(hit, "snippet", "") or "")
            candidates.append({
                "title": getattr(hit, "title", "") or "",
                "chunk": body,
                "url": url,
                "metadata": {"topic": "", "region": "", "source_type": "web"},
            })
        except Exception as exc_cand:
            logger.debug("Web candidate skipped (%s).", type(exc_cand).__name__)
            continue

    try:
        from sard.rag.relevance import filter_relevant_evidence as _gate
        gated = _gate(query, candidates)
        gated_urls = {(g.get("url") or "") for g in gated}
    except Exception as exc_gate:
        logger.debug("Web relevance gate skipped (%s).", type(exc_gate).__name__)
        gated_urls = {(c.get("url") or "") for c in candidates}

    web_evidence: list[EvidenceItem] = []
    rank = 0
    for hit in results:
        if (getattr(hit, "url", "") or "") not in gated_urls:
            continue
        adapted = _web_evidence_item(hit, rank)
        if adapted is None:
            continue
        if any(e.citation_id == adapted.citation_id for e in evidence):
            continue
        web_evidence.append(adapted)
        rank += 1
        if len(web_evidence) >= _WEB_MAX_RESULTS:
            break

    info["web_result_count"] = len(web_evidence)
    info["web_ms"] = round((time.monotonic() - t0) * 1000, 3)
    if not web_evidence:
        warnings.append("لم تُسفر نتائج البحث الحي عن دليل صالح؛ سيُبنى الرد على الدليل المحلي.")
        info["web_unavailable_warning"] = True
    return web_evidence, warnings, info


def retrieve(state: dict, deps) -> dict:
    run = state.get("run_id") or ""
    start = time.monotonic()
    events = [
        make_event(EVENT_STARTED, run, "retrieve", "started", summary="بدء الاسترجاع")
    ]

    rag_service: Optional[RAGService] = getattr(deps, "rag_service", None)
    plan = state.get("plan")
    filters: dict = {}
    if plan is not None and len(plan.evidence_topics) == 1:
        filters = {"topic": plan.evidence_topics[0]}

    query = state.get("original_request") or ""
    retrieval_queries = [query]
    fallback_events = []
    warnings = []
    evidence: list[EvidenceItem] = []
    mode = RAGMode.UNAVAILABLE.value
    reranking_used = None
    model_route = {}
    timings: dict = {}

    if rag_service is None:
        warnings.append("خدمة الاسترجاع غير مهيأة؛ لا يتوفر دليل خارجي لهذا التشغيل.")
        mode = RAGMode.UNAVAILABLE.value
        events.append(
            make_event(
                EVENT_RETRIEVAL_MODE_CHANGED,
                run,
                "retrieve",
                "unavailable",
                summary="لا توجد خدمة استرجاع — الوضع غير متاح",
            )
        )
    else:
        answer = None
        try:
            answer = rag_service.answer(query, filters=filters if filters else None)
        except Exception:
            # Let the graph guard classify and sanitize typed failures.  Swallowing
            # here would hide authentication/schema/dimension errors and make the
            # run look like an ordinary empty retrieval.
            raise

        if answer is not None:
            raw_mode = getattr(answer, "retrieval_mode", "") or ""
            reranking_used = getattr(answer, "reranker_used", None)
            mode = normalize_retrieval_mode(raw_mode, reranking_used)
            model_route = dict(getattr(answer, "model_route", {}) or {})

            dense_by_cit = _score_lookup(getattr(answer, "dense_candidates", []) or [])
            fts_by_cit = _score_lookup(getattr(answer, "fts_candidates", []) or [])
            fused_by_cit = _score_lookup(getattr(answer, "fused_candidates", []) or [])
            generation_model = model_route.get("generation")

            selected = list(getattr(answer, "selected_context", []) or [])
            for candidate in selected:
                dense = dense_by_cit.get(candidate.citation_id)
                fts = fts_by_cit.get(candidate.citation_id)
                fused = fused_by_cit.get(candidate.citation_id)
                item = _evidence_item(candidate, mode, generation_model)
                merge = dict(
                    dense_score=item.dense_score if dense is None else dense.dense_score,
                    dense_rank=item.dense_rank if dense is None else dense.dense_rank,
                    fts_score=item.fts_score if fts is None else fts.fts_score,
                    fts_rank=item.fts_rank if fts is None else fts.fts_rank,
                    fused_score=item.fused_score if fused is None else fused.fused_score,
                    fused_rank=item.fused_rank if fused is None else fused.fused_rank,
                )
                evidence.append(
                    EvidenceItem(
                        **{
                            **item.__dict__,
                            **merge,
                        }
                    )
                )

            fallback_events.extend(adapt_fallback_events(getattr(answer, "fallback_events", []) or []))
            for fallback in fallback_events:
                if fallback.outcome == "success" and (fallback.degraded or fallback.selected_fallback != "primary"):
                    events.append(
                        make_event(
                            EVENT_MODEL_FALLBACK_ACTIVATED,
                            run,
                            "retrieve",
                            "degraded",
                            summary="تم تفعيل نموذج احتياطي أثناء الاسترجاع",
                            degraded=True,
                        )
                    )

            if mode != raw_mode and raw_mode:
                events.append(
                    make_event(
                        EVENT_RETRIEVAL_MODE_CHANGED,
                        run,
                        "retrieve",
                        mode,
                        summary=f"طبيعَة وضع الاسترجاع إلى {mode}",
                        source_count=len(evidence),
                    )
                )

            for warning in getattr(answer, "warnings", []) or []:
                warnings.append(str(warning))

            fetched_timings = getattr(answer, "timings_ms", {}) or {}
            for key, value in fetched_timings.items():
                if isinstance(value, (int, float)):
                    timings.setdefault(f"retrieval_{key}", round(float(value), 3))

    if not evidence and mode == RAGMode.UNAVAILABLE.value:
        warnings.append("لا يوجد دليل مسترجع لهذا الطلب.")

    # Budgeted web leg (Parallel -> Tavily -> Exa): warranted on
    # freshness, empty local evidence, or low local confidence. Opt-in via
    # GraphDependencies.enable_web_search (live entry points enable it);
    # with no provider keys it appends an honest unavailable warning and
    # changes nothing else.
    web_info: dict[str, Any] = {}
    web_evidence: list[EvidenceItem] = []
    web_warnings: list[str] = []
    if getattr(deps, "enable_web_search", False):
        try:
            web_evidence, web_warnings, web_info = _run_web_leg(query, evidence, deps)
        except Exception as exc_web:
            try:
                from sard.agent.deadline import DeadlineCancelledError as _WebCancelled
            except Exception:
                _WebCancelled = None  # type: ignore[assignment]
            if _WebCancelled is not None and isinstance(exc_web, _WebCancelled):
                raise
            logger.debug("Graph web leg skipped (%s).", type(exc_web).__name__)
            web_evidence, web_warnings = [], []
    if web_evidence:
        evidence.extend(web_evidence)
        retrieval_queries = [*retrieval_queries, f"web:{query}"]
    warnings.extend(web_warnings)
    web_ms = web_info.get("web_ms") if isinstance(web_info, dict) else None
    if isinstance(web_ms, (int, float)):
        timings["retrieve_web_ms"] = web_ms

    duration_ms = (time.monotonic() - start) * 1000
    events.append(
        make_event(
            EVENT_COMPLETED,
            run,
            "retrieve",
            "completed",
            summary=f"اكتمل الاسترجاع بأسلوب {mode}",
            duration_ms=duration_ms,
            source_count=len(evidence),
        )
    )

    return {
        "retrieval_queries": retrieval_queries,
        "retrieval_filters": filters,
        "evidence": evidence,
        "retrieval_mode": mode,
        "reranking_used": reranking_used,
        "retrieval_warnings": warnings,
        "web_search_triggered": bool(web_info.get("web_search_triggered")) if isinstance(web_info, dict) else False,
        "web_search_reason": web_info.get("web_search_reason", "") if isinstance(web_info, dict) else "",
        "web_result_count": int(web_info.get("web_result_count", 0)) if isinstance(web_info, dict) else 0,
        "web_unavailable_warning": bool(web_info.get("web_unavailable_warning")) if isinstance(web_info, dict) else False,
        "model_routes": {"retrieval": model_route},
        "fallback_events": fallback_events,
        "timings": {**timings, "retrieve_ms": duration_ms},
        "progress_events": events,
        "warnings": warnings,
    }
