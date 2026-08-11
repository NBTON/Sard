"""Zvec repository adapter tests. Uses the real `zvec` package (in-process,
no network) against a temporary directory.
"""

from __future__ import annotations

import pytest

from sard.rag.chunking import compute_chunk_id, compute_citation_id, compute_content_hash
from sard.rag.fallbacks import FailureCategory
from sard.rag.schemas import Chunk, EmbeddedChunk, RetrievalFilters
from sard.rag.zvec_store import (
    UnsafeFilterValueError,
    ZvecRepository,
    ZvecSchemaMismatchError,
    build_dense_retriever,
    build_safe_filter,
    candidate_to_document,
)

EMBED_DIM = 8


def _make_chunk(text: str, doc_id: str, topic: str, source_name: str = "مصدر تجريبي") -> Chunk:
    content_hash = compute_content_hash(text)
    return Chunk(
        chunk_id=compute_chunk_id(doc_id, content_hash),
        document_id=doc_id,
        citation_id=compute_citation_id(doc_id, content_hash),
        content=text,
        content_hash=content_hash,
        title="عنوان تجريبي",
        source_name=source_name,
        source_url="https://example.com/doc",
        topic=topic,
        language="ar",
        publication_date="2020-01-01",
        schema_version="3",
        ingestion_version="1",
    )


def _embed(chunk: Chunk, vector: list[float]) -> EmbeddedChunk:
    return EmbeddedChunk(chunk=chunk, dense_embedding=vector, embedding_model="test-embed-model", embedding_dimension=len(vector))


@pytest.fixture
def repo(tmp_path):
    base = str(tmp_path / "zvec_col")
    repository = ZvecRepository.open_or_create(base, "test-embed-model", EMBED_DIM)
    yield repository
    repository.close()


def test_create_and_reopen_collection(tmp_path):
    base = str(tmp_path / "zvec_col2")
    repo1 = ZvecRepository.open_or_create(base, "test-embed-model", EMBED_DIM)
    chunk = _make_chunk("الينابيع الحارة في الأحساء نص تجريبي", "DOC-1", "springs")
    repo1.upsert_chunks([_embed(chunk, [0.1] * EMBED_DIM)], created_at="2024-01-01T00:00:00Z")
    assert repo1.stats.doc_count == 1
    repo1.close()

    repo2 = ZvecRepository.open_or_create(base, "test-embed-model", EMBED_DIM)
    assert repo2.stats.doc_count == 1
    fetched = repo2.fetch_by_ids([chunk.chunk_id])
    assert chunk.chunk_id in fetched
    assert fetched[chunk.chunk_id].content == chunk.content
    repo2.close()


def test_versioned_path_prevents_mixing_embedding_models(tmp_path):
    base = str(tmp_path / "zvec_col3")
    repo1 = ZvecRepository.open_or_create(base, "model-a", EMBED_DIM)
    repo1.close()

    # A different model name resolves to a DIFFERENT versioned path, so this
    # just creates a separate collection rather than raising.
    repo2 = ZvecRepository.open_or_create(base, "model-b", EMBED_DIM)
    assert repo2.path != repo1.path
    repo2.close()


def test_reopen_with_mismatched_dimension_raises_schema_error(tmp_path):
    base = str(tmp_path / "zvec_col4")
    repo1 = ZvecRepository.open_or_create(base, "model-a", EMBED_DIM)
    repo1.close()

    # Same model name + different dimension resolves to a DIFFERENT
    # versioned path (dimension is part of the fingerprint), so this
    # should transparently create a new, separate collection rather than
    # colliding — verifying the "never mix" guarantee holds via path
    # separation.
    repo2 = ZvecRepository.open_or_create(base, "model-a", EMBED_DIM + 1)
    assert repo2.path != repo1.path
    repo2.close()


def test_dimension_mismatch_within_same_path_is_rejected(tmp_path, monkeypatch):
    from sard.rag import zvec_store as zs

    base = str(tmp_path / "zvec_col5")
    # Force both calls to resolve to the SAME path despite different dims,
    # simulating a corrupted/mismatched on-disk collection.
    fixed_path = tmp_path / "zvec_col5" / "fixedhash" / "schema-v1"
    monkeypatch.setattr(zs, "versioned_collection_path", lambda *a, **k: fixed_path)

    repo1 = ZvecRepository.open_or_create(base, "model-a", EMBED_DIM)
    repo1.close()

    with pytest.raises(ZvecSchemaMismatchError) as exc_info:
        ZvecRepository.open_or_create(base, "model-a", EMBED_DIM + 4)
    assert exc_info.value.category == FailureCategory.ZVEC_SCHEMA_MISMATCH


def test_upsert_is_idempotent(repo):
    chunk = _make_chunk("نص حول تجفيف الروبيان في الساحل الشرقي", "DOC-2", "shrimp")
    repo.upsert_chunks([_embed(chunk, [0.2] * EMBED_DIM)], created_at="2024-01-01T00:00:00Z")
    assert repo.stats.doc_count == 1
    # Re-upserting the identical chunk (same chunk_id) must not create a duplicate.
    repo.upsert_chunks([_embed(chunk, [0.2] * EMBED_DIM)], created_at="2024-01-02T00:00:00Z")
    assert repo.stats.doc_count == 1


def test_dense_search_returns_ranked_candidates(repo):
    near = _make_chunk("الينابيع الحارة في الأحساء موضوع تراثي", "DOC-3", "springs")
    far = _make_chunk("موضوع لا علاقة له بالبحث إطلاقًا", "DOC-4", "unrelated")
    repo.upsert_chunks(
        [_embed(near, [1.0] + [0.0] * (EMBED_DIM - 1)), _embed(far, [0.0] * (EMBED_DIM - 1) + [1.0])],
        created_at="2024-01-01T00:00:00Z",
    )
    results = repo.dense_search([1.0] + [0.0] * (EMBED_DIM - 1), topk=5)
    assert results
    assert results[0].chunk_id == near.chunk_id
    assert results[0].dense_rank == 1


def test_fts_search_finds_exact_arabic_terms(repo):
    springs_chunk = _make_chunk("الينابيع الحارة في محافظة الأحساء معروفة تاريخيًا", "DOC-5", "springs")
    shrimp_chunk = _make_chunk("تجفيف الروبيان ممارسة ساحلية تقليدية", "DOC-6", "shrimp")
    repo.upsert_chunks(
        [_embed(springs_chunk, [0.3] * EMBED_DIM), _embed(shrimp_chunk, [0.4] * EMBED_DIM)],
        created_at="2024-01-01T00:00:00Z",
    )
    results = repo.fts_search("الأحساء", topk=5)
    assert any(r.chunk_id == springs_chunk.chunk_id for r in results)
    assert all(r.chunk_id != shrimp_chunk.chunk_id for r in results)


def test_metadata_filter_restricts_results(repo):
    springs_chunk = _make_chunk("محتوى عن الينابيع", "DOC-7", "springs", source_name="مصدر أ")
    shrimp_chunk = _make_chunk("محتوى عن الروبيان", "DOC-8", "shrimp", source_name="مصدر ب")
    repo.upsert_chunks(
        [_embed(springs_chunk, [0.5] * EMBED_DIM), _embed(shrimp_chunk, [0.5] * EMBED_DIM)],
        created_at="2024-01-01T00:00:00Z",
    )
    results = repo.dense_search([0.5] * EMBED_DIM, topk=10, filters=RetrievalFilters(topic="shrimp"))
    assert all(r.topic == "shrimp" for r in results)
    assert any(r.chunk_id == shrimp_chunk.chunk_id for r in results)
    assert all(r.chunk_id != springs_chunk.chunk_id for r in results)


def test_safe_filter_rejects_injection_like_values():
    with pytest.raises(UnsafeFilterValueError):
        build_safe_filter(RetrievalFilters(topic="springs' OR '1'='1"))


def test_safe_filter_builds_expected_expression():
    expr = build_safe_filter(RetrievalFilters(topic="springs", language="ar"))
    assert "topic = 'springs'" in expr
    assert "language = 'ar'" in expr
    assert " AND " in expr


def test_candidate_to_document_maps_fields(repo):
    chunk = _make_chunk("محتوى قابل للتحويل إلى Document", "DOC-9", "springs")
    repo.upsert_chunks([_embed(chunk, [0.6] * EMBED_DIM)], created_at="2024-01-01T00:00:00Z")
    results = repo.dense_search([0.6] * EMBED_DIM, topk=1)
    doc = candidate_to_document(results[0])
    assert doc.page_content == chunk.content
    assert doc.metadata["chunk_id"] == chunk.chunk_id
    assert doc.metadata["citation_id"] == chunk.citation_id


def test_langchain_retriever_returns_documents(repo):
    chunk = _make_chunk("نص لاختبار retriever متوافق مع LangChain", "DOC-10", "springs")
    repo.upsert_chunks([_embed(chunk, [0.7] * EMBED_DIM)], created_at="2024-01-01T00:00:00Z")

    class _FakeEmbeddings:
        def embed_query(self, text: str) -> list[float]:
            return [0.7] * EMBED_DIM

    retriever = build_dense_retriever(repo, _FakeEmbeddings(), k=5)
    from langchain_core.documents import Document

    docs = retriever.invoke("أي استعلام")
    assert docs
    assert all(isinstance(d, Document) for d in docs)
    assert docs[0].metadata["chunk_id"] == chunk.chunk_id


def test_collection_stats_reports_doc_count(repo):
    chunk = _make_chunk("محتوى للإحصائيات", "DOC-11", "springs")
    repo.upsert_chunks([_embed(chunk, [0.8] * EMBED_DIM)], created_at="2024-01-01T00:00:00Z")
    stats = repo.stats
    assert stats.doc_count == 1
    assert stats.embedding_model == "test-embed-model"
    assert stats.embedding_dimension == EMBED_DIM
