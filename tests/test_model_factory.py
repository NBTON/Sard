"""Tests for the central model factory (sard/config/models.py).

These tests never make network calls. Constructing a LangChain provider
chat-model object (e.g. ChatAnthropic/ChatOpenAI) does not itself contact
the provider's API, so a fake, non-functional API key is sufficient here.
"""

from __future__ import annotations

import pytest
from langchain_core.language_models.chat_models import BaseChatModel

from sard.config.models import ModelConfigError, get_chat_model, get_model_settings


@pytest.fixture(autouse=True)
def _clean_model_env(monkeypatch):
    """Ensure each test starts from a blank model-configuration slate."""
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


def test_missing_provider_raises_config_error():
    with pytest.raises(ModelConfigError):
        get_model_settings()


def test_unsupported_provider_raises_config_error(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "made-up-provider")
    monkeypatch.setenv("MODEL_NAME", "some-model")

    with pytest.raises(ModelConfigError, match="غير مدعوم"):
        get_model_settings()


def test_missing_model_name_raises_config_error(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "anthropic")

    with pytest.raises(ModelConfigError):
        get_model_settings()


def test_invalid_temperature_raises_config_error(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("MODEL_NAME", "claude-sonnet-4-5")
    monkeypatch.setenv("MODEL_TEMPERATURE", "not-a-number")

    with pytest.raises(ModelConfigError):
        get_model_settings()


def test_valid_settings_are_resolved(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "OpenAI")  # case-insensitive
    monkeypatch.setenv("MODEL_NAME", "gpt-4o-mini")
    monkeypatch.setenv("MODEL_TEMPERATURE", "0.5")

    settings = get_model_settings()

    assert settings.provider == "openai"
    assert settings.model_name == "gpt-4o-mini"
    assert settings.temperature == 0.5


def test_missing_anthropic_api_key_raises_config_error(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("MODEL_NAME", "claude-sonnet-4-5")

    with pytest.raises(ModelConfigError, match="ANTHROPIC_API_KEY"):
        get_chat_model()


def test_missing_openai_api_key_raises_config_error(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_NAME", "gpt-4o-mini")

    with pytest.raises(ModelConfigError, match="OPENAI_API_KEY"):
        get_chat_model()


def test_missing_nvidia_api_key_raises_config_error(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "nvidia")
    monkeypatch.setenv("MODEL_NAME", "nvidia/nemotron-3-super-120b-a12b")

    with pytest.raises(ModelConfigError, match="NVIDIA_API_KEY"):
        get_chat_model()


def test_anthropic_provider_builds_a_langchain_chat_model(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("MODEL_NAME", "claude-sonnet-4-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")

    model = get_chat_model()

    assert isinstance(model, BaseChatModel)


def test_openai_provider_builds_a_langchain_chat_model(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("MODEL_NAME", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")

    model = get_chat_model()

    assert isinstance(model, BaseChatModel)


def test_nvidia_provider_builds_a_langchain_chat_model(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "nvidia")
    monkeypatch.setenv("MODEL_NAME", "nvidia/nemotron-3-super-120b-a12b")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-not-a-real-key")

    model = get_chat_model()

    assert isinstance(model, BaseChatModel)
