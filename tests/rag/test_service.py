"""Offline contract tests for the provider-independent RAG service."""

from __future__ import annotations

import pytest

from sard.config.rag import ModelRoute, RAGSettings
from sard.rag.answer import AnswerService
from sard.rag.chunking import compute_chunk_id, compute_citation_id, compute_content_hash
from sard.rag.embeddings import EmbeddingService
from sard.rag.query_rewriter import QueryRewriteService
from sard.rag.rerank import RerankService
from sard.rag.retrieve import RetrievalDependencies, RetrievalService
from sard.rag.schemas import Chunk, EmbeddedChunk
from sard.rag.service import RAGService
from sard.rag.zvec_store import ZvecRepository


def _settings(**overrides) -> RAGSettings:
    values = dict(
        nvidia_api_key="nvapi-test",
        chat_base_url=None,
        embedding_base_url=None,
        rerank_base_url=None,
        chat_route=ModelRoute("generation", "chat-primary", ()),
        query_route=ModelRoute("query_rewrite", "query-primary", ()),
        embedding_route=ModelRoute("embedding", "embed-primary", ()),
        embedding_fallback_model="nv-embed-v1",
        rerank_route=ModelRoute("rerank", "rerank-primary", ()),
        vision_route=ModelRoute("vision", "vision-primary", ()),
        translation_route=ModelRoute("translation", "translate-primary", ()),
        safety_route=ModelRoute("safety", "safety-primary", ()),
        request_timeout_seconds=2.0,
        max_retries=1,
        zvec_collection_path="data/zvec/test",
        dense_candidates=5,
        fts_candidates=5,
        fused_candidates=5,
        final_top_k=3,
        enable_query_rewrite=False,
        enable_fts=True,
        enable_rerank=False,
    )
    values.update(overrides)
    return RAGSettings(**values)


class _FakeEmbeddingModel:
    def embed_documents(self, texts):
        return [[0.5, 0.5, 0.5, 0.5] for _ in texts]

    def embed_query(self, text):
        return [0.5, 0.5, 0.5, 0.5]


def test_rag_service_answer_returns_complete_structured_contract(tmp_path):
    settings = _settings(zvec_collection_path=str(tmp_path / "zvec"))
    repo = ZvecRepository.open_or_create(settings.zvec_collection_path, "embed-primary", 4)
    text = "نص موثق عن الينابيع الحارة في الأحساء."
    content_hash = compute_content_hash(text)
    chunk = Chunk(
        chunk_id=compute_chunk_id("DOC-1", content_hash),
        document_id="DOC-1",
        citation_id=compute_citation_id("DOC-1", content_hash),
        content=text,
        content_hash=content_hash,
        title="مصدر الينابيع",
        source_name="مصدر تجريبي",
        source_url="https://example.org/springs",
        topic="springs",
        language="ar",
        publication_date=None,
        schema_version="3",
        ingestion_version="1",
    )
    repo.upsert_chunks(
        [EmbeddedChunk(chunk, [0.5, 0.5, 0.5, 0.5], "embed-primary", 4)],
        created_at="2024-01-01T00:00:00Z",
    )

    embedding_service = EmbeddingService(
        settings=settings,
        model_factory=lambda model_id, effective_settings: _FakeEmbeddingModel(),
    )
    retrieval_service = RetrievalService(
        RetrievalDependencies(repo, "embed-primary", embedding_service), settings=settings
    )
    answer_service = AnswerService(
        settings=settings,
        chat_model_factory=lambda model_id, effective_settings: (_ for _ in ()).throw(
            RuntimeError("offline generation unavailable")
        ),
    )
    service = RAGService(
        repository=repo,
        embedding_service=embedding_service,
        embedding_model_id="embed-primary",
        query_rewrite_service=QueryRewriteService(settings=settings),
        retrieval_service=retrieval_service,
        rerank_service=RerankService(settings=settings),
        answer_service=answer_service,
        settings=settings,
    )
    try:
        result = service.answer("ما الذي توضحه المصادر؟", filters={"topic": "springs"})
    finally:
        service.close()

    assert result.question == "ما الذي توضحه المصادر؟"
    assert result.rewritten_queries
    assert result.dense_candidates or result.fts_candidates
    assert result.fused_candidates
    assert result.selected_context
    assert "الينابيع الحارة" in result.answer_text
    assert result.citations[0].title == "مصدر الينابيع"
    assert result.citations[0].source_url == "https://example.org/springs"
    assert result.model_route["generation"] is None
    assert result.retrieval_mode in {"hybrid", "dense_only", "fts_only_emergency"}
    assert result.timings_ms["total_ms"] >= 0


def test_rag_service_rejects_unknown_filters(tmp_path):
    settings = _settings(zvec_collection_path=str(tmp_path / "zvec"))
    repo = ZvecRepository.open_or_create(settings.zvec_collection_path, "embed-primary", 4)
    service = RAGService(
        repository=repo,
        embedding_service=EmbeddingService(settings=settings, model_factory=lambda *_: _FakeEmbeddingModel()),
        embedding_model_id="embed-primary",
        query_rewrite_service=QueryRewriteService(settings=settings),
        retrieval_service=RetrievalService(
            RetrievalDependencies(repo, "embed-primary", EmbeddingService(settings=settings, model_factory=lambda *_: _FakeEmbeddingModel())),
            settings=settings,
        ),
        rerank_service=RerankService(settings=settings),
        answer_service=AnswerService(settings=settings, chat_model_factory=lambda *_: None),
        settings=settings,
    )
    try:
        with pytest.raises(ValueError, match="Unknown retrieval filter"):
            service.answer("سؤال", filters={"unsupported": "x"})
    finally:
        service.close()
