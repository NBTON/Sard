"""Pure production controller for Streamlit session/run state."""

from __future__ import annotations

from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional

from sard.application.contracts import UIExecutionMode, UIProgressEvent, UIRunRequest, UIRunResult


KEYS = {
    "service": "step7_service",
    "request": "step7_request",
    "run_id": "step7_run_id",
    "progress": "step7_progress",
    "result": "step7_result",
    "error": "step7_error",
    "last_query": "step7_last_query",
    "demo_flag": "step7_demo_flag",
    "run_token": "step7_run_token",
    "intent": "step7_intent",
    "cal_view": "step7_cal_view",
    "executed_token": "step7_executed_token",
}


@dataclass(frozen=True)
class RunIntent:
    mode: UIExecutionMode
    query: str


def initialize_session(
    state: MutableMapping[str, Any], service_factory: Callable[[], Any]
) -> None:
    if KEYS["service"] not in state:
        state[KEYS["service"]] = service_factory()
    defaults = {
        KEYS["request"]: None,
        KEYS["run_id"]: None,
        KEYS["progress"]: [],
        KEYS["result"]: None,
        KEYS["error"]: None,
        KEYS["last_query"]: "",
        KEYS["demo_flag"]: False,
        KEYS["run_token"]: 0,
        KEYS["intent"]: None,
        KEYS["cal_view"]: None,
        KEYS["executed_token"]: None,
    }
    for key, value in defaults.items():
        state.setdefault(key, value)


def stage_intent(
    state: MutableMapping[str, Any], mode: UIExecutionMode, query: str
) -> None:
    state[KEYS["intent"]] = RunIntent(mode=mode, query=query)


def consume_intent(state: MutableMapping[str, Any]) -> Optional[RunIntent]:
    intent = state.get(KEYS["intent"])
    state[KEYS["intent"]] = None
    return intent if isinstance(intent, RunIntent) else None


def begin_run(state: MutableMapping[str, Any], request: UIRunRequest) -> int:
    token = int(state.get(KEYS["run_token"], 0)) + 1
    state[KEYS["request"]] = request
    state[KEYS["run_id"]] = request.run_id
    state[KEYS["progress"]] = []
    state[KEYS["result"]] = None
    state[KEYS["error"]] = None
    state[KEYS["cal_view"]] = None
    state[KEYS["demo_flag"]] = request.execution_mode is UIExecutionMode.CACHED_DEMO
    state[KEYS["last_query"]] = request.query
    state[KEYS["run_token"]] = token
    return token


def claim_execution(state: MutableMapping[str, Any]) -> bool:
    """Claim the current token once; rerenders cannot execute it again."""

    token = state.get(KEYS["run_token"])
    if token is None or state.get(KEYS["executed_token"]) == token:
        return False
    state[KEYS["executed_token"]] = token
    return True


def append_progress(state: MutableMapping[str, Any], event: UIProgressEvent) -> None:
    state[KEYS["progress"]].append(event)


def finish_run(state: MutableMapping[str, Any], result: UIRunResult) -> None:
    state[KEYS["result"]] = result
    state[KEYS["error"]] = result.error_message or None


def terminal_status(result: Optional[UIRunResult]) -> tuple[str, str]:
    if result is None:
        return "تعذّر استلام نتيجة نهائية", "error"
    if result.graph_outcome == "completed":
        return "اكتمل التنفيذ", "complete"
    if result.graph_outcome == "partial":
        return "اكتمل التنفيذ جزئيًا", "complete"
    return "تعذّر إكمال التنفيذ", "error"


def inclusive_dates(
    start: Optional[date], end: Optional[date]
) -> tuple[date, ...]:
    """Return an ordered inclusive range; reversed input is invalid/empty."""

    if start is None:
        return ()
    if end is None or end == start:
        return (start,)
    if end < start:
        return ()
    return tuple(
        start + timedelta(days=offset)
        for offset in range((end - start).days + 1)
    )
