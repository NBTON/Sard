"""Unit and integration tests for Sard's Isnād Provenance Planner.

Verifies:
1. Weak chain (no sources) -> 'refuse' or 'ask', with zero model hallucinations.
2. Regional conflict (Asir vs Najd traditions) -> conflict surfaced, no merged traditions.
3. Photo labeled Hijazi while evidence is Najdi -> refuse invented lineage.
4. L3 User Register safety -> retains preferences without fabricating cultural facts.
5. Canvas node_id -> drills down to exact L1 atoms and L0 raw excerpts.
6. Najdi door photo demo -> recognizes tamarisk wood/motifs, rejects Hijazi Mangour, lists image limitations.
7. Score matrix -> high, medium, and low confidence rules.
"""

from __future__ import annotations

import pytest

from sard.memory import (
    IsnadMemory,
    L0EvidenceStore,
    L1AtomStore,
    L3UserRegister,
)
from sard.planner import (
    GroundedRetriever,
    IsnadAssembler,
    IsnadPlanner,
    classify_request,
    decide_action,
    locate_cultural_context,
    score_isnad_chain,
)
from sard.schemas.isnad import ClaimAtom, Evidence, IsnadChain


# ---------------------------------------------------------------------------
# Test 1: Weak Chain Rejection (No Sources)
# ---------------------------------------------------------------------------
def test_weak_chain_refuses_or_asks_without_hallucinating():
    memory = IsnadMemory()
    planner = IsnadPlanner(
        memory=memory,
        retriever=GroundedRetriever(
            l0_store=memory.l0,
            rag_search_fn=lambda q, limit: [],
            parallel_search_fn=lambda **kw: [],
            multimodal_extract_fn=lambda q, **kw: [],
        ),
    )

    result = planner.plan_and_execute(
        query="حدثني عن طقس قبلي غير موثق ومخترع تماماً في جبال نجد",
    )

    assert result.chain.decision in ("refuse", "ask")
    assert result.chain.score == "low"
    assert "عذرًا" in (result.answer_ar or "") or "يُرجى توضيح" in (result.answer_ar or "")
    assert len(result.visible_sources) == 0


# ---------------------------------------------------------------------------
# Test 2: Regional Conflict Surfacing (Asir vs Najd)
# ---------------------------------------------------------------------------
def test_asir_vs_najd_regional_conflict_surfaces_difference_not_merged():
    memory = IsnadMemory()

    # Pre-populate L0 with conflicting regional evidence
    memory.l0.store_evidence(
        excerpt="في عسير، تشتمل ضيافة الأعياد على أطباق العريكة والمبثوثة وقرص الميفا وعسل السدر الجبلي.",
        origin="هيئة فنون الطهي - تراث عسير",
        region="asir",
        source_type="ministry",
        date_or_period="تراث متوارث",
    )
    memory.l0.store_evidence(
        excerpt="في نجد، يتصدر المنسف والقرصان والجريش والحنيني مائدة العيد والضيافة التقليدية.",
        origin="دارة الملك عبدالعزيز - عادات نجد",
        region="najd",
        source_type="ministry",
        date_or_period="القرن الرابع عشر الهجري",
    )

    planner = IsnadPlanner(
        memory=memory,
        retriever=GroundedRetriever(
            l0_store=memory.l0,
            rag_search_fn=lambda q, limit: [
                {
                    "text": "في عسير، تشتمل ضيافة الأعياد على أطباق العريكة والمبثوثة وقرص الميفا.",
                    "title": "أطعمة عسير",
                    "source_name": "هيئة فنون الطهي",
                    "doc_id": "culinary-asir",
                },
                {
                    "text": "في نجد، يتصدر المنسف والقرصان والجريش والحنيني مائدة العيد.",
                    "title": "أطعمة نجد",
                    "source_name": "دارة الملك عبدالعزيز",
                    "doc_id": "dara-najd",
                },
            ],
            parallel_search_fn=lambda **kw: [],
        ),
    )

    result = planner.plan_and_execute(
        query="ما هي أطعمة العيد والضيافة التقليدية المشتركة في نجد وعسير؟",
    )

    # Must detect regional divergence/conflict and surface distinction without averaging
    assert len(result.chain.conflicts) > 0
    assert any("تعارض إقليمي" in c or "مناطق مختلفة" in c for c in result.chain.conflicts)
    assert result.chain.decision == "hedge"
    assert "تمايز التقاليد الإقليمية" in (result.answer_ar or "")
    assert "عسير" in (result.answer_ar or "")
    assert "نجد" in (result.answer_ar or "")


# ---------------------------------------------------------------------------
# Test 3: Door Photo Lineage Protection (Rejecting Hijazi Attribution for Najdi Door)
# ---------------------------------------------------------------------------
def test_door_image_labeled_hijazi_while_retrieval_is_najdi_refuses_invented_lineage():
    memory = IsnadMemory()

    # User uploads a photo claiming Hijazi origin, but official corpus proves it is Najdi tamarisk wood
    mock_files = {
        "door_photo.jpg": {
            "file_type": "image",
            "extracted_text": "صورة باب خشبي عليه نقوش هندسية نجدية ومسامير مقببة",
            "description": "باب خشبي مصنوع من خشب الأثل بنقوش هندسية نجدية",
        }
    }

    planner = IsnadPlanner(
        memory=memory,
        retriever=GroundedRetriever(
            l0_store=memory.l0,
            rag_search_fn=lambda q, limit: [
                {
                    "text": "الأبواب النجدية التراثية تُصنع حصراً من خشب الأثل المحلي وتنقش بزخارف هندسية ومسامير مقببة وضبة خشبية.",
                    "title": "العمارة التقليدية في نجد",
                    "source_name": "هيئة التراث",
                    "doc_id": "heritage-najdi-door",
                }
            ],
            parallel_search_fn=lambda **kw: [],
        ),
    )

    result = planner.plan_and_execute(
        query="أخبرني عن هذا الباب الحجازي التراثي @door_photo.jpg",
        mock_multimodal_files=mock_files,
    )

    # Planner must either refuse the false Hijazi lineage or correct it with Najdi tamarisk lineage
    assert "الأثل" in (result.answer_ar or "") or result.chain.decision == "refuse"
    assert "يختلف هذا الباب جوهرياً عن الأبواب والرواشين الحجازية" in (result.answer_ar or "") or "تعارض" in str(result.chain.conflicts)


# ---------------------------------------------------------------------------
# Test 4: L3 Register Safety (Cannot Supply Missing Cultural Facts)
# ---------------------------------------------------------------------------
def test_l3_register_retains_user_preferences_without_supplying_cultural_facts():
    memory = IsnadMemory()
    register = memory.l3

    # Turn 1: User visits from Hijaz with dialect preference
    register.update_interaction(
        session_id="session-user-123",
        stance="local",
        language="ar",
        dialect="hijazi",
        region="hijaz",
        topic="جدة التاريخية",
    )

    profile = register.get_or_create_profile("session-user-123")
    assert profile.preferred_dialect_or_register == "hijazi"
    assert "hijaz" in profile.last_discussed_regions

    # Turn 2: User asks an ungrounded question about a different topic
    # L0 and L1 must remain empty of invented facts; L3 cannot supply evidence
    planner = IsnadPlanner(
        memory=memory,
        retriever=GroundedRetriever(
            l0_store=memory.l0,
            rag_search_fn=lambda q, limit: [],
            parallel_search_fn=lambda **kw: [],
        ),
    )

    result = planner.plan_and_execute(
        query="ما هي مكونات طبق غير موجود؟",
        session_id="session-user-123",
    )

    # Must refuse or ask despite L3 having a profile
    assert result.chain.decision in ("refuse", "ask")
    assert len(result.visible_sources) == 0


# ---------------------------------------------------------------------------
# Test 5: Canvas Node Drill-Down to Exact L1 Atoms and L0 Excerpt
# ---------------------------------------------------------------------------
def test_canvas_node_id_drills_down_to_l1_atoms_and_l0_excerpt():
    memory = IsnadMemory()

    ev = memory.l0.store_evidence(
        excerpt="قصر المصمك هو حصن مبني من اللبن والطين يقع في وسط مدينة الرياض.",
        origin="دارة الملك عبدالعزيز",
        region="najd",
        source_type="dated_text",
        url_or_doc_id="dara-masmak-01",
    )

    atom = memory.l1.add_atom(
        text="قصر المصمك حصن طيني تراثي في الرياض",
        region="najd",
        source_ids=[ev.source_id],
        confidence="high",
    )

    canvas = memory.create_canvas("req-test-drill")
    canvas.set_stage_status(
        "retrieve",
        "completed",
        evidence_ids=[ev.source_id],
        atom_ids=[atom.claim_id],
    )

    # Drill down by node_id
    drill_result = canvas.drill_down("node-3-retrieve")

    assert drill_result["node_id"] == "node-3-retrieve"
    assert len(drill_result["atoms"]) == 1
    assert drill_result["atoms"][0]["claim_id"] == atom.claim_id
    assert len(drill_result["evidence"]) == 1
    assert drill_result["evidence"][0]["source_id"] == ev.source_id
    assert "قصر المصمك" in drill_result["raw_excerpts"][0]["excerpt"]

    # Mermaid graph rendering check
    mermaid_text = canvas.to_mermaid()
    assert "graph TD" in mermaid_text
    assert "node-3-retrieve" in mermaid_text


# ---------------------------------------------------------------------------
# Test 6: Najdi Door Photo Demo
# ---------------------------------------------------------------------------
def test_najdi_wooden_door_demo_full_flow():
    memory = IsnadMemory()

    mock_files = {
        "najdi_door.jpg": {
            "file_type": "image",
            "extracted_text": "باب خشبي قديم ذو مصراعين بنقوش هندسية وزهرة الوردة التراثية ومسامير حديدية",
            "description": "باب تراثي من العمارة النجدية مصنوع من خشب الأثل",
        }
    }

    planner = IsnadPlanner(
        memory=memory,
        retriever=GroundedRetriever(
            l0_store=memory.l0,
            rag_search_fn=lambda q, limit: [
                {
                    "text": "تشتهر الأبواب النجدية بصناعتها من خشب الأثل ونقشها بزخارف هندسية ومسامير مقببة وضبة خشبية (سكرة).",
                    "title": "أبواب نجد التراثية",
                    "source_name": "هيئة التراث - وزارة الثقافة",
                    "doc_id": "heritage-najd-wood",
                },
                {
                    "text": "تعتمد النجارة التقليدية في نجد على خشب الأثل المحلي المقاوم للحرارة وحفر الأشكال الهندسية المحروقة.",
                    "title": "الحرف والصناعات التقليدية",
                    "source_name": "المتحف الوطني السعودي",
                    "doc_id": "museum-crafts-najd",
                },
            ],
            parallel_search_fn=lambda **kw: [],
        ),
    )

    result = planner.plan_and_execute(
        query="Tell me the story of this door @najdi_door.jpg (أخبرني بقصة هذا الباب)",
        mock_multimodal_files=mock_files,
    )

    assert result.chain.classification == "object_from_image"
    assert result.chain.region == "najd"
    assert result.chain.score in ("high", "medium")
    assert result.chain.decision == "generate"
    assert "خشب الأثل" in (result.answer_ar or "")
    assert "الرواشين الحجازية" in (result.answer_ar or "")
    assert "ما لا يمكن للصورة إثباته" in (result.answer_ar or "")
    assert len(result.visible_sources) >= 2


# ---------------------------------------------------------------------------
# Test 7: Scoring Matrix (High, Medium, Low)
# ---------------------------------------------------------------------------
def test_score_chain_matrix():
    ev_official_1 = Evidence(
        source_id="src-1",
        origin="وزارة الثقافة",
        region="najd",
        source_type="ministry",
        excerpt="شاهد رسمي 1",
        raw_ref="l0://src-1",
    )
    ev_official_2 = Evidence(
        source_id="src-2",
        origin="دارة الملك عبدالعزيز",
        region="najd",
        source_type="dated_text",
        excerpt="شاهد رسمي 2",
        raw_ref="l0://src-2",
    )
    ev_user = Evidence(
        source_id="src-3",
        origin="مرفق المستخدم",
        region="najd",
        source_type="user_upload",
        excerpt="صورة غير موثقة",
        raw_ref="l0://src-3",
    )

    # 1. High score: >=2 official sources, same region, no conflict
    chain_high = IsnadChain(
        request_id="r1",
        classification="place",
        region="najd",
        evidence=[ev_official_1, ev_official_2],
        atoms=[],
        conflicts=[],
        score="low",
        decision="refuse",
    )
    assert score_isnad_chain(chain_high) == "high"

    # 2. Medium score: 1 official source
    chain_med = IsnadChain(
        request_id="r2",
        classification="place",
        region="najd",
        evidence=[ev_official_1],
        atoms=[],
        conflicts=[],
        score="low",
        decision="refuse",
    )
    assert score_isnad_chain(chain_med) == "medium"

    # 3. Low score: only user upload
    chain_low = IsnadChain(
        request_id="r3",
        classification="place",
        region="najd",
        evidence=[ev_user],
        atoms=[],
        conflicts=[],
        score="low",
        decision="refuse",
    )
    assert score_isnad_chain(chain_low) == "low"
