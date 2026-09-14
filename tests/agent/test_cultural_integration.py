"""Layered cultural-integration checks using fakes only at external boundaries."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from sard.agent.chat_service import ChatService
from sard.agent.cultural_router import CulturalRouter
from sard.memory import IsnadMemory
from sard.rag.query_rewriter import deterministic_query_variants
from sard.rag.relevance import (
    filter_relevant_evidence,
    query_profile,
    relevance_details,
    strong_product_grounding,
)
from sard.schemas.isnad import IsnadChain, PlannerResult


def _hit(*, topic: str, sector: str, region: str = "all", score: float = 0.9, chunk: str = "موضع موثق") -> dict:
    return {
        "title": topic,
        "chunk": chunk,
        "score": score,
        "metadata": {
            "topic": topic,
            "sector": sector,
            "region": region,
            "region_code": region,
            "source_name": "مصدر رسمي",
            "source_url": "https://example.test/source",
            "citation_id": "CIT-TEST-1",
        },
    }


def test_query_profile_handles_single_paraphrased_and_reordered_topics():
    cases = (
        ("كيف يحفظ أهل الساحل الشرقي الجمبري قديماً؟", "shrimp_drying"),
        ("في الأحساء، ما علاقة العيون الحارة بالموروث الطبيعي؟", "al_ahsa_springs"),
        ("كيف يقدم أهل المجلس القهوة العربية؟", "saudi_coffee_majlis"),
        ("ما خبز الجنوب التقليدي في عسير؟", "southern_bread"),
    )

    for question, topic in cases:
        assert topic in query_profile(question).topics
    assert "al_ahsa_springs" not in query_profile("ما تاريخ الأحساء؟").topics


def test_relevance_gate_rejects_cross_sector_and_cross_region_matches():
    coffee_query = "ما آداب تقديم القهوة السعودية في المجلس؟"
    coffee = _hit(topic="القهوة السعودية", sector="culinary")
    bisht = _hit(topic="البشت الحساوي", sector="fashion", region="eastern", chunk="البشت الحساوي حرفة وأزياء تراثية")
    assert relevance_details(coffee_query, coffee)["accepted"] is True
    assert relevance_details(coffee_query, bisht)["accepted"] is False

    bread_query = "ما خبز الجنوب التقليدي في عسير؟"
    unrelated_food = _hit(topic="جريش", sector="culinary", region="najd", chunk="الجريش طبق نجدي تقليدي")
    assert filter_relevant_evidence(bread_query, [unrelated_food]) == []


def test_strong_product_grounding_is_required_for_proposals_and_fashion_is_gated():
    query = "أنشئ بطاقة عن القهوة السعودية وآداب المجلس"
    strong = _hit(topic="القهوة السعودية", sector="culinary", chunk="القهوة السعودية وآداب تقديم الفنجان في المجلس")
    fashion = _hit(topic="البشت الحساوي", sector="fashion", region="eastern", chunk="البشت الحساوي من الأزياء والحرف")
    assert strong_product_grounding(query, [strong]) is True
    assert strong_product_grounding(query, [fashion]) is False
    assert strong_product_grounding("أنشئ بطاقة عن خبز الجنوب في عسير", []) is False


def test_deterministic_rewrite_keeps_safe_springs_equivalent():
    variants = deterministic_query_variants("أين تقع الينابيع الحارة في الأحساء؟")
    assert any("العيون الحارة" in variant for variant in variants)


def test_router_produces_source_grounded_arabic_for_southern_bread():
    source = _hit(
        topic="خبز الجنوب",
        sector="culinary",
        region="asir",
        chunk="تذكر هيئة تطوير منطقة عسير خبز التنور ضمن المأكولات الشعبية المحلية في عسير.",
    )
    router = CulturalRouter(
        rag_search_fn=lambda query, limit: [source],
        parallel_search_fn=lambda **kwargs: [],
        parallel_extract_fn=lambda **kwargs: [],
        multimodal_extract_fn=lambda query, **kwargs: [],
    )

    result = router.answer_query("ما خبز الجنوب التقليدي في عسير؟", lang="ar")

    assert "خبز التنور" in result.answer_text
    assert result.citations
    assert result.citations[0]["url"] == "https://example.test/source"


def test_router_grounding_covers_all_four_cultural_entities():
    cases = (
        (
            "كيف يحفظ أهل الساحل الشرقي الجمبري؟",
            _hit(topic="تجفيف الروبيان", sector="culinary", region="eastern", chunk="يوثق الشاهد تجفيف الروبيان في الساحل الشرقي."),
            "تجفيف الروبيان",
        ),
        (
            "ما الينابيع الحارة في الأحساء؟",
            _hit(topic="الينابيع الحارة", sector="place", region="eastern", chunk="يوثق الشاهد الينابيع الحارة في الأحساء."),
            "الينابيع الحارة",
        ),
        (
            "ما آداب تقديم القهوة السعودية في المجلس؟",
            _hit(topic="القهوة السعودية", sector="culinary", chunk="يوثق الشاهد آداب تقديم القهوة السعودية في المجلس."),
            "القهوة السعودية",
        ),
        (
            "ما خبز الجنوب في عسير؟",
            _hit(topic="خبز الجنوب", sector="culinary", region="asir", chunk="يوثق الشاهد خبز الجنوب في عسير."),
            "خبز الجنوب",
        ),
    )

    for question, source, grounded_term in cases:
        router = CulturalRouter(
            rag_search_fn=lambda query, limit, source=source: [source],
            parallel_search_fn=lambda **kwargs: [],
            parallel_extract_fn=lambda **kwargs: [],
            multimodal_extract_fn=lambda query, **kwargs: [],
        )
        result = router.answer_query(question, lang="ar")
        assert grounded_term in result.answer_text
        assert result.citations


def test_router_abstains_when_southern_bread_has_no_evidence():
    router = CulturalRouter(
        rag_search_fn=lambda query, limit: [],
        parallel_search_fn=lambda **kwargs: [],
        parallel_extract_fn=lambda **kwargs: [],
        multimodal_extract_fn=lambda query, **kwargs: [],
    )

    result = router.answer_query("ما خبز الجنوب التقليدي في عسير؟", lang="ar")

    assert result.citations == []
    assert "مصادر موثقة" in result.answer_text


def test_medical_belief_is_qualified_without_becoming_a_treatment_claim():
    source = _hit(
        topic="الينابيع الحارة",
        sector="place",
        region="eastern",
        chunk="ترتبط بعض العيون الحارة بمعتقدات محلية حول الاستشفاء.",
    )
    router = CulturalRouter(
        rag_search_fn=lambda query, limit: [source],
        parallel_search_fn=lambda **kwargs: [],
        parallel_extract_fn=lambda **kwargs: [],
        multimodal_extract_fn=lambda query, **kwargs: [],
    )

    result = router.answer_query("هل تعالج الينابيع الحارة في الأحساء أمراض الجلد؟", lang="ar")

    assert "ليس دليلاً طبياً" in result.answer_text
    assert result.citations


def test_chat_service_recovers_source_url_and_preserves_done_input_contract():
    memory = IsnadMemory()
    evidence = memory.l0.store_evidence(
        excerpt="القهوة السعودية من عادات الضيافة في المجلس.",
        origin="مصدر القهوة",
        region="national",
        source_type="ministry",
        url_or_doc_id="CIT-COFFEE",
        raw_data={
            "title": "القهوة السعودية وآداب المجلس",
            "score": 0.91,
            "metadata": {
                "topic": "القهوة السعودية",
                "sector": "culinary",
                "region": "all",
                "source_url": "https://example.test/coffee",
                "citation_id": "CIT-COFFEE",
            },
        },
    )
    plan = PlannerResult(
        chain=IsnadChain(
            request_id="req-coffee",
            classification="etiquette",
            region="national",
            evidence=[evidence],
            atoms=[],
            score="high",
            decision="generate",
        ),
        answer_ar="تقدم القهوة السعودية في المجلس وفق الشاهد.",
        answer_en="Saudi coffee is served in the majlis according to the evidence.",
        visible_sources=[evidence],
    )
    planner = SimpleNamespace(memory=memory)
    service = ChatService(chat_model=object(), planner=planner)

    with patch.object(service, "ask_isnad", return_value=plan):
        result = service.ask("ما آداب تقديم القهوة السعودية في المجلس؟", use_hybrid_retrieval=True, session_id="sess-coffee")

    assert result.ok is True
    assert result.citations[0]["citation_id"] == "CIT-COFFEE"
    assert result.citations[0]["source_url"] == "https://example.test/coffee"
    assert result.planner_result is not None


def test_chat_service_timeout_fails_closed_without_cross_topic_fallback():
    service = ChatService(chat_model=object(), planner=SimpleNamespace(memory=IsnadMemory()))
    with patch.object(service, "ask_isnad", side_effect=TimeoutError("deadline exceeded")):
        result = service.ask("ما خبز الجنوب في عسير؟", use_hybrid_retrieval=True, lang="ar")

    assert result.ok is False
    assert "المهلة" in result.error_message
    assert result.text == ""


def test_recipe_proposal_is_failed_closed_without_strong_evidence():
    class _Model:
        def invoke(self, messages):
            return SimpleNamespace(content="إجابة عامة")

    calls = []

    def render(request, deadline_monotonic=None):
        calls.append(request)
        raise AssertionError("ungrounded recipe must not reach the renderer")

    service = ChatService(
        chat_model=_Model(),
        orchestrator=SimpleNamespace(generate_artifact=render),
    )

    result = service.ask("أنشئ بطاقة وصفة خبز الجنوب في عسير بصيغة PDF", use_hybrid_retrieval=False)

    assert result.ok is True
    assert calls == []
    assert result.artifacts
    assert result.artifacts[0]["status"] == "failed"
    assert result.artifacts[0]["error_category"] == "insufficient_evidence"
