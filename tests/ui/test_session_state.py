"""Step 7 UI session-state contract tests.

Streamlit reruns are the largest functional conflict risk (frozen contract,
"Parallel boundaries and conflict risks").  These tests exercise the stable
session keys and rerun rules without a live Streamlit runtime: the service
instance, current request/run ID, progress list, final result, date inputs,
retry token and demo flag live in session state; buttons set intent; only a
new explicit run token may call ``stream_run()``.

These are pure tests against the test-side ``SessionState`` model; the real
``sard/ui/app.py`` is integration-owner-only during Phase 1.
"""

from tests.helpers.session import (
    SESSION_DATE_INPUTS_KEY,
    SESSION_DEMO_ENABLED_KEY,
    SESSION_DEMO_FALLBACK_REQUESTED_KEY,
    SESSION_EXECUTED_TOKEN_KEY,
    SESSION_KEYS,
    SESSION_PENDING_TOKEN_KEY,
    SESSION_PROGRESS_KEY,
    SESSION_REQUEST_KEY,
    SESSION_RESULT_KEY,
    SESSION_RETRY_TOKEN_KEY,
    SESSION_SERVICE_KEY,
    SessionState,
)
from tests.helpers.step7_contracts import UIExecutionMode, UIProgressEvent, UIStage, UIProgressState


def test_session_keys_are_the_stable_frozen_set():
    assert SESSION_KEYS == (
        SESSION_SERVICE_KEY,
        SESSION_REQUEST_KEY,
        SESSION_EXECUTED_TOKEN_KEY,
        SESSION_PENDING_TOKEN_KEY,
        SESSION_PROGRESS_KEY,
        SESSION_RESULT_KEY,
        SESSION_DATE_INPUTS_KEY,
        SESSION_RETRY_TOKEN_KEY,
        SESSION_DEMO_ENABLED_KEY,
        SESSION_DEMO_FALLBACK_REQUESTED_KEY,
    )
    assert len(SESSION_KEYS) == len(set(SESSION_KEYS))


def test_service_is_created_once_and_lazily_per_session():
    session = SessionState()
    factories = []

    def make_service():
        factories.append(1)
        return object()

    first = session.service(make_service)
    again = session.service(make_service)
    assert first is again
    assert len(factories) == 1
    assert session.service() is first


def test_separate_sessions_hold_separate_service_instances():
    first = SessionState()
    second = SessionState()

    def make_service():
        return object()

    a = first.service(make_service)
    b = second.service(make_service)
    assert a is not b
    assert SESSION_SERVICE_KEY in first and SESSION_SERVICE_KEY in second


def test_button_sets_intent_and_only_new_token_starts_a_run():
    session = SessionState()
    assert session.should_start_run() is False

    session.set_run_intent("run-token-1")
    assert session.should_start_run() is True
    assert session[SESSION_PENDING_TOKEN_KEY] == "run-token-1"

    session.mark_run_executed("run-token-1")
    assert session[SESSION_EXECUTED_TOKEN_KEY] == "run-token-1"
    assert session.should_start_run() is False
    assert SESSION_PENDING_TOKEN_KEY not in session


def test_rerun_with_same_token_never_restarts_the_graph():
    session = SessionState()
    session.set_run_intent("run-abc")
    session.mark_run_executed("run-abc")
    session.set_run_intent("run-abc")
    assert session.should_start_run() is False


def test_retry_is_a_new_explicit_run_token():
    session = SessionState()
    session.set_run_intent("run-abc")
    session.mark_run_executed("run-abc")

    session.set_run_intent("run-abc-retry")
    assert session.should_start_run() is True
    assert session[SESSION_PENDING_TOKEN_KEY] == "run-abc-retry"


def test_progress_and_result_survive_reruns():
    session = SessionState()
    event = UIProgressEvent(0, "run-1", UIStage.UNDERSTAND, UIProgressState.WAITING, "waiting", "ts", simulated=True)
    session.append_progress(event)
    session.append_progress(event)

    result = object()
    session.set_result(result)

    assert len(session.get(SESSION_PROGRESS_KEY)) == 2
    assert session.get(SESSION_PROGRESS_KEY)[0].simulated is True
    assert session[SESSION_RESULT_KEY] is result


def test_date_inputs_and_retry_token_are_retained_keys():
    session = SessionState()
    session[SESSION_DATE_INPUTS_KEY] = ("2026-11-01",)
    session[SESSION_RETRY_TOKEN_KEY] = "run-abc-retry-2"
    assert session[SESSION_DATE_INPUTS_KEY] == ("2026-11-01",)
    assert session[SESSION_RETRY_TOKEN_KEY] == "run-abc-retry-2"


def test_cached_demo_is_explicit_and_never_falls_through_to_live():
    session = SessionState()
    resolved = session.resolve_execution_mode(UIExecutionMode.CACHED_DEMO)
    assert resolved is UIExecutionMode.CACHED_DEMO

    session[SESSION_DEMO_ENABLED_KEY] = False
    session[SESSION_DEMO_FALLBACK_REQUESTED_KEY] = False
    assert session.resolve_execution_mode(UIExecutionMode.CACHED_DEMO) is UIExecutionMode.CACHED_DEMO


def test_live_run_switches_to_demo_only_after_manual_fallback():
    session = SessionState()

    session[SESSION_DEMO_ENABLED_KEY] = False
    session[SESSION_DEMO_FALLBACK_REQUESTED_KEY] = False
    assert session.resolve_execution_mode(UIExecutionMode.LIVE) is UIExecutionMode.LIVE

    session[SESSION_DEMO_ENABLED_KEY] = True
    session[SESSION_DEMO_FALLBACK_REQUESTED_KEY] = False
    assert session.resolve_execution_mode(UIExecutionMode.LIVE) is UIExecutionMode.LIVE

    session[SESSION_DEMO_ENABLED_KEY] = False
    session[SESSION_DEMO_FALLBACK_REQUESTED_KEY] = True
    assert session.resolve_execution_mode(UIExecutionMode.LIVE) is UIExecutionMode.LIVE

    session[SESSION_DEMO_ENABLED_KEY] = True
    session[SESSION_DEMO_FALLBACK_REQUESTED_KEY] = True
    assert session.resolve_execution_mode(UIExecutionMode.LIVE) is UIExecutionMode.CACHED_DEMO
