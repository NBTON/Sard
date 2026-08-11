"""Golden-set retrieval evaluation tests: pass/fail logic, MRR, Recall@K,
term-based nDCG, per-channel hits, routes, and honest gate reporting.
"""

from __future__ import annotations

import json

import pytest

from sard.config.rag import RAGSettings, ModelRoute
from sard.rag.chunking import compute_chunk_id, compute_citation_id, compute_content_hash
from sard.rag.evaluate import (
    _binary_ndcg,
    evaluate_case,
    load_golden_cases,
    run_golden_evaluation,
)
from sard.rag.retrieve import RetrievalDependencies, RetrievalService
from sard.rag.schemas import Chunk, EmbeddedChunk
from sard.rag.zvec_store import ZvecRepository

EMBED_DIM = 8


def _settings(**overrides) -> RAGSettings:
    base = dict(
        nvidia_api_key="nvapi-test",
        chat_base_url=None,
        embedding_base_url=None,
        rerank_base_url=None,
        chat_route=ModelRoute("generation", "chat-primary", ()),
        query_route=ModelRoute("query_rewrite", "query-primary", ()),
        embedding_route=ModelRoute("embedding", "test-embed", ()),
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


class _FakeEmbeddings:
    def embed_query(self, text):
        return [0.5] * EMBED_DIM


def _make_chunk(text: str, doc_id: str, topic: str = "springs") -> Chunk:
    content_hash = compute_content_hash(text)
    return Chunk(
        chunk_id=compute_chunk_id(doc_id, content_hash),
        document_id=doc_id,
        citation_id=compute_citation_id(doc_id, content_hash),
        content=text,
        content_hash=content_hash,
        title=f"عنوان {doc_id}",
        source_name="مصدر تجريبي",
        source_url=f"https://example.com/{doc_id}",
        topic=topic,
        language="ar",
        publication_date="2020-01-01",
        schema_version="3",
        ingestion_version="1",
    )


@pytest.fixture
def retrieval_service(tmp_path):
    base = str(tmp_path / "zvec")
    repo = ZvecRepository.open_or_create(base, "test-embed", EMBED_DIM)
    chunks = [
        _make_chunk("الينابيع الحارة في الأحساء تُستخدم للاستشفاء منذ القدم.", "DOC-1"),
        _make_chunk("عيون الأحساء المائية وردت في كتب الجغرافيين العرب.", "DOC-2"),
        _make_chunk("نص لا علاقة له بالموضوع إطلاقًا.", "DOC-3", topic="unrelated"),
    ]
    for c in chunks:
        repo.upsert_chunks(
            [EmbeddedChunk(chunk=c, dense_embedding=[0.5] * EMBED_DIM, embedding_model="test-embed", embedding_dimension=EMBED_DIM)],
            created_at="2024-01-01T00:00:00Z",
        )
    service = RetrievalService(
        RetrievalDependencies(repository=repo, embedding_model_id="test-embed", embedding_service=_FakeEmbeddings()),
        settings=_settings(),
    )
    yield service
    repo.close()


def _case(case_id, question, terms, topic="springs"):
    return {
        "id": case_id,
        "question_ar": question,
        "topic_ar": topic,
        "retrieval_terms_ar": terms,
        "expected_answer_facts": [],
        "disallowed_claims_ar": [],
        "expected_source_types": [],
        "pass_fail_notes_ar": "",
    }


def test_evaluate_case_passes_when_terms_are_retrieved(retrieval_service):
    case = _case("CASE-OK", "أين تقع الينابيع الحارة؟", ["الينابيع الحارة", "الأحساء"])
    result = evaluate_case(case, retrieval_service, k=6)
    assert result.passed is True
    assert result.dense_hit
    assert result.fused_hit
    assert result.matched_terms
    assert result.mrr_contribution > 0.0
    assert result.ndcg_contribution > 0.0
    assert result.retrieved_chunk_ids
    assert result.retrieved_citation_ids
    assert result.retrieved_source_titles
    assert result.retrieval_mode in ("hybrid", "dense_only", "fts_only_emergency")


def test_evaluate_case_fails_when_evidence_is_absent(retrieval_service):
    case = _case("CASE-MISS", "ما هي ممارسة تجفيف الروبيان التراثية؟", ["تجفيف الروبيان", "البرية"])
    result = evaluate_case(case, retrieval_service, k=6)
    assert result.passed is False
    assert result.matched_terms == []
    assert result.mrr_contribution == 0.0
    assert result.ndcg_contribution == 0.0
    assert "لم يتم العثور" in result.reason


def test_evaluate_case_uses_rerank_service_when_provided(retrieval_service):
    from sard.rag.rerank import RerankService

    case = _case("CASE-RERANK", "أين تقع الينابيع الحارة؟", ["الينابيع الحارة", "الأحساء"])
    result = evaluate_case(case, retrieval_service, rerank_service=RerankService(settings=_settings()), k=6)
    assert result.reranker_used in ("rrf_fallback", "dense_fallback", "fts_fallback", "nvidia")
    assert result.reranked_hit is True


def test_run_golden_evaluation_reports_gate_honestly(tmp_path, retrieval_service):
    golden = tmp_path / "golden.json"
    cases = [
        _case("SARD-OK-001", "أين تقع الينابيع الحارة؟", ["الينابيع الحارة", "الأحساء"]),
        _case("SARD-MISS-001", "ما هو تجفيف الروبيان؟", ["تجفيف الروبيان"]),
    ]
    golden.write_text(json.dumps({"golden_cases": cases}, ensure_ascii=False), encoding="utf-8")

    report = run_golden_evaluation(golden, retrieval_service, k=6, gate_threshold=8)
    assert report.total_cases == 2
    assert report.passed_cases == 1
    assert report.gate_passed is False  # 1/2 cannot pass an 8/10 gate
    assert report.mean_reciprocal_rank == pytest.approx(1.0 / 1)  # first fused hit at rank 1
    assert report.recall_at_k_fused == pytest.approx(0.5)
    assert report.recall_at_k_fts == pytest.approx(0.5)
    assert report.questions_needing_improvement == [cases[1]["question_ar"]]
    assert report.embedding_model_used == "test-embed"

    d = report.to_dict()
    assert d["recall_at_k_dense"] == 0.5
    assert "ndcg_note" in d
    assert "gate_threshold" in d


def test_run_golden_evaluation_gate_passes_with_enough_evidence(tmp_path, retrieval_service):
    golden = tmp_path / "golden.json"
    cases = [
        _case(f"OK-{i:03d}", "أين تقع الينابيع الحارة؟", ["الينابيع الحارة", "الأحساء", "عيون"])
        for i in range(9)
    ] + [
        _case("MISS-001", "ما هو تجفيف الروبيان؟", ["تجفيف الروبيان", "الجاشع"])
    ]
    golden.write_text(json.dumps({"golden_cases": cases}, ensure_ascii=False), encoding="utf-8")

    report = run_golden_evaluation(golden, retrieval_service, k=6, gate_threshold=8)
    assert report.passed_cases == 9
    assert report.gate_passed is True
    assert report.questions_needing_improvement == [cases[9]["question_ar"]]


def test_binary_ndcg_math():
    # [relevant, relevant, irrelevant] -> DCG = 1/1 + 1/2 + 0/3 = 1.5; IDCG same
    # ordering -> nDCG = 1.0.
    from sard.rag.schemas import RetrievedCandidate

    def cand(content):
        return RetrievedCandidate(
            chunk_id="c",
            document_id="d",
            citation_id="CIT-AAAAAAAAAAAA",
            content=content,
            title="t",
            source_name="s",
            source_url="u",
            topic="t",
            language="ar",
            publication_date=None,
            page_number=None,
        )

    rel = cand("الينابيع الحارة في الأحساء")
    irrel = cand("نص لا علاقة له")
    assert _binary_ndcg([rel, rel, irrel], ["الينابيع الحارة"], k=3) == pytest.approx(1.0)
    # Reversed order -> nDCG < 1.0.
    assert _binary_ndcg([irrel, rel, rel], ["الينابيع الحارة"], k=3) < 1.0
    # No relevance -> 0.0.
    assert _binary_ndcg([irrel, irrel], ["الينابيع الحارة"], k=3) == 0.0
    # Empty list -> 0.0.
    assert _binary_ndcg([], ["الينابيع الحارة"], k=3) == 0.0


def test_load_golden_cases_reads_cases(tmp_path):
    golden = tmp_path / "golden.json"
    golden.write_text(
        json.dumps({"golden_cases": [_case("A", "سؤال", ["مصطلح"])]}, ensure_ascii=False),
        encoding="utf-8",
    )
    cases = load_golden_cases(golden)
    assert len(cases) == 1
    assert cases[0]["id"] == "A"


def test_malformed_golden_cases_fail_with_actionable_validation(tmp_path, retrieval_service):
    golden = tmp_path / "malformed.json"
    golden.write_text(json.dumps({"golden_cases": [{"id": "BAD"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="question_ar"):
        run_golden_evaluation(golden, retrieval_service)
