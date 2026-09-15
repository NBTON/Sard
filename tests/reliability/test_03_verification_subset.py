"""J5: single unsupported claim -> 9 survive (boundary G, mocked verifier).

Uses the real verification subset helpers (no live model):
  sard/agent/routing.py:collect_verified_claims / assemble_partial_answer
  sard/outputs/validation.py:accepted_claims / verified_answer_subset /
    build_verified_render_input
"""
from __future__ import annotations

from types import SimpleNamespace

from sard.agent.routing import assemble_partial_answer, collect_verified_claims
from sard.outputs.validation import accepted_claims, build_verified_render_input, verified_answer_subset

from .conftest import make_citation_sources, make_claim_records


def test_05_nine_of_ten_claims_survive_routing_subset():
    claims = make_claim_records(9, 1, unsupported_index=4)
    assert len(claims) == 10
    verified = collect_verified_claims({"atomic_claims": claims})
    assert len(verified) == 9
    assert all("غير مدعوم" not in c.text for c in verified)
    answer = assemble_partial_answer({"atomic_claims": claims})
    assert "ادعاء غير مدعوم" not in answer
    assert "تم استبعاد 1 ادعاء" in answer
    # All 9 supported claim bodies are surfaced with their citations.
    for claim in verified:
        assert claim.text[:12] in answer
        for cid in claim.citation_ids:
            assert f"[{cid}]" in answer


def test_05b_validation_accepted_claims_matches_router():
    claims = make_claim_records(9, 1, unsupported_index=9)
    assert len(accepted_claims(claims)) == 9
    assert len(collect_verified_claims({"atomic_claims": claims})) == 9


def test_05c_verified_answer_subset_drops_only_unsupported_block():
    claims = make_claim_records(9, 1, unsupported_index=2)
    sources = make_citation_sources(10)  # CIT-100..109 covers every cited claim
    allowed = {s.citation_id for s in sources}
    # Draft answer cites every supported claim plus one fabricated id.
    supported_cids = [c.citation_ids[0] for c in claims if c.citation_ids]
    assert len(supported_cids) == 9
    draft = " ".join(f"نص {i} [{cid}]" for i, cid in enumerate(supported_cids))
    draft += " [CIT-FAKE999]"
    subset = verified_answer_subset(draft, claims, allowed)
    assert "ادعاء غير مدعوم" not in subset
    assert "CIT-FAKE999" not in subset
    for cid in supported_cids:
        assert f"[{cid}]" in subset


def test_05d_build_verified_render_input_keeps_nine_drops_one():
    # Unsupported claim is last so the 9 supported CIT-100..108 align with sources.
    claims = make_claim_records(9, 1, unsupported_index=9)
    sources = make_citation_sources(9)
    # Final answer references only supported citations.
    answer = " ".join(f"حقيقة {i} [CIT-{100 + i:03d}]" for i in range(9))
    state = {
        "run_id": "run-reliability-05",
        "final_answer": answer,
        "sources": sources,
        "atomic_claims": claims,
        "warnings": [],
        "verification_result": SimpleNamespace(passed=True),
        "verification_exhausted": False,
        "retrieval_mode": "hybrid_reranked",
        "model_fallback_used": False,
        "fallback_events": [],
        "itinerary": None,
    }
    rendered = build_verified_render_input(state)
    assert len(rendered.accepted_citation_ids) == 9
    assert "غير مدعوم" not in rendered.final_answer
    assert len(rendered.sources) == 9
    # No fabricated citation survives the boundary.
    for src in rendered.sources:
        assert src.citation_id in rendered.accepted_citation_ids
