"""J8: client cancellation + deadline (boundary E, mocked, deterministic).

Contract: after cancel, no new candidates/renders start and the store
refuses late writes. Production enforces the store half via
ArtifactOrchestrator.generate_artifact(deadline_monotonic) (G11); the
cancellable-runner harness below models the no-new-work half.
"""
from __future__ import annotations

import threading
import time

from sard.outputs.orchestrator import ArtifactOrchestrator, FileSystemArtifactStore

from .conftest import CountingStore, artifact_request


class CancellableRunner:
    """Spec helper: checks a cancel flag before each unit of work."""

    def __init__(self):
        self.cancelled = threading.Event()
        self.started: list[str] = []

    def run_candidate(self, name: str, fn):
        if self.cancelled.is_set():
            return ("cancelled", None)
        self.started.append(name)
        return ("ok", fn())

    def cancel(self):
        self.cancelled.set()


def test_08_expired_deadline_refuses_store_and_reports_timeout(tmp_path):
    inner = FileSystemArtifactStore(tmp_path)
    counting = CountingStore(inner)
    orch = ArtifactOrchestrator(counting)
    before = {p.name for p in tmp_path.iterdir() if p.is_file()}
    past = time.monotonic() - 1.0
    res = orch.generate_artifact(artifact_request("pdf"), deadline_monotonic=past)
    assert res.status == "failed"
    assert res.error_category == "timeout"
    assert res.download_url is None
    assert counting.store_calls == [], "late output must never reach the store"
    after = {p.name for p in tmp_path.iterdir() if p.is_file()}
    assert after - before == set()


def test_08b_cancel_between_two_renders_second_refused(tmp_path):
    inner = FileSystemArtifactStore(tmp_path)
    counting = CountingStore(inner)
    orch = ArtifactOrchestrator(counting)
    runner = CancellableRunner()
    first = runner.run_candidate("pdf-1", lambda: orch.generate_artifact(artifact_request("txt", raw_text="first")))
    assert first[0] == "ok" and first[1].status == "created"
    assert len(counting.store_calls) == 1
    runner.cancel()
    # After cancel the runner refuses to start new renders; even a direct
    # late call with an expired deadline must be refused by the store boundary.
    status, _ = runner.run_candidate("pdf-2", lambda: orch.generate_artifact(artifact_request("txt")))
    assert status == "cancelled"
    late = orch.generate_artifact(artifact_request("txt", raw_text="late"), deadline_monotonic=time.monotonic() - 5)
    assert late.status == "failed" and late.error_category == "timeout"
    assert len(counting.store_calls) == 1, "no new store writes after cancel"


def test_08c_no_new_model_candidates_after_cancel():
    from sard.rag.fallbacks import CircuitBreaker, ModelCandidate, run_with_fallback

    runner = CancellableRunner()
    breaker = CircuitBreaker()
    factory_calls: list[str] = []
    candidates = [
        ModelCandidate("primary", "hosted", "primary"),
        ModelCandidate("fallback", "hosted", "fallback_1", degraded=True),
    ]

    def _call(candidate):
        factory_calls.append(candidate.model_id)
        if candidate.model_id == "primary":
            runner.cancel()  # client disconnects while primary fails
            raise TimeoutError("primary timed out")
        return "should-not-run-after-cancel"

    def _guarded_call(candidate):
        if runner.cancelled.is_set() and candidate.model_id != "primary":
            raise TimeoutError("cancelled before next candidate; refusing new work")
        return _call(candidate)

    try:
        run_with_fallback("agent_factual", candidates, _guarded_call, max_retries_per_candidate=1, circuit_breaker=breaker, sleep_fn=lambda s: None)
        outcome = "unexpected-success"
    except Exception as exc:
        outcome = type(exc).__name__
    assert factory_calls == ["primary"], f"no new candidates after cancel, got {factory_calls}"
    assert outcome in ("AllCandidatesFailedError", "TimeoutError", "Exception")


# --- Coordination with workstream E: real Deadline + cancel_event ----------


def test_08d_cancel_event_refuses_render_and_store(tmp_path):
    """Real cancel_event path: failed/cancelled, zero store writes."""
    inner = FileSystemArtifactStore(tmp_path)
    counting = CountingStore(inner)
    orch = ArtifactOrchestrator(counting)
    ev = threading.Event()
    ev.set()  # client disconnected before generation started
    res = orch.generate_artifact(artifact_request("txt", raw_text="late"), cancel_event=ev)
    assert res.status == "failed"
    assert res.error_category == "cancelled"
    assert res.download_url is None
    assert counting.store_calls == []


def test_08e_expired_deadline_object_refuses_store(tmp_path):
    """Real Deadline path (workstream E): expired -> failed/timeout, no orphan."""
    from sard.agent.deadline import deadline_from_timeout

    inner = FileSystemArtifactStore(tmp_path)
    counting = CountingStore(inner)
    orch = ArtifactOrchestrator(counting)
    dl = deadline_from_timeout(30.0)
    dl.monotonic_end = time.monotonic() - 1.0  # force expiry without sleeping
    res = orch.generate_artifact(artifact_request("txt", raw_text="late"), deadline=dl)
    assert res.status == "failed"
    assert res.error_category == "timeout"
    assert counting.store_calls == []


def test_08f_deadline_check_blocks_new_candidate_after_cancel():
    """Deadline.check is the gate every stage must call before new work."""
    import threading as _threading

    from sard.agent.deadline import DeadlineCancelledError, DeadlineTimeoutError, deadline_from_timeout

    ev = _threading.Event()
    dl = deadline_from_timeout(30.0, label="candidate")
    dl.check("candidate-1")  # not cancelled: passes
    ev_exc = None
    dl.cancel_event = ev
    ev.set()
    try:
        dl.check("candidate-2")
    except DeadlineCancelledError as exc:
        ev_exc = exc
    assert ev_exc is not None
    assert ev_exc.error_category == "cancelled"

    expired = deadline_from_timeout(30.0, label="render")
    expired.monotonic_end = time.monotonic() - 0.5
    try:
        expired.check("render-2")
        uncancelled = True
    except DeadlineTimeoutError:
        uncancelled = False
    assert uncancelled is False
