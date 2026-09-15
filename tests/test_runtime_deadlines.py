"""Workstream E: hierarchical deadlines + cancellation conformance.

Covers the NEW requirements (additive; existing G10/G11/itinerary tests stay):

- outer deadline caps a 3-candidates x 2-retries x 30s chain
- cancel flag stops new candidates (typed DeadlineCancelledError)
- reserve preserved (no new work once remaining-reserve<=0)
- float compat shim still accepted everywhere
- SSE run_id in FIRST status event + GET /api/runs/:id poll-after-abort
- CORS: explicit origins, never wildcard+credentials
"""

from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from sard.agent.deadline import (
    Deadline,
    DeadlineCancelledError,
    DeadlineTimeoutError,
    coerce_deadline,
    deadline_from_timeout,
)
from sard.api.server import app
from sard.rag.fallbacks import (
    AllCandidatesFailedError,
    CircuitBreaker,
    FailureCategory,
    ModelCandidate,
    run_with_fallback,
)

client = TestClient(app)


def _cands(*ids: str) -> list[ModelCandidate]:
    return [
        ModelCandidate(model_id=mid, endpoint_type="hosted", label=("primary" if i == 0 else f"fallback_{i}"))
        for i, mid in enumerate(ids)
    ]


# --- outer deadline caps 3x2x30 chain ---------------------------------------

def test_outer_deadline_caps_candidate_chain():
    cands = _cands("m1", "m2", "m3")
    calls: list[str] = []

    def hanging_call(candidate):
        calls.append(candidate.model_id)
        time.sleep(30.0)  # simulated hung provider; daemon-abandoned
        return "never"

    dl = deadline_from_timeout(1.0, label="test-chain")
    t0 = time.monotonic()
    with pytest.raises((AllCandidatesFailedError, DeadlineTimeoutError, TimeoutError)):
        run_with_fallback(
            "chain_cap_test",
            cands,
            hanging_call,
            max_retries_per_candidate=2,
            deadline=dl,
            sleep_fn=lambda s: None,
            circuit_breaker=CircuitBreaker(),
        )
    elapsed = time.monotonic() - t0
    # 3x2x30 = 180s unbounded; outer deadline must cap near ~1s + slack.
    assert elapsed < 8.0, f"outer deadline did not cap chain ({elapsed:.1f}s)"
    # Per-attempt budgets shrink: a hung chain cannot complete all 6 attempts.
    assert len(calls) <= 3, f"too many doomed attempts started: {calls}"


# --- cancel flag stops new candidates ---------------------------------------

def test_cancel_flag_stops_new_candidates():
    cands = _cands("m1", "m2", "m3")
    cancel = threading.Event()
    calls: list[str] = []

    def flaky_call(candidate):
        calls.append(candidate.model_id)
        if len(calls) == 1:
            cancel.set()  # abort requested mid-chain
            raise Exception("503 temporarily unavailable")
        return "should-never-run"

    with pytest.raises(DeadlineCancelledError):
        run_with_fallback(
            "cancel_test",
            cands,
            flaky_call,
            max_retries_per_candidate=2,
            cancel_event=cancel,
            sleep_fn=lambda s: None,
            circuit_breaker=CircuitBreaker(),
        )
    assert calls == ["m1"], f"cancel did not stop new candidates: {calls}"


# --- reserve preserved -------------------------------------------------------

def test_reserve_preserved_no_new_work():
    # 10s left but 10s reserve -> no budget for new work.
    dl = Deadline(monotonic_end=time.monotonic() + 10.0, reserve_s=10.0, label="reserve")
    with pytest.raises(DeadlineTimeoutError):
        dl.check("stage-x")

    # run_with_fallback must not invoke any candidate once reserve covers all.
    cands = _cands("m1", "m2")
    calls: list[str] = []
    tight = Deadline(monotonic_end=time.monotonic() + 0.3, reserve_s=30.0, label="tight")
    with pytest.raises(AllCandidatesFailedError) as excinfo:
        run_with_fallback(
            "reserve_test",
            cands,
            lambda c: calls.append(c.model_id) or "x",
            max_retries_per_candidate=2,
            deadline=tight,
            sleep_fn=lambda s: None,
            circuit_breaker=CircuitBreaker(),
        )
    assert calls == [], f"doomed candidate started despite reserve: {calls}"
    assert excinfo.value.events, "expected timeout events recorded"
    assert all(e.failure_category == FailureCategory.TIMEOUT for e in excinfo.value.events)


def test_per_attempt_budget_shrinks():
    dl = deadline_from_timeout(6.0, label="budget")
    assert dl.time_left_for_attempt(6, cap_s=30.0) == pytest.approx(1.0, abs=0.6)
    assert dl.time_left_for_attempt(1, cap_s=30.0) <= 6.5


# --- float compat shim -------------------------------------------------------

def test_float_compat_shim():
    end = time.monotonic() + 5.0
    dl = coerce_deadline(end, label="shim")
    assert isinstance(dl, Deadline)
    assert dl.remaining() > 0
    assert coerce_deadline(None) is None
    # Legacy float kwarg on run_with_fallback still enforced.
    cands = _cands("m1")
    with pytest.raises(AllCandidatesFailedError):
        run_with_fallback(
            "shim_test",
            cands,
            lambda c: (_ for _ in ()).throw(Exception("503 down")),
            max_retries_per_candidate=1,
            deadline_monotonic=time.monotonic() - 1.0,  # already past
            sleep_fn=lambda s: None,
            circuit_breaker=CircuitBreaker(),
        )


# --- run_id first-event + poll endpoint --------------------------------------

def _sse_events(text: str) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    cur_name: str | None = None
    cur_data: list[str] = []
    for line in text.splitlines():
        if line.startswith("event:"):
            if cur_name is not None:
                events.append((cur_name, "\n".join(cur_data)))
            cur_name = line.split("event:", 1)[1].strip()
            cur_data = []
        elif line.startswith("data:"):
            cur_data.append(line.split("data:", 1)[1].strip())
        elif line.strip() == "" and cur_name is not None:
            events.append((cur_name, "\n".join(cur_data)))
            cur_name = None
            cur_data = []
    if cur_name is not None:
        events.append((cur_name, "\n".join(cur_data)))
    return events


def test_sse_first_status_carries_run_id_and_poll_endpoint():
    resp = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hello"}]})
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")
    events = _sse_events(resp.text)
    kinds = [k for k, _ in events]
    assert kinds[0] == "status", f"first event must be status, got {kinds[:3]}"
    first = json.loads(events[0][1])
    assert first.get("run_id", "").startswith("chat-"), f"FIRST status lacks run_id: {first}"
    assert "done" in kinds, "SSE must terminate with done"
    done = json.loads(dict((k, v) for k, v in reversed(events))["done"])
    assert done.get("run_id") == first["run_id"], "done run_id must match first status run_id"

    # Poll-after-abort endpoint exposes the minimal run record.
    poll = client.get(f"/api/runs/{first['run_id']}")
    assert poll.status_code == 200, poll.text[:200]
    record = poll.json()
    assert record["run_id"] == first["run_id"]
    assert record["done"] is True
    assert record["status"] in ("succeeded", "failed", "cancelled", "timeout")
    assert isinstance(record.get("artifacts"), list)


def test_runs_poll_unknown_id_404():
    resp = client.get("/api/runs/does-not-exist-123")
    assert resp.status_code == 404


# --- CORS --------------------------------------------------------------------

def test_cors_explicit_origins_no_wildcard_credentials():
    from fastapi.middleware.cors import CORSMiddleware

    found = False
    for mw in app.user_middleware:
        if mw.cls is CORSMiddleware:
            found = True
            origins = (mw.kwargs.get("allow_origins") or [])
            assert "*" not in origins, "wildcard origin must never pair with credentials"
            assert "http://localhost:3000" in origins
    assert found, "CORSMiddleware missing"

    # Allowed localhost origin is reflected...
    ok = client.get("/api/health", headers={"Origin": "http://localhost:3000"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    # ...unlisted origins are not.
    evil = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert evil.headers.get("access-control-allow-origin") != "https://evil.example"
