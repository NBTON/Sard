"""Tests for capability-aware OpenRouter routing (workstream B).

All providers are scripted fakes — no network, no API keys. Each test
injects explicit ``candidates`` so production RAG settings are never read,
except the static-table filter tests which assert on :func:`get_route`
directly (offline-safe: settings are read from the environment only).
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

from sard.config.model_router import ModelRouter, provider_name_for_model
from sard.config.providers import ModelProvider
from sard.config.routing_table import (
    OPENROUTER_CANDIDATES,
    TaskClass,
    get_route,
)
from sard.config.routing_table import RouteCandidate
from sard.rag.fallbacks import FailureCategory


class _ScriptedChatModel:
    """Fake chat model playing a per-model action queue."""

    def __init__(self, actions: list, calls: list):
        self._actions = list(actions)
        self._calls = calls

    def invoke(self, messages: Any) -> Any:
        self._calls.append(messages)
        if not self._actions:
            return SimpleNamespace(content="default-ok")
        action, payload = self._actions.pop(0)
        if action == "ok":
            return SimpleNamespace(content=payload)
        if action == "raise":
            raise payload
        if action == "sleep":
            time.sleep(payload)
            return SimpleNamespace(content="slow-ok")
        raise AssertionError(f"unknown action {action!r}")


class _ScriptedProvider(ModelProvider):
    """Fake provider serving scripted per-model action queues."""

    def __init__(self, name: str, scripts: dict[str, list]):
        self.name = name
        self._scripts = scripts
        self.builds: list[tuple[str, float]] = []
        self.calls: list = []

    def build_chat(self, model_id: str, timeout_s: float) -> Any:
        self.builds.append((model_id, float(timeout_s)))
        return _ScriptedChatModel(self._scripts.get(model_id, []), self.calls)

    def build_embeddings(self, model_id: str, timeout_s: float) -> Any:  # pragma: no cover
        raise AssertionError("embeddings not used in these tests")

    def supports(self, task_class: Any) -> bool:
        return True


def _router(scripts_a: dict, scripts_b: dict | None = None, **kwargs: Any) -> tuple[ModelRouter, _ScriptedProvider, _ScriptedProvider]:
    prov_a = _ScriptedProvider("openrouter", scripts_a)
    prov_b = _ScriptedProvider("nvidia", scripts_b or {})
    router = ModelRouter(
        providers={"openrouter": prov_a, "nvidia": prov_b},
        sleep_fn=lambda _s: None,
        **kwargs,
    )
    return router, prov_a, prov_b


def _legs(*model_ids: str, provider: str = "openrouter", deadline: float = 5.0) -> list[RouteCandidate]:
    return [
        RouteCandidate(provider=provider, model_id=mid, deadline_s=deadline,
                       label="primary" if i == 0 else f"fallback_{i}", degraded=i > 0)
        for i, mid in enumerate(model_ids)
    ]


def test_429_retries_once_then_advances_to_next_candidate():
    router, _, _ = _router(
        {"model-a": [("raise", Exception("429 Too Many Requests"))]},
        {"model-b": [("ok", "hello")]},
    )
    result = router.invoke_chat(TaskClass.COMPOSE_SHORT, ["hi"], candidates=_legs("model-a") + _legs("model-b", provider="nvidia"))

    assert result.success
    assert result.text == "hello"
    assert result.model_used == "model-b"
    assert result.provider == "nvidia"
    failures = [r for r in result.receipts if r.model == "model-a"]
    assert len(failures) == 2  # initial + exactly 1 transient retry
    assert all(r.failure_category == FailureCategory.RATE_LIMIT.value for r in failures)
    assert result.receipts[-1].outcome == "success"


def test_timeout_advances_to_next_candidate():
    router, _, _ = _router(
        {"model-a": [("sleep", 0.6)]},  # exceeds the 0.2s leg budget via executor timeout
        {"model-b": [("ok", "recovered")]},
    )
    legs = _legs("model-a", deadline=0.2) + _legs("model-b", provider="nvidia", deadline=5.0)
    result = router.invoke_chat(TaskClass.COMPOSE_SHORT, ["hi"], candidates=legs)

    assert result.success
    assert result.model_used == "model-b"
    assert result.receipts[0].failure_category == FailureCategory.TIMEOUT.value


def test_sdk_timeout_error_advances_without_sleeping():
    router, _, _ = _router(
        {"model-a": [("raise", TimeoutError("timed out waiting"))]},
        {"model-b": [("ok", "fine")]},
    )
    result = router.invoke_chat(TaskClass.COMPOSE_SHORT, ["hi"], candidates=_legs("model-a") + _legs("model-b", provider="nvidia"))

    assert result.success
    assert result.receipts[0].failure_category == FailureCategory.TIMEOUT.value


def test_invalid_json_advances_immediately_without_reinvoke():
    """Release contract: malformed JSON is terminal for the leg — no re-ask.

    Exact counts: model-a invoked exactly once (MALFORMED_OUTPUT receipt,
    parse_attempts == 1), then model-b wins. The second scripted bad payload
    must never be consumed.
    """
    router, prov_a, prov_b = _router(
        {"model-a": [("ok", "not json at all {{"), ("ok", "still not json")]},
        {"model-b": [("ok", '{"topic": "diriyah"}')]},
    )
    parsed, result = router.invoke_json(
        TaskClass.STRUCTURED_JSON, ["hi"], candidates=_legs("model-a") + _legs("model-b", provider="nvidia")
    )

    assert parsed == {"topic": "diriyah"}
    assert result.success
    assert result.model_used == "model-b"
    assert len(prov_a.calls) == 1  # malformed leg invoked exactly once
    assert len(prov_b.calls) == 1
    first_leg = result.receipts[0]
    assert first_leg.model == "model-a"
    assert first_leg.parse_attempts == 1
    assert first_leg.failure_category == FailureCategory.MALFORMED_OUTPUT.value
    assert first_leg.outcome == "failure"


def test_auth_failure_skips_immediately_without_retry():
    router, prov_a, _ = _router(
        {"model-a": [("raise", Exception("401 invalid api key"))]},
        {"model-b": [("ok", "hello")]},
    )
    result = router.invoke_chat(TaskClass.COMPOSE_SHORT, ["hi"], candidates=_legs("model-a") + _legs("model-b", provider="nvidia"))

    assert result.success
    assert result.model_used == "model-b"
    first_leg = [r for r in result.receipts if r.model == "model-a"]
    assert len(first_leg) == 1  # NON_RETRYABLE: no retry, straight to next leg
    assert first_leg[0].attempt == 1
    assert first_leg[0].failure_category == FailureCategory.AUTHENTICATION.value


def test_5xx_gets_one_retry_then_next_leg():
    router, _, _ = _router(
        {"model-a": [("raise", Exception("503 Service Unavailable"))]},
        {"model-b": [("ok", "hello")]},
    )
    result = router.invoke_chat(TaskClass.COMPOSE_SHORT, ["hi"], candidates=_legs("model-a") + _legs("model-b", provider="nvidia"))

    assert result.success
    assert len([r for r in result.receipts if r.model == "model-a"]) == 2


def test_404_does_not_retry_same_leg():
    router, _, _ = _router(
        {"model-a": [("raise", Exception("404 model not found"))]},
        {"model-b": [("ok", "hello")]},
    )
    result = router.invoke_chat(TaskClass.COMPOSE_SHORT, ["hi"], candidates=_legs("model-a") + _legs("model-b", provider="nvidia"))

    assert result.success
    assert len([r for r in result.receipts if r.model == "model-a"]) == 1


def test_deadline_shrinks_per_attempt_timeout():
    router, prov_a, _ = _router({"model-a": [("ok", "fast")]})
    deadline = time.monotonic() + 0.6
    result = router.invoke_chat(
        TaskClass.COMPOSE_LONGFORM, ["hi"],
        deadline_monotonic=deadline,
        candidates=_legs("model-a", deadline=20.0) + _legs("model-b", provider="nvidia", deadline=20.0),
    )

    assert result.success
    _, timeout_used = prov_a.builds[0]
    assert timeout_used < 20.0  # shrunk: remaining / legs-left, not the full budget
    assert timeout_used <= 0.61
    assert result.receipts[0].deadline_remaining_ms is not None


def test_breaker_opens_after_three_failures():
    router, prov_a, _ = _router({"only": [("raise", Exception("401 invalid api key"))]})
    legs = _legs("only")
    for _ in range(3):
        result = router.invoke_chat(TaskClass.COMPOSE_SHORT, ["hi"], candidates=legs)
        assert not result.success
    assert len(prov_a.builds) == 3

    skipped = router.invoke_chat(TaskClass.COMPOSE_SHORT, ["hi"], candidates=legs)

    assert not skipped.success
    assert len(prov_a.builds) == 3  # breaker open: provider not consulted again
    assert skipped.receipts[-1].breaker_skipped is True
    assert skipped.receipts[-1].outcome == "skipped_circuit_open"


def test_routers_do_not_share_breaker_state():
    router_a, prov_a, _ = _router({"only": [("raise", Exception("401 nope"))]})
    router_b, prov_b, _ = _router({"only": [("raise", Exception("401 nope"))]})
    legs = _legs("only")
    for _ in range(4):
        router_a.invoke_chat(TaskClass.COMPOSE_SHORT, ["hi"], candidates=legs)
    assert len(prov_a.builds) == 3  # router A breaker opened

    router_b.invoke_chat(TaskClass.COMPOSE_SHORT, ["hi"], candidates=legs)
    assert len(prov_b.builds) == 1  # router B unaffected (no global singleton)


def test_structured_tasks_use_structured_capable_legs():
    for task in (TaskClass.PLAN, TaskClass.VERIFY, TaskClass.STRUCTURED_JSON):
        for leg in get_route(task):
            if leg.provider == "openrouter" and leg.model_id in OPENROUTER_CANDIDATES:
                assert OPENROUTER_CANDIDATES[leg.model_id].supports_structured, (task, leg.model_id)


def test_vision_route_uses_vision_capable_legs():
    for leg in get_route(TaskClass.VISION):
        if leg.provider == "openrouter" and leg.model_id in OPENROUTER_CANDIDATES:
            assert OPENROUTER_CANDIDATES[leg.model_id].supports_vision, leg.model_id


def test_static_ids_are_bakeoff_canonical_not_human_names():
    for task in TaskClass:
        for leg in get_route(task):
            if leg.provider != "openrouter":
                continue
            if leg.model_id in OPENROUTER_CANDIDATES:
                continue  # catalog-verified canonical ID
            # Operator/configured overrides must still be fully qualified IDs.
            assert "/" in leg.model_id, f"human-name-as-ID rejected: {leg.model_id!r}"


def test_fast_primary_is_catalog_verified_and_budgets_cover_smoke_p50():
    """Every default OpenRouter leg must be catalog-verified (no fail-open
    unverified primary), and LFM-bearing fast legs must budget >= 12s so the
    9.74s benchmark p50 can complete instead of timing out."""
    from sard.config.routing_table import CONFIGURED_FAST_PRIMARY

    assert CONFIGURED_FAST_PRIMARY in OPENROUTER_CANDIDATES, (
        f"fast primary {CONFIGURED_FAST_PRIMARY!r} is not catalog-verified"
    )
    for task in (TaskClass.FAST_CLASSIFY, TaskClass.QUERY_REWRITE):
        legs = [leg for leg in get_route(task) if leg.provider == "openrouter"]
        assert legs, f"{task} has no OpenRouter legs"
        for leg in legs:
            assert leg.model_id in OPENROUTER_CANDIDATES, leg.model_id
            if leg.model_id == "liquid/lfm-2.5-2.6b:free":
                assert leg.deadline_s >= 12.0, f"LFM leg budget too tight: {leg.deadline_s}s"


def test_embed_route_is_primary_only_never_mixed_dims():
    legs = get_route(TaskClass.EMBED_TEXT)
    nvidia_legs = [leg for leg in legs if leg.provider == "nvidia"]
    assert len(nvidia_legs) == 1
    assert all(leg.provider != "openrouter" for leg in legs)  # embeds stay on NVIDIA path


def test_rerank_and_tts_are_deterministic_until_validated():
    router, _, _ = _router({})
    for task in (TaskClass.RERANK, TaskClass.TTS):
        result = router.invoke_chat(task, ["hi"])
        assert not result.success
        assert result.deterministic_fallback is True
        assert result.receipts[0].outcome == "unsupported"
        assert result.receipts[0].provider == "deterministic"


def test_receipts_carry_no_payloads_or_secrets():
    router, _, _ = _router(
        {"model-a": [("raise", Exception("429 slow down"))]},
        {"model-b": [("ok", "hello world")]},
    )
    result = router.invoke_chat(TaskClass.COMPOSE_SHORT, ["super secret prompt"], candidates=_legs("model-a") + _legs("model-b", provider="nvidia"))
    for receipt in result.receipts:
        blob = receipt.to_dict()
        assert set(blob) == {
            "task_class", "provider", "model", "attempt", "failure_category",
            "outcome", "latency_ms", "deadline_remaining_ms", "breaker_skipped", "parse_attempts",
        }
        assert "super secret prompt" not in str(blob)
        assert "hello world" not in str(blob)


def test_legacy_channels_carry_provider_alongside_model():
    router, _, _ = _router({}, {"model-b": [("ok", "hi")]})
    result = router.invoke_chat(TaskClass.COMPOSE_SHORT, ["hi"], candidates=_legs("model-b", provider="nvidia"))

    assert result.model_routes == {"compose_short": "model-b"}
    assert result.provider_routes == {"compose_short": "nvidia"}
    events = result.to_fallback_events()
    assert events and events[0].outcome == "success"
    assert result.to_timings(12.5) == {"compose_short_ms": 12.5}


def test_provider_name_selection():
    assert provider_name_for_model("google/gemma-4-31b-it:free") == "openrouter"
    assert provider_name_for_model("custom/vendor-model") == "openrouter"
    assert provider_name_for_model("nemotron-3-super-120b-a12b") == "nvidia"
    assert provider_name_for_model("") == "nvidia"
    # Slashed NVIDIA NIM IDs must not be misrouted to OpenRouter by the
    # "/" heuristic (legacy factory default shape).
    assert provider_name_for_model("meta/llama-3.1-70b-instruct") == "nvidia"
    assert provider_name_for_model("nvidia/llama-3.1-nemotron-70b-instruct") == "nvidia"


def test_reasoning_channel_reader_recovers_thinking_mandatory_leg():
    """A leg with empty content but reasoning_content text succeeds on the
    same leg (no MALFORMED degradation hop)."""
    from sard.config.model_router import _reasoning_channel_text

    resp = SimpleNamespace(
        content="",
        additional_kwargs={"reasoning_content": "إجابة من قناة التفكير"},
    )
    assert _reasoning_channel_text(resp) == "إجابة من قناة التفكير"
    assert _reasoning_channel_text(SimpleNamespace(content="x")) == ""

    router, _, _ = _router({"model-a": []})
    text = router._invoke_once(
        _ScriptedReasoningModel("إجابة من قناة التفكير"), ["hi"], 5.0
    )
    assert text == "إجابة من قناة التفكير"


class _ScriptedReasoningModel:
    """Fake thinking-mandatory model: empty content, answer in reasoning."""

    def __init__(self, reasoning: str):
        self._reasoning = reasoning

    def invoke(self, messages: Any) -> Any:
        return SimpleNamespace(
            content="", additional_kwargs={"reasoning_content": self._reasoning}
        )


def test_reasoning_off_flag_only_for_flagged_ids(monkeypatch):
    """needs_reasoning_off IDs get extra_body reasoning.enabled=false."""
    import langchain_openai

    seen: dict[str, Any] = {}

    class _FakeChatOpenAI:
        def __init__(self, **kwargs: Any):
            seen.update(kwargs)

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", _FakeChatOpenAI)

    import sard.config.providers.openrouter as or_mod

    provider = or_mod.OpenRouterProvider(api_key="test-key")
    provider.build_chat("dots-studio/dots-3-note-preview:free", 5.0)
    assert seen.get("extra_body") == {"reasoning": {"enabled": False}}
    seen.clear()
    provider.build_chat("liquid/lfm-2.5-2.6b:free", 5.0)
    assert "extra_body" not in seen
