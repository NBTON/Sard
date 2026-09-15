"""Provider abstraction for capability-aware model routing (workstream B).

Every provider implements :class:`ModelProvider`:

- ``build_chat(model_id, timeout_s)`` — return a LangChain-compatible chat
  model (only needs ``.invoke(messages)``).
- ``build_embeddings(model_id, timeout_s)`` — return a LangChain-compatible
  embeddings object (only needs ``.embed_documents/.embed_query``).
- ``supports(task_class)`` — capability gate used by the routing table to
  filter candidates (vision / structured-output requirements).
- ``normalize_error(exc)`` — map any build/invoke failure to the shared
  :class:`sard.rag.fallbacks.FailureCategory` taxonomy. No new failure
  schema is introduced here.

No secrets are accepted, stored, or logged by these classes: credentials
are read from the environment at build time only, and telemetry receipts
carry provider/model/latency metadata — never prompts, payloads, or keys.
"""

from __future__ import annotations

import abc
from typing import Any

from sard.rag.fallbacks import FailureCategory, classify_exception

try:  # TaskClass lives in routing_table; imported lazily to avoid cycles.
    from sard.config.routing_table import TaskClass
except Exception:  # pragma: no cover - routing_table is always present in practice
    TaskClass = Any  # type: ignore[assignment,misc]


class ModelProvider(abc.ABC):
    """Abstract model provider. Build methods never perform network I/O."""

    name: str = "base"

    @abc.abstractmethod
    def build_chat(self, model_id: str, timeout_s: float) -> Any:
        """Return a chat model for ``model_id`` honoring ``timeout_s``."""

    @abc.abstractmethod
    def build_embeddings(self, model_id: str, timeout_s: float) -> Any:
        """Return an embeddings object for ``model_id`` honoring ``timeout_s``."""

    @abc.abstractmethod
    def supports(self, task_class: Any) -> bool:
        """Return True when this provider can serve ``task_class``."""

    def normalize_error(self, exc: BaseException) -> FailureCategory:
        """Map any failure to the shared fallback taxonomy (no new schema)."""
        return classify_exception(exc)


__all__ = ["ModelProvider", "TaskClass"]
