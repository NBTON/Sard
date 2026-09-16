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

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from sard.agent.events import (
    EVENT_FAILED,
    EVENT_WAITING,
    NON_RETRYABLE_FAILURE_KINDS,
    FailureKind,
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

    Workstream E (additive): ``deadline`` (a
    :class:`sard.agent.deadline.Deadline` or float ``monotonic_end``) and
    ``cancel_event`` (threading.Event, plumbed from the server cancel flag)
    are polled before each node and before render/store so cancelled runs
    write no late artifacts.
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
    deadline: Optional[Any] = None
    cancel_event: Optional[Any] = None
    # Web-search leg for the ``retrieve`` node (Parallel -> Tavily -> Exa
    # failover, budgeted by the run deadline). Opt-in: live entry points
    # (API server, CLI, application service) set it True; unit tests leave
    # it False for deterministic, network-free runs.
    enable_web_search: bool = False


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


logger = logging.getLogger(__name__)


def _cancelled_node_failure(run: str, name: str, start: float, state: dict, deps: GraphDependencies) -> dict:
    """Typed node failure for CANCELLATION — distinguishable from timeout.

    Cancellation is never a degraded/ordinary failure: the error carries
    kind ``CANCELLED`` and ``retryable=False`` (timeouts stay retryable), so
    server layers can tell "client went away" apart from "provider was slow"
    via :func:`classify_failure_to_kind` without message-substring matching.

    It also exhausts the verify->compose retry loop (``verification_exhausted``
    + ``compose_retry_count`` past the cap) and seeds an honest partial
    answer: a cancelled run must terminate at render instead of cycling
    compose<->verify until the recursion limit.
    """
    duration_ms = (time.monotonic() - start) * 1000
    try:
        max_retries = int(getattr(deps, "compose_max_retries", 2) or 0)
    except Exception as exc_cap:
        logger.debug("Cancel retry-cap read skipped (%s).", type(exc_cap).__name__)
        max_retries = 2
    try:
        from sard.agent.routing import assemble_partial_answer as _assemble

        partial_text = _assemble(state)
    except Exception as exc_partial:
        logger.debug("Cancel partial-answer build skipped (%s).", type(exc_partial).__name__)
        partial_text = "أُلغي الطلب قبل اكتمال الصياغة؛ لا توجد حقائق مُتحقق منها لعرضها."
    return {
        "errors": [
            make_error(
                run,
                name,
                FailureKind.CANCELLED,
                f"أُلغي الطلب (cancelled) قبل/أثناء {name}؛ لا تُعَد المحاولة ولا تُستكمل كفشل عادي.",
                False,
            )
        ],
        "node_failures": [name],
        "verification_exhausted": True,
        "compose_retry_count": max_retries + 1,
        "final_answer": partial_text,
        "graph_outcome": "partial",
        "progress_events": [
            make_event(
                EVENT_FAILED,
                run,
                name,
                "failed",
                summary=f"أُلغي الطلب (cancelled) في {name}؛ توقفت المراحل الجديدة.",
                duration_ms=duration_ms,
                degraded=True,
            )
        ],
        "warnings": [f"أُلغي الطلب أثناء {name} (cancelled)؛ حُفظ المتاح فقط دون متابعة كفشل عادي."],
        "timings": {f"{name}_node_ms": duration_ms},
    }


def _guard_node(name: str, fn: Callable, deps: GraphDependencies) -> Callable:
    def run(state: dict) -> dict:
        run = state.get("run_id") or ""
        start = time.monotonic()
        # Workstream E: poll hierarchical deadline + server cancel flag
        # before each node; a cancelled/expired run stops new stages (no
        # late renders) instead of running. Cancellation produces the typed
        # cancelled failure (never degraded, never retryable); expiry the
        # typed timeout failure.
        try:
            _cancel = getattr(deps, "cancel_event", None)
            if _cancel is not None:
                try:
                    if _cancel.is_set():
                        from sard.agent.deadline import DeadlineCancelledError as _DC

                        raise _DC(f"cancelled before node '{name}'", stage=name)
                except _DC:
                    raise
                except Exception as exc_flag:
                    logger.debug("Node cancel flag read skipped (%s).", type(exc_flag).__name__)
            _dl = getattr(deps, "deadline", None)
            if _dl is not None:
                try:
                    from sard.agent.deadline import coerce_deadline as _coerce

                    _dl_obj = _coerce(_dl, label=name)
                    if _dl_obj is not None:
                        _dl_obj.check(name)
                except Exception:
                    # coerce/check raises typed Timeout/Cancelled — let the
                    # handler below convert to a typed node failure.
                    raise
        except Exception as exc:
            try:
                from sard.agent.deadline import DeadlineCancelledError as _DCG

                if isinstance(exc, _DCG):
                    return _cancelled_node_failure(run, name, start, state, deps)
            except Exception as exc_probe:
                logger.debug("Cancel-type probe skipped (%s).", type(exc_probe).__name__)
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
                        summary=f"توقف {name}: انتهت المهلة أو أُلغي الطلب.",
                        duration_ms=duration_ms,
                        degraded=True,
                    )
                ],
                "warnings": [f"توقف {name} بسبب انتهاء المهلة أو الإلغاء؛ حُفظ المتاح فقط."],
                "timings": {f"{name}_node_ms": duration_ms},
            }
        try:
            updates = fn(state, deps)
            duration_ms = (time.monotonic() - start) * 1000
            timings = dict(updates.get("timings") or {})
            timings[f"{name}_node_ms"] = duration_ms
            updates["timings"] = timings
            return updates
        except Exception as exc:
            try:
                from sard.agent.deadline import DeadlineCancelledError as _DCG2

                if isinstance(exc, _DCG2):
                    return _cancelled_node_failure(run, name, start, state, deps)
            except Exception as exc_probe2:
                logger.debug("Cancel-type probe skipped (%s).", type(exc_probe2).__name__)
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
    deadline: Optional[Any] = None,
    deadline_monotonic: Optional[Any] = None,
    cancel_event: Optional[Any] = None,
) -> dict:
    """Convenient runner: compile, seed state, invoke, return final state dict.

    Workstream E (additive): ``deadline`` / ``deadline_monotonic`` /
    ``cancel_event`` are stored on a shallow copy of ``dependencies`` so
    node guards poll them without changing node signatures.
    """
    if not request or not request.strip():
        raise ValueError("request must be a non-empty string.")
    deps = dependencies or default_dependencies()
    if deadline is not None or deadline_monotonic is not None or cancel_event is not None:
        import dataclasses as _dc

        # Coerce once so every node (understand/plan/compose/verify) and every
        # model call (invoke/invoke_json) observes the same hierarchical
        # Deadline + cancel_event with remaining-time budgets enforced.
        _coerced: Any = deadline if deadline is not None else deadline_monotonic
        _effective_cancel = cancel_event if cancel_event is not None else getattr(deps, "cancel_event", None)
        try:
            from sard.agent.deadline import coerce_deadline as _coerce_dl

            _coerced = _coerce_dl(_coerced, cancel_event=_effective_cancel, label="pipeline")
            if _coerced is not None and _effective_cancel is None:
                try:
                    _effective_cancel = getattr(_coerced, "cancel_event", None)
                except Exception as exc_cancel:
                    logger.debug("Pipeline cancel read skipped (%s).", type(exc_cancel).__name__)
        except Exception as exc_coerce:
            logger.debug("Pipeline deadline coerce skipped (%s).", type(exc_coerce).__name__)
            _coerced = deadline if deadline is not None else deadline_monotonic
        try:
            deps = _dc.replace(
                deps,
                deadline=_coerced,
                cancel_event=_effective_cancel,
            )
        except Exception as exc_replace:
            logger.debug("Pipeline deps replace skipped (%s).", type(exc_replace).__name__)
            try:
                deps.deadline = _coerced
            except Exception as exc_dl:
                logger.debug("Pipeline deadline assign skipped (%s).", type(exc_dl).__name__)
            try:
                if _effective_cancel is not None:
                    deps.cancel_event = _effective_cancel
            except Exception as exc_cancel2:
                logger.debug("Pipeline cancel assign skipped (%s).", type(exc_cancel2).__name__)
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
