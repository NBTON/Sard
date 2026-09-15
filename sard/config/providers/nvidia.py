"""NVIDIA provider: thin wrapper over the central RAG factories.

Delegates to :func:`sard.config.rag.build_chat_model`,
:func:`sard.config.rag.build_embeddings_model`, and
:func:`sard.config.rag.build_rerank_model` — this module adds only
per-attempt timeout narrowing (via a frozen-settings copy) and the
provider interface. Embedding and rerank IDs stay on this path: they are
not OpenRouter chat IDs.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Optional

from sard.config.providers import ModelProvider


class NvidiaProvider(ModelProvider):
    """Thin wrapper over the existing NVIDIA NIM factories."""

    name = "nvidia"

    def _settings_with_timeout(self, timeout_s: float, base_url_attr: Optional[str] = None) -> Any:
        from sard.config.rag import get_rag_settings

        settings = get_rag_settings()
        try:
            return dataclasses.replace(settings, request_timeout_seconds=float(timeout_s))
        except Exception:
            return settings

    def build_chat(self, model_id: str, timeout_s: float) -> Any:
        from sard.config import rag as rag_config

        settings = self._settings_with_timeout(timeout_s)
        return rag_config.build_chat_model(model_id, settings)

    def build_embeddings(self, model_id: str, timeout_s: float) -> Any:
        from sard.config import rag as rag_config

        settings = self._settings_with_timeout(timeout_s)
        return rag_config.build_embeddings_model(model_id, settings)

    def build_rerank(self, model_id: str, timeout_s: float) -> Any:
        from sard.config import rag as rag_config

        settings = self._settings_with_timeout(timeout_s)
        return rag_config.build_rerank_model(model_id, settings)

    def supports(self, task_class: Any) -> bool:
        # Runtime NVIDIA model IDs come from operator configuration; their
        # capabilities are unknown statically, so fail open here. The
        # router-level deterministic fallbacks (RERANK/TTS) are decided by
        # ModelRouter before providers are consulted.
        return True


__all__ = ["NvidiaProvider"]
