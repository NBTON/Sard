"""Retrieval node: ``retrieve``.

Calls ONLY the injected Step 3 ``RAGService`` provider-independent interface
(never Zvec or an SDK directly), passing the Arabic query plus supported
metadata filters.  Adapts the returned ``RAGAnswer`` into typed graph
evidence with stable citation IDs, per-channel scores, models, normalized
mode, fallbacks, timings and warnings.
"""

from __future__ import annotations

import time
from typing import Optional

from sard.agent.events import (
    EVENT_COMPLETED,
    EVENT_MODEL_FALLBACK_ACTIVATED,
    EVENT_RETRIEVAL_MODE_CHANGED,
    EVENT_STARTED,
    SafeFallbackEvent,
    make_event,
)
from sard.agent.routing import normalize_retrieval_mode
from sard.agent.state import EvidenceItem, RAGMode
from sard.rag.schemas import RetrievedCandidate
from sard.rag.service import RAGService


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

            for raw_event in getattr(answer, "fallback_events", []) or []:
                fallback_events.append(
                    SafeFallbackEvent(
                        use_case=getattr(raw_event, "use_case", ""),
                        requested_model=getattr(raw_event, "requested_model", ""),
                        resolved_model=getattr(raw_event, "resolved_model", ""),
                        attempt=getattr(raw_event, "attempt", 0),
                        outcome=getattr(raw_event, "outcome", ""),
                        degraded=getattr(raw_event, "quality_degraded", False),
                        failure_category=(
                            getattr(getattr(raw_event, "failure_category", None), "value", None)
                        ),
                        latency_ms=getattr(raw_event, "latency_ms", 0.0),
                    )
                )
                if getattr(raw_event, "outcome", "") == "success" and (
                    getattr(raw_event, "quality_degraded", False)
                    or getattr(raw_event, "selected_fallback", "primary") != "primary"
                ):
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
        "model_routes": {"retrieval": model_route},
        "fallback_events": fallback_events,
        "timings": {**timings, "retrieve_ms": duration_ms},
        "progress_events": events,
        "warnings": warnings,
    }