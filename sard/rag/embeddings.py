"""NVIDIA NIM embeddings with strict model-mixing prevention.

Wraps ``NVIDIAEmbeddings`` (via the central factory in
``sard.config.rag``) behind the fallback policy in
``sard.rag.fallbacks``. Implements the embedding route from the spec:

1. Primary model (``NVIDIA_EMBEDDING_MODEL_PRIMARY``), retried within the
   configured retry budget.
2. The SAME model at a configured alternate endpoint (self-hosted <->
   hosted), if one is configured.
3. Anything beyond that (switching to ``nv-embed-v1``, or falling back to
   full-text-only retrieval) is an explicit decision made by the caller
   (``sard/rag/ingest.py`` / ``sard/rag/retrieve.py``) — this module never
   silently substitutes a different embedding model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from sard.config.rag import RAGSettings, build_embeddings_model, get_rag_settings
from sard.rag.fallbacks import (
    AllCandidatesFailedError,
    CircuitBreaker,
    FailureCategory,
    FallbackClassifiedError,
    FallbackEvent,
    ModelCandidate,
    run_with_fallback,
)

DEFAULT_BATCH_SIZE = 16
PROBE_TEXT = "نص تجريبي لتحديد أبعاد المتجه."


@dataclass
class EmbeddingOutcome:
    vectors: list[list[float]]
    model_used: str
    events: list[FallbackEvent]


def _validate_vectors(
    vectors: list[list[float]],
    expected_dim: Optional[int] = None,
    expected_count: Optional[int] = None,
) -> int:
    if expected_count is not None and len(vectors) != expected_count:
        raise FallbackClassifiedError(
            FailureCategory.MALFORMED_OUTPUT,
            f"Embedding call returned {len(vectors)} vectors for {expected_count} texts.",
        )
    if not vectors:
        raise FallbackClassifiedError(
            FailureCategory.MALFORMED_OUTPUT, "Embedding call returned no vectors."
        )
    dim = len(vectors[0])
    if dim == 0:
        raise FallbackClassifiedError(
            FailureCategory.EMBEDDING_DIMENSION_MISMATCH, "Embedding vector has zero dimension."
        )
    for vec in vectors:
        if len(vec) != dim:
            raise FallbackClassifiedError(
                FailureCategory.EMBEDDING_DIMENSION_MISMATCH,
                f"Inconsistent embedding dimensions within one batch: {len(vec)} vs {dim}.",
            )
        if not all(math.isfinite(x) for x in vec):
            raise FallbackClassifiedError(
                FailureCategory.MALFORMED_OUTPUT,
                "Embedding vector contains non-finite (NaN/inf) values.",
            )
        if all(x == 0.0 for x in vec):
            raise FallbackClassifiedError(
                FailureCategory.MALFORMED_OUTPUT, "Embedding vector is entirely empty/zero."
            )
    if expected_dim is not None and dim != expected_dim:
        raise FallbackClassifiedError(
            FailureCategory.EMBEDDING_DIMENSION_MISMATCH,
            f"Embedding dimension {dim} does not match expected {expected_dim}.",
        )
    return dim


def _endpoint_candidates(model_id: str, settings: RAGSettings) -> list[ModelCandidate]:
    """The SAME model at up to two endpoints: configured, then hosted default."""
    candidates: list[ModelCandidate] = []
    if settings.embedding_base_url:
        candidates.append(ModelCandidate(model_id=model_id, endpoint_type="self_hosted", label="primary"))
        candidates.append(
            ModelCandidate(model_id=model_id, endpoint_type="hosted", label="alternate_endpoint")
        )
    else:
        candidates.append(ModelCandidate(model_id=model_id, endpoint_type="hosted", label="primary"))
    return candidates


class EmbeddingService:
    """Embeds document chunks (passage mode) and queries (query mode)
    using exactly one, explicitly-chosen embedding model per call.
    """

    def __init__(
        self,
        settings: Optional[RAGSettings] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        model_factory=build_embeddings_model,
    ):
        self._settings = settings or get_rag_settings()
        self._breaker = circuit_breaker or CircuitBreaker()
        self._model_factory = model_factory

    def _build_for_candidate(self, candidate: ModelCandidate):
        settings = self._settings
        if candidate.endpoint_type == "hosted":
            effective_settings = RAGSettings(**{**settings.__dict__, "embedding_base_url": None})
        else:
            effective_settings = settings
        return self._model_factory(candidate.model_id, effective_settings)

    def embed_documents(self, model_id: str, texts: list[str], expected_dim: Optional[int] = None) -> EmbeddingOutcome:
        """Embed a batch of corpus chunks in passage/document mode."""
        candidates = _endpoint_candidates(model_id, self._settings)

        def call(candidate: ModelCandidate):
            model = self._build_for_candidate(candidate)
            vectors = model.embed_documents(texts)
            _validate_vectors(vectors, expected_dim, expected_count=len(texts))
            return vectors

        vectors, events = run_with_fallback(
            "embedding_documents",
            candidates,
            call,
            max_retries_per_candidate=max(1, self._settings.max_retries),
            circuit_breaker=self._breaker,
        )
        return EmbeddingOutcome(vectors=vectors, model_used=model_id, events=events)

    def embed_query(self, model_id: str, text: str, expected_dim: Optional[int] = None) -> EmbeddingOutcome:
        """Embed a single user query in query mode."""
        candidates = _endpoint_candidates(model_id, self._settings)

        def call(candidate: ModelCandidate):
            model = self._build_for_candidate(candidate)
            vector = model.embed_query(text)
            _validate_vectors([vector], expected_dim, expected_count=1)
            return [vector]

        vectors, events = run_with_fallback(
            "embedding_query",
            candidates,
            call,
            max_retries_per_candidate=max(1, self._settings.max_retries),
            circuit_breaker=self._breaker,
        )
        return EmbeddingOutcome(vectors=vectors, model_used=model_id, events=events)

    def discover_dimension(self, model_id: str) -> int:
        """Discover the embedding dimension from a real probe call.

        Never hardcodes a dimension — required before creating a Zvec
        collection so the collection path can encode the true dimension.
        """
        outcome = self.embed_documents(model_id, [PROBE_TEXT])
        return len(outcome.vectors[0])


__all__ = [
    "EmbeddingService",
    "EmbeddingOutcome",
    "AllCandidatesFailedError",
    "DEFAULT_BATCH_SIZE",
]
