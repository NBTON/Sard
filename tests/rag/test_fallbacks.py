"""Central fallback policy tests (no network required)."""

from sard.rag.fallbacks import (
    AllCandidatesFailedError,
    CircuitBreaker,
    FailureCategory,
    FallbackClassifiedError,
    ModelCandidate,
    classify_exception,
    run_with_fallback,
)


def test_classify_exception_authentication():
    assert classify_exception(Exception("401 Unauthorized: invalid api key")) == FailureCategory.AUTHENTICATION


def test_classify_exception_rate_limit():
    assert classify_exception(Exception("429 Too Many Requests")) == FailureCategory.RATE_LIMIT


def test_classify_exception_timeout():
    assert classify_exception(TimeoutError("Request timed out")) == FailureCategory.TIMEOUT


def test_classify_exception_unknown_default():
    assert classify_exception(Exception("something bizarre happened")) == FailureCategory.UNKNOWN


def test_classified_error_short_circuits_message_sniffing():
    err = FallbackClassifiedError(FailureCategory.EMBEDDING_DIMENSION_MISMATCH, "dims differ")
    assert classify_exception(err) == FailureCategory.EMBEDDING_DIMENSION_MISMATCH


def test_run_with_fallback_succeeds_on_primary():
    candidates = [ModelCandidate(model_id="m1", endpoint_type="hosted", label="primary")]
    result, events = run_with_fallback("test_use_case", candidates, lambda c: "ok")
    assert result == "ok"
    assert events[-1].outcome == "success"


def test_run_with_fallback_moves_to_next_candidate_on_failure():
    candidates = [
        ModelCandidate(model_id="bad", endpoint_type="hosted", label="primary"),
        ModelCandidate(model_id="good", endpoint_type="hosted", label="fallback_1", degraded=True),
    ]

    def call(candidate):
        if candidate.model_id == "bad":
            raise Exception("model not found (404)")
        return "recovered"

    result, events = run_with_fallback("test_use_case", candidates, call, sleep_fn=lambda s: None)
    assert result == "recovered"
    failures = [e for e in events if e.outcome == "failure"]
    assert failures and failures[0].failure_category == FailureCategory.MODEL_UNAVAILABLE
    assert events[-1].selected_fallback == "fallback_1"
    assert events[-1].quality_degraded is True


def test_authentication_failures_are_never_retried():
    attempts = []

    def call(candidate):
        attempts.append(candidate.model_id)
        raise Exception("401 unauthorized")

    candidates = [ModelCandidate(model_id="m1", endpoint_type="hosted", label="primary")]
    try:
        run_with_fallback("auth_test", candidates, call, max_retries_per_candidate=3, sleep_fn=lambda s: None)
    except AllCandidatesFailedError:
        pass
    assert len(attempts) == 1  # no retries for auth failures


def test_transient_failures_are_retried_within_budget():
    attempts = []

    def call(candidate):
        attempts.append(1)
        raise Exception("503 temporarily unavailable")

    candidates = [ModelCandidate(model_id="m1", endpoint_type="hosted", label="primary")]
    try:
        run_with_fallback("transient_test", candidates, call, max_retries_per_candidate=3, sleep_fn=lambda s: None)
    except AllCandidatesFailedError:
        pass
    assert len(attempts) == 3


def test_all_candidates_failed_raises_with_events():
    candidates = [ModelCandidate(model_id="m1", endpoint_type="hosted", label="primary")]

    def call(candidate):
        raise Exception("boom")

    try:
        run_with_fallback("failing_case", candidates, call, max_retries_per_candidate=1, sleep_fn=lambda s: None)
        assert False, "should have raised"
    except AllCandidatesFailedError as exc:
        assert exc.use_case == "failing_case"
        assert len(exc.events) == 1


def test_circuit_breaker_opens_after_threshold_and_skips_calls():
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=999)
    candidates = [ModelCandidate(model_id="m1", endpoint_type="hosted", label="primary")]

    def call(candidate):
        raise Exception("boom")

    for _ in range(2):
        try:
            run_with_fallback(
                "breaker_case", candidates, call, max_retries_per_candidate=1,
                circuit_breaker=breaker, sleep_fn=lambda s: None,
            )
        except AllCandidatesFailedError:
            pass

    assert breaker.is_open("breaker_case", "m1", "hosted") is True

    calls_after_open = []

    def call_after_open(candidate):
        calls_after_open.append(1)
        return "should not run"

    try:
        run_with_fallback(
            "breaker_case", candidates, call_after_open, max_retries_per_candidate=1,
            circuit_breaker=breaker, sleep_fn=lambda s: None,
        )
        assert False, "should have raised since circuit is open"
    except AllCandidatesFailedError as exc:
        assert exc.events[0].outcome == "skipped_circuit_open"
    assert not calls_after_open
