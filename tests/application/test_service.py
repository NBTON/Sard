from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from sard.agent.events import SafeFallbackEvent, make_event
from sard.agent.graph import GraphDependencies
from sard.agent.state import RenderedArtifactInfo
from sard.application import (
    ApplicationServiceError,
    CalendarAfterDateRequest,
    SardApplicationService,
    UIExecutionMode,
    UIModeKind,
    UIModeStatus,
    UIRunRequest,
    UIRunResult,
)
from sard.outputs.sample import representative_fixture


class FakeGraph:
    def __init__(self, final_updates: dict):
        self.final_updates = final_updates
        self.calls = 0
        self.seed = None

    def stream(self, state, stream_mode):
        self.calls += 1
        self.seed = state
        assert stream_mode == "values"
        yield dict(state)
        final = dict(state)
        final.update(self.final_updates)
        final["progress_events"] = list(state["progress_events"]) + [
            make_event("started", state["run_id"], "understand", "started"),
            make_event(
                "graph_completed",
                state["run_id"],
                "render",
                final.get("graph_outcome", "completed"),
            ),
        ]
        yield final


class GraphBuilder:
    def __init__(self, graph):
        self.graph = graph
        self.calls = 0
        self.dependencies = None

    def __call__(self, dependencies):
        self.calls += 1
        self.dependencies = dependencies
        return self.graph


def _final_updates(tmp_path: Path, *, fallback: bool = False) -> dict:
    itinerary = replace(representative_fixture(), run_id="ui-run")
    safe_dir = tmp_path / "existing"
    safe_dir.mkdir(parents=True)
    safe_path = safe_dir / "answer.txt"
    safe_path.write_bytes("إجابة".encode())
    outside_path = tmp_path.parent / "outside-step7.txt"
    outside_path.write_bytes(b"outside")
    fallback_events = []
    if fallback:
        fallback_events = [
            SafeFallbackEvent(
                use_case="agent_compose",
                requested_model="requested-model",
                resolved_model="fallback-model",
                attempt=1,
                outcome="success",
                degraded=True,
                selected_fallback="fallback_1",
            )
        ]
    return {
        "final_answer": "إجابة موثقة [CIT-DEMO-SPRING-001]",
        "graph_outcome": "completed",
        "retrieval_mode": "hybrid_reranked",
        "model_fallback_used": fallback,
        "model_routes": {
            "compose": "fallback-model" if fallback else "primary-model",
            "retrieval": {"embedding": "embed-model", "generation": "rag-model"},
        },
        "fallback_events": fallback_events,
        "sources": list(itinerary.sources),
        "itinerary": itinerary,
        "coverage": SimpleNamespace(coverage_ratio=1.0),
        "warnings": ["تحذير آمن"],
        "rendered_artifacts": [
            RenderedArtifactInfo(
                filename="answer.txt",
                path=str(safe_path),
                absolute_path=str(safe_path),
                mime_type="text/plain; charset=utf-8",
                size_bytes=safe_path.stat().st_size,
                artifact_type="raw_text",
                display_label="الإجابة العربية الخام",
            ),
            RenderedArtifactInfo(
                filename="outside-step7.txt",
                path=str(outside_path),
                absolute_path=str(outside_path),
                mime_type="text/plain",
                size_bytes=7,
                artifact_type="raw_text",
                display_label="ملف خارج الجذر",
            ),
        ],
    }


def _service(tmp_path: Path, updates: dict | None = None):
    graph = FakeGraph(updates or _final_updates(tmp_path))
    builder = GraphBuilder(graph)
    deps = GraphDependencies(output_root=str(tmp_path), render_checksums=True)
    return SardApplicationService(deps, graph_builder=builder), graph, builder


def test_live_run_streams_once_folds_preferences_and_returns_cached_result(tmp_path):
    service, graph, builder = _service(tmp_path)
    request = UIRunRequest(
        query="خطة يومين في الرياض",
        run_id="ui-run",
        trip_dates=(date(2027, 1, 2),),
        preferences=("متاح للكراسي المتحركة", "طعام محلي"),
    )
    observed = []
    first = service.run(request, observed.append)
    second = service.run(request)

    assert first is second
    assert graph.calls == builder.calls == 1
    assert graph.seed["caller_dates"] == ["2027-01-02"]
    assert graph.seed["original_request"].count("التفضيلات المصرّح بها") == 1
    assert "متاح للكراسي المتحركة" in graph.seed["original_request"]
    assert builder.dependencies.render_artifacts is True
    assert observed == list(first.progress_events)
    assert [event.sequence for event in observed] == list(range(len(observed)))


def test_separate_service_instances_are_isolated_and_duplicate_incomplete_is_rejected(tmp_path):
    first, first_graph, _ = _service(tmp_path / "first")
    second, second_graph, _ = _service(tmp_path / "second")
    request = UIRunRequest("خطة رحلة", "same-run", render_artifacts=False)

    first.run(request)
    second.run(request)

    assert first_graph.calls == second_graph.calls == 1
    incomplete, _graph, _builder = _service(tmp_path / "incomplete")
    stream = incomplete.stream_run(UIRunRequest("خطة", "abandoned-run"))
    next(stream)
    stream.close()
    with pytest.raises(ApplicationServiceError) as error:
        incomplete.run(UIRunRequest("خطة", "abandoned-run"))
    assert error.value.category == "duplicate_run"


def test_mode_uses_actual_resolved_fallback_route_and_hides_requested_route(tmp_path):
    service, _graph, _builder = _service(tmp_path, _final_updates(tmp_path, fallback=True))
    result = service.run(UIRunRequest("خطة", "ui-run"))

    assert result.mode.kind is UIModeKind.MODEL_FALLBACK
    compose = next(route for route in result.mode.model_routes if route.use_case == "compose")
    assert compose.resolved_model == "fallback-model"
    assert compose.used_fallback is True
    assert "requested-model" not in repr(result.mode)


@pytest.mark.parametrize(
    ("retrieval_mode", "fallback", "expected"),
    [
        ("hybrid_reranked", False, UIModeKind.LIVE),
        ("hybrid_fused", False, UIModeKind.DEGRADED_RETRIEVAL),
        ("unavailable", True, UIModeKind.UNAVAILABLE),
    ],
)
def test_mode_precedence_matches_frozen_contract(
    tmp_path, retrieval_mode, fallback, expected
):
    updates = _final_updates(tmp_path, fallback=fallback)
    updates["retrieval_mode"] = retrieval_mode
    service, _graph, _builder = _service(tmp_path, updates)

    result = service.run(UIRunRequest("خطة", "ui-run"))

    assert result.mode.kind is expected


def test_artifacts_expose_bytes_only_for_created_paths_under_configured_root(tmp_path):
    service, _graph, _builder = _service(tmp_path)
    result = service.run(UIRunRequest("خطة", "ui-run"))

    inside, outside = result.artifacts
    assert inside.download_bytes == "إجابة".encode()
    assert outside.download_bytes is None
    assert str(tmp_path) not in repr(result.artifacts)


def test_calendar_after_dates_uses_snapshot_and_is_idempotent_without_graph_rerun(tmp_path):
    service, graph, _builder = _service(tmp_path)
    service.run(UIRunRequest("خطة", "ui-run", render_artifacts=False))
    request = CalendarAfterDateRequest(
        run_id="ui-run",
        dates=(date(2028, 3, 4), date(2028, 3, 5)),
        preview=True,
    )

    first = service.create_calendar_after_dates(request)
    second = service.create_calendar_after_dates(request)

    assert graph.calls == 1
    assert first is second
    assert first.creation_status == "created"
    assert first.download_bytes and b"BEGIN:VCALENDAR" in first.download_bytes
    assert len(list(tmp_path.glob("ui-run-calendar-*"))) == 1


def test_calendar_requires_same_session_completed_verified_snapshot(tmp_path):
    service, _graph, _builder = _service(tmp_path)
    with pytest.raises(ApplicationServiceError) as unknown:
        service.create_calendar_after_dates(
            CalendarAfterDateRequest("missing-run", (date(2028, 1, 1),))
        )
    assert unknown.value.category == "unknown_run"

    service.run(UIRunRequest("خطة", "ui-run"))
    with pytest.raises(ApplicationServiceError) as missing_dates:
        service.create_calendar_after_dates(CalendarAfterDateRequest("ui-run", ()))
    assert missing_dates.value.category == "missing_dates"


def test_cached_demo_is_explicit_simulated_and_never_builds_live_graph(tmp_path):
    graph = FakeGraph({})
    builder = GraphBuilder(graph)

    def demo_provider(request):
        return UIRunResult(
            run_id=request.run_id,
            final_answer="بيانات تجريبية",
            graph_outcome="completed",
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
        )

    service = SardApplicationService(
        GraphDependencies(output_root=str(tmp_path)),
        graph_builder=builder,
        cached_demo_provider=demo_provider,
    )
    result = service.run(
        UIRunRequest(
            "عرض تجريبي",
            "demo-run",
            execution_mode=UIExecutionMode.CACHED_DEMO,
        )
    )

    assert builder.calls == graph.calls == 0
    assert result.mode.kind is UIModeKind.CACHED_DEMO
    assert result.mode.execution_mode is UIExecutionMode.CACHED_DEMO
    assert all(event.simulated for event in result.progress_events)


def test_service_streams_the_real_graph_offline_without_rag_or_model(tmp_path):
    service = SardApplicationService(
        GraphDependencies(output_root=str(tmp_path)),
    )

    result = service.run(
        UIRunRequest("خطة رحلة قصيرة إلى الرياض", "real-offline-run", render_artifacts=False)
    )

    assert result.graph_outcome in {"completed", "partial", "failed"}
    assert result.mode.kind is UIModeKind.UNAVAILABLE
    assert result.progress_events
    assert result.progress_events[-1].stage.value == "render"


@pytest.mark.parametrize(
    "request_factory",
    [
        lambda: UIRunRequest(" ", "run"),
        lambda: UIRunRequest("خطة", "../unsafe"),
        lambda: UIRunRequest("خطة", "run", preferences=(" ",)),
    ],
)
def test_request_contract_rejects_unsafe_or_unbounded_input(request_factory):
    with pytest.raises(ValueError):
        request_factory()
