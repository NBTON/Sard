"""Hybrid retrieval: dense search + Zvec full-text search + deterministic fusion.

Reranking and answer generation are deliberately separate services
(``sard/rag/rerank.py``, ``sard/rag/answer.py``); this module only produces
:class:`~sard.rag.schemas.RetrievalResult` with dense, full-text, and fused
candidate lists plus an honest :class:`~sard.rag.schemas.RetrievalMode`.

Fusion uses Reciprocal Rank Fusion (RRF) — a transparent, deterministic,
parameter-light method — rather than a learned or opaque combiner.
Candidates are deduplicated by chunk ID first, then by content hash (in
case identical text was ingested under two different documents).
"""

from __future__ import annotations

import logging
import time
from types import SimpleNamespace
from dataclasses import dataclass
from typing import Optional

from sard.config.rag import RAGSettings, get_rag_settings
from sard.rag.embeddings import EmbeddingService, _validate_vectors
from sard.rag.fallbacks import (
    AllCandidatesFailedError,
    CircuitBreaker,
    FallbackEvent,
    classify_exception,
)
from sard.rag.schemas import (
    RetrievalFilters,
    RetrievalMode,
    RetrievalResult,
    RetrievedCandidate,
    RewrittenQuery,
    ScoreType,
)
from sard.rag.zvec_store import ZvecRepository

logger = logging.getLogger(__name__)

RRF_K = 60


def reciprocal_rank_fusion(
    dense: list[RetrievedCandidate], fts: list[RetrievedCandidate], k: int = RRF_K
) -> list[RetrievedCandidate]:
    """Deterministic RRF over two ranked lists, deduplicated by chunk ID
    and then by content hash."""
    scores: dict[str, float] = {}
    by_chunk_id: dict[str, RetrievedCandidate] = {}

    for rank, c in enumerate(dense, start=1):
        scores[c.chunk_id] = scores.get(c.chunk_id, 0.0) + 1.0 / (k + rank)
        by_chunk_id.setdefault(c.chunk_id, c)

    for rank, c in enumerate(fts, start=1):
        scores[c.chunk_id] = scores.get(c.chunk_id, 0.0) + 1.0 / (k + rank)
        existing = by_chunk_id.get(c.chunk_id)
        if existing is None:
            by_chunk_id[c.chunk_id] = c
        else:
            existing.fts_score = c.fts_score
            existing.fts_rank = c.fts_rank

    for chunk_id, candidate in by_chunk_id.items():
        candidate.fused_score = scores[chunk_id]

    # Dedup by content hash: keep only the highest-scoring chunk per hash.
    best_by_hash: dict[str, RetrievedCandidate] = {}
    for candidate in by_chunk_id.values():
        key = candidate.content_hash or candidate.chunk_id
        current_best = best_by_hash.get(key)
        if current_best is None or (candidate.fused_score or 0.0) > (current_best.fused_score or 0.0):
            best_by_hash[key] = candidate

    ranked = sorted(best_by_hash.values(), key=lambda c: c.fused_score or 0.0, reverse=True)
    for rank, c in enumerate(ranked, start=1):
        c.fused_rank = rank
    return ranked


def calibrate_candidate_confidence(
    candidate: RetrievedCandidate,
    dense_threshold: float = 0.65,
    min_confidence: float = 0.60,
) -> float:
    """Compute a calibrated confidence score [0.0, 1.0] across heterogeneous score scales."""
    dense_score = candidate.dense_score
    fts_score = candidate.fts_score

    # 1. Dense cosine similarity calibration [-1.0, 1.0]
    dense_conf = 0.0
    if dense_score is not None:
        if dense_score >= 0.80:
            dense_conf = min(0.98, 0.85 + (dense_score - 0.80) * 0.6)
        elif dense_score >= dense_threshold:
            dense_conf = 0.65 + (dense_score - dense_threshold) * 1.3
        elif dense_score >= (dense_threshold - 0.10):
            dense_conf = 0.35 + (dense_score - (dense_threshold - 0.10)) * 3.0
        else:
            dense_conf = max(0.0, (dense_score - 0.30) * 1.0) if dense_score > 0.30 else 0.0

    # 2. FTS score calibration (BM25 raw score scale)
    fts_conf = 0.0
    if fts_score is not None and fts_score > 0:
        f_score = float(fts_score)
        if f_score >= 1.5:
            fts_conf = min(0.95, 0.70 + (f_score - 1.5) * 0.1)
        elif f_score >= 1.0:
            fts_conf = 0.55 + (f_score - 1.0) * 0.30
        elif f_score >= 0.5:
            fts_conf = 0.35 + (f_score - 0.5) * 0.40
        else:
            # Low BM25 score (<0.5) indicates accidental single-stopword hit (e.g. "في")
            fts_conf = f_score * 0.50

    # 3. Channel combination
    if dense_score is not None and fts_score is not None and fts_score > 0:
        if dense_score >= dense_threshold and fts_conf >= 0.55:
            # Strong dual-channel corroboration
            conf = min(0.98, max(dense_conf, fts_conf) + 0.08)
        elif dense_score >= dense_threshold:
            # Strong dense with weak FTS
            conf = dense_conf
        elif fts_conf >= 0.60:
            # Strong FTS with weak dense
            conf = fts_conf
        else:
            # Both channels weak or accidental nearest-neighbor / stopword
            conf = max(dense_conf, fts_conf) * 0.4
    elif fts_score is not None and fts_score > 0:
        conf = fts_conf
    elif dense_score is not None:
        if dense_score >= dense_threshold:
            conf = dense_conf
        else:
            # Uncorroborated low-similarity nearest neighbor: severe penalty
            conf = dense_conf * 0.4
    else:
        conf = 0.0

    candidate.confidence_score = round(conf, 4)
    candidate.score_type = ScoreType.CALIBRATED_CONFIDENCE.value
    candidate.is_relevant = (candidate.confidence_score >= min_confidence)
    return candidate.confidence_score


@dataclass
class RetrievalDependencies:
    repository: ZvecRepository
    embedding_model_id: str
    embedding_service: EmbeddingService


class RetrievalService:
    def __init__(
        self,
        deps: RetrievalDependencies,
        settings: Optional[RAGSettings] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        self._deps = deps
        self._settings = settings or get_rag_settings()
        self._breaker = circuit_breaker

    def retrieve(
        self, rewritten: RewrittenQuery, filters: Optional[RetrievalFilters] = None
    ) -> RetrievalResult:
        settings = self._settings
        warnings: list[str] = []
        fallback_events = []
        dense_candidates: list[RetrievedCandidate] = []
        fts_candidates: list[RetrievedCandidate] = []
        dense_available = True

        query_text = rewritten.normalized_question or rewritten.original_question
        if isinstance(filters, dict):
            filters = RetrievalFilters(**filters)

        dense_start = time.monotonic()
        try:
            outcome = self._embed_query_compat(query_text)
            query_vector = outcome.vectors[0]
            fallback_events.extend(getattr(outcome, "events", []))
            dense_candidates = self._deps.repository.dense_search(
                query_vector, topk=settings.dense_candidates, filters=filters
            )
        except AllCandidatesFailedError as exc:
            dense_available = False
            fallback_events.extend(exc.events)
            warnings.append(
                "تعذّر استرجاع النتائج الدلالية (dense) لعدم توفر نموذج التضمين؛ "
                "تم التحويل إلى وضع البحث النصي الكامل فقط."
            )
            logger.warning("Dense retrieval unavailable: %s", exc)
        except Exception as exc:
            dense_available = False
            fallback_events.append(
                FallbackEvent(
                    use_case="dense_retrieval",
                    requested_model=self._deps.embedding_model_id,
                    resolved_model=self._deps.embedding_model_id,
                    endpoint_type="zvec",
                    attempt=1,
                    failure_category=classify_exception(exc),
                    selected_fallback="full_text",
                    quality_degraded=True,
                    latency_ms=(time.monotonic() - dense_start) * 1000,
                    outcome="failure",
                )
            )
            warnings.append(
                "تعذّر استرجاع النتائج الدلالية بسبب خطأ في نموذج التضمين أو مخطط المجموعة؛ "
                "تم التحويل إلى وضع البحث النصي الكامل إن كان متاحًا."
            )

        if settings.enable_fts:
            fts_start = time.monotonic()
            try:
                fts_queries = list(
                    dict.fromkeys(
                        q.strip()
                        for q in [query_text, rewritten.original_question, *rewritten.search_variants]
                        if q and q.strip()
                    )
                )
                fts_lists = [
                    self._deps.repository.fts_search(
                        query, topk=settings.fts_candidates, filters=filters
                    )
                    for query in fts_queries
                ]
                fts_candidates = self._merge_channel_candidates(fts_lists, "fts_score")
            except Exception as exc:
                fallback_events.append(
                    FallbackEvent(
                        use_case="full_text_retrieval",
                        requested_model="zvec-fts",
                        resolved_model="zvec-fts",
                        endpoint_type="zvec",
                        attempt=1,
                        failure_category=classify_exception(exc),
                        selected_fallback="unavailable",
                        quality_degraded=True,
                        latency_ms=(time.monotonic() - fts_start) * 1000,
                        outcome="failure",
                    )
                )
                logger.warning("Full-text retrieval failed: %s", type(exc).__name__)
                warnings.append("تعذّر تنفيذ البحث النصي الكامل (FTS) لهذا الاستعلام.")

        if dense_available and (fts_candidates or not settings.enable_fts):
            fused = reciprocal_rank_fusion(dense_candidates, fts_candidates)
            mode = RetrievalMode.HYBRID if fts_candidates else RetrievalMode.DENSE_ONLY
        elif dense_available:
            fused = reciprocal_rank_fusion(dense_candidates, [])
            mode = RetrievalMode.DENSE_ONLY
        elif fts_candidates:
            fused = reciprocal_rank_fusion([], fts_candidates)
            mode = RetrievalMode.FTS_ONLY_EMERGENCY
        else:
            fused = []
            mode = RetrievalMode.UNAVAILABLE
            warnings.append("تعذّر الاسترجاع بالكامل: لا نتائج دلالية ولا نصية متاحة.")

        # Calibrate all candidate scores against explicit threshold
        dense_thresh = getattr(settings, "dense_similarity_threshold", 0.65)
        min_conf = getattr(settings, "min_evidence_confidence", 0.60)

        for candidate in fused:
            calibrate_candidate_confidence(candidate, dense_thresh, min_conf)

        raw_fused = fused[: settings.fused_candidates]
        relevant_fused = [c for c in raw_fused if c.is_relevant]

        has_relevant = bool(relevant_fused)
        top_confidence = raw_fused[0].confidence_score if raw_fused else 0.0

        if not has_relevant:
            relevance_decision = "no_relevant_evidence"
            warnings.append("لم يتم العثور على شواهد محلية كافية الثقة بالاستعلام.")
            fused_to_return = []
        else:
            relevance_decision = "relevant"
            fused_to_return = relevant_fused

        return RetrievalResult(
            query=rewritten.original_question,
            rewritten=rewritten,
            dense_candidates=dense_candidates,
            fts_candidates=fts_candidates,
            fused_candidates=fused_to_return,
            reranked_candidates=[],
            mode=mode,
            reranker_used="",
            fallback_events=fallback_events,
            warnings=warnings,
            is_relevant=has_relevant,
            relevance_decision=relevance_decision,
            top_confidence=top_confidence,
        )

    @staticmethod
    def _merge_channel_candidates(
        candidate_lists: list[list[RetrievedCandidate]], score_field: str
    ) -> list[RetrievedCandidate]:
        by_key: dict[str, RetrievedCandidate] = {}
        for candidates in candidate_lists:
            for candidate in candidates:
                key = candidate.content_hash or candidate.chunk_id
                current = by_key.get(key)
                if current is None or (getattr(candidate, score_field) or 0.0) > (
                    getattr(current, score_field) or 0.0
                ):
                    by_key[key] = candidate
        merged = sorted(
            by_key.values(),
            key=lambda candidate: getattr(candidate, score_field) or 0.0,
            reverse=True,
        )
        for rank, candidate in enumerate(merged, start=1):
            if score_field == "fts_score":
                candidate.fts_rank = rank
        return merged

    def _embed_query_compat(self, query_text: str):
        """Call the internal embedding service while accepting LangChain-shaped fakes.

        The production service needs the collection's model ID to enforce model
        isolation.  LangChain ``Embeddings`` implementations expose only
        ``embed_query(text)``.  Keeping this adapter at the boundary avoids
        leaking either calling convention into the rest of retrieval.
        """
        try:
            outcome = self._deps.embedding_service.embed_query(
                self._deps.embedding_model_id, query_text
            )
        except TypeError as first_error:
            try:
                outcome = self._deps.embedding_service.embed_query(query_text)
            except TypeError:
                raise first_error
        if hasattr(outcome, "vectors"):
            _validate_vectors(
                outcome.vectors,
                expected_dim=self._deps.repository.embedding_dimension,
                expected_count=1,
            )
            return outcome
        if isinstance(outcome, list):
            vector = outcome[0] if outcome and isinstance(outcome[0], list) else outcome
            _validate_vectors(
                [vector],
                expected_dim=self._deps.repository.embedding_dimension,
                expected_count=1,
            )
            return SimpleNamespace(vectors=[vector], events=[])
        raise TypeError("Embedding service returned an unsupported query result shape.")
