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

    assert result.ok is False
    assert "simulated provider outage" not in result.error_message
    assert result.error_message


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
