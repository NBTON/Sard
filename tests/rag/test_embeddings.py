"""EmbeddingService tests with a fake NVIDIAEmbeddings-shaped model
(no network access, no API key required).
"""

from __future__ import annotations

import pytest

from sard.config.rag import RAGSettings, ModelRoute
from sard.rag.embeddings import EmbeddingService
from sard.rag.fallbacks import AllCandidatesFailedError, CircuitBreaker


def _settings(**overrides) -> RAGSettings:
    base = dict(
        nvidia_api_key="nvapi-test",
        chat_base_url=None,
        embedding_base_url=None,
        rerank_base_url=None,
        chat_route=ModelRoute("generation", "chat-primary", ("chat-fb1",)),
        query_route=ModelRoute("query_rewrite", "query-primary", ("query-fb1",)),
        embedding_route=ModelRoute("embedding", "embed-primary", ()),
        embedding_fallback_model="nv-embed-v1",
        rerank_route=ModelRoute("rerank", "rerank-primary", ()),
        vision_route=ModelRoute("vision", "vision-primary", ()),
        translation_route=ModelRoute("translation", "translate-primary", ()),
        safety_route=ModelRoute("safety", "safety-primary", ()),
        request_timeout_seconds=5.0,
        max_retries=2,
        zvec_collection_path="data/zvec/test",
        dense_candidates=10,
        fts_candidates=10,
        fused_candidates=10,
        final_top_k=5,
        enable_query_rewrite=True,
        enable_fts=True,
        enable_rerank=True,
    )
    base.update(overrides)
    return RAGSettings(**base)


class _FakeEmbeddingsModel:
    def __init__(self, dim=4, vectors_override=None, raise_error=None):
        self.dim = dim
        self.vectors_override = vectors_override
        self.raise_error = raise_error

    def embed_documents(self, texts):
        if self.raise_error:
            raise self.raise_error
        if self.vectors_override is not None:
            return self.vectors_override
        return [[0.1] * self.dim for _ in texts]

    def embed_query(self, text):
        if self.raise_error:
            raise self.raise_error
        if self.vectors_override is not None:
            return self.vectors_override[0]
        return [0.1] * self.dim


def test_embed_documents_success():
    service = EmbeddingService(
        settings=_settings(),
        model_factory=lambda model_id, settings: _FakeEmbeddingsModel(dim=4),
    )
    outcome = service.embed_documents("embed-primary", ["نص أول", "نص ثاني"])
    assert len(outcome.vectors) == 2
    assert len(outcome.vectors[0]) == 4
    assert outcome.model_used == "embed-primary"


def test_embed_documents_rejects_dimension_mismatch_within_batch():
    bad_model = _FakeEmbeddingsModel(vectors_override=[[0.1, 0.2], [0.1, 0.2, 0.3]])
    service = EmbeddingService(
        settings=_settings(max_retries=1),
        model_factory=lambda model_id, settings: bad_model,
    )
    with pytest.raises(AllCandidatesFailedError):
        service.embed_documents("embed-primary", ["a", "b"])


def test_embed_documents_rejects_short_batch_output():
    bad_model = _FakeEmbeddingsModel(vectors_override=[[0.1, 0.2, 0.3, 0.4]])
    service = EmbeddingService(
        settings=_settings(max_retries=1),
        model_factory=lambda model_id, settings: bad_model,
    )
    with pytest.raises(AllCandidatesFailedError):
        service.embed_documents("embed-primary", ["a", "b"])


def test_embed_documents_rejects_non_finite_values():
    bad_model = _FakeEmbeddingsModel(vectors_override=[[float("nan"), 0.2, 0.3, 0.4]])
    service = EmbeddingService(
        settings=_settings(max_retries=1),
        model_factory=lambda model_id, settings: bad_model,
    )
    with pytest.raises(AllCandidatesFailedError):
        service.embed_documents("embed-primary", ["a"])


def test_embed_documents_rejects_all_zero_vector():
    bad_model = _FakeEmbeddingsModel(vectors_override=[[0.0, 0.0, 0.0, 0.0]])
    service = EmbeddingService(
        settings=_settings(max_retries=1),
        model_factory=lambda model_id, settings: bad_model,
    )
    with pytest.raises(AllCandidatesFailedError):
        service.embed_documents("embed-primary", ["a"])


def test_embed_falls_back_to_alternate_endpoint_when_self_hosted_configured():
    calls = []

    def factory(model_id, settings):
        endpoint = "self_hosted" if settings.embedding_base_url else "hosted"
        calls.append(endpoint)
        if endpoint == "self_hosted":
            return _FakeEmbeddingsModel(raise_error=Exception("connection refused"))
        return _FakeEmbeddingsModel(dim=4)

    service = EmbeddingService(
        settings=_settings(embedding_base_url="https://my-nim.local/v1", max_retries=1),
        model_factory=factory,
    )
    outcome = service.embed_documents("embed-primary", ["نص"])
    assert calls == ["self_hosted", "hosted"]
    assert len(outcome.vectors[0]) == 4


def test_discover_dimension_uses_a_real_probe_call():
    service = EmbeddingService(
        settings=_settings(),
        model_factory=lambda model_id, settings: _FakeEmbeddingsModel(dim=7),
    )
    assert service.discover_dimension("embed-primary") == 7


def test_embed_query_uses_query_mode_not_document_mode():
    class _ModeTrackingModel:
        def embed_documents(self, texts):
            raise AssertionError("should not be called for a query embedding")

        def embed_query(self, text):
            return [0.5, 0.5, 0.5, 0.5]

    service = EmbeddingService(
        settings=_settings(),
        model_factory=lambda model_id, settings: _ModeTrackingModel(),
    )
    outcome = service.embed_query("embed-primary", "سؤال المستخدم")
    assert outcome.vectors[0] == [0.5, 0.5, 0.5, 0.5]


def test_all_candidates_failed_carries_events_for_observability():
    service = EmbeddingService(
        settings=_settings(max_retries=1),
        circuit_breaker=CircuitBreaker(),
        model_factory=lambda model_id, settings: _FakeEmbeddingsModel(raise_error=Exception("500 server error")),
    )
    with pytest.raises(AllCandidatesFailedError) as exc_info:
        service.embed_documents("embed-primary", ["a"])
    assert exc_info.value.events
    assert exc_info.value.events[0].use_case == "embedding_documents"
