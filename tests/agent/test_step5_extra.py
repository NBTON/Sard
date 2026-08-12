"""Additional Step 5 tests to cover required scenarios."""

from __future__ import annotations

import os
import pytest
from sard.agent.graph import run_pipeline
from sard.agent.state import RAGMode
from tests.agent.test_core_graph import (
    _make_deps,
    _evidence_answer,
    _scripts_for_success,
    FakeRAGService,
    FakeChatModel,
)


def test_cli_demo_mode_output():
    """Test the output of the agent CLI demo mode."""
    # This just ensures we can import and nothing crashes.
    from sard.cli.agent import _demo_dependencies
    deps = _demo_dependencies()
    result = run_pipeline("أنشئ برنامجًا سياحيًا تراثيًا", dependencies=deps)
    assert result["graph_outcome"] in {"completed", "partial"}


def test_missing_optional_constraints():
    scripts = _scripts_for_success()
    scripts[0] = '{"intent":"travel_planning","destination":null,"duration_days":null,"audience":[],"interests":[],"timing":null,"user_facts":[],"missing_constraints":[],"assumptions":[]}'
    rag = FakeRAGService(_evidence_answer())
    deps = _make_deps(scripts, rag_service=rag, max_retries=2)
    result = run_pipeline("رحلة", dependencies=deps)
    
    assert "وجهة السفر غير محددة" in result["missing_constraints"]
    assert "مدة الرحلة غير محددة" in result["missing_constraints"]


def test_deterministic_understanding_fallback():
    # Provide no scripts for understand, forcing a parse failure -> fallback
    scripts = [
        # understand parsing fails (empty script)
        "",
        # plan
        '{"focus_summary":"استكشاف","days":[{"day_index":1,"focus":"استكشاف","time_blocks":[{"period":"الصباح","activity_type":"استكشاف"}]}],"activity_types":["استكشاف"],"evidence_topics":["استكشاف"],"open_questions":[],"constraints":[]}',
        # compose
        "دليل [CIT-RIY01].",
        # verify
        '{"claims":[{"claim_id":"CLAIM-01-001","status":"supported","correction":"","note":""}]}'
    ]
    rag = FakeRAGService(_evidence_answer())
    deps = _make_deps(scripts, rag_service=rag, max_retries=2)
    result = run_pipeline("رحلة للرياض", dependencies=deps)
    assert result.get("understanding_degraded") is True
    assert result["destination"] is None # basic fallback


def test_hybrid_retrieval_and_reranker_fallback():
    answer = _evidence_answer()
    answer.retrieval_mode = "hybrid"
    answer.reranker_used = "nvidia"
    rag = FakeRAGService(answer)
    deps = _make_deps(_scripts_for_success(), rag_service=rag)
    result = run_pipeline("الرياض", dependencies=deps)
    assert result["retrieval_mode"] == RAGMode.HYBRID_RERANKED.value


def test_full_text_only():
    answer = _evidence_answer()
    answer.retrieval_mode = "full_text_only"
    answer.reranker_used = None
    rag = FakeRAGService(answer)
    deps = _make_deps(_scripts_for_success(), rag_service=rag)
    result = run_pipeline("الرياض", dependencies=deps)
    assert result["retrieval_mode"] == RAGMode.FULL_TEXT_ONLY.value


def test_no_evidence():
    answer = _evidence_answer()
    answer.dense_candidates = []
    answer.fts_candidates = []
    answer.fused_candidates = []
    answer.selected_context = []
    rag = FakeRAGService(answer)
    scripts = _scripts_for_success()
    deps = _make_deps(scripts, rag_service=rag)
    result = run_pipeline("الرياض", dependencies=deps)
    
    assert result["graph_outcome"] == "partial"
    assert "لا دليل متاح" in result["final_answer"] or "أدلة كافية" in result["final_answer"]


def test_unknown_citation_rejection():
    scripts = _scripts_for_success()
    # Compose includes an unknown citation
    scripts[2] = "معلومات جيدة [CIT-UNKNOWN]."
    rag = FakeRAGService(_evidence_answer())
    deps = _make_deps(scripts, rag_service=rag, max_retries=0)
    result = run_pipeline("الرياض", dependencies=deps)
    
    claims = result.get("atomic_claims", [])
    assert any(c.status.value == "unsupported" for c in claims)


def test_unsupported_and_contradicted_claims():
    scripts = _scripts_for_success()
    # model returns contradicted
    scripts[3] = '{"claims":[{"claim_id":"CLAIM-01-001","status":"contradicted","correction":"","note":""}]}'
    rag = FakeRAGService(_evidence_answer())
    deps = _make_deps(scripts, rag_service=rag, max_retries=0)
    result = run_pipeline("الرياض", dependencies=deps)
    assert "CLAIM-01-001" in result["unsupported_claims"]


def test_model_fallback_events():
    answer = _evidence_answer()
    class FakeEvent:
        use_case = "test"
        requested_model = "m1"
        resolved_model = "m2"
        attempt = 1
        outcome = "success"
        quality_degraded = True
        failure_category = None
        latency_ms = 100
        selected_fallback = "m2"
        
    answer.fallback_events = [FakeEvent()]
    rag = FakeRAGService(answer)
    deps = _make_deps(_scripts_for_success(), rag_service=rag)
    result = run_pipeline("الرياض", dependencies=deps)
    
    events = [e.kind for e in result["progress_events"]]
    assert "model_fallback_activated" in events


def test_sanitized_events():
    deps = _make_deps(_scripts_for_success(), rag_service=FakeRAGService(_evidence_answer()))
    result = run_pipeline("الرياض", dependencies=deps)
    
    for ev in result["progress_events"]:
        assert "API_KEY" not in getattr(ev, "summary", "")


def test_structured_itinerary_validation_and_unsupported_blocked_at_render():
    scripts = _scripts_for_success()
    # Verify rejects the claim covering CIT-RIY01
    scripts[3] = '{"claims":[{"claim_id":"CLAIM-01-001","status":"unsupported","correction":"","note":""},{"claim_id":"CLAIM-01-002","status":"unsupported","correction":"","note":""}]}'
    rag = FakeRAGService(_evidence_answer())
    deps = _make_deps(scripts, rag_service=rag, max_retries=0)
    result = run_pipeline("الرياض", dependencies=deps)
    
    # Rendering should reject unsupported factual structured fields
    # verify node removes unsupported claims. Render node will set itinerary to None.
    assert result["itinerary"] is None
    assert any("تم رفض الحقول الواقعية غير المدعومة" in w or "يتضمن استشهادات غير صالحة" in w for w in result["warnings"])



@pytest.mark.live
def test_live_integration():
    """Live NVIDIA+Zvec integration test gated by credentials/env and collection."""
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        pytest.skip("No NVIDIA_API_KEY provided")
    
    from sard.agent.graph import default_dependencies, run_pipeline
    deps = default_dependencies(open_rag=True)
    if deps.rag_service is None:
        pytest.skip("RAG service could not be initialized")
        
    result = run_pipeline("أنشئ برنامجًا سياحيًا", dependencies=deps)
    assert result["graph_outcome"] in {"completed", "partial"}
