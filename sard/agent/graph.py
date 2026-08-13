"""LangGraph pipeline builder and runner for the Sard agent core.

Wires ``understand -> plan -> retrieve -> compose -> verify -> render`` in
exactly that logical order.  The verify node routes back to ``compose`` with
structured feedback while ``compose_retry_count`` remains under the cap; on
exhaustion it emits an honest partial answer and proceeds to render
validation.  Node exceptions are converted to typed state failures so the
graph always finishes as ``completed`` / ``partial`` / ``failed`` state
rather than crashing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from sard.agent.events import (
    EVENT_FAILED,
    EVENT_WAITING,
    NON_RETRYABLE_FAILURE_KINDS,
    make_error,
    make_event,
    safe_chain_message,
)
from sard.agent.models import AgentModelService
from sard.agent.nodes.understand import understand
from sard.agent.nodes.plan import plan
from sard.agent.nodes.retrieve import retrieve
from sard.agent.nodes.compose import compose
from sard.agent.nodes.verify import verify
from sard.agent.nodes.render import render
from sard.agent.routing import classify_failure_to_kind, route_after_verification
from sard.agent.state import GraphState, initial_state

NODE_NAMES = ("understand", "plan", "retrieve", "compose", "verify", "render")


@dataclass
class GraphDependencies:
    """Injected, provider-neutral dependencies for the agent graph.

    ``rag_service`` is the public Step 3 ``RAGService`` interface; nodes never
    touch Zvec or provider SDKs directly.  ``model_service`` is the centralized
    agent model service (inject offline fakes for tests).
    """

    rag_service: Optional[Any] = None
    model_service: Optional[AgentModelService] = None
    settings: Optional[Any] = None
    render_smoke: Optional[Callable[..., Any]] = None
    render_artifacts: bool = False
    output_root: Optional[str] = None
    render_checksums: bool = False
    caller_dates: tuple[str, ...] = ()
    preview_calendar: bool = False
    compose_max_retries: int = 2


def default_dependencies(open_rag: bool = False) -> GraphDependencies:
    """Build runnable dependencies; ``open_rag`` best-effort opens Step 3."""
    deps = GraphDependencies(model_service=AgentModelService())
    if open_rag:
        try:
            from sard.rag.service import RAGService

            deps.rag_service = RAGService.open_readonly(deps.settings)
        except Exception:
            deps.rag_service = None
    return deps


def _guard_node(name: str, fn: Callable, deps: GraphDependencies) -> Callable:
    def run(state: dict) -> dict:
        run = state.get("run_id") or ""
        start = time.monotonic()
        try:
            updates = fn(state, deps)
            duration_ms = (time.monotonic() - start) * 1000
            timings = dict(updates.get("timings") or {})
            timings[f"{name}_node_ms"] = duration_ms
            updates["timings"] = timings
            return updates
        except Exception as exc:
            kind = classify_failure_to_kind(exc)
            duration_ms = (time.monotonic() - start) * 1000
            return {
                "errors": [
                    make_error(
                        run,
                        name,
                        kind,
                        safe_chain_message(exc),
                        kind not in NON_RETRYABLE_FAILURE_KINDS,
                    )
                ],
                "node_failures": [name],
                "progress_events": [
                    make_event(
                        EVENT_FAILED,
                        run,
                        name,
                        "failed",
                        summary=f"فشل في الخطوة {name}",
                        duration_ms=duration_ms,
                        degraded=True,
                    )
                ],
                "warnings": [f"حدث خطأ أثناء {name}؛ سُجّل التشغيل بحالة فشل."],
                "timings": {f"{name}_node_ms": duration_ms},
            }

    return run


def build_graph(dependencies: Optional[GraphDependencies] = None) -> CompiledStateGraph:
    """Compile the real LangGraph pipeline with chosen dependencies."""
    deps = dependencies or GraphDependencies()

    builder = StateGraph(GraphState)
    builder.add_node("understand", _guard_node("understand", understand, deps))
    builder.add_node("plan", _guard_node("plan", plan, deps))
    builder.add_node("retrieve", _guard_node("retrieve", retrieve, deps))
    builder.add_node("compose", _guard_node("compose", compose, deps))
    builder.add_node("verify", _guard_node("verify", verify, deps))
    builder.add_node("render", _guard_node("render", render, deps))

    builder.add_edge(START, "understand")
    builder.add_edge("understand", "plan")
    builder.add_edge("plan", "retrieve")
    builder.add_edge("retrieve", "compose")
    builder.add_edge("compose", "verify")
    builder.add_conditional_edges(
        "verify",
        route_after_verification,
        {"compose": "compose", "render": "render"},
    )
    builder.add_edge("render", END)

    return builder.compile()


def run_pipeline(
    request: str,
    dependencies: Optional[GraphDependencies] = None,
    run_id: Optional[str] = None,
    *,
    caller_dates: Optional[list[str] | tuple[str, ...]] = None,
    preview_calendar: Optional[bool] = None,
) -> dict:
    """Convenient runner: compile, seed state, invoke, return final state dict."""
    if not request or not request.strip():
        raise ValueError("request must be a non-empty string.")
    deps = dependencies or default_dependencies()
    graph = build_graph(deps)
    state = initial_state(
        request,
        run_id=run_id,
        compose_max_retries=deps.compose_max_retries,
    )
    state["caller_dates"] = list(caller_dates if caller_dates is not None else deps.caller_dates)
    state["preview_calendar"] = deps.preview_calendar if preview_calendar is None else preview_calendar
    state["output_root"] = deps.output_root
    state["render_checksums"] = deps.render_checksums
    state["progress_events"] = [
        make_event(EVENT_WAITING, state["run_id"], "pipeline", "waiting", summary="في الانتظار")
    ]
    return graph.invoke(state)
