"""J1–J3: model provider failure matrix (boundary B, mocked, deterministic).

Covers spec items:
  1. primary 429 -> fallback succeeds + total deadline respected
  2. primary timeout -> next candidate
  3. primary invalid JSON -> structured retry (bounded) then explicit
     MALFORMED_OUTPUT; transport failure + valid fallback JSON -> switch.
No live network, no secrets.
"""
from __future__ import annotations

import time

from sard.agent.models import AgentModelService
from sard.rag.fallbacks import FailureCategory

from .conftest import make_factory, make_test_settings


def _service(plan, breaker, sleep_fn, **overrides):
    calls: list[str] = []
    settings = make_test_settings(
        primary=overrides.pop("primary", "test-primary"),
        fallbacks=overrides.pop("fallbacks", ("test-fallback",)),
    )
    svc = AgentModelService(
        settings=settings,
        chat_model_factory=make_factory(plan, calls),
        circuit_breaker=breaker,
        sleep_fn=sleep_fn,
        **overrides,
    )
    return svc, calls


def test_01_primary_429_fallback_succeeds_within_deadline(fresh_breaker, noop_sleep):
    """Spec J1: primary 429 -> fallback succeeds, deadline respected."""
    plan = {"test-primary": "raise-429", "test-fallback": "ok:إجابة موثقة من المسار الاحتياطي"}
    svc, calls = _service(plan, fresh_breaker, noop_sleep, max_retries_per_candidate=1)
    deadline = 5.0
    start = time.monotonic()
    resp = svc.invoke("factual", "نظام", "ما هي العمارة النجدية؟")
    elapsed = time.monotonic() - start
    assert resp.success is True
    assert resp.text == "إجابة موثقة من المسار الاحتياطي"
    assert resp.model_used == "test-fallback"
    assert resp.degraded is True
    assert calls[0] == "test-primary" and "test-fallback" in calls
    assert elapsed < deadline, f"fallback exceeded deadline: {elapsed:.2f}s"
    assert sum(noop_sleep.calls) < deadline
    kinds = [e.failure_category for e in resp.events if e.outcome == "failure"]
    assert FailureCategory.RATE_LIMIT in kinds


def test_02_primary_timeout_moves_to_next_candidate(fresh_breaker, noop_sleep):
    """Spec J2: primary timeout -> next candidate, no hang, no crash."""
    plan = {"test-primary": "raise-timeout", "test-fallback": "ok:رد احتياطي بعد المهلة"}
    svc, calls = _service(plan, fresh_breaker, noop_sleep, max_retries_per_candidate=1)
    start = time.monotonic()
    resp = svc.invoke("factual", "نظام", "حدثني عن القط العسيري")
    elapsed = time.monotonic() - start
    assert resp.success is True
    assert resp.model_used == "test-fallback"
    assert calls.count("test-primary") == 1
    assert elapsed < 5.0
    kinds = [e.failure_category for e in resp.events if e.outcome == "failure"]
    assert FailureCategory.TIMEOUT in kinds


def test_03_invalid_json_structured_retry_never_fabricates(fresh_breaker, noop_sleep):
    """Spec J3a: invalid JSON -> bounded structured retry, explicit failure, no fabrication."""
    plan = {"test-primary": "ok:not json at all {{{", "test-fallback": "ok:also not json }}}{{{"}
    svc, calls = _service(plan, fresh_breaker, noop_sleep, max_structured_attempts=2)
    parsed, resp = svc.invoke_json("structured", "نظام", "أعد JSON", allowed_keys=("city",))
    assert parsed is None, "must not fabricate a dict from invalid JSON"
    assert resp.success is False
    assert resp.failure_category == FailureCategory.MALFORMED_OUTPUT
    # Bounded retry: exactly max_structured_attempts transport successes, then give up.
    assert calls.count("test-primary") == 2
    assert "city" not in (parsed or {})


def test_03b_transport_failure_then_fallback_valid_json_switches(fresh_breaker, noop_sleep):
    """Spec J3b: primary transport failure -> switch to fallback valid JSON."""
    plan = {
        "test-primary": "raise-429",
        "test-fallback": 'ok:{"city": "الرياض", "region": "نجد", "extra": "dropped"}',
    }
    svc, calls = _service(plan, fresh_breaker, noop_sleep)
    parsed, resp = svc.invoke_json("structured", "نظام", "أعد JSON", allowed_keys=("city", "region"))
    assert parsed == {"city": "الرياض", "region": "نجد"}
    assert resp.success is True
    assert resp.model_used == "test-fallback"
    assert "extra" not in parsed


def test_all_candidates_fail_is_explicit_failure_not_crash(fresh_breaker, noop_sleep):
    plan = {"test-primary": "raise-500", "test-fallback": "raise-timeout"}
    svc, _ = _service(plan, fresh_breaker, noop_sleep, max_retries_per_candidate=1)
    resp = svc.invoke("factual", "نظام", "سؤال")
    assert resp.success is False
    assert resp.text == ""
    assert resp.failure_category is not None
    assert "تعذّر" in resp.error_message
    parsed, resp2 = svc.invoke_json("structured", "نظام", "سؤال", allowed_keys=("a",))
    assert parsed is None
    assert resp2.success is False


def test_auth_failure_is_non_retryable_skips_wasted_retry(fresh_breaker, noop_sleep):
    """Documents NON_RETRYABLE contract: auth moves to next candidate immediately."""
    plan = {"test-primary": "raise-auth", "test-fallback": "ok:احتياطي بعد خطأ صلاحيات"}
    svc, calls = _service(plan, fresh_breaker, noop_sleep, max_retries_per_candidate=3)
    resp = svc.invoke("factual", "نظام", "سؤال")
    assert resp.success is True
    assert resp.model_used == "test-fallback"
    # Auth must not be retried on the same candidate: exactly one primary attempt.
    assert calls.count("test-primary") == 1
    kinds = [e.failure_category for e in resp.events if e.outcome == "failure"]
    assert FailureCategory.AUTHENTICATION in kinds
