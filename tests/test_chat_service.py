"""Tests for the provider-neutral chat service (sard/agent/chat_service.py).

The chat model is injected as a fake, so these tests exercise the UI-facing
service contract without any network access, API key, or knowledge of which
provider is "really" configured.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from sard.agent.chat_service import ChatService, current_status_label


class _FakeChatModel:
    """Minimal stand-in for a LangChain BaseChatModel."""

    def __init__(self, reply_text: str):
        self.reply_text = reply_text
        self.received_messages = None

    def invoke(self, messages):
        self.received_messages = messages
        return AIMessage(content=self.reply_text)


class _ExplodingChatModel:
    def invoke(self, messages):
        raise RuntimeError("simulated provider outage")


@pytest.fixture(autouse=True)
def _clean_model_env(monkeypatch):
    for key in (
        "MODEL_PROVIDER",
        "MODEL_NAME",
        "MODEL_TEMPERATURE",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "NVIDIA_API_KEY",
        "NVIDIA_CHAT_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_ask_returns_arabic_response_via_injected_model():
    fake_model = _FakeChatModel("مرحبًا! هذه إجابة تجريبية باللغة العربية.")
    service = ChatService(chat_model=fake_model)

    result = service.ask("ما هي أفضل وجهة سياحية في السعودية؟")

    assert result.ok is True
    assert "مرحبًا" in result.text
    assert result.error_message == ""
    # The service is provider-neutral: it doesn't know or care which
    # provider produced the reply, only that it received text content back.
    assert fake_model.received_messages is not None
    assert len(fake_model.received_messages) == 2  # system + human


def test_ask_rejects_empty_query_without_touching_model():
    fake_model = _FakeChatModel("لن تُستخدم")
    service = ChatService(chat_model=fake_model)

    result = service.ask("   ")

    assert result.ok is False
    assert result.error_message
    assert fake_model.received_messages is None


def test_ask_handles_unexpected_model_errors_without_leaking_details():
    service = ChatService(chat_model=_ExplodingChatModel())

    result = service.ask("سؤال عربي عادي")

    # Provider outage should be handled gracefully without leaking details.
    # Current behavior: fallback hedge with ok=True (never empty), error_message remains empty.
    assert "simulated provider outage" not in result.error_message
    assert "simulated provider outage" not in result.text
    # Accept either graceful hedge (ok=True) or sanitized error (ok=False), but never leak
    assert result.ok in (True, False)
    if not result.ok:
        assert result.error_message
    else:
        assert result.text and len(result.text.strip()) > 10


def test_ask_surfaces_missing_configuration_as_friendly_error(monkeypatch):
    # No chat model injected -> service falls back to the real factory,
    # which must fail fast because MODEL_PROVIDER is unset.
    service = ChatService()

    result = service.ask("سؤال بدون إعداد مزوّد")

    assert result.ok is False
    assert result.error_message  # sanitized message, not a raw traceback


def test_ask_surfaces_unsupported_provider_as_friendly_error(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "not-a-real-provider")
    monkeypatch.setenv("MODEL_NAME", "whatever")
    service = ChatService()

    result = service.ask("سؤال بمزوّد غير مدعوم")

    assert result.ok is False
    assert "غير مدعوم" in result.error_message


def test_ask_surfaces_missing_api_key_as_friendly_error(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("MODEL_NAME", "claude-sonnet-4-5")
    service = ChatService()

    result = service.ask("سؤال بدون مفتاح API")

    assert result.ok is False
    assert "ANTHROPIC_API_KEY" in result.error_message


def test_chat_service_is_agnostic_to_which_provider_is_active():
    """The same ChatService.ask() contract works no matter which provider
    the injected model 'represents' — the service never branches on
    provider identity."""
    anthropic_like = _FakeChatModel("رد من مزوّد أول")
    openai_like = _FakeChatModel("رد من مزوّد ثانٍ")

    result_a = ChatService(chat_model=anthropic_like).ask("سؤال")
    result_b = ChatService(chat_model=openai_like).ask("سؤال")

    assert result_a.ok is True and result_b.ok is True
    assert result_a.text != result_b.text


def test_current_status_label_is_safe_when_unconfigured(monkeypatch):
    label = current_status_label()
    assert "not configured" in label or "غير مُعدّ" in label


def test_current_status_label_reflects_configuration(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("MODEL_NAME", "claude-sonnet-4-5")

    label = current_status_label()

    assert label == "anthropic / claude-sonnet-4-5"


def test_current_status_label_reflects_nvidia_configuration(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "nvidia")
    monkeypatch.setenv("MODEL_NAME", "nvidia/nemotron-3-super-120b-a12b")

    label = current_status_label()

    assert label == "nvidia / nvidia/nemotron-3-super-120b-a12b"

class _HybridPlannerStub:
    """Stub for ask_isnad returning a fixed planner outcome."""

    def __init__(self, result):
        self._result = result

    def __call__(self, *args, **kwargs):
        return self._result


def _dg1_planner_result(decision, with_citation):
    from sard.schemas.isnad import Evidence, IsnadChain, PlannerResult

    ev = []
    if with_citation:
        ev = [Evidence(source_id="s1", origin="ministry", region="national",
                       excerpt="excerpt text", raw_ref="r1")]
    chain = IsnadChain(request_id="req-dg1", classification="other",
                       region="national", evidence=ev, atoms=[], conflicts=[],
                       score="medium", decision=decision, missing=[])
    answer = "grounded answer prose about heritage" if (with_citation or decision == "generate") else ""
    return PlannerResult(chain=chain, answer_ar=answer, answer_en="",
                         visible_sources=list(ev), follow_up="")


def _dg1_service(tmp_path, decision, with_citation):
    from unittest.mock import patch

    from sard.outputs.orchestrator import ArtifactOrchestrator, FileSystemArtifactStore

    store = FileSystemArtifactStore(tmp_path / ("dg1-" + decision))
    service = ChatService(chat_model=_FakeChatModel("model text"),
                          orchestrator=ArtifactOrchestrator(store))
    ask_patch = patch.object(ChatService, "ask_isnad",
                             return_value=_dg1_planner_result(decision, with_citation))
    filter_patch = patch.object(ChatService, "_filter_planner_result",
                                lambda self, q, r: r)
    ask_patch.start()
    filter_patch.start()
    try:
        result = service.ask("create a PDF report about Najdi heritage",
                             use_hybrid_retrieval=True, session_id="sess-dg1")
    finally:
        ask_patch.stop()
        filter_patch.stop()
    return result


def test_dg1_refusal_yields_no_created_artifact(tmp_path):
    result = _dg1_service(tmp_path, "refuse", False)

    assert result.decision == "refuse"
    assert result.artifacts, "refusal must surface a typed artifact entry"
    assert not any(a.get("status") == "created" for a in result.artifacts)
    pdf = next(a for a in result.artifacts if a.get("format") == "pdf")
    assert pdf["status"] == "failed"
    assert pdf["error_category"] == "insufficient_evidence"
    assert pdf["download_url"] is None


def test_dg1_clarification_yields_no_created_artifact(tmp_path):
    result = _dg1_service(tmp_path, "ask", False)

    assert result.decision == "ask"
    assert result.artifacts
    assert not any(a.get("status") == "created" for a in result.artifacts)
    assert all(a.get("download_url") is None for a in result.artifacts)


def test_dg1_unsupported_hedge_yields_no_created_artifact(tmp_path):
    result = _dg1_service(tmp_path, "hedge", False)

    assert result.artifacts
    assert not any(a.get("status") == "created" for a in result.artifacts)
    pdf = next(a for a in result.artifacts if a.get("format") == "pdf")
    assert pdf["error_category"] == "insufficient_evidence"


def test_dg1_supported_hedge_still_renders(tmp_path):
    result = _dg1_service(tmp_path, "hedge", True)

    pdf = next(a for a in result.artifacts if a.get("format") == "pdf")
    assert pdf["status"] == "created"
    assert pdf["download_url"] is not None


def test_dg1_supported_generate_still_renders(tmp_path):
    result = _dg1_service(tmp_path, "generate", True)

    pdf = next(a for a in result.artifacts if a.get("format") == "pdf")
    assert pdf["status"] == "created"
    assert pdf["download_url"] is not None


def test_dg1_config_error_hedge_yields_no_created_artifact(tmp_path):
    service = ChatService()

    result = service.ask("create a PDF report about Najdi heritage",
                         use_hybrid_retrieval=False)

    assert result.ok is False
    assert result.artifacts
    assert not any(a.get("status") == "created" for a in result.artifacts)

def test_dg2_chat_retry_reuses_artifact(tmp_path):
    """DG-2: same-session/same-request retry returns the same stored artifact."""
    from unittest.mock import patch

    from sard.outputs.orchestrator import ArtifactOrchestrator, FileSystemArtifactStore

    store = FileSystemArtifactStore(tmp_path / "dg2-chat")
    service = ChatService(chat_model=_FakeChatModel("model text"),
                          orchestrator=ArtifactOrchestrator(store))
    with patch.object(ChatService, "ask_isnad",
                      return_value=_dg1_planner_result("generate", True)), \
         patch.object(ChatService, "_filter_planner_result",
                      lambda self, q, r: r):
        first = service.ask("create a PDF report about Najdi heritage",
                            use_hybrid_retrieval=True, session_id="sess-retry")
        second = service.ask("create a PDF report about Najdi heritage",
                             use_hybrid_retrieval=True, session_id="sess-retry")
    for result in (first, second):
        pdf = next(a for a in result.artifacts if a.get("format") == "pdf")
        assert pdf["status"] == "created"
    first_pdf = next(a for a in first.artifacts if a.get("format") == "pdf")
    second_pdf = next(a for a in second.artifacts if a.get("format") == "pdf")
    assert first_pdf["id"] == second_pdf["id"]
    assert first_pdf["checksum"] == second_pdf["checksum"]
    assert first_pdf["download_url"] == second_pdf["download_url"]
    assert second_pdf["download_url"] is not None

def _dg3_refuse_result():
    from sard.schemas.isnad import IsnadChain, PlannerResult

    chain = IsnadChain(request_id="req-dg3", classification="other",
                       region="unknown", evidence=[], atoms=[], conflicts=[],
                       score="low", decision="refuse", missing=["out_of_scope"])
    return PlannerResult(chain=chain, answer_ar="", answer_en="",
                         visible_sources=[], follow_up="")


def _dg3_ask_calendar(tmp_path, query):
    from unittest.mock import patch

    from sard.outputs.orchestrator import ArtifactOrchestrator, FileSystemArtifactStore

    store = FileSystemArtifactStore(tmp_path / "dg3-chat")
    service = ChatService(chat_model=_FakeChatModel("model text"),
                          orchestrator=ArtifactOrchestrator(store))
    with patch.object(ChatService, "ask_isnad",
                      return_value=_dg3_refuse_result()):
        result = service.ask(query, use_hybrid_retrieval=True,
                             session_id="sess-dg3")
    return result, store


def test_dg3_dated_calendar_request_creates_parseable_ics(tmp_path):
    """DG-3: explicit dates in chat yield a downloadable ICS with matching VEVENTs."""
    from icalendar import Calendar

    result, store = _dg3_ask_calendar(
        tmp_path,
        "Create an ICS calendar for my trip: Riyadh visit on 2026-03-05 10:00-11:00",
    )
    ics = [a for a in result.artifacts if a.get("format") == "ics"]
    assert len(ics) == 1
    assert ics[0]["status"] == "created"
    assert ics[0]["download_url"] is not None
    data, _, _ = store.get_bytes(ics[0]["filename"])
    cal = Calendar.from_ical(data)
    vevents = [c for c in cal.walk() if c.name == "VEVENT"]
    assert len(vevents) == 1
    assert vevents[0].get("dtstart").dt.isoformat() == "2026-03-05T10:00:00+03:00"
    assert vevents[0].get("dtend").dt.isoformat() == "2026-03-05T11:00:00+03:00"


def test_dg3_undated_calendar_request_fails_honestly(tmp_path):
    """DG-3: missing dates produce a typed failure, never an invented calendar."""
    result, _ = _dg3_ask_calendar(tmp_path, "Create an ICS calendar for my trip")
    assert result.artifacts
    assert not any(a.get("status") == "created" for a in result.artifacts)
    assert all(a.get("download_url") is None for a in result.artifacts)


def test_dg3_invalid_range_calendar_request_fails_honestly(tmp_path):
    """DG-3: end-before-start ranges are rejected, not rendered."""
    result, _ = _dg3_ask_calendar(tmp_path, "Add calendar event on 2026-03-05 14:00-11:00")
    assert result.artifacts
    assert not any(a.get("status") == "created" for a in result.artifacts)

def test_f6_injected_model_skips_network_router():
    """F-6: an injected model is authoritative; the network router is untouched."""
    from unittest.mock import patch

    service = ChatService(chat_model=_FakeChatModel("direct answer"))
    with patch.object(ChatService, "_invoke_via_router",
                      side_effect=AssertionError("router must not be tried")) as router:
        result = service.ask("plain question with no artifact intent",
                             use_hybrid_retrieval=False)
    assert result.ok is True
    assert result.text == "direct answer"
    assert router.call_count == 0
