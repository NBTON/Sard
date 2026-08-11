"""Top-level, provider-independent RAG service.

This is the ONLY RAG entry point the Streamlit UI (and, later, the
LangGraph ``retrieve``/``answer`` nodes) should call. It never exposes a
Zvec ``Collection`` or an NVIDIA SDK object — only plain dataclasses from
``sard.rag.schemas``.

    answer = rag_service.answer(question="...", filters={"topic": "..."})

Internally it wires together (each independently testable/replaceable):

    query_rewriter -> retrieve -> rerank -> answer
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sard.config.rag import RAGSettings, get_rag_settings
from sard.rag.answer import AnswerService
from sard.rag.embeddings import EmbeddingService
from sard.rag.fallbacks import CircuitBreaker
from sard.rag.normalize import normalize_arabic
from sard.rag.query_rewriter import QueryRewriteService
from sard.rag.rerank import RerankService
from sard.rag.retrieve import RetrievalDependencies, RetrievalService
from sard.rag.schemas import RAGAnswer, RetrievalFilters, RewrittenQuery
from sard.rag.zvec_store import ZvecRepository

logger = logging.getLogger(__name__)


class RAGServiceUnavailableError(Exception):
    """Raised when no ingested collection exists yet for the configured
    embedding model. Safe to display to end users."""


@dataclass
class RAGService:
    repository: ZvecRepository
    embedding_service: EmbeddingService
    embedding_model_id: str
    query_rewrite_service: QueryRewriteService
    retrieval_service: RetrievalService
    rerank_service: RerankService
    answer_service: AnswerService
    settings: RAGSettings

    @classmethod
    def open_readonly(cls, settings: Optional[RAGSettings] = None) -> "RAGService":
        """Open the RAG service against an already-ingested collection.

        Does not require an NVIDIA API key just to open: the collection's
        embedding dimension is read from its on-disk metadata (recorded at
        ingestion time), not re-discovered via a network probe.
        """
        settings = settings or get_rag_settings()
        embedding_model_id = settings.embedding_route.primary

        repository = ZvecRepository.find_existing_for_model(
            settings.zvec_collection_path, embedding_model_id
        )
        if repository is None:
            raise RAGServiceUnavailableError(
                "لا توجد مجموعة بيانات مُفهرسة بعد لنموذج التضمين الحالي "
                f"({embedding_model_id}). شغّل أمر الفهرسة أولًا: "
                "`uv run python -m sard.cli.rag ingest data/corpus`."
            )

        breaker = CircuitBreaker()
        embedding_service = EmbeddingService(settings=settings, circuit_breaker=breaker)
        query_rewrite_service = QueryRewriteService(settings=settings, circuit_breaker=breaker)
        retrieval_service = RetrievalService(
            RetrievalDependencies(
                repository=repository,
                embedding_model_id=embedding_model_id,
                embedding_service=embedding_service,
            ),
            settings=settings,
            circuit_breaker=breaker,
        )
        rerank_service = RerankService(settings=settings, circuit_breaker=breaker)
        answer_service = AnswerService(settings=settings, circuit_breaker=breaker)

        return cls(
            repository=repository,
            embedding_service=embedding_service,
            embedding_model_id=embedding_model_id,
            query_rewrite_service=query_rewrite_service,
            retrieval_service=retrieval_service,
            rerank_service=rerank_service,
            answer_service=answer_service,
            settings=settings,
        )

    def answer(self, question: str, filters: Optional[dict] = None) -> RAGAnswer:
        timings: dict[str, float] = {}
        all_events = []

        t0 = time.monotonic()
        if self.settings.enable_query_rewrite:
            rewritten, rw_events = self.query_rewrite_service.rewrite(question)
        else:
            normalized = normalize_arabic(question)
            rewritten, rw_events = (
                RewrittenQuery(
                    original_question=question,
                    normalized_question=normalized,
                    search_variants=[normalized],
                    rewrite_succeeded=False,
                ),
                [],
            )
        all_events.extend(rw_events)
        timings["query_rewrite_ms"] = (time.monotonic() - t0) * 1000

        retrieval_filters: Optional[RetrievalFilters] = None
        if filters:
            known = {"topic", "source_name", "language", "publication_date"}
            unknown = sorted(set(filters) - known)
            if unknown:
                raise ValueError(
                    "Unknown retrieval filter key(s): " + ", ".join(unknown)
                    + f". Supported keys: {sorted(known)}."
                )
            retrieval_filters = RetrievalFilters(**filters)

        t0 = time.monotonic()
        retrieval_result = self.retrieval_service.retrieve(rewritten, retrieval_filters)
        all_events.extend(retrieval_result.fallback_events)
        timings["retrieve_ms"] = (time.monotonic() - t0) * 1000

        t0 = time.monotonic()
        rerank_outcome = self.rerank_service.rerank(
            question, retrieval_result.fused_candidates, top_n=self.settings.final_top_k
        )
        all_events.extend(rerank_outcome.events)
        timings["rerank_ms"] = (time.monotonic() - t0) * 1000

        t0 = time.monotonic()
        answer_result, gen_events = self.answer_service.generate(question, rerank_outcome.candidates)
        all_events.extend(gen_events)
        timings["generation_ms"] = (time.monotonic() - t0) * 1000

        warnings = list(retrieval_result.warnings) + list(answer_result.warnings)

        model_route = {
            "embedding": next(
                (
                    event.resolved_model
                    for event in reversed(retrieval_result.fallback_events)
                    if getattr(event, "outcome", None) == "success"
                ),
                self.embedding_model_id,
            ),
            "query_rewrite": rewritten.model_used,
            "rerank": rerank_outcome.model_used or rerank_outcome.method_used,
            "generation": answer_result.model_used,
        }

        timings["total_ms"] = sum(timings.values())

        return RAGAnswer(
            question=question,
            rewritten_queries=rewritten.search_variants,
            dense_candidates=retrieval_result.dense_candidates,
            fts_candidates=retrieval_result.fts_candidates,
            fused_candidates=retrieval_result.fused_candidates,
            selected_context=rerank_outcome.candidates,
            answer_text=answer_result.answer_text,
            citations=answer_result.citations,
            model_route=model_route,
            fallback_events=all_events,
            retrieval_mode=retrieval_result.mode.value,
            reranker_used=rerank_outcome.method_used,
            timings_ms=timings,
            warnings=warnings,
        )

    def close(self) -> None:
        self.repository.close()
