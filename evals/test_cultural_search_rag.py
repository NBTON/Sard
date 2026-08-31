"""Evaluation suite for Cultural Search + RAG grounding.

Tests the 3 mandatory evaluation scenarios:
1) Fact in RAG corpus -> MUST NOT web-search, cited from RAG.
2) Current cultural event / "this year" -> MUST web-search via Parallel Search, cited [Web: ...].
3) Low-coverage culture question -> RAG miss triggers Parallel Search -> synthesized cited answer.

Also tests tool schemas, source policies, URL safety, budget limits (max 2 search, 1 extract),
and graceful fallback on web failure.
"""

import pytest
from unittest.mock import MagicMock, patch

from sard.agent.cultural_router import (
    CulturalRouter,
    RetrievalDecision,
    RAG_HIGH_CONFIDENCE_THRESHOLD,
)
from sard.agent.chat_service import ChatService
from sard.agent.tools.cultural_tools import (
    parallel_extract,
    parallel_search,
    rag_search,
    _apply_cultural_source_policy,
    _infer_cultural_metadata,
)


# =========================================================================
# Scenario 1: Fact in RAG Corpus (MUST NOT web-search)
# =========================================================================

def test_eval_1_fact_in_rag_corpus_must_not_web_search():
    """Eval 1: Stable factual question present in RAG corpus MUST NOT trigger web search."""
    router = CulturalRouter()
    
    # Query about traditional shrimp drying in Tarout / Eastern Province (in corpus)
    query = "كيف تتم ممارسة تجفيف الروبيان التقليدية في جزيرة تاروت بالمنطقة الشرقية؟"
    
    rag_res, web_res, ext_res, decision = router.route_and_retrieve(query)
    
    assert decision.rag_executed is True
    assert decision.rag_candidate_count > 0
    assert decision.rag_top_score >= RAG_HIGH_CONFIDENCE_THRESHOLD
    assert decision.is_in_corpus_topic is True
    assert decision.is_time_sensitive is False
    
    # Hard Rule D: Must NOT trigger web search
    assert decision.web_search_triggered is False
    assert decision.web_search_count == 0
    assert len(web_res) == 0
    assert len(ext_res) == 0

    # Test full synthesized response
    res = router.answer_query(query)
    assert len(res.rag_sources) > 0
    assert len(res.web_sources) == 0
    assert len(res.citations) > 0
    assert res.citations[0]["type"] == "rag"
    assert res.answer_text != ""



# =========================================================================
# Scenario 2: Current Cultural Event / "This Year" (MUST web-search)
# =========================================================================

def test_eval_2_current_cultural_event_this_year_must_web_search():
    """Eval 2: Time-sensitive / 2026 event query MUST trigger Parallel Search."""
    # Mock parallel_search to return a realistic verified cultural event result
    mock_web_results = [
        {
            "url": "https://www.moc.gov.sa/events/2026-heritage-festival",
            "title": "مهرجانات وفعاليات وزارة الثقافة لعام 2026",
            "excerpts": [
                "تطلق وزارة الثقافة فعاليات مهرجان التراث الثقافي لعام 2026 بمشاركة الهيئات الثقافية الـ11."
            ],
            "publish_date": "2026-01-15",
        }
    ]

    mock_search = MagicMock(return_value=mock_web_results)
    mock_extract = MagicMock(return_value=[])
    router = CulturalRouter(parallel_search_fn=mock_search, parallel_extract_fn=mock_extract)

    query = "ما هي أبرز مواسم وفعاليات وزارة الثقافة المقامة هذا العام 2026؟"
    rag_res, web_res, ext_res, decision = router.route_and_retrieve(query)

    assert decision.is_time_sensitive is True
    # Hard Rule B: Must trigger web search
    assert decision.web_search_triggered is True
    assert decision.web_search_count >= 1
    assert mock_search.called
    assert len(web_res) > 0

    # Test synthesized cited output
    res = router.answer_query(query)
    assert len(res.citations) > 0
    assert any(c.get("type") == "web" for c in res.citations)
    assert any(c.get("url") == "https://www.moc.gov.sa/events/2026-heritage-festival" for c in res.citations)
    assert res.answer_text != ""


# =========================================================================
# Scenario 3: Low-Coverage Culture Question (RAG miss -> Parallel -> cited)
# =========================================================================

def test_eval_3_low_coverage_culture_question_rag_miss_triggers_parallel():
    """Eval 3: Culture / etiquette topic absent from local RAG corpus triggers Parallel Search."""
    mock_web_results = [
        {
            "url": "https://reab.pro/en/info/business-traditions-and-mentality/business-etiquette-in-qatar",
            "title": "Business Etiquette in Qatar - Greeting and Handshake",
            "excerpts": [
                "In Qatari business meetings, greetings are formal. A handshake is standard between men; allow traditional coffee to be served with the right hand."
            ],
            "publish_date": "2025-08-01",
        }
    ]

    mock_search = MagicMock(return_value=mock_web_results)
    mock_extract = MagicMock(return_value=[])
    mock_rag = MagicMock(return_value=[])  # RAG miss

    router = CulturalRouter(rag_search_fn=mock_rag, parallel_search_fn=mock_search, parallel_extract_fn=mock_extract)


    query = "Find how Qatari business greeting etiquette works in Doha offices in 2026, including handshake and coffee service."
    rag_res, web_res, ext_res, decision = router.route_and_retrieve(query)

    assert decision.rag_top_score == 0.0
    # Hard Rule B: Low RAG score / Out-of-corpus triggers Parallel Search
    assert decision.web_search_triggered is True
    assert len(web_res) > 0
    assert mock_search.called

    # Synthesize answer
    res = router.answer_query(query)
    assert len(res.citations) > 0
    assert any(c.get("type") == "web" for c in res.citations)
    assert res.citations[0]["url"] == "https://reab.pro/en/info/business-traditions-and-mentality/business-etiquette-in-qatar"
    assert res.answer_text != ""


# =========================================================================
# Tool Schema & Metadata Verification
# =========================================================================

def test_rag_search_returns_valid_cultural_metadata():
    """Verify rag_search returns the required metadata keys."""
    results = rag_search("عيون الأحساء الحارة", k=3)
    assert isinstance(results, list)
    if results:
        first = results[0]
        assert "source" in first
        assert "title" in first
        assert "chunk" in first
        assert "score" in first
        assert "metadata" in first
        
        meta = first["metadata"]
        assert "culture" in meta
        assert "region" in meta
        assert "topic" in meta
        assert "language" in meta
        assert meta["language"] in ("ar", "en")


def test_infer_cultural_metadata_accuracy():
    """Verify cultural and topic metadata classification."""
    meta_ahsa = _infer_cultural_metadata("ينابيع الأحساء", "العيون الحارة في واحة الأحساء بالمنطقة الشرقية", "springs")
    assert "الشرقية" in meta_ahsa["culture"]
    assert meta_ahsa["topic"] == "springs"

    meta_qatar = _infer_cultural_metadata("Business in Doha", "Office etiquette and greeting handshake in Qatar", "business")
    assert "قطر" in meta_qatar["culture"]
    assert meta_qatar["topic"] == "etiquette"


# =========================================================================
# Cultural Source Policy & Safety Filtering
# =========================================================================

def test_cultural_source_policy_filtering():
    """Verify clickbait listicles are filtered and institutional sources boosted."""
    raw_results = [
        {"url": "https://www.buzzfeed.com/listicle/10-weird-customs", "title": "10 Crazy Traditions", "excerpts": ["listicle"]},
        {"url": "https://saudipedia.com/article/traditional-majlis", "title": "المجلس السعودي", "excerpts": ["آداب الضيافة والمجلس"]},
        {"url": "https://www.pinterest.com/pin/12345", "title": "Pins", "excerpts": ["pins"]},
        {"url": "https://visitqatar.com/en/culture-and-heritage", "title": "Qatar Heritage", "excerpts": ["Qatari traditions"]},
    ]

    filtered = _apply_cultural_source_policy(raw_results, max_results=8)
    urls = [item["url"] for item in filtered]

    # Blocked
    assert not any("buzzfeed.com" in u for u in urls)
    assert not any("pinterest.com" in u for u in urls)

    # Allowed & Boosted
    assert any("saudipedia.com" in u for u in urls)
    assert any("visitqatar.com" in u for u in urls)


# =========================================================================
# Budget Limits & Graceful Fallback
# =========================================================================

def test_budget_caps_enforced():
    """Assert maximum 2 searches and 1 extract per user turn."""
    mock_search = MagicMock(return_value=[
        {"url": "https://moc.gov.sa/page1", "title": "MOC 1", "excerpts": ["short"]},
    ])
    mock_extract = MagicMock(return_value=[
        {"url": "https://moc.gov.sa/page1", "title": "MOC 1", "markdown": "# Content"}
    ])

    router = CulturalRouter(
        rag_search_fn=lambda q, k: [],  # RAG miss
        parallel_search_fn=mock_search,
        parallel_extract_fn=mock_extract,
    )

    query = "حدث ثقافي جديد 2026"
    rag_res, web_res, ext_res, decision = router.route_and_retrieve(
        query, max_search_calls=2, max_extract_calls=1
    )

    assert mock_search.call_count <= 2
    assert mock_extract.call_count <= 1


def test_graceful_fallback_when_web_fails():
    """If Parallel Search raises an exception, router answers from RAG without crashing."""
    mock_rag_item = {
        "source": "دليل التراث",
        "title": "مقدمة التراث",
        "chunk": "التراث السعودي زاخر بالتقاليد العريقة.",
        "score": 0.60,
        "metadata": {"source_url": "doc1.md", "culture": "سعودي", "topic": "heritage", "language": "ar"},
    }

    def failing_search(*args, **kwargs):
        raise ConnectionError("Parallel API unreachable")

    router = CulturalRouter(
        rag_search_fn=lambda q, k: [mock_rag_item],
        parallel_search_fn=failing_search,
    )

    query = "فعالية تراثية غير مسجلة 2026"
    res = router.answer_query(query)

    assert res.decision.web_unavailable_warning is True
    assert len(res.citations) > 0
    assert res.citations[0]["type"] == "rag"
    assert res.answer_text != ""


def test_chat_service_hybrid_retrieval_integration():
    """Test ChatService.ask with hybrid retrieval enabled."""
    service = ChatService()
    result = service.ask("كيف تتم ممارسة تجفيف الروبيان في جزيرة تاروت بالمنطقة الشرقية؟", use_hybrid_retrieval=True)
    assert result.ok is True
    assert result.text != ""
    assert len(result.citations) > 0


# =========================================================================
# Regression Suite: Out-of-Corpus, Out-of-Domain, and Zero Contamination
# =========================================================================

CONTAMINATION_TERMS = [
    "تجفيف الروبيان",
    "تجفيف الربيان",
    "الروبيان المجفف",
    "العيون الحارة",
    "الينابيع الحارة",
    "جزيرة تاروت",
]


def assert_no_topic_contamination(query: str, res: CulturalQueryResult):
    """Automatically fail unrelated cases if retrieved context or answer contains pilot contamination."""
    q_norm = query.lower()
    is_legit_shrimp = any(k in q_norm for k in ["روبيان", "ربيان", "تاروت", "shrimp", "tarout"])
    is_legit_springs = any(k in q_norm for k in ["ينابيع", "عين حارة", "عيون حارة", "عين الحارة", "springs", "كبريتية"])

    if is_legit_shrimp or is_legit_springs:
        return

    # Check RAG sources
    for r in res.rag_sources:
        chunk_text = (r.get("chunk", "") + " " + r.get("title", "")).lower()
        for term in CONTAMINATION_TERMS:
            assert term not in chunk_text, (
                f"Topic contamination detected in RAG sources for query '{query}': found '{term}'"
            )

    # Check citations
    for c in res.citations:
        cit_text = (str(c.get("title", "")) + " " + str(c.get("snippet", "")) + " " + str(c.get("id", ""))).lower()
        for term in CONTAMINATION_TERMS:
            assert term not in cit_text, (
                f"Topic contamination detected in citations for query '{query}': found '{term}'"
            )

    # Check answer text
    answer_lower = res.answer_text.lower()
    for term in CONTAMINATION_TERMS:
        assert term not in answer_lower, (
            f"Topic contamination detected in answer text for query '{query}': found '{term}'"
        )


def _mock_web_search_for(query: str):
    """Returns mock web results tailored to the query."""
    return [
        {
            "url": f"https://saudipedia.com/{abs(hash(query))}",
            "title": f"توثيق رسمي: {query}",
            "excerpts": [f"معلومات موثقة وشاملة حول {query} وتاريخها وتقاليدها العريقة."],
            "publish_date": "2026-01-01",
        }
    ]


@pytest.mark.parametrize(
    "query",
    [
        "زراعة النخيل في القصيم",
        "العمارة التقليدية في رجال ألمع",
        "تاريخ جدة البلد",
        "الحرف التقليدية في الجوف",
        "التراث البحري في جازان",
    ],
)
def test_out_of_corpus_saudi_queries_route_to_web_without_contamination(query):
    """Out-of-corpus Saudi cultural queries must reject local pilot corpus, route to web, and have zero contamination."""
    mock_search = MagicMock(side_effect=lambda objective, queries, limit=3: _mock_web_search_for(query))
    router = CulturalRouter(parallel_search_fn=mock_search, parallel_extract_fn=lambda *a, **kw: [])

    rag_res, web_res, ext_res, decision = router.route_and_retrieve(query)

    # Local RAG must return 0 relevant results
    assert len(rag_res) == 0, f"RAG returned local chunks for out-of-corpus query '{query}'"
    assert decision.is_in_corpus_topic is False
    assert decision.web_search_triggered is True

    # Answer query
    res = router.answer_query(query)
    assert len(res.rag_sources) == 0
    assert len(res.web_sources) > 0
    assert all(c["type"] == "web" for c in res.citations)

    # Assert ZERO topic contamination
    assert_no_topic_contamination(query, res)


@pytest.mark.parametrize(
    "query",
    [
        "Explain photosynthesis",
        "Create a project-management report",
    ],
)
def test_out_of_domain_general_queries_zero_local_results_zero_contamination(query):
    """General out-of-domain queries must return 0 local results and zero contamination."""
    router = CulturalRouter(parallel_search_fn=lambda *a, **kw: [], parallel_extract_fn=lambda *a, **kw: [])

    rag_res, web_res, ext_res, decision = router.route_and_retrieve(query)

    assert len(rag_res) == 0, f"RAG returned local chunks for non-cultural query '{query}'"
    assert decision.is_in_corpus_topic is False

    res = router.answer_query(query)
    assert len(res.rag_sources) == 0
    assert len(res.citations) == 0

    assert_no_topic_contamination(query, res)


def test_legitimate_shrimp_drying_query_passes_in_corpus():
    """Legitimate shrimp-drying query must pass via RAG without triggering web search."""
    router = CulturalRouter()
    query = "كيف تتم ممارسة تجفيف الروبيان التقليدية في جزيرة تاروت بالمنطقة الشرقية؟"

    rag_res, web_res, ext_res, decision = router.route_and_retrieve(query)

    assert decision.is_in_corpus_topic is True
    assert decision.rag_candidate_count > 0
    assert decision.rag_top_score >= RAG_HIGH_CONFIDENCE_THRESHOLD
    assert decision.web_search_triggered is False
    assert len(rag_res) > 0

    res = router.answer_query(query)
    assert len(res.rag_sources) > 0
    assert len(res.web_sources) == 0
    assert any(c["type"] == "rag" for c in res.citations)


def test_legitimate_al_ahsa_springs_query_passes_in_corpus():
    """Legitimate Al-Ahsa springs query must pass via RAG without triggering web search."""
    router = CulturalRouter()
    query = "أين تقع أشهر الينابيع والعيون الحارة في واحة الأحساء بالمنطقة الشرقية؟"

    rag_res, web_res, ext_res, decision = router.route_and_retrieve(query)

    assert decision.is_in_corpus_topic is True
    assert decision.rag_candidate_count > 0
    assert decision.rag_top_score >= RAG_HIGH_CONFIDENCE_THRESHOLD
    assert decision.web_search_triggered is False
    assert len(rag_res) > 0

    res = router.answer_query(query)
    assert len(res.rag_sources) > 0
    assert len(res.web_sources) == 0
    assert any(c["type"] == "rag" for c in res.citations)


def test_comprehensive_metrics_and_zero_contamination_rate():
    """Evaluate overall metrics across benchmark query set to verify zero topic-contamination."""
    benchmark_queries = [
        # In-corpus (2)
        ("كيف تتم ممارسة تجفيف الروبيان التقليدية في جزيرة تاروت بالمنطقة الشرقية؟", True, False),
        ("أين تقع أشهر الينابيع والعيون الحارة في واحة الأحساء بالمنطقة الشرقية؟", True, False),
        # Out-of-corpus cultural (5)
        ("زراعة النخيل في القصيم", False, True),
        ("العمارة التقليدية في رجال ألمع", False, True),
        ("تاريخ جدة البلد", False, True),
        ("الحرف التقليدية في الجوف", False, True),
        ("التراث البحري في جازان", False, True),
        # Out-of-domain general (2)
        ("Explain photosynthesis", False, True),
        ("Create a project-management report", False, True),
        # Time-sensitive event (1)
        ("ما هي أبرز مواسم وفعاليات وزارة الثقافة المقامة هذا العام 2026؟", False, True),
    ]

    mock_search = MagicMock(side_effect=lambda objective, queries, limit=3: _mock_web_search_for(queries[0]))
    router = CulturalRouter(parallel_search_fn=mock_search, parallel_extract_fn=lambda *a, **kw: [])

    in_corpus_hits = 0
    in_corpus_total = 0
    out_of_domain_rejected = 0
    out_of_domain_total = 0
    web_routing_correct = 0
    total_queries = len(benchmark_queries)
    contaminated_unrelated = 0
    unrelated_total = 0

    for query, is_in_corpus, should_web_route in benchmark_queries:
        rag_res, web_res, ext_res, decision = router.route_and_retrieve(query)
        res = router.answer_query(query)

        if is_in_corpus:
            in_corpus_total += 1
            if len(res.rag_sources) > 0 and not decision.web_search_triggered:
                in_corpus_hits += 1
        else:
            out_of_domain_total += 1
            unrelated_total += 1
            if len(rag_res) == 0:
                out_of_domain_rejected += 1

            # Check contamination
            try:
                assert_no_topic_contamination(query, res)
            except AssertionError:
                contaminated_unrelated += 1

        if decision.web_search_triggered == should_web_route:
            web_routing_correct += 1

    recall_at_k = in_corpus_hits / in_corpus_total if in_corpus_total else 1.0
    out_of_domain_rejection_rate = out_of_domain_rejected / out_of_domain_total if out_of_domain_total else 1.0
    web_routing_accuracy = web_routing_correct / total_queries
    topic_contamination_rate = contaminated_unrelated / unrelated_total if unrelated_total else 0.0

    assert recall_at_k == 1.0, f"Recall@K for in-corpus questions is {recall_at_k}"
    assert out_of_domain_rejection_rate == 1.0, f"Out-of-domain rejection rate is {out_of_domain_rejection_rate}"
    assert web_routing_accuracy == 1.0, f"Web routing accuracy is {web_routing_accuracy}"
    assert topic_contamination_rate == 0.0, f"Topic contamination rate is {topic_contamination_rate} (expected 0.0)"


