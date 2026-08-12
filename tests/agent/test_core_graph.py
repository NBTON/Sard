"""Focused offline tests for the Step 5 agent core graph.

Covers: offline compilation, a full success path, the verification-retry path,
the exhaustion -> honest-partial path, and node-exception -> typed-failure
conversion.  No network, NVIDIA, or real RAG/Zvec is touched.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from sard.agent.events import FailureKind
from sard.agent.graph import GraphDependencies, build_graph, run_pipeline
from sard.agent.models import AgentModelService
from sard.agent.state import ClaimStatus, RAGMode
from sard.config.rag import ModelRoute, RAGSettings
from sard.rag.fallbacks import CircuitBreaker
from sard.rag.schemas import (
    RAGAnswer,
    RetrievedCandidate,
    RetrievalMode,
    Citation,
)


def _settings() -> RAGSettings:
    return RAGSettings(
        nvidia_api_key="nvapi-test",
        chat_base_url=None,
        embedding_base_url=None,
        rerank_base_url=None,
        chat_route=ModelRoute("generation", "fake-chat", ()),
        query_route=ModelRoute("query_rewrite", "fake-query", ()),
        embedding_route=ModelRoute("embedding", "fake-embed", ()),
        embedding_fallback_model="nv-embed-v1",
        rerank_route=ModelRoute("rerank", "fake-rerank", ()),
        vision_route=ModelRoute("vision", "fake-vision", ()),
        translation_route=ModelRoute("translation", "fake-translate", ()),
        safety_route=ModelRoute("safety", "fake-safety", ()),
        request_timeout_seconds=2.0,
        max_retries=1,
        zvec_collection_path="data/zvec/test",
        dense_candidates=5,
        fts_candidates=5,
        fused_candidates=5,
        final_top_k=4,
        enable_query_rewrite=False,
        enable_fts=True,
        enable_rerank=False,
    )


@dataclass
class FakeChatModel:
    scripts: list[str] = field(default_factory=list)
    model_id: str = "fake-chat"

    def invoke(self, messages, **kwargs):
        content = self.scripts.pop(0)
        return _Reply(content)

    @property
    def remaining(self) -> int:
        return len(self.scripts)


@dataclass
class _Reply:
    content: str


def _make_deps(scripts: list[str], rag_service=None, max_retries: int = 2) -> GraphDependencies:
    model = FakeChatModel(scripts=scripts)
    settings = _settings()
    service = AgentModelService(
        settings=settings,
        chat_model_factory=lambda model_id, s: model,
        circuit_breaker=CircuitBreaker(),
        max_retries_per_candidate=1,
    )
    return GraphDependencies(
        rag_service=rag_service,
        model_service=service,
        settings=settings,
        compose_max_retries=max_retries,
    )


def _candidate(citation_id: str, content: str, **extra) -> RetrievedCandidate:
    fields = dict(
        chunk_id=f"CHUNK-{citation_id}",
        document_id="DOC-RIY",
        citation_id=citation_id,
        content=content,
        title="مصدر الرياض",
        source_name="دليل الرياض",
        source_url="https://example.org/riyadh",
        topic="طعام",
        language="ar",
        publication_date="2025-01-01",
        page_number=3,
    )
    fields.update(extra)
    return RetrievedCandidate(**fields)


class FakeRAGService:
    def __init__(self, answer: RAGAnswer):
        self._answer = answer
        self.calls = []

    def answer(self, question, filters=None):
        self.calls.append((question, filters))
        return self._answer


def _evidence_answer() -> RAGAnswer:
    riy01 = _candidate("CIT-RIY01", "تُعد الأسواق الشعبية في الرياض وجهة بارزة للزوار.")
    riy02 = _candidate("CIT-RIY02", "يُفضّل زيارة الأسواق في ساعات المساء خلال الصيف.")
    return RAGAnswer(
        question="خطة لرحلة إلى الرياض",
        rewritten_queries=["خطة لرحلة إلى الرياض"],
        dense_candidates=[riy01, riy02],
        fts_candidates=[riy01, riy02],
        fused_candidates=[riy01, riy02],
        selected_context=[riy01, riy02],
        answer_text="",
        citations=[
            Citation(citation_id="CIT-RIY01", title="مصدر الرياض", source_name="دليل الرياض",
                     source_url="https://example.org/riyadh", chunk_id="CHUNK-CIT-RIY01"),
            Citation(citation_id="CIT-RIY02", title="مصدر الرياض", source_name="دليل الرياض",
                     source_url="https://example.org/riyadh", chunk_id="CHUNK-CIT-RIY02"),
        ],
        model_route={"embedding": "fake-embed", "query_rewrite": None, "rerank": "fake-rerank", "generation": "fake-chat"},
        fallback_events=[],
        retrieval_mode=RetrievalMode.HYBRID.value,
        reranker_used="nvidia",
        timings_ms={"total_ms": 12.0},
        warnings=[],
    )


def _unsupported_evidence_answer() -> RAGAnswer:
    answer = _evidence_answer()
    return answer


def test_graph_compiles_offline_with_no_dependencies():
    graph = build_graph()
    assert graph is not None


def test_offline_no_dependencies_run_finishes_as_partial_not_crash():
    result = run_pipeline("خطة رحلة إلى الرياض", dependencies=GraphDependencies())
    assert result["graph_outcome"] in {"partial", "completed", "failed"}
    assert result["final_answer"]
    assert result["errors"] == []
    kinds = [event.kind for event in result["progress_events"]]
    assert "graph_completed" in kinds


def _scripts_for_success():
    return [
        '{"intent":"travel_planning","destination":"الرياض","duration_days":2,'
        '"audience":["بالغون"],"interests":["طعام","أسواق"],"timing":null,'
        '"user_facts":[],"missing_constraints":[],"assumptions":[]}',
        '{"focus_summary":"استكشاف الأسواق","days":[{"day_index":1,"focus":"أسواق الرياض",'
        '"time_blocks":[{"period":"المساء","activity_type":"أسواق"}]}],'
        '"activity_types":["أسواق"],"evidence_topics":["طعام"],'
        '"open_questions":["ما أشهر الأسواق؟"],"constraints":[]}',
        "الأسواق الشعبية في الرياض وجهة بارزة للزوار [CIT-RIY01]. "
        "يُفضّل زيارتها مساءً في الصيف [CIT-RIY02].",
        '{"claims":[{"claim_id":"CLAIM-01-001","status":"supported","correction":"","note":""},'
        '{"claim_id":"CLAIM-01-002","status":"supported","correction":"","note":""}]}',
    ]


def test_success_path_reaches_completed_with_full_coverage():
    rag = FakeRAGService(_evidence_answer())
    deps = _make_deps(_scripts_for_success(), rag_service=rag, max_retries=2)
    result = run_pipeline("خطة لرحلة إلى الرياض", dependencies=deps)

    assert result["graph_outcome"] == "completed"
    assert result["final_answer"]
    assert result["draft"]
    assert result["itinerary"] is not None
    assert result["coverage"] is not None
    assert result["coverage"].coverage_ratio == 1.0
    assert result["retrieval_mode"] == RAGMode.HYBRID_RERANKED.value
    assert result["sources"]
    assert len(result["atomic_claims"]) == 2
    assert all(
        claim.status in {ClaimStatus.SUPPORTED, ClaimStatus.PARTIALLY_SUPPORTED}
        for claim in result["atomic_claims"]
    )
    assert result["errors"] == []
    assert result["verification_history"]
    assert len(rag.calls) == 1 and rag.calls[0][1] == {"topic": "طعام"}


def test_retry_path_recomposes_then_passes():
    scripts = _scripts_for_success()[:3]
    scripts.append(
        '{"claims":[{"claim_id":"CLAIM-01-001","status":"supported","correction":"","note":""},'
        '{"claim_id":"CLAIM-01-002","status":"unsupported","correction":"أزله أو أعد صياغته","note":""}]}'
    )
    scripts.append(
        "الأسواق الشعبية في الرياض وجهة بارزة للزوار [CIT-RIY01]. "
        "تتوفر معلومات محدودة عن مواقيت الزيارة في المصادر الحالية."
    )
    scripts.append(
        '{"claims":[{"claim_id":"CLAIM-02-001","status":"supported","correction":"","note":""},'
        '{"claim_id":"CLAIM-02-002","status":"explicitly_uncertain","correction":"","note":""}]}'
    )

    rag = FakeRAGService(_evidence_answer())
    deps = _make_deps(scripts, rag_service=rag, max_retries=2)
    result = run_pipeline("خطة لرحلة إلى الرياض", dependencies=deps)

    assert result["graph_outcome"] == "completed"
    assert result["compose_retry_count"] == 1
    assert result["verification_result"] is not None and result["verification_result"].passed
    assert len(result["verification_history"]) == 2
    assert result["coverage"].coverage_ratio == 1.0
    events = [event.kind for event in result["progress_events"]]
    assert "retried" in events


def test_exhaustion_emits_honest_partial_and_does_not_crash():
    scripts = _scripts_for_success()[:3]
    scripts.append(
        '{"claims":[{"claim_id":"CLAIM-01-001","status":"supported","correction":"","note":""},'
        '{"claim_id":"CLAIM-01-002","status":"unsupported","correction":"إزالة","note":""}]}'
    )
    scripts.append(
        "الأسواق الشعبية في الرياض وجهة بارزة للزوار [CIT-RIY01]. "
        "سعر الدخول خمسون ريالًا [CIT-RIY02]."
    )
    scripts.append(
        '{"claims":[{"claim_id":"CLAIM-02-001","status":"supported","correction":"","note":""},'
        '{"claim_id":"CLAIM-02-002","status":"unsupported","correction":"إزالة","note":""}]}'
    )

    rag = FakeRAGService(_evidence_answer())
    deps = _make_deps(scripts, rag_service=rag, max_retries=1)
    result = run_pipeline("خطة لرحلة إلى الرياض", dependencies=deps)

    assert result["graph_outcome"] == "partial"
    assert result["verification_exhausted"] is True
    assert result["compose_retry_count"] == 2
    assert result["final_answer"]
    assert "CIT-RIY01" in result["final_answer"]
    assert "CIT-RIY02" not in result["final_answer"]
    assert result["unsupported_claims"] == ["CLAIM-02-002"]
    kinds = [error.kind for error in result["errors"]]
    assert "verification_exhausted" in kinds
    assert "failed" in [event.kind for event in result["progress_events"]]


class _ExplodingRAG:
    def answer(self, question, filters=None):
        raise RuntimeError("backend exploded")
    def close(self):
        pass


def test_node_exception_converted_to_typed_failure_not_crash():
    scripts = _scripts_for_success()
    deps = _make_deps(scripts, rag_service=_ExplodingRAG(), max_retries=2)
    result = run_pipeline("خطة لرحلة إلى الرياض", dependencies=deps)

    assert result["graph_outcome"] in {"partial", "failed", "completed"}
    assert "retrieve" in result["node_failures"]
    assert result["errors"]
    kinds = {error.kind for error in result["errors"]}
    assert not (kinds & {FailureKind.AUTH.value, FailureKind.EMBEDDING_MISMATCH.value})
    assert result["retrieval_mode"] == RAGMode.UNAVAILABLE.value
    assert result["final_answer"]