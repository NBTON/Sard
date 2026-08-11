"""RetrievalService + Reciprocal Rank Fusion tests."""

from __future__ import annotations

import pytest

from sard.config.rag import ModelRoute, RAGSettings
from sard.rag.chunking import compute_chunk_id, compute_citation_id, compute_content_hash
from sard.rag.embeddings import EmbeddingService
from sard.rag.fallbacks import AllCandidatesFailedError
from sard.rag.retrieve import RetrievalDependencies, RetrievalService, reciprocal_rank_fusion
from sard.rag.schemas import Chunk, EmbeddedChunk, RetrievalMode, RewrittenQuery
from sard.rag.zvec_store import ZvecRepository

EMBED_DIM = 6


def _settings(**overrides) -> RAGSettings:
    base = dict(
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
        request_timeout_seconds=5.0,
        max_retries=1,
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


def _rewritten(question: str) -> RewrittenQuery:
    return RewrittenQuery(
        original_question=question,
        normalized_question=question,
        search_variants=[question],
        rewrite_succeeded=False,
    )


def _make_chunk(text: str, doc_id: str, topic="springs") -> Chunk:
    h = compute_content_hash(text)
    return Chunk(
        chunk_id=compute_chunk_id(doc_id, h),
        document_id=doc_id,
        citation_id=compute_citation_id(doc_id, h),
        content=text,
        content_hash=h,
        title="عنوان",
        source_name="مصدر",
        source_url="https://example.com",
        topic=topic,
        language="ar",
        publication_date=None,
        schema_version="3",
        ingestion_version="1",
    )


@pytest.fixture
def repo(tmp_path):
    base = str(tmp_path / "col")
    r = ZvecRepository.open_or_create(base, "embed-primary", EMBED_DIM)
    chunk_springs = _make_chunk("الينابيع الحارة في الأحساء موضوع تراثي مهم جدًا", "DOC-1", "springs")
    chunk_shrimp = _make_chunk("تجفيف الروبيان ممارسة ساحلية تقليدية قديمة", "DOC-2", "shrimp")
    r.upsert_chunks(
        [
            EmbeddedChunk(chunk_springs, [1.0, 0, 0, 0, 0, 0], "embed-primary", EMBED_DIM),
            EmbeddedChunk(chunk_shrimp, [0, 0, 0, 0, 0, 1.0], "embed-primary", EMBED_DIM),
        ],
        created_at="2024-01-01T00:00:00Z",
    )
    yield r
    r.close()


class _FakeEmbeddingService:
    def __init__(self, vector=None, raise_error=None):
        self.vector = vector or [1.0, 0, 0, 0, 0, 0]
        self.raise_error = raise_error

    def embed_query(self, model_id, text, expected_dim=None):
        if self.raise_error:
            raise self.raise_error

        class _O:
            pass

        o = _O()
        o.vectors = [self.vector]
        return o


def test_hybrid_retrieval_returns_dense_and_fts_and_fused(repo):
    service = RetrievalService(
        RetrievalDependencies(repo, "embed-primary", _FakeEmbeddingService()),
        settings=_settings(),
    )
    result = service.retrieve(_rewritten("الأحساء"))
    assert result.mode == RetrievalMode.HYBRID
    assert result.dense_candidates
    assert result.fused_candidates
    assert result.dense_candidates[0].topic == "springs"


def test_dense_only_mode_when_fts_disabled(repo):
    service = RetrievalService(
        RetrievalDependencies(repo, "embed-primary", _FakeEmbeddingService()),
        settings=_settings(enable_fts=False),
    )
    result = service.retrieve(_rewritten("الأحساء"))
    assert result.mode == RetrievalMode.DENSE_ONLY
    assert not result.fts_candidates


def test_fts_only_emergency_mode_when_embeddings_unavailable(repo):
    service = RetrievalService(
        RetrievalDependencies(
            repo, "embed-primary", _FakeEmbeddingService(raise_error=AllCandidatesFailedError("embedding_query", []))
        ),
        settings=_settings(),
    )
    result = service.retrieve(_rewritten("الأحساء"))
    assert result.mode == RetrievalMode.FTS_ONLY_EMERGENCY
    assert result.warnings
    assert not result.dense_candidates


def test_metadata_filters_applied_through_service(repo):
    from sard.rag.schemas import RetrievalFilters

    service = RetrievalService(
        RetrievalDependencies(repo, "embed-primary", _FakeEmbeddingService()),
        settings=_settings(),
    )
    result = service.retrieve(_rewritten("موضوع"), filters=RetrievalFilters(topic="shrimp"))
    assert all(c.topic == "shrimp" for c in result.dense_candidates)


def test_reciprocal_rank_fusion_combines_and_dedups():
    from sard.rag.schemas import RetrievedCandidate

    def c(cid, content_hash=None):
        return RetrievedCandidate(
            chunk_id=cid,
            document_id="D",
            citation_id=f"CIT-{cid}",
            content="x",
            title="t",
            source_name="s",
            source_url="u",
            topic="springs",
            language="ar",
            publication_date=None,
            page_number=None,
            content_hash=content_hash or cid,
        )

    dense = [c("a"), c("b"), c("c")]
    fts = [c("b"), c("a"), c("d")]
    fused = reciprocal_rank_fusion(dense, fts)
    ids = [x.chunk_id for x in fused]
    assert set(ids) == {"a", "b", "c", "d"}
    # "a" and "b" appear in both lists -> should outrank chunks appearing once.
    assert ids.index("a") < ids.index("c")
    assert ids.index("b") < ids.index("d")


def test_fusion_deduplicates_by_content_hash_across_different_chunk_ids():
    from sard.rag.schemas import RetrievedCandidate

    def c(cid, score_rank_hint):
        return RetrievedCandidate(
            chunk_id=cid,
            document_id="D",
            citation_id=f"CIT-{cid}",
            content="same content, different chunk id",
            title="t",
            source_name="s",
            source_url="u",
            topic="springs",
            language="ar",
            publication_date=None,
            page_number=None,
            content_hash="SAME-HASH",
        )

    dense = [c("chunk-1", 1)]
    fts = [c("chunk-2", 1)]
    fused = reciprocal_rank_fusion(dense, fts)
    assert len(fused) == 1  # deduplicated despite different chunk IDs
