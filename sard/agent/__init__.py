"""Agent/orchestration layer.

Home to `ChatService` (existing UI boundary) plus the Step 5 LangGraph core:
a provider-independent pipeline
``understand -> plan -> retrieve -> compose -> verify -> render``.

The core lives in :mod:`sard.agent.graph`, typed state in
:mod:`sard.agent.state`, safe events in :mod:`sard.agent.events`, and the
centralized model boundary in :mod:`sard.agent.models`.
"""

from __future__ import annotations

from sard.agent.chat_service import ChatService, ChatResult
from sard.agent.graph import GraphDependencies, build_graph, default_dependencies, run_pipeline
from sard.agent.state import GraphState, initial_state

__all__ = [
    "ChatService",
    "ChatResult",
    "GraphDependencies",
    "GraphState",
    "build_graph",
    "default_dependencies",
    "initial_state",
    "run_pipeline",
]