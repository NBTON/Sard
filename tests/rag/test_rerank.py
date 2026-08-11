"""RerankService tests with a fake NVIDIARerank-shaped compressor."""

from __future__ import annotations

import pytest

from sard.config.rag import ModelRoute, RAGSettings
from sard.rag.rerank import RerankService
from sard.rag.schemas import RetrievedCandidate


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


def _candidate(chunk_id, dense_score=None, fts_score=None, fused_score=None) -> RetrievedCandidate:
    return RetrievedCandidate(
        chunk_id=chunk_id,
        document_id="DOC-1",
        citation_id=f"CIT-{chunk_id}",
        content=f"محتوى {chunk_id}",
        title="عنوان",
        source_name="مصدر",
        source_url="https://example.com",
        topic="springs",
        language="ar",
        publication_date=None,
        page_number=None,
        dense_score=dense_score,
        fts_score=fts_score,
        fused_score=fused_score,
    )


class _FakeRerankModel:
    def __init__(self, order=None, raise_error=None, empty=False):
        self.order = order
        self.raise_error = raise_error
        self.empty = empty

    def compress_documents(self, documents, query):
        if self.raise_error:
            raise self.raise_error
        if self.empty:
            return []
        by_id = {d.metadata["chunk_id"]: d for d in documents}
        ordered = self.order or list(by_id.keys())
        result = []
        for cid in ordered:
            if cid not in by_id:
                from langchain_core.documents import Document

                result.append(Document(page_content="", metadata={"chunk_id": cid, "relevance_score": 0.9}))
                continue
            doc = by_id[cid]
            doc.metadata["relevance_score"] = 0.9
            result.append(doc)
        return result


def test_rerank_success_reorders_and_preserves_metadata():
    candidates = [_candidate("a"), _candidate("b"), _candidate("c")]
    service = RerankService(
        settings=_settings(),
        rerank_model_factory=lambda model_id, settings: _FakeRerankModel(order=["c", "a", "b"]),
    )
    outcome = service.rerank("query", candidates, top_n=3)
    assert outcome.method_used == "nvidia"
    assert [c.chunk_id for c in outcome.candidates] == ["c", "a", "b"]
    assert outcome.candidates[0].rerank_rank == 1
    assert outcome.candidates[0].rerank_score == 0.9
    assert outcome.candidates[0].content == "محتوى c"  # original chunk data preserved


def test_rerank_falls_back_to_rrf_when_nvidia_unavailable():
    candidates = [
        _candidate("a", fused_score=0.5),
        _candidate("b", fused_score=0.9),
    ]
    service = RerankService(
        settings=_settings(max_retries=1),
        rerank_model_factory=lambda model_id, settings: _FakeRerankModel(raise_error=Exception("503")),
    )
    outcome = service.rerank("query", candidates, top_n=2)
    assert outcome.method_used == "rrf_fallback"
    assert outcome.candidates[0].chunk_id == "b"  # higher fused score wins


def test_rerank_falls_back_to_dense_ranking_when_no_fused_score():
    candidates = [_candidate("a", dense_score=0.2), _candidate("b", dense_score=0.8)]
    service = RerankService(
        settings=_settings(max_retries=1),
        rerank_model_factory=lambda model_id, settings: _FakeRerankModel(raise_error=Exception("down")),
    )
    outcome = service.rerank("query", candidates, top_n=2)
    assert outcome.method_used == "dense_fallback"
    assert outcome.candidates[0].chunk_id == "b"


def test_rerank_falls_back_to_fts_ranking_as_last_resort():
    candidates = [_candidate("a", fts_score=0.1), _candidate("b", fts_score=0.7)]
    service = RerankService(
        settings=_settings(max_retries=1),
        rerank_model_factory=lambda model_id, settings: _FakeRerankModel(raise_error=Exception("down")),
    )
    outcome = service.rerank("query", candidates, top_n=2)
    assert outcome.method_used == "fts_fallback"
    assert outcome.candidates[0].chunk_id == "b"


def test_rerank_disabled_skips_nvidia_entirely():
    called = []

    def factory(model_id, settings):
        called.append(1)
        return _FakeRerankModel()

    candidates = [_candidate("a", fused_score=0.3), _candidate("b", fused_score=0.6)]
    service = RerankService(settings=_settings(enable_rerank=False), rerank_model_factory=factory)
    outcome = service.rerank("query", candidates, top_n=2)
    assert not called
    assert outcome.method_used == "rrf_fallback"


def test_rerank_empty_candidates_short_circuits():
    service = RerankService(settings=_settings())
    outcome = service.rerank("query", [], top_n=5)
    assert outcome.candidates == []


def test_rerank_handles_empty_nvidia_response_as_failure():
    candidates = [_candidate("a", fused_score=0.4)]
    service = RerankService(
        settings=_settings(max_retries=1),
        rerank_model_factory=lambda model_id, settings: _FakeRerankModel(empty=True),
    )
    outcome = service.rerank("query", candidates, top_n=1)
    assert outcome.method_used == "rrf_fallback"


@pytest.mark.parametrize("order", [["unknown"], ["a", "a"]])
def test_rerank_rejects_unmapped_or_duplicate_output(order):
    candidates = [_candidate("a", fused_score=0.4), _candidate("b", fused_score=0.2)]
    service = RerankService(
        settings=_settings(max_retries=1),
        rerank_model_factory=lambda model_id, settings: _FakeRerankModel(order=order),
    )
    outcome = service.rerank("query", candidates, top_n=2)
    assert outcome.method_used == "rrf_fallback"
    assert outcome.events
    assert outcome.events[0].failure_category.value == "malformed_structured_output"
