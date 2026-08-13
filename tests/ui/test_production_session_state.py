from __future__ import annotations

from datetime import date

from sard.application.contracts import (
    UIExecutionMode,
    UIModeKind,
    UIModeStatus,
    UIProgressEvent,
    UIProgressState,
    UIRunRequest,
    UIRunResult,
    UIStage,
)
from sard.ui import presentation
from sard.ui import session_state as session


class Service:
    pass


def _result(run_id: str, outcome: str = "completed") -> UIRunResult:
    return UIRunResult(
        run_id=run_id,
        final_answer="نتيجة",
        graph_outcome=outcome,
        mode=UIModeStatus(
            kind=UIModeKind.LIVE,
            retrieval_mode="hybrid_reranked",
            model_fallback_used=False,
            execution_mode=UIExecutionMode.LIVE,
        ),
        sources=(),
        itinerary=None,
        artifacts=(),
        progress_events=(),
        warnings=("<script>تنبيه</script>",),
    )


def test_production_controller_intent_execution_retry_persistence_and_reset():
    state = {}
    service = Service()
    session.initialize_session(state, lambda: service)
    session.stage_intent(state, UIExecutionMode.LIVE, "طلب")
    intent = session.consume_intent(state)
    assert intent and intent.query == "طلب"
    assert session.consume_intent(state) is None

    first = UIRunRequest("طلب", "run-one")
    token = session.begin_run(state, first)
    assert session.claim_execution(state) is True
    assert session.claim_execution(state) is False
    event = UIProgressEvent(
        1, "run-one", UIStage.UNDERSTAND, UIProgressState.ACTIVE, "started", "now"
    )
    session.append_progress(state, event)
    session.finish_run(state, _result("run-one"))
    assert state[session.KEYS["progress"]] == [event]
    assert state[session.KEYS["result"]].run_id == "run-one"

    state[session.KEYS["cal_view"]] = object()
    retry = UIRunRequest("طلب", "run-two")
    assert session.begin_run(state, retry) == token + 1
    assert state[session.KEYS["result"]] is None
    assert state[session.KEYS["progress"]] == []
    assert state[session.KEYS["cal_view"]] is None
    assert session.claim_execution(state) is True


def test_production_sessions_isolate_services_and_demo_intent_is_explicit():
    first, second = {}, {}
    session.initialize_session(first, Service)
    session.initialize_session(second, Service)
    assert first[session.KEYS["service"]] is not second[session.KEYS["service"]]
    session.stage_intent(first, UIExecutionMode.CACHED_DEMO, "hero")
    intent = session.consume_intent(first)
    assert intent and intent.mode is UIExecutionMode.CACHED_DEMO
    assert second[session.KEYS["intent"]] is None


def test_terminal_status_and_warning_rendering_are_truthful_and_safe():
    assert session.terminal_status(_result("a")) == ("اكتمل التنفيذ", "complete")
    assert session.terminal_status(_result("b", "partial")) == (
        "اكتمل التنفيذ جزئيًا",
        "complete",
    )
    assert session.terminal_status(_result("c", "failed"))[1] == "error"
    assert session.terminal_status(None)[1] == "error"
    assert presentation.warning_messages(_result("a")) == (
        "&lt;script&gt;تنبيه&lt;/script&gt;",
    )


def test_production_date_range_keeps_one_date_and_rejects_reverse():
    one = date(2027, 5, 20)
    assert session.inclusive_dates(one, None) == (one,)
    assert session.inclusive_dates(one, date(2027, 5, 19)) == ()
    assert session.inclusive_dates(one, date(2027, 5, 21)) == (
        one,
        date(2027, 5, 21),
    )
