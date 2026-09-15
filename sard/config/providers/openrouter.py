"""OpenRouter provider: OpenAI-compatible chat via ``ChatOpenAI``.

Routes use the STATIC canonical free-model table in
:mod:`sard.config.routing_table` (exact IDs from the bakeoff report
``docs/reports/model_bakeoff_20260915.md`` +
``docs/reports/model_bakeoff_routing_20260915.json``). Human-readable
labels are never used as model IDs, and embedding/rerank IDs stay on the
NVIDIA path (they are not OpenRouter chat IDs — see the bakeoff report).

:func:`refresh_catalog` is an OPTIONAL, read-only freshness check: routing
never depends on the live catalog. ``httpx`` is imported lazily inside
that function so the runtime never hard-requires it (import-hazard fix).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from sard.config.providers import ModelProvider
from sard.config.routing_table import (
    OPENROUTER_CANDIDATES,
    STRUCTURED_TASKS,
    VISION_TASKS,
    TaskClass,
)

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL_DEFAULT = "https://openrouter.ai/api/v1"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


class OpenRouterProvider(ModelProvider):
    """Primary chat provider via OpenRouter's OpenAI-compatible endpoint."""

    name = "openrouter"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        referer: Optional[str] = None,
        title: Optional[str] = None,
    ):
        # Credentials are held in memory only for construction; never logged.
        self._api_key = api_key if api_key is not None else _env("OPENROUTER_API_KEY")
        self._base_url = base_url or _env("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL_DEFAULT)
        self._referer = referer or _env("OPENROUTER_REFERER", "https://sard.local")
        self._title = title or _env("OPENROUTER_TITLE", "Sard Cultural Assistant")

    def build_chat(self, model_id: str, timeout_s: float) -> Any:
        if not self._api_key:
            from sard.config.models import ModelConfigError

            raise ModelConfigError(
                "مفتاح OPENROUTER_API_KEY غير موجود. الرجاء إضافته إلى ملف .env بعد التدوير."
            )
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            from sard.config.models import ModelConfigError

            raise ModelConfigError(
                "حزمة langchain-openai غير مثبّتة. ثبّتها عبر: uv sync --extra openai"
            ) from exc
        return ChatOpenAI(
            model=model_id,
            temperature=0.2,
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=float(timeout_s),
            default_headers={
                "HTTP-Referer": self._referer,
                "X-Title": self._title,
            },
        )

    def build_embeddings(self, model_id: str, timeout_s: float) -> Any:
        """Embeddings stay on the NVIDIA path — never OpenRouter chat IDs."""
        from sard.rag.fallbacks import FallbackClassifiedError, FailureCategory

        raise FallbackClassifiedError(
            FailureCategory.MODEL_UNAVAILABLE,
            "OpenRouter chat models do not serve embeddings; use the NVIDIA path.",
        )

    def supports(self, task_class: Any) -> bool:
        # Provider-level gate: the router additionally filters per-model via
        # the static capability flags in routing_table. Unknown tasks pass
        # through (fail-open); RERANK/TTS are handled as deterministic
        # fallbacks by ModelRouter before providers are consulted.
        return True

    def supports_model(self, model_id: str, task_class: Any) -> bool:
        """Per-model capability gate against the static candidate flags."""
        flags = OPENROUTER_CANDIDATES.get(model_id)
        if flags is None:
            return True  # unknown (e.g. operator override) — fail open
        if isinstance(task_class, TaskClass):
            if task_class in VISION_TASKS and not flags.supports_vision:
                return False
            if task_class in STRUCTURED_TASKS and not flags.supports_structured:
                return False
        return True


def refresh_catalog(timeout_s: float = 8.0) -> list[dict[str, Any]]:
    """OPTIONAL read-only freshness check of the OpenRouter model catalog.

    Returns a list of ``{"id": ..., "free": bool}`` dicts, or ``[]`` when
    the network, ``httpx``, or the endpoint is unavailable. Routing never
    depends on this — the static table is the source of truth.
    """
    try:
        import httpx  # lazy: never a hard runtime dependency
    except ImportError:
        logger.debug("httpx unavailable; skipping OpenRouter catalog refresh.")
        return []
    base = _env("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL_DEFAULT).rstrip("/")
    key = _env("OPENROUTER_API_KEY")
    headers: dict[str, str] = {}
    if key:
        # The key is used in-memory only and never logged.
        headers["Authorization"] = f"Bearer {key}"
    try:
        resp = httpx.get(f"{base}/models", headers=headers, timeout=float(timeout_s))
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.debug("OpenRouter catalog refresh failed (%s); using static table.", type(exc).__name__)
        return []
    out: list[dict[str, Any]] = []
    for entry in data.get("data", []):
        mid = entry.get("id", "")
        if not mid:
            continue
        pricing = entry.get("pricing", {}) or {}
        free = str(pricing.get("prompt", "")) == "0" and str(pricing.get("completion", "")) == "0"
        out.append({"id": mid, "free": free})
    return out


__all__ = ["OpenRouterProvider", "refresh_catalog", "OPENROUTER_BASE_URL_DEFAULT"]
