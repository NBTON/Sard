"""Workstream G claim-level verification tests (10-claim fixture).

Covers: claim classifier, layered L1-L7 short-circuit, per-scope survival
(no whole-doc gate), isolated 1/10 failure with no recompose, strip-claim
keep-row validation, per-scope render skips, stable evidence IDs + reorder
stability, high-risk flagging, non-factual exemption.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sard.agent.nodes import verify as verify_mod
from sard.agent.nodes.verify import (
    classify_claim,
    evidence_ordinals,
    stable_evidence_id,
    verify,
)
from sard.agent.state import ClaimStatus
from sard.agent.state import EvidenceItem
from sard.outputs.schemas import (
    CitationSource,
    FieldSupport,
    Itinerary,
    ItineraryDay,
    ItineraryStop,
    TextBlock,
)
from sard.outputs.validation import (
    build_verified_render_input,
    evidence_ordinals_for_validation,
    stable_evidence_id_for_validation,
)


def _ev(citation_id: str, content: str) -> EvidenceItem:
    return EvidenceItem(
        citation_id=citation_id,
        chunk_id=f"CHUNK-{citation_id}",
        content=content,
        title=f"مصدر {citation_id}",
        source_name="دليل الرياض",
        source_url="https://example.org/riyadh",
        mode="hybrid_reranked",
    )


def _src(citation_id: str) -> CitationSource:
    return CitationSource(citation_id, f"مصدر {citation_id}", "https://example.org/riyadh")


EVIDENCE_CONTENTS = {
    "CIT-001": "تُعد الأسواق الشعبية في الرياض وجهة بارزة للزوار وتفتح أبوابها يوميًا.",
    "CIT-002": "يُفضّل زيارة الأسواق في ساعات المساء خلال الصيف للاستمتاع بالأجواء.",
    "CIT-003": "يقع المتحف الوطني في الرياض ويعرض تاريخ المملكة لزواره الكرام.",
    "CIT-004": "تتوفر المطاعم الشعبية في وسط الرياض وتقدم أطباقًا تقليدية للزوار.",
    "CIT-005": "يُعد برج المملكة في الرياض معلمًا معماريًا بارزًا لجميع الزوار.",
    "CIT-006": "تقع الدرعية التاريخية قرب الرياض وتضم مواقع تراثية عريقة للزوار.",
    "CIT-008": "يُقام مهرجان الرياض السنوي في الشتاء ويجذب آلاف الزوار سنويًا.",
    "CIT-009": "توفر حدائق الرياض مساحات خضراء واسعة للعائلات والزوار دائمًا.",
}


def _evidence() -> list[EvidenceItem]:
    return [_ev(cid, content) for cid, content in EVIDENCE_CONTENTS.items()]


def _draft() -> str:
    p0 = (
        "الأسواق الشعبية في الرياض وجهة بارزة للزوار [CIT-001]. "
        "يُفضّل زيارة الأسواق في ساعات المساء خلال الصيف [CIT-002]. "
        "يقع المتحف الوطني في الرياض ويعرض تاريخ المملكة [CIT-003]."
    )
    p1 = (
        "تتوفر المطاعم الشعبية في وسط الرياض وتقدم أطباقًا تقليدية [CIT-004]. "
        "يُعد برج المملكة في الرياض معلمًا معماريًا بارزًا [CIT-005]. "
        "تقع الدرعية التاريخية قرب الرياض وتضم مواقع تراثية عريقة [CIT-006]. "
        "سعر الدخول خمسون ريالًا [CIT-UNKNOWN]."
    )
    p2 = (
        "يُقام مهرجان الرياض السنوي في الشتاء ويجذب آلاف الزوار [CIT-008]. "
        "توفر حدائق الرياض مساحات خضراء واسعة للعائلات [CIT-009]. "
        "وفيما يلي ملخص منظم للرحلة."
    )
    return f"{p0}\n\n{p1}\n\n{p2}"


def _deps():
    return SimpleNamespace(model_service=None)


def test_classifier_covers_six_classes():
    assert classify_claim("الأسواق في الرياض كبيرة [CIT-001].") == "factual"
    assert classify_claim("سعر الدخول خمسون ريالًا.") == "high_risk_factual"
    assert classify_claim("وفيما يلي ملخص الرحلة.") == "non_factual"
    assert classify_claim("نوصي بزيارة المساء.") == "non_factual"
    assert classify_claim("يبدو أن السوق يعكس تاريخ المدينة [CIT-001].") == "interpretive"
    assert classify_claim("أود زيارة الرياض مع عائلتي.") == "user_provided"
    assert classify_claim("معلومات محدودة عن المواعيد.") == "uncertain"


def test_non_factual_never_demands_citations():
    state = {
        "run_id": "t",
        "draft": "وفيما يلي ملخص منظم للرحلة.",
        "evidence": _evidence(),
        "compose_retry_count": 0,
        "compose_max_retries": 2,
    }
    out = verify(state, _deps())
    assert len(out["atomic_claims"]) == 1
    assert out["atomic_claims"][0].status == ClaimStatus.NON_FACTUAL
    assert out["atomic_claims"][0].reason_codes == ("non_factual",)
    assert out["verification_result"].passed is True


def test_interpretive_needs_hedge_not_removal():
    state = {
        "run_id": "t",
        "draft": "يشير إلى تاريخ المدينة العريق [CIT-001].",
        "evidence": _evidence(),
        "compose_retry_count": 0,
        "compose_max_retries": 2,
    }
    out = verify(state, _deps())
    # No hedge -> PARTIAL with correction, not removal.
    assert out["atomic_claims"][0].status == ClaimStatus.PARTIALLY_SUPPORTED
    assert out["atomic_claims"][0].claim_class == "interpretive"


def test_ten_claim_isolated_failure_no_retry():
    state = {
        "run_id": "ten",
        "draft": _draft(),
        "evidence": _evidence(),
        "compose_retry_count": 0,
        "compose_max_retries": 2,
    }
    out = verify(state, _deps())
    records = out["atomic_claims"]
    assert len(records) == 10
    # CLAIM-01-007 is the price with CIT-UNKNOWN.
    bad = [r for r in records if r.claim_id == "CLAIM-01-007"]
    assert len(bad) == 1
    assert bad[0].status == ClaimStatus.UNSUPPORTED
    assert "unknown_citation" in bad[0].reason_codes
    # Unsupported singleton.
    assert out["unsupported_claims"] == ["CLAIM-01-007"]
    # No recompose loop for isolated 1/10 failure.
    assert out.get("compose_retry_count", 0) == 0
    kinds = [e.kind for e in out["progress_events"]]
    assert "retried" not in kinds
    # Per-scope survival map present; no scope wiped.
    survival = out["scope_survival"]
    assert set(survival) == {"answer:p0", "answer:p1", "answer:p2"}
    assert all(not bucket["wiped"] for bucket in survival.values())
    assert survival["answer:p1"]["total"] == 4
    assert survival["answer:p1"]["survived"] == 3
    # Non-factual exempt.
    nf = [r for r in records if r.claim_class == "non_factual"]
    assert len(nf) == 1 and nf[0].status == ClaimStatus.NON_FACTUAL
    # Passed with warning (per-scope survival, no whole-doc gate).
    assert out["verification_result"].passed is True


def test_stable_evidence_ids_and_reorder_stable():
    evidence = _evidence()
    ids_before = [stable_evidence_id(e.citation_id, e.chunk_id, e.content) for e in evidence]
    ord_before = evidence_ordinals(evidence)
    state = {
        "run_id": "t",
        "draft": _draft(),
        "evidence": evidence,
        "compose_retry_count": 0,
        "compose_max_retries": 2,
    }
    out1 = verify(state, _deps())
    reversed_evidence = list(reversed(evidence))
    ord_after = evidence_ordinals(reversed_evidence)
    # Ordinals remap with order, but citation_ids never change.
    assert set(ord_before) == set(ord_after)
    assert ord_before["CIT-001"] != ord_after["CIT-001"]
    state2 = {**state, "evidence": reversed_evidence}
    out2 = verify(state2, _deps())
    assert out2["unsupported_claims"] == out1["unsupported_claims"] == ["CLAIM-01-007"]
    # Validation-side helpers agree.
    assert stable_evidence_id_for_validation("CIT-001", "CHUNK-CIT-001", "x").startswith("CIT-001:CHUNK-CIT-001:")
    assert set(evidence_ordinals_for_validation([_src("CIT-001"), _src("CIT-002")])) == {"CIT-001", "CIT-002"}


def test_high_risk_requires_strong_grounding_and_flags_row():
    # Price claim with a KNOWN citation but no lexical grounding -> unsupported + flagged.
    state = {
        "run_id": "t",
        "draft": "سعر الدخول خمسون ريالًا [CIT-002].",
        "evidence": _evidence(),
        "compose_retry_count": 0,
        "compose_max_retries": 2,
    }
    out = verify(state, _deps())
    rec = out["atomic_claims"][0]
    assert rec.claim_class == "high_risk_factual"
    assert rec.status == ClaimStatus.UNSUPPORTED
    assert rec.flagged_row is True


def _itinerary_two_rows() -> Itinerary:
    from datetime import time as _time

    def _stop(cid: str, stop_id: str, title: str, start: _time, end: _time) -> ItineraryStop:
        return ItineraryStop(
            time=f"{start.strftime('%H:%M')} - {end.strftime('%H:%M')}",
            title=title,
            location="الرياض",
            paragraphs=(TextBlock(f"وصف موثق للرحلة [{cid}]", (cid,)),),
            stop_id=stop_id,
            start_time=start,
            end_time=end,
            citation_ids=(cid,),
            field_support=(
                FieldSupport("title", (cid,)),
                FieldSupport("location", (cid,)),
                FieldSupport("time", (cid,)),
                FieldSupport("description", (cid,)),
            ),
        )

    day = ItineraryDay(
        "اليوم الأول",
        None,
        (
            _stop("CIT-001", "s1", "المحطة الأولى", _time(9, 0), _time(10, 0)),
            _stop("CIT-002", "s2", "المحطة الثانية", _time(11, 0), _time(12, 0)),
        ),
        notes=(),
        relative_day_number=1,
        field_support=(FieldSupport("title", ("CIT-001",)), FieldSupport("date", provenance="user_provided")),
    )
    from datetime import date

    day = ItineraryDay(day.title, date(2026, 11, 1), day.stops, day.notes, day.relative_day_number, day.field_support)
    return Itinerary(
        title="رحلة الرياض",
        summary="ملخص موثق [CIT-001]",
        days=(day,),
        sources=tuple(_src(cid) for cid in list(EVIDENCE_CONTENTS) + ["CIT-UNKNOWN"] if cid != "CIT-UNKNOWN"),
        generated_at=datetime(2026, 8, 13, 9, 30, tzinfo=ZoneInfo("Asia/Riyadh")),
        citation_ids=(),
        field_support=(
            FieldSupport("title", provenance="user_provided"),
            FieldSupport("summary", ("CIT-001",)),
        ),
    )


def test_validation_row_survival_and_render_completed_with_warning(tmp_path: Path):
    from sard.agent.nodes.render import render as render_node

    # Verify first to get per-scope atomic claims.
    state_v = {
        "run_id": "ten-render",
        "draft": _draft(),
        "evidence": _evidence(),
        "compose_retry_count": 0,
        "compose_max_retries": 2,
    }
    vout = verify(state_v, _deps())
    assert vout["verification_result"].passed is True

    sources = [_src(cid) for cid in EVIDENCE_CONTENTS]
    # Final answer after validation strips the unknown citation (9 survive).
    from sard.outputs.validation import verified_answer_subset

    rebuilt = verified_answer_subset(_draft(), vout["atomic_claims"], {s.citation_id for s in sources})
    assert "CIT-UNKNOWN" not in rebuilt
    for cid in EVIDENCE_CONTENTS:
        assert cid in rebuilt
    # Paragraph text survives (first paragraph intact).
    assert "الأسواق الشعبية" in rebuilt

    itinerary = _itinerary_two_rows()
    state = {
        "run_id": "ten-render",
        "final_answer": rebuilt,
        "draft": _draft(),
        "itinerary": itinerary,
        "sources": sources,
        "atomic_claims": vout["atomic_claims"],
        "verification_result": vout["verification_result"],
        "verification_exhausted": False,
        "retrieval_mode": "hybrid_reranked",
        "fallback_events": [],
        "model_fallback_used": False,
        "warnings": list(vout.get("warnings", [])),
        "caller_dates": [],
        "travel_dates": [],
    }
    verified = build_verified_render_input(state)
    # Good table rows survive: itinerary kept (not None).
    assert verified.itinerary is not None
    assert sum(len(d.stops) for d in verified.itinerary.days) == 2

    deps = SimpleNamespace(render_artifacts=True, output_root=str(tmp_path), render_checksums=False, preview_calendar=False)
    result = render_node(state, deps)
    # PDF + calendar created, outcome completed with warning.
    by_type = {a.artifact_type: a for a in result["rendered_artifacts"]}
    assert by_type["pdf"].creation_status == "created"
    assert by_type["calendar"].creation_status == "created"
    assert result["graph_outcome"] == "completed"
    assert result["warnings"], "expected completed-with-warning for isolated failure"
    assert Path(by_type["pdf"].absolute_path).is_file()
    assert Path(by_type["calendar"].absolute_path).is_file()


def test_render_skips_only_empty_scopes_with_specific_categories(tmp_path: Path):
    from sard.agent.nodes.render import render as render_node

    sources = [_src("CIT-001")]
    state = {
        "run_id": "empty-scope",
        "final_answer": "إجابة موثقة [CIT-001].",
        "draft": "إجابة موثقة [CIT-001].",
        "itinerary": None,
        "sources": sources,
        "atomic_claims": [],
        "verification_result": None,
        "verification_exhausted": False,
        "retrieval_mode": "hybrid_reranked",
        "fallback_events": [],
        "model_fallback_used": False,
        "warnings": [],
        "caller_dates": ["2026-11-01"],
        "travel_dates": [],
    }
    deps = SimpleNamespace(render_artifacts=True, output_root=str(tmp_path), render_checksums=False, preview_calendar=False)
    result = render_node(state, deps)
    by_type = {a.artifact_type: a for a in result["rendered_artifacts"]}
    # Specific empty_scope category, never generic no_verified_itinerary for partial.
    assert by_type["pdf"].error_category == "empty_scope"
    assert by_type["calendar"].error_category == "empty_scope"
    for a in result["rendered_artifacts"]:
        assert a.error_category != "no_verified_itinerary"
