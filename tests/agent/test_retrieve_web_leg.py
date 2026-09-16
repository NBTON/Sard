"""Graph retrieve node: budgeted web-search leg (Parallel -> Tavily -> Exa).

Offline-deterministic: fanout is monkeypatched (no network), provider keys
are scrubbed where the honest-degrade path is asserted.
"""
from __future__ import annotations

import importlib

from sard.agent.graph import GraphDependencies
from sard.agent.state import EvidenceItem, initial_state
from sard.rag.search_providers import make_search_result

retrieve_mod = importlib.import_module("sard.agent.nodes.retrieve")


def _state(query: str) -> dict:
    st = initial_state(query)
    st["original_request"] = query
    st["run_id"] = "run-web-leg-test"
    return st


def _hit(url: str, title: str, body: str):
    return make_search_result(
        provider="parallel",
        query="خطة رحلة إلى الرياض",
        url=url,
        title=title,
        content=body,
    )


def test_web_leg_off_by_default_without_flag(monkeypatch):
    """Default deps must not touch the network even when keys exist."""
    def _boom(*args, **kwargs):
        raise AssertionError("fanout must not run when enable_web_search is False")

    monkeypatch.setattr("sard.rag.search_providers.fanout_search", _boom)
    out = retrieve_mod.retrieve(_state("أي استعلام"), GraphDependencies())
    assert out["web_search_triggered"] is False
    assert out["web_result_count"] == 0
    assert out["evidence"] == []


def test_web_leg_triggers_on_empty_local_and_adapts(monkeypatch):
    """Empty local evidence triggers fanout; survivors become CIT-WEB evidence."""
    body = (
        "دليل سياحي شامل لمدينة الرياض يشمل الدرعية التاريخية ومركز الملك "
        "عبد الله المالي والمتاحف والمطاعم الشعبية في العاصمة السعودية."
    )
    hits = [_hit("https://example.com/riyadh-guide", "دليل الرياض السياحي", body)]

    def _fake_fanout(objective, queries, **kwargs):
        assert kwargs.get("max_results", 0) > 0
        return hits, [{"provider": "parallel", "ok": True}], {}

    monkeypatch.setattr("sard.rag.search_providers.fanout_search", _fake_fanout)
    # Identity relevance gate: wiring/dedup/ID contract under test, not the gate.
    monkeypatch.setattr(
        "sard.rag.relevance.filter_relevant_evidence",
        lambda query, cands: list(cands),
    )
    deps = GraphDependencies(enable_web_search=True)
    out = retrieve_mod.retrieve(_state("خطة رحلة إلى الرياض"), deps)
    assert out["web_search_triggered"] is True
    assert out["web_result_count"] == 1
    assert len(out["evidence"]) == 1
    item = out["evidence"][0]
    assert isinstance(item, EvidenceItem)
    assert item.citation_id.startswith("CIT-WEB-")
    assert item.chunk_id == f"{item.citation_id}-c1"
    assert item.source_url == "https://example.com/riyadh-guide"
    assert len(item.content) >= 20
    assert "web:خطة رحلة إلى الرياض" in out["retrieval_queries"]


def test_web_leg_dedups_against_local_evidence(monkeypatch):
    """Same canonical URL + same content as local evidence is dropped."""
    body = (
        "دليل سياحي شامل لمدينة الرياض يشمل الدرعية التاريخية ومركز الملك "
        "عبد الله المالي والمتاحف والمطاعم الشعبية في العاصمة السعودية."
    )
    hits = [_hit("https://example.com/riyadh-guide", "دليل الرياض السياحي", body)]

    def _fake_fanout(objective, queries, **kwargs):
        return hits, [{"provider": "parallel", "ok": True}], {}

    monkeypatch.setattr("sard.rag.search_providers.fanout_search", _fake_fanout)
    monkeypatch.setattr(
        "sard.rag.relevance.filter_relevant_evidence",
        lambda query, cands: list(cands),
    )

    # Seed local evidence with the same URL + content via a fake RAG service.
    from sard.rag.schemas import RetrievedCandidate

    local = RetrievedCandidate(
        citation_id="CIT-LOCAL-001",
        chunk_id="CIT-LOCAL-001-c1",
        document_id="doc-local-001",
        content=body,
        title="دليل الرياض السياحي",
        source_name="example",
        source_url="https://example.com/riyadh-guide",
        topic="riyadh",
        language="ar",
        publication_date=None,
        page_number=None,
        fused_score=0.2,
        fused_rank=1,
    )

    class _FakeAnswer:
        retrieval_mode = "hybrid"
        reranker_used = None
        model_route = {}
        dense_candidates = []
        fts_candidates = []
        fused_candidates = []
        selected_context = [local]
        fallback_events = []
        warnings = []
        timings_ms = {}

    class _FakeRAG:
        def answer(self, query, filters=None):
            return _FakeAnswer()

    deps = GraphDependencies(rag_service=_FakeRAG(), enable_web_search=True)
    out = retrieve_mod.retrieve(_state("خطة رحلة إلى الرياض"), deps)
    assert out["web_search_triggered"] is True
    # Local item kept, web duplicate dropped.
    assert [e.citation_id for e in out["evidence"]] == ["CIT-LOCAL-001"]
    assert out["web_result_count"] == 0


def test_web_leg_honest_degrade_without_keys(monkeypatch):
    """Triggered but keyless: no fabrication, Arabic warning, flag set."""
    for var in ("PARALLEL_API_KEY", "TAVILY_API_KEY", "TIVALY_API_KEY", "EXA_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    deps = GraphDependencies(enable_web_search=True)
    out = retrieve_mod.retrieve(_state("سؤال يحتاج بحثا حيا"), deps)
    assert out["web_search_triggered"] is True
    assert out["web_result_count"] == 0
    assert out["web_unavailable_warning"] is True
    assert out["evidence"] == []
    assert any("المحلي" in w for w in out["retrieval_warnings"])
