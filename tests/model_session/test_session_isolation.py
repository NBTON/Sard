"""Session isolation & cache namespacing focused tests (Phase-1, isolated).

These tests do not edit shared Chat/API files; they exercise boundaries
read-only and document current behavior. Failures here are *expected* for
two cases (over-filter, fallback contamination) to prove R1/R4 bugs without
mutating production code — Phase-2 owns fixes.

Run: uv run python -m pytest tests/model_session/test_session_isolation.py -v
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from sard.memory import IsnadMemory
from sard.memory.l0_evidence import L0EvidenceStore
from sard.planner.pipeline import IsnadPlanner
from sard.config.rag import ModelRoute, RAGSettings
from sard.rag.query_rewriter import QueryRewriteService


def _rag_settings() -> RAGSettings:
    return RAGSettings(
        nvidia_api_key="test",
        chat_base_url=None,
        embedding_base_url=None,
        rerank_base_url=None,
        chat_route=ModelRoute("generation", "chat-primary", ()),
        query_route=ModelRoute("query_rewrite", "query-primary", ("query-fb1", "query-fb2")),
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


class _FakeResp:
    def __init__(self, content: str):
        self.content = content


class _FakeModel:
    def __init__(self, content="{}"):
        self.content = content
        self.calls = 0

    def invoke(self, msgs):
        self.calls += 1
        return _FakeResp(self.content)


def test_l3_session_isolation():
    """L3UserRegister must not leak regions/topics across session_id."""
    mem = IsnadMemory()
    planner = IsnadPlanner(memory=mem)

    # Session A: eastern region query (contains eastern keyword)
    planner.plan_and_execute(
        query="تجفيف الروبيان في تاروت خبرني",
        session_id="sess-eastern-A",
        llm_invoke_fn=lambda s, u: "جواب روبيان",
    )
    prof_a = mem.l3.get_or_create_profile("sess-eastern-A")

    # Session B: neutral Qatar query, should not inherit eastern
    planner.plan_and_execute(
        query="ما عاصمة قطر؟",
        session_id="sess-neutral-B",
        llm_invoke_fn=lambda s, u: "الدوحة",
    )
    prof_b = mem.l3.get_or_create_profile("sess-neutral-B")

    assert "eastern" not in prof_b.last_discussed_regions, f"neutral session leaked eastern: {prof_b.last_discussed_regions}"
    # A should have eastern, B should have none or at least not eastern
    assert prof_a.last_discussed_regions != prof_b.last_discussed_regions or prof_b.last_discussed_regions == []


def test_l3_caps_and_persistence_within_session():
    mem = IsnadMemory()
    # Push 7 regions (cap is 5) via direct update_interaction
    for i, region in enumerate(["eastern", "najd", "hijaz", "asir", "north", "south", "national"]):
        mem.l3.update_interaction(session_id="sess-cap", region=region)  # type: ignore

    prof = mem.l3.get_or_create_profile("sess-cap")
    assert len(prof.last_discussed_regions) <= 5
    # topics cap 10
    for i in range(12):
        mem.l3.update_interaction(session_id="sess-cap", topic=f"topic-{i}")
    prof2 = mem.l3.get_or_create_profile("sess-cap")
    assert len(prof2.recent_query_topics) <= 10
    assert prof2.recent_query_topics[-1] == "topic-11"


def test_l0_l1_per_instance_isolation():
    """L0/L1 stores are per IsnadMemory instance, not global singleton."""
    mem1 = IsnadMemory()
    mem2 = IsnadMemory()
    ev1 = mem1.l0.store_evidence(excerpt="شرق نص 1", origin="src1", region="eastern")
    ev2 = mem2.l0.store_evidence(excerpt="شرق نص 2", origin="src2", region="najd")
    assert mem1.l0.get_evidence(ev1.source_id) is not None
    assert mem1.l0.get_evidence(ev2.source_id) is None
    assert mem2.l0.get_evidence(ev2.source_id) is not None
    assert mem2.l0.get_evidence(ev1.source_id) is None


def test_query_rewrite_cache_hit_and_namespace():
    """Cache key = (normalized_query, model_id) — same query hits, different query/model misses."""
    calls: list[str] = []

    def factory(model_id, settings):
        calls.append(model_id)
        return _FakeModel(content='{"normalized_question": "سؤال", "search_variants": ["سؤال", "بديل"]}')

    svc = QueryRewriteService(settings=_rag_settings(), chat_model_factory=factory)
    svc.rewrite("سؤال يتكرر")
    svc.rewrite("سؤال يتكرر")  # should hit cache, no new factory call for primary
    assert calls.count("query-primary") == 1, f"second call should be cached, calls={calls}"
    svc.rewrite("سؤال مختلف")
    assert calls.count("query-primary") == 2, "different normalized query should miss"
    # Different model would be separate key (not exercised here but key includes model_id)


def test_query_rewrite_cache_does_not_leak_pilot_entities_to_neutral():
    """Neutral query must never gain pilot entities via cache."""
    def factory(model_id, settings):
        # Even if model hallucinates pilot entity, sanitization should strip it
        return _FakeModel(content='{"normalized_question": "سؤال", "search_variants": ["سؤال", "الروبيان المجفف"]}')

    svc = QueryRewriteService(settings=_rag_settings(), chat_model_factory=factory)
    result, _ = svc.rewrite("ما هو برنامج سياحي في قطر؟")
    # "الروبيان المجفف" should be sanitized away because original is neutral
    assert all("روبيان" not in v for v in result.search_variants), f"leaked pilot entity: {result.search_variants}"


def test_deterministic_variants_allowlist_overfilter_documents_bug():
    """Current deterministic expansion drops valid lexical variants (R4 over-filter).

    This test documents the BUG: تجفيف الروبيان should expand to الروبيان المجفف
    but sanitization rejects it because the variant surface was not verbatim in original.
    Phase-2 should fix `_sanitize_search_variants` to allow deterministic equivalents.
    """
    from sard.rag.query_rewriter import deterministic_query_variants

    variants = deterministic_query_variants("ما هي حرفة تجفيف الروبيان في تاروت؟")
    # Current behavior: only original survives (over-filter)
    # Expected after fix: should contain the lexical variant "الروبيان المجفف"
    # We assert the BUG is present (so test passes now, fails after fix — flip it then).
    assert len(variants) == 1, f"bug not reproduced: got {variants}"
    assert variants[0].startswith("ما هي حرفة تجفيف الروبيان")
    # Document expected post-fix assertion (commented, for Phase-2):
    # assert any("الروبيان المجفف" in v for v in variants)


def test_planner_locate_does_not_use_l3_as_ground_truth():
    """L3 must never supply cultural evidence — locate is query-derived only."""
    from sard.planner.locate import locate_cultural_context

    # Even if L3 has eastern history, locate for Qatar query should be eastern? No, Qatar not in patterns → unknown
    loc = locate_cultural_context("ما عاصمة قطر؟")
    # Qatar not in region patterns (only Najd/Hijaz/Asir/Eastern/North/South/National)
    # So region should be unknown, not eastern, even if L3 holds eastern
    assert loc.region in ("unknown", "national")
    # L3 has no API to provide ClaimAtoms/Evidence
    mem = IsnadMemory()
    assert not hasattr(mem.l3, "get_evidence")
    assert not hasattr(mem.l3, "get_atom")
