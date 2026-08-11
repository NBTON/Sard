"""NVIDIA NIM reranking, with a transparent deterministic fallback chain.

Route (per spec):

1. Primary: ``NVIDIA_RERANK_MODEL_PRIMARY`` (``rerank-qa-mistral-4b``).
2. First fallback: deterministic dense/full-text Reciprocal Rank Fusion
   (already computed upstream — see ``sard/rag/retrieve.py``'s fusion step).
3. Second fallback: dense-search ranking alone.
4. Last fallback: full-text ranking alone.

Reranking never fails the whole RAG request: whichever tier is used, the
result always records which method actually ran (``method_used``) so the
active retrieval/reranking route is observable end-to-end.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from sard.config.rag import RAGSettings, build_rerank_model, get_rag_settings
from sard.rag.chunking import approx_token_count
from sard.rag.fallbacks import (
    AllCandidatesFailedError,
    CircuitBreaker,
    FailureCategory,
    FallbackClassifiedError,
    FallbackEvent,
    ModelCandidate,
    run_with_fallback,
)
from sard.rag.schemas import RetrievedCandidate

MAX_PASSAGE_TOKENS = 400  # token-aware truncation for the reranker call only


@dataclass
class RerankOutcome:
    candidates: list[RetrievedCandidate]
    method_used: str  # "nvidia" | "rrf_fallback" | "dense_fallback" | "fts_fallback"
    events: list[FallbackEvent]
    model_used: Optional[str] = None


def _truncate_for_rerank(text: str, max_tokens: int = MAX_PASSAGE_TOKENS) -> str:
    words = text.split()
    if len(words) <= max_tokens:
        return text
    return " ".join(words[:max_tokens])


class RerankService:
    def __init__(
        self,
        settings: Optional[RAGSettings] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        rerank_model_factory=build_rerank_model,
    ):
        self._settings = settings or get_rag_settings()
        self._breaker = circuit_breaker or CircuitBreaker()
        self._rerank_model_factory = rerank_model_factory

    def _candidates(self) -> list[ModelCandidate]:
        route = self._settings.rerank_route
        endpoint_type = "self_hosted" if self._settings.rerank_base_url else "hosted"
        return [ModelCandidate(model_id=route.primary, endpoint_type=endpoint_type, label="primary")]

    def rerank(
        self, query: str, candidates: list[RetrievedCandidate], top_n: int
    ) -> RerankOutcome:
        if not candidates:
            return RerankOutcome(candidates=[], method_used="rrf_fallback", events=[])

        if not self._settings.enable_rerank:
            return self._fallback_rank(candidates, top_n, method="rrf_fallback")

        nvidia_candidates = self._candidates()

        def call(candidate: ModelCandidate) -> list[RetrievedCandidate]:
            from langchain_core.documents import Document

            model = self._rerank_model_factory(candidate.model_id, self._settings)
            docs = [
                Document(
                    page_content=_truncate_for_rerank(c.content),
                    metadata={"chunk_id": c.chunk_id, "_index": i},
                )
                for i, c in enumerate(candidates)
            ]
            reranked_docs = model.compress_documents(documents=docs, query=query)
            if not reranked_docs:
                raise FallbackClassifiedError(
                    FailureCategory.MALFORMED_OUTPUT, "NVIDIA reranker returned no results."
                )

            by_chunk_id = {c.chunk_id: c for c in candidates}
            output: list[RetrievedCandidate] = []
            seen_ids: set[str] = set()
            for rank, doc in enumerate(reranked_docs, start=1):
                chunk_id = doc.metadata.get("chunk_id")
                original = by_chunk_id.get(chunk_id)
                if original is None:
                    raise FallbackClassifiedError(
                        FailureCategory.MALFORMED_OUTPUT,
                        "NVIDIA reranker returned an unknown chunk ID.",
                    )
                if chunk_id in seen_ids:
                    raise FallbackClassifiedError(
                        FailureCategory.MALFORMED_OUTPUT,
                        "NVIDIA reranker returned a duplicate chunk ID.",
                    )
                seen_ids.add(chunk_id)
                score = doc.metadata.get("relevance_score")
                if not isinstance(score, (int, float)) or not math.isfinite(float(score)):
                    raise FallbackClassifiedError(
                        FailureCategory.MALFORMED_OUTPUT,
                        "NVIDIA reranker returned a non-numeric relevance score.",
                    )
                original.rerank_score = float(score)
                original.rerank_rank = rank
                output.append(original)
            if not output:
                raise FallbackClassifiedError(
                    FailureCategory.MALFORMED_OUTPUT,
                    "NVIDIA reranker results could not be mapped back to source chunks.",
                )
            return output[:top_n]

        try:
            result, events = run_with_fallback(
                "rerank",
                nvidia_candidates,
                call,
                max_retries_per_candidate=max(1, self._settings.max_retries),
                circuit_breaker=self._breaker,
            )
            model_used = next(
                (
                    event.resolved_model
                    for event in reversed(events)
                    if event.outcome == "success"
                ),
                None,
            )
            return RerankOutcome(
                candidates=result,
                method_used="nvidia",
                events=events,
                model_used=model_used,
            )
        except AllCandidatesFailedError as exc:
            outcome = self._fallback_rank(candidates, top_n, method="rrf_fallback")
            outcome.events = exc.events
            return outcome

    @staticmethod
    def _fallback_rank(
        candidates: list[RetrievedCandidate], top_n: int, method: str
    ) -> RerankOutcome:
        """Deterministic fallback: prefer existing fused rank, else dense,
        else full-text ranking — whichever score is actually present."""
        has_fused = any(c.fused_score is not None for c in candidates)
        has_dense = any(c.dense_score is not None for c in candidates)

        if has_fused:
            ranked = sorted(candidates, key=lambda c: c.fused_score or 0.0, reverse=True)
            chosen_method = method
        elif has_dense:
            ranked = sorted(candidates, key=lambda c: c.dense_score or 0.0, reverse=True)
            chosen_method = "dense_fallback"
        else:
            ranked = sorted(candidates, key=lambda c: c.fts_score or 0.0, reverse=True)
            chosen_method = "fts_fallback"

        for rank, c in enumerate(ranked, start=1):
            c.rerank_rank = rank
        return RerankOutcome(candidates=ranked[:top_n], method_used=chosen_method, events=[])
