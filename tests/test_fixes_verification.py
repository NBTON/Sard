"""Verification tests for Sard Agent fixes — artifact routing, scope guardrails, i18n, retrieval confidence.

Covers acceptance scenarios 1-10.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

from sard.agent.capability_routing import classify_intent, is_format_question, Capability
from sard.agent.lang_utils import resolve_language, detect_language
from sard.agent.scope_guard import is_out_of_scope, scope_guard_response, check_scope_before_retrieval
from sard.agent.cultural_router import CulturalRouter
from sard.agent.chat_service import ChatService
from langchain_core.messages import AIMessage
from unittest.mock import MagicMock
from sard.schemas.isnad import PlannerResult, IsnadChain


# ---- Acceptance 1: Arabic PPTX in chat triggers artifact ----
def test_arabic_presentation_triggers_pptx():
    intent = classify_intent("اعطني ملف بوربوينت عن القهوة السعودية")
    assert intent.domain_capability == Capability.PRESENTATION_DECK
    assert "pptx" in intent.requested_formats
    assert intent.explicit_artifact_request is True

def test_arabic_presentation_colloquial():
    intent = classify_intent("احتاج برزنتيشن عن الضيافة")
    assert intent.domain_capability == Capability.PRESENTATION_DECK
    assert "pptx" in intent.requested_formats

# ---- Acceptance 1b: English PPTX ----
def test_english_presentation_triggers():
    for q in ["Create a presentation about Saudi coffee.", "I need a PowerPoint about hospitality."]:
        intent = classify_intent(q)
        assert intent.domain_capability == Capability.PRESENTATION_DECK, q
        assert "pptx" in intent.requested_formats
        assert intent.explicit_artifact_request

# ---- Acceptance 2: Indirect printable report triggers PDF ----
def test_indirect_report_triggers_pdf():
    intent = classify_intent("حول هذا إلى تقرير قابل للتحميل")
    assert "pdf" in intent.requested_formats
    assert intent.explicit_artifact_request is True
    # also English
    intent2 = classify_intent("Create a printable report about AlUla")
    assert "pdf" in intent2.requested_formats

def test_report_downloadable_english():
    intent = classify_intent("Convert this to a downloadable report")
    assert "pdf" in intent.requested_formats

# ---- Acceptance 3: Heritage question does not accidentally trigger artifact ----
def test_heritage_question_no_artifact():
    intent = classify_intent("ما هي القهوة السعودية؟")
    assert intent.explicit_artifact_request is False
    assert intent.requested_formats == ("text",)
    # English heritage
    intent2 = classify_intent("What are the best hotels in Riyadh?")
    assert intent2.explicit_artifact_request is False

def test_format_question_no_artifact():
    for q in ["ما هو PDF؟", "What is PPTX?", "ما هي مميزات ملف PDF؟"]:
        assert is_format_question(q) is True, q
        intent = classify_intent(q)
        assert intent.explicit_artifact_request is False, q
        assert "pptx" not in intent.requested_formats or intent.domain_capability == Capability.SIMPLE_CONVERSATION, q

# ---- Acceptance 4: Samurai-only blocked ----
def test_samurai_blocked_ar():
    oos, _ = is_out_of_scope("حدثني عن تقاليد الساموراي اليابانية")
    assert oos is True
    blocked, text = check_scope_before_retrieval("حدثني عن الساموراي", lang="ar")
    assert blocked is True
    assert "سرد" in text

def test_samurai_blocked_en():
    oos, _ = is_out_of_scope("Tell me about Japanese samurai traditions")
    assert oos is True
    blocked, text = check_scope_before_retrieval("Tell me about samurai", lang="en")
    assert blocked is True
    assert "Sard" in text

def test_chat_service_blocks_samurai():
    class FakeModel:
        def invoke(self, msgs):
            return AIMessage(content="should not be called")
    svc = ChatService(chat_model=FakeModel(), planner=MagicMock(), router=MagicMock())
    res = svc.ask("Tell me about Japanese samurai traditions", lang="en", use_hybrid_retrieval=True)
    assert res.decision == "scope_block"
    assert "outside my" in res.text.lower() or "Sard" in res.text

# ---- Acceptance 5: Comparative Saudi+Japanese allowed ----
def test_comparative_allowed():
    oos, _ = is_out_of_scope("Compare Saudi hospitality and Japanese hospitality")
    assert oos is False
    oos2, _ = is_out_of_scope("قارن بين الضيافة السعودية واليابانية")
    assert oos2 is False

def test_comparative_not_blocked_in_router():
    router = CulturalRouter(rag_search_fn=lambda q,k: [], parallel_search_fn=lambda **kw: [], parallel_extract_fn=lambda **kw: [])
    res = router.answer_query("Compare Saudi hospitality and Japanese hospitality", lang="en")
    # Should not be scope block (which returns scope message), should be hedge due to no evidence
    assert "outside my" not in res.answer_text.lower()
    # Should not contain irrelevant samurai block

# ---- Acceptance 6: English locale + English prompt -> English response ----
def test_english_locale_produces_english():
    class FakeModel:
        def invoke(self, msgs):
            return AIMessage(content="fake")
    def mock_plan(query, session_id=None, mock_multimodal_files=None, llm_invoke_fn=None, status_callback=None, lang="ar"):
        chain = IsnadChain(request_id="r", classification="other", region="unknown", evidence=[], atoms=[], conflicts=[], score="high", decision="generate")
        if lang == "en":
            return PlannerResult(chain=chain, answer_ar=None, answer_en="English verified answer", visible_sources=[])
        else:
            return PlannerResult(chain=chain, answer_ar="Arabic answer", answer_en=None, visible_sources=[])
    svc = ChatService(chat_model=FakeModel())
    svc.planner.plan_and_execute = mock_plan
    res = svc.ask("What is Saudi coffee?", lang="en", use_hybrid_retrieval=True)
    assert "English" in res.text

# ---- Acceptance 7: Arabic locale + Arabic prompt -> Arabic response ----
def test_arabic_locale_produces_arabic():
    class FakeModel:
        def invoke(self, msgs):
            return AIMessage(content="fake")
    def mock_plan(query, session_id=None, mock_multimodal_files=None, llm_invoke_fn=None, status_callback=None, lang="ar"):
        chain = IsnadChain(request_id="r", classification="other", region="unknown", evidence=[], atoms=[], conflicts=[], score="high", decision="generate")
        if lang == "en":
            return PlannerResult(chain=chain, answer_ar=None, answer_en="English", visible_sources=[])
        else:
            return PlannerResult(chain=chain, answer_ar="Arabic answer", answer_en=None, visible_sources=[])
    svc = ChatService(chat_model=FakeModel())
    svc.planner.plan_and_execute = mock_plan
    res = svc.ask("ما هي القهوة السعودية؟", lang="ar", use_hybrid_retrieval=True)
    assert "Arabic" in res.text

# ---- Acceptance 8: Explicit locale deterministic for mixed prompt ----
def test_mixed_prompt_explicit_locale_wins():
    assert resolve_language("en", "ما هي القهوة السعودية؟ What is coffee?") == "en"
    assert resolve_language("ar", "What is Saudi coffee? ما هي القهوة") == "ar"
    # Infer without explicit
    assert detect_language("ما هي القهوة؟") == "ar"
    assert detect_language("What is coffee?") == "en"
    # Explicit overrides inference
    assert resolve_language("en", "ما هي القهوة السعودية؟") == "en"
    assert resolve_language("ar", "What is Saudi coffee?") == "ar"

# ---- Acceptance 9: Unknown proverb with weak retrieval causes clarification not unrelated citation ----
def test_unknown_proverb_clarification_ar():
    router = CulturalRouter()
    # Use a nonsense proverb unlikely to be in KB
    res = router.answer_query("ما معنى المثل النادر المزعوم خرطي؟", lang="ar", llm_invoke_fn=None)
    # Should be clarification asking for exact wording/region, not unrelated citation
    assert len(res.citations) == 0, "Should not cite unrelated regional articles"
    assert "النص الحرفي" in res.answer_text or "توضيح" in res.answer_text
    assert "خرطي" not in " ".join([c.get("title","") for c in res.citations])  # no irrelevant

def test_unknown_proverb_clarification_en():
    router = CulturalRouter()
    res = router.answer_query("Explain the proverb: خرطي unknown", lang="en", llm_invoke_fn=None)
    assert len(res.citations) == 0
    assert "exact wording" in res.answer_text.lower() or "clarify" in res.answer_text.lower()

# ---- Acceptance 10: Previously working artifact flows still pass (PDF, ICS, SVG, PPTX) ----
def test_artifact_flows_basic():
    from sard.outputs.orchestrator import ArtifactOrchestrator
    from sard.agent.capability_routing import StructuredIntent, Capability
    orch = ArtifactOrchestrator()
    # PPTX
    intent = StructuredIntent(domain_capability=Capability.PRESENTATION_DECK, requested_formats=("pptx",), explicit_artifact_request=True, extracted_topic="Saudi coffee", region="الرياض")
    res = orch.orchestrate_from_intent(intent, raw_text="Test presentation about Saudi coffee")
    assert any(r.format == "pptx" and r.status == "created" for r in res)
    # PDF
    intent2 = StructuredIntent(domain_capability=Capability.SAUDI_CULTURAL_FACTUAL, requested_formats=("pdf",), explicit_artifact_request=True, extracted_topic="AlUla heritage", region="العلا")
    res2 = orch.orchestrate_from_intent(intent2, raw_text="Report about AlUla")
    assert any(r.format == "pdf" and r.status == "created" for r in res2)
    # ICS
    intent3 = StructuredIntent(domain_capability=Capability.CALENDAR_SYNC, requested_formats=("ics",), explicit_artifact_request=True, extracted_topic="سهيل", region="المملكة")
    res3 = orch.orchestrate_from_intent(intent3, raw_text="Heritage calendar")
    assert any(r.format == "ics" and r.status == "created" for r in res3)
    # SVG
    intent4 = StructuredIntent(domain_capability=Capability.DIAGRAM_GENERATION, requested_formats=("svg",), explicit_artifact_request=True, extracted_topic="Majlis etiquette flowchart", region="نجد")
    res4 = orch.orchestrate_from_intent(intent4, raw_text="Flowchart")
    assert any(r.format == "svg" and r.status == "created" for r in res4)
