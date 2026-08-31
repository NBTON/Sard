"""Centralized, provider-neutral LangChain chat-model factory.

This module is the ONLY place in the codebase that should import a
provider-specific LangChain integration package (e.g. ``langchain_anthropic``,
``langchain_openai``). Every other module — the chat service, the Streamlit
UI, and later the LangGraph pipeline — must depend only on the
``BaseChatModel`` interface returned by :func:`get_chat_model`.

Adding a new provider (e.g. Google Gemini, Groq, Ollama, Hugging Face) later
should only require:

1. Adding the provider's LangChain integration package as an optional
   dependency in ``pyproject.toml``.
2. Writing a small ``_build_<provider>(settings)`` function below that
   returns a LangChain chat model.
3. Registering it in ``_PROVIDER_BUILDERS`` and ``SUPPORTED_PROVIDERS``.

No UI or business-logic code needs to change when a provider is added.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

from dotenv import load_dotenv
from langchain_core.language_models.chat_models import BaseChatModel

load_dotenv()
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if (_PROJECT_ROOT / ".env").exists():
    load_dotenv(_PROJECT_ROOT / ".env")

logger = logging.getLogger(__name__)


class ModelConfigError(Exception):
    """Raised when the model configuration is invalid or incomplete.

    Messages on this exception are written to be safe to show directly to
    end users in the UI: they never contain API keys or raw provider
    error payloads.
    """


@dataclass(frozen=True)
class ModelSettings:
    """Resolved, provider-neutral model configuration."""

    provider: str
    model_name: str
    temperature: float


# Register newly supported providers here.
SUPPORTED_PROVIDERS: tuple[str, ...] = ("nvidia", "anthropic", "openai", "openrouter", "gemini", "google")


def _read_settings() -> ModelSettings:
    """Read and validate model configuration from environment variables.

    Does not touch any provider SDK — this only validates provider-neutral
    settings (which provider, which model, which temperature).
    """
    provider = os.environ.get("MODEL_PROVIDER", "").strip().lower()
    model_name = os.environ.get("MODEL_NAME", "").strip()
    temperature_raw = os.environ.get("MODEL_TEMPERATURE", "0.2").strip()

    # Auto-detection of provider from available keys if not explicitly set or set to auto
    if not provider or provider == "auto":
        if os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip():
            provider = "gemini"
            if not model_name:
                model_name = "gemini-2.0-flash"
        elif os.environ.get("OPENAI_API_KEY", "").strip():
            provider = "openai"
            if not model_name:
                model_name = "gpt-4o-mini"
        elif os.environ.get("ANTHROPIC_API_KEY", "").strip():
            provider = "anthropic"
            if not model_name:
                model_name = "claude-3-5-sonnet-20241022"
        elif os.environ.get("OPENROUTER_API_KEY", "").strip():
            provider = "openrouter"
            if not model_name:
                model_name = "google/gemini-2.0-flash-001"
        elif os.environ.get("NVIDIA_API_KEY", "").strip():
            provider = "nvidia"
            if not model_name:
                model_name = "meta/llama-3.1-70b-instruct"

    if not provider:
        raise ModelConfigError(
            "لم يتم تحديد مزوّد النموذج. الرجاء ضبط متغيّر البيئة "
            "MODEL_PROVIDER في ملف .env (مثال: gemini أو openai أو anthropic)."
        )

    if provider in ("google", "gemini"):
        provider = "gemini"
        if not model_name:
            model_name = "gemini-2.0-flash"

    if provider not in SUPPORTED_PROVIDERS:
        supported = "، ".join(SUPPORTED_PROVIDERS)
        raise ModelConfigError(
            f"مزوّد النموذج '{provider}' غير مدعوم حاليًا. "
            f"المزوّدون المدعومون حاليًا: {supported}."
        )

    if not model_name:
        raise ModelConfigError(
            "لم يتم تحديد اسم النموذج. الرجاء ضبط متغيّر البيئة MODEL_NAME "
            "باسم نموذج صالح لدى المزوّد المختار."
        )

    try:
        temperature = float(temperature_raw)
    except ValueError as exc:
        raise ModelConfigError(
            "قيمة MODEL_TEMPERATURE غير صالحة. الرجاء استخدام رقم عشري، مثل 0.2."
        ) from exc

    return ModelSettings(provider=provider, model_name=model_name, temperature=temperature)


def _build_anthropic(settings: ModelSettings) -> BaseChatModel:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise ModelConfigError(
            "مفتاح ANTHROPIC_API_KEY غير موجود. الرجاء إضافته إلى ملف .env."
        )

    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as exc:
        raise ModelConfigError(
            "حزمة langchain-anthropic غير مثبّتة. ثبّتها عبر: "
            'uv sync --extra anthropic'
        ) from exc

    return ChatAnthropic(
        model=settings.model_name,
        temperature=settings.temperature,
        api_key=api_key,
    )


def _build_openai(settings: ModelSettings) -> BaseChatModel:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ModelConfigError(
            "مفتاح OPENAI_API_KEY غير موجود. الرجاء إضافته إلى ملف .env."
        )

    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise ModelConfigError(
            "حزمة langchain-openai غير مثبّتة. ثبّتها عبر: "
            'uv sync --extra openai'
        ) from exc

    return ChatOpenAI(
        model=settings.model_name,
        temperature=settings.temperature,
        api_key=api_key,
    )


def _build_nvidia(settings: ModelSettings) -> BaseChatModel:
    """Build the regular chat model through NVIDIA NIM."""
    api_key = os.environ.get("NVIDIA_API_KEY", "").strip()
    base_url = os.environ.get("NVIDIA_CHAT_BASE_URL", "").strip()
    if not api_key and not base_url:
        raise ModelConfigError(
            "NVIDIA_API_KEY is missing. Add it to .env for hosted NVIDIA NIM, "
            "or set NVIDIA_CHAT_BASE_URL for a self-hosted NIM endpoint."
        )

    try:
        from langchain_nvidia_ai_endpoints import ChatNVIDIA
    except ImportError as exc:
        raise ModelConfigError(
            "The langchain-nvidia-ai-endpoints package is not installed. "
            "Install it with: uv sync --extra nvidia"
        ) from exc

    kwargs = {
        "model": settings.model_name,
        "temperature": settings.temperature,
        "max_retries": 1,
    }
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    return ChatNVIDIA(**kwargs)


def _build_openrouter(settings: ModelSettings) -> BaseChatModel:
    """Build chat model via OpenRouter OpenAI-compatible endpoint."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise ModelConfigError(
            "مفتاح OPENROUTER_API_KEY غير موجود. الرجاء إضافته إلى ملف .env بعد التدوير."
        )
    base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip()
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise ModelConfigError(
            "حزمة langchain-openai غير مثبّتة. ثبّتها عبر: uv sync --extra openai"
        ) from exc
    return ChatOpenAI(
        model=settings.model_name,
        temperature=settings.temperature,
        api_key=api_key,
        base_url=base_url,
        default_headers={
            "HTTP-Referer": os.environ.get("OPENROUTER_REFERER", "https://sard.local"),
            "X-Title": os.environ.get("OPENROUTER_TITLE", "Sard Cultural Assistant"),
        },
    )


def _build_gemini(settings: ModelSettings) -> BaseChatModel:
    """Build chat model via Google Gemini (Gemini 1.5/2.0/2.5 Flash)."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise ModelConfigError(
            "مفتاح GEMINI_API_KEY أو GOOGLE_API_KEY غير موجود. الرجاء إضافته إلى ملف .env."
        )
    # 1. Try langchain-google-genai if installed
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=settings.model_name or "gemini-2.0-flash",
            temperature=settings.temperature,
            google_api_key=api_key,
        )
    except ImportError:
        pass

    # 2. Try standard OpenAI-compatible endpoint with langchain-openai
    try:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=settings.model_name or "gemini-2.0-flash",
            temperature=settings.temperature,
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
    except ImportError as exc:
        raise ModelConfigError(
            "حزمة langchain-google-genai أو langchain-openai غير مثبّتة."
        ) from exc


# Provider name -> builder function. Add new providers here.
_PROVIDER_BUILDERS: Dict[str, Callable[[ModelSettings], BaseChatModel]] = {
    "nvidia": _build_nvidia,
    "anthropic": _build_anthropic,
    "openai": _build_openai,
    "openrouter": _build_openrouter,
    "gemini": _build_gemini,
    "google": _build_gemini,
}


def get_model_settings() -> ModelSettings:
    """Return the resolved, provider-neutral model settings.

    Raises:
        ModelConfigError: if the provider/model/temperature configuration
            is invalid. Safe to display to end users.
    """
    return _read_settings()


def get_chat_model() -> BaseChatModel:
    """Build and return a LangChain chat model based on environment config.

    This is the single entry point application code should use to obtain a
    chat model. The returned object always implements LangChain's
    ``BaseChatModel`` interface, regardless of provider.

    Raises:
        ModelConfigError: if the provider/model/key configuration is
            invalid or the required integration package is missing. The
            exception message is safe to display to end users.
    """
    settings = _read_settings()
    builder = _PROVIDER_BUILDERS[settings.provider]

    logger.info(
        "Building chat model (provider=%s, model=%s, temperature=%s)",
        settings.provider,
        settings.model_name,
        settings.temperature,
    )

    try:
        return builder(settings)
    except ModelConfigError:
        raise
    except Exception as exc:  # pragma: no cover - defensive catch-all
        logger.exception(
            "Unexpected error building chat model (provider=%s)", settings.provider
        )
        raise ModelConfigError(
            "تعذّر تهيئة نموذج الدردشة بسبب خطأ غير متوقع. "
            "راجع سجلات التشغيل المحلية للتفاصيل."
        ) from exc


def get_dashscope_key() -> Optional[str]:
    """Retrieve DashScope API key for Qwen multimodal features if set."""
    return os.environ.get("DASHSCOPE_API_KEY", "").strip() or None


def has_dashscope_multimodal() -> bool:
    """Check if DashScope multimodal API is configured."""
    return bool(get_dashscope_key())
