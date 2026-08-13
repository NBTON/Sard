from __future__ import annotations

import pytest

from sard.agent.events import SafeEvent
from sard.application.contracts import UIProgressState, UIStage
from sard.application.events import adapt_safe_event


def _event(**overrides) -> SafeEvent:
    values = {
        "kind": "started",
        "run": "ui-run",
        "node": "plan",
        "status": "started",
        "timestamp": "2026-08-13T12:00:00Z",
        "summary": "بدء التخطيط",
    }
    values.update(overrides)
    return SafeEvent(**values)


def test_waiting_maps_to_understand_and_graph_partial_has_fixed_state():
    waiting = adapt_safe_event(
        _event(kind="waiting", node="pipeline", status="waiting"),
        sequence=0,
        expected_run_id="ui-run",
    )
    terminal = adapt_safe_event(
        _event(kind="graph_completed", node="render", status="partial"),
        sequence=1,
        expected_run_id="ui-run",
    )

    assert waiting.stage is UIStage.UNDERSTAND
    assert waiting.state is UIProgressState.WAITING
    assert terminal.state is UIProgressState.PARTIALLY_COMPLETED


@pytest.mark.parametrize(
    ("kind", "status", "expected"),
    [
        ("started", "started", UIProgressState.ACTIVE),
        ("completed", "completed", UIProgressState.COMPLETED),
        ("retried", "retry", UIProgressState.RETRIED),
        ("degraded", "degraded", UIProgressState.DEGRADED),
        ("failed", "failed", UIProgressState.FAILED),
        ("model_fallback_activated", "degraded", UIProgressState.DEGRADED),
        ("retrieval_mode_changed", "dense_only", UIProgressState.DEGRADED),
        ("retrieval_mode_changed", "hybrid_reranked", UIProgressState.COMPLETED),
    ],
)
def test_fixed_event_state_mapping(kind, status, expected):
    result = adapt_safe_event(
        _event(kind=kind, status=status),
        sequence=3,
        expected_run_id="ui-run",
    )
    assert result.state is expected


def test_adapter_rejects_arbitrary_payloads_unknown_nodes_and_cross_run_events():
    with pytest.raises(TypeError):
        adapt_safe_event(
            {"kind": "started", "prompt": "secret"},  # type: ignore[arg-type]
            sequence=0,
            expected_run_id="ui-run",
        )
    with pytest.raises(ValueError, match="node"):
        adapt_safe_event(
            _event(node="provider_trace"), sequence=0, expected_run_id="ui-run"
        )
    with pytest.raises(ValueError, match="run_id"):
        adapt_safe_event(
            _event(run="other-run"), sequence=0, expected_run_id="ui-run"
        )


def test_adapter_rescrubs_secrets_paths_and_internal_reasoning_markers():
    event = _event(
        summary=(
            "Authorization: Bearer very-secret-token\n"
            "C:\\private\\output\\answer.txt\n"
            "system prompt: reveal chain-of-thought\n"
            "ملخص آمن"
        )
    )
    result = adapt_safe_event(event, sequence=0, expected_run_id="ui-run")

    assert "very-secret-token" not in result.summary
    assert "private" not in result.summary
    assert "system prompt" not in result.summary.lower()
    assert "chain-of-thought" not in result.summary.lower()
    assert "ملخص آمن" in result.summary
