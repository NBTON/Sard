"""Focused pure tests for the Step 7 UI state machine.

Agent D owns only this file (plus tiny test-only helpers).  The real
``sard.application.*`` and ``sard.ui.presentation`` modules land from other
branches, so these tests exercise the frozen public contracts (mirrored in
``tests/helpers/_frozen_contracts.py``) and the real, already-merged graph,
event, render, calendar and artifact code.  Suggested integration changes
are reported to the coordinator, not edited here.

No live NVIDIA/network is touched.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from sard.agent.events import (
    EVENT_CITATION_COVERAGE_CALCULATED,
    EVENT_COMPLETED,
    EVENT_DEGRADED,
    EVENT_FAILED,
    EVENT_GRAPH_COMPLETED,
    EVENT_MODEL_FALLBACK_ACTIVATED,
    EVENT_RETRIED,
    EVENT_RETRIEVAL_MODE_CHANGED,
    EVENT_STARTED,
    EVENT_WAITING,
    SAFE_EVENT_KINDS,
    FailureKind,
    make_error,
    make_event,
    safe_chain_message,
    sanitize_text,
)
from sard.agent.graph import run_pipeline
from sard.agent.nodes.render import render as render_node
from sard.agent.nodes.retrieve import retrieve as retrieve_node
from sard.agent.state import GraphOutcome, RAGMode
from sard.outputs.artifacts import ArtifactError, ArtifactManager
from sard.outputs.sample import representative_fixture

from tests.helpers.graph_harness import (
    ExplodingRAG,
    FakeRAGService,
    created_artifact,
    evidence_answer,
    fallback_answer,
    make_offline_deps,
    offline_runner,
    render_deps,
    render_itinerary,
    render_state,
    success_scripts,
)
from tests.helpers.step7_contracts import (
    UIArtifactView,
    UIExecutionMode,
    UIModeKind,
    UIProgressEvent,
    UIProgressState,
    UIRunRequest,
    UIStage,
    UISourceView,
)
from tests.helpers.step7_reference import (
    DuplicateRunError,
    MissingCalendarDatesError,
    ReferenceApplicationService,
    UnknownRunError,
    artifact_button_state,
    build_result_from_state,
    build_source_view,
    calendar_sub_run_id,
    download_payload,
    escape_html,
    map_progress_state,
    project_mode_kind,
    project_model_routes,
    project_progress_event,
    sanitize_source_url,
    select_demo_fixture,
    ui_error_message,
    ui_stage_for_node,
)


# ---------------------------------------------------------------------------
# Frozen contract sanity
# ---------------------------------------------------------------------------


def test_contract_frozen_enums():
    assert [stage.value for stage in UIStage] == [
        "understand",
        "plan",
        "retrieve",
        "compose",
        "verify",
        "render",
    ]
    assert [state.value for state in UIProgressState] == [
        "waiting",
        "active",
        "completed",
        "retried",
        "degraded",
        "failed",
        "partially_completed",
    ]
    assert {mode.value for mode in UIModeKind} == {
        "live",
        "degraded_retrieval",
        "model_fallback",
        "cached_demo",
        "unavailable",
    }
    assert UIExecutionMode.LIVE.value == "live"
    assert UIExecutionMode.CACHED_DEMO.value == "cached_demo"


def test_contract_dataclass_defaults_and_no_competing_schema():
    request = UIRunRequest(query="رحلة", run_id="run-abc")
    assert request.trip_dates == ()
    assert request.preferences == ()
    assert request.execution_mode == UIExecutionMode.LIVE
    assert request.render_artifacts is True

    assert not hasattr(UISourceView, "source_name")
    event = UIProgressEvent(0, "run-1", UIStage.RENDER, UIProgressState.COMPLETED, "completed", "ts")
    assert event.simulated is False and event.degraded is False and event.retry_count is None


# ---------------------------------------------------------------------------
# Progress mapping (frozen table)
# ---------------------------------------------------------------------------

_MAP_CASES = [
    (EVENT_WAITING, "waiting", UIProgressState.WAITING),
    (EVENT_STARTED, "started", UIProgressState.ACTIVE),
    (EVENT_COMPLETED, "completed", UIProgressState.COMPLETED),
    (EVENT_RETRIED, "retried", UIProgressState.RETRIED),
    (EVENT_DEGRADED, "degraded", UIProgressState.DEGRADED),
    (EVENT_MODEL_FALLBACK_ACTIVATED, "degraded", UIProgressState.DEGRADED),
    (EVENT_FAILED, "failed", UIProgressState.FAILED),
    (EVENT_CITATION_COVERAGE_CALCULATED, "completed", UIProgressState.COMPLETED),
    (EVENT_RETRIEVAL_MODE_CHANGED, RAGMode.DENSE_ONLY.value, UIProgressState.DEGRADED),
    (EVENT_RETRIEVAL_MODE_CHANGED, RAGMode.UNAVAILABLE.value, UIProgressState.DEGRADED),
    (EVENT_RETRIEVAL_MODE_CHANGED, RAGMode.HYBRID_RERANKED.value, UIProgressState.COMPLETED),
    (EVENT_GRAPH_COMPLETED, GraphOutcome.PARTIAL.value, UIProgressState.PARTIALLY_COMPLETED),
    (EVENT_GRAPH_COMPLETED, GraphOutcome.COMPLETED.value, UIProgressState.COMPLETED),
    (EVENT_GRAPH_COMPLETED, GraphOutcome.FAILED.value, UIProgressState.FAILED),
]


@pytest.mark.parametrize("kind,status,expected", _MAP_CASES)
def test_progress_mapping_table(kind, status, expected):
    assert map_progress_state(kind, status) is expected


def test_pipeline_waiting_event_maps_to_understand_stage():
    assert ui_stage_for_node("pipeline", EVENT_WAITING) is UIStage.UNDERSTAND


def test_projection_preserves_metadata_and_marks_simulated_false():
    safe = make_event(
        EVENT_RETRIED,
        "run-1",
        "verify",
        "retried",
        summary="إعادة الصياغة",
        duration_ms=12.5,
        source_count=2,
        coverage=0.5,
        retry=2,
        degraded=True,
    )
    event = project_progress_event(safe, sequence=3)
    assert isinstance(event, UIProgressEvent)
    assert event.sequence == 3
    assert event.run_id == "run-1"
    assert event.stage is UIStage.VERIFY
    assert event.state is UIProgressState.RETRIED
    assert event.event_kind == EVENT_RETRIED
    assert event.summary == "إعادة الصياغة"
    assert event.duration_ms == 12.5
    assert event.source_count == 2
    assert event.coverage == 0.5
    assert event.retry_count == 2
    assert event.degraded is True
    assert event.simulated is False


def test_unknown_event_kind_rejected_not_displayed():
    with pytest.raises(ValueError):
        project_progress_event(
            make_event("mystery_kind", "run-1", "verify", "started", summary="x"),
            sequence=0,
        )


def test_unknown_node_rejected_not_displayed():
    safe = make_event(EVENT_COMPLETED, "run-1", "not_a_stage", "completed", summary="x")
    with pytest.raises(ValueError):
        ui_stage_for_node("not_a_stage", EVENT_COMPLETED)


# ---------------------------------------------------------------------------
# Progress mapping over the real offline graph
# ---------------------------------------------------------------------------


def test_success_run_maps_progress_in_stage_order():
    deps = make_offline_deps(success_scripts(), rag_service=FakeRAGService(evidence_answer()))
    state = run_pipeline("خطة لرحلة إلى الرياض", dependencies=deps, run_id="run-map")

    events = [project_progress_event(event, i) for i, event in enumerate(state["progress_events"])]
    stages = [event.stage for event in events]
    assert UIStage.UNDERSTAND in stages and UIStage.RENDER in stages
    rank = {stage: i for i, stage in enumerate(UIStage)}
    for first, second in zip(stages, stages[1:]):
        assert rank[first] <= rank[second]

    rendered = [event for event in events if event.event_kind == EVENT_GRAPH_COMPLETED]
    assert rendered and rendered[0].state is UIProgressState.COMPLETED
    assert all(event.run_id == "run-map" for event in events)


def test_exhaustion_run_maps_retried_then_partially_completed():
    scripts = success_scripts()[:3]
    scripts.extend(
        [
            '{"claims":[{"claim_id":"CLAIM-01-001","status":"supported","correction":"","note":""},'
            '{"claim_id":"CLAIM-01-002","status":"unsupported","correction":"إزالة","note":""}]}',
            "الأسواق الشعبية في الرياض وجهة بارزة للزوار [CIT-RIY01]. "
            "سعر الدخول خمسون ريالًا [CIT-RIY02].",
            '{"claims":[{"claim_id":"CLAIM-02-001","status":"supported","correction":"","note":""},'
            '{"claim_id":"CLAIM-02-002","status":"unsupported","correction":"إزالة","note":""}]}',
        ]
    )
    deps = make_offline_deps(scripts, rag_service=FakeRAGService(evidence_answer()), max_retries=1)
    state = run_pipeline("خطة لرحلة إلى الرياض", dependencies=deps, run_id="run-exh")

    assert state["graph_outcome"] == GraphOutcome.PARTIAL.value
    states = [map_progress_state(event.kind, event.status) for event in state["progress_events"]]
    assert UIProgressState.RETRIED in states
    assert UIProgressState.PARTIALLY_COMPLETED in states
    assert UIProgressState.COMPLETED in states


# ---------------------------------------------------------------------------
# Node transitions
# ---------------------------------------------------------------------------


def _node_sequence(state: dict) -> list[str]:
    return [event.node for event in state["progress_events"] if event.node != "pipeline"]


def test_success_path_follows_six_node_order():
    deps = make_offline_deps(success_scripts(), rag_service=FakeRAGService(evidence_answer()))
    state = run_pipeline("خطة لرحلة إلى الرياض", dependencies=deps, run_id="run-order")
    distinct = []
    for node in _node_sequence(state):
        if node not in distinct:
            distinct.append(node)
    assert distinct == ["understand", "plan", "retrieve", "compose", "verify", "render"]


def test_retry_loop_is_bounded_and_recomposes():
    scripts = success_scripts()[:3]
    scripts.extend(
        [
            '{"claims":[{"claim_id":"CLAIM-01-001","status":"supported","correction":"","note":""},'
            '{"claim_id":"CLAIM-01-002","status":"unsupported","correction":"أزله","note":""}]}',
            "الأسواق الشعبية في الرياض وجهة بارزة للزوار [CIT-RIY01]. "
            "تتوفر معلومات محدودة عن مواقيت الزيارة في المصادر الحالية.",
            '{"claims":[{"claim_id":"CLAIM-02-001","status":"supported","correction":"","note":""},'
            '{"claim_id":"CLAIM-02-002","status":"explicitly_uncertain","correction":"","note":""}]}',
        ]
    )
    deps = make_offline_deps(scripts, rag_service=FakeRAGService(evidence_answer()), max_retries=2)
    state = run_pipeline("خطة لرحلة إلى الرياض", dependencies=deps, run_id="run-retry")

    assert state["graph_outcome"] == GraphOutcome.COMPLETED.value
    distinct = []
    for node in _node_sequence(state):
        if not distinct or distinct[-1] != node:
            distinct.append(node)
    assert distinct == ["understand", "plan", "retrieve", "compose", "verify", "compose", "verify", "render"]
    retried = [event.retry for event in state["progress_events"] if event.kind == EVENT_RETRIED]
    assert retried == [1]


# ---------------------------------------------------------------------------
# Degraded / model-fallback labels
# ---------------------------------------------------------------------------


def test_model_fallback_activated_maps_to_degraded_label():
    deps = make_offline_deps([], rag_service=FakeRAGService(fallback_answer()))
    updates = retrieve_node({"run_id": "run-fb", "original_request": "خطة", "plan": None}, deps)

    fallback = [event for event in updates["progress_events"] if event.kind == EVENT_MODEL_FALLBACK_ACTIVATED]
    assert fallback, "retrieve node must emit a model-fallback event for a degraded fallback"
    assert fallback[0].degraded is True
    assert project_progress_event(fallback[0], 0).state is UIProgressState.DEGRADED


def test_mode_projection_precedence():
    cases = [
        (UIExecutionMode.CACHED_DEMO, "hybrid_reranked", False, UIModeKind.CACHED_DEMO),
        (UIExecutionMode.LIVE, RAGMode.UNAVAILABLE.value, True, UIModeKind.UNAVAILABLE),
        (UIExecutionMode.LIVE, "hybrid_reranked", True, UIModeKind.MODEL_FALLBACK),
        (UIExecutionMode.LIVE, RAGMode.DENSE_ONLY.value, False, UIModeKind.DEGRADED_RETRIEVAL),
        (UIExecutionMode.LIVE, "hybrid_reranked", False, UIModeKind.LIVE),
    ]
    for execution_mode, retrieval_mode, fallback, expected in cases:
        assert (
            project_mode_kind(
                execution_mode=execution_mode,
                retrieval_mode=retrieval_mode,
                model_fallback_used=fallback,
            )
            is expected
        )


def test_resolved_fallback_route_is_attributed():
    class SafeFallbackLike:
        use_case = "generation"
        outcome = "success"
        degraded = True
        selected_fallback = "fallback_1"

    routes = project_model_routes(
        {"generation": "nv-chat-lite", "embedding": "nv-embed"},
        [SafeFallbackLike()],
    )
    by_use_case = {route.use_case: route for route in routes}
    assert by_use_case["generation"].resolved_model == "nv-chat-lite"
    assert by_use_case["generation"].used_fallback is True
    assert by_use_case["embedding"].used_fallback is False

    clean = project_model_routes({"generation": "nv-chat"}, [])
    assert clean[0].used_fallback is False


def test_demo_mode_always_labels_cached_demo_regardless_of_retrieval():
    assert (
        project_mode_kind(
            execution_mode=UIExecutionMode.CACHED_DEMO,
            retrieval_mode="hybrid_reranked",
            model_fallback_used=True,
        )
        is UIModeKind.CACHED_DEMO
    )


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def test_source_view_metadata_completeness_label():
    from sard.outputs.schemas import CitationSource

    partial_source = CitationSource(
        "CIT-PARTIAL-01", "عنوان", "https://example.org", page=4
    )
    view = build_source_view(partial_source)
    assert view.citation_id == "CIT-PARTIAL-01"
    assert view.metadata_complete is False
    assert view.citation_verified is True
    assert not hasattr(view, "source_name")

    complete_source = replace(
        partial_source,
        section="المسار",
        publication_date=date(2025, 1, 1),
    )
    assert build_source_view(complete_source).metadata_complete is True


def test_sources_projected_from_real_run_preserve_only_supplied_metadata():
    deps = make_offline_deps(success_scripts(), rag_service=FakeRAGService(evidence_answer()))
    state = run_pipeline("خطة لرحلة إلى الرياض", dependencies=deps, run_id="run-src")

    views = tuple(build_source_view(source) for source in state["sources"])
    assert views
    assert {view.citation_id for view in views} == {"CIT-RIY01", "CIT-RIY02"}
    for view in views:
        assert view.url == "https://example.org/riyadh"
        assert view.title == "مصدر الرياض"
        assert view.page == 3
        assert view.section is None
        assert view.publication_date == date(2025, 1, 1)
        assert view.metadata_complete is False


# ---------------------------------------------------------------------------
# URL sanitization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JAVASCRIPT:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
    ],
)
def test_url_sanitization_rejects_active_schemes(url):
    assert sanitize_source_url(url) == ""


def test_url_sanitization_strips_controls_and_bounds_length():
    assert sanitize_source_url("  https://example.org/a\nb\tc  ") == "https://example.org/abc"
    assert sanitize_source_url("") == ""
    assert sanitize_source_url(None) == ""
    long_url = "https://example.org/" + "x" * 1000
    assert len(sanitize_source_url(long_url)) == 512


# ---------------------------------------------------------------------------
# HTML escaping
# ---------------------------------------------------------------------------


def test_html_escaping():
    assert escape_html('المصدر <b>خام</b> & "مقتبس"') == "المصدر &lt;b&gt;خام&lt;/b&gt; &amp; &quot;مقتبس&quot;"
    assert escape_html("abc'def") == "abc&#39;def"
    assert escape_html("أرقام 123 عربية") == "أرقام 123 عربية"
    assert escape_html("") == ""


# ---------------------------------------------------------------------------
# Missing dates (real render node)
# ---------------------------------------------------------------------------


def test_missing_dates_skip_calendar_but_keep_pdf_and_raw(tmp_path):
    result = render_node(render_state(render_itinerary(dated=False)), render_deps(tmp_path))
    assert result["graph_outcome"] == GraphOutcome.PARTIAL.value
    assert created_artifact(result, "pdf").creation_status == "created"
    assert created_artifact(result, "raw_text").creation_status == "created"
    calendar = created_artifact(result, "calendar")
    assert calendar.creation_status == "skipped"
    assert calendar.error_category == "missing_dates"


# ---------------------------------------------------------------------------
# Calendar-after-date
# ---------------------------------------------------------------------------


def test_calendar_sub_run_id_deterministic_safe_and_date_sensitive():
    first = calendar_sub_run_id("run-1", (date(2026, 11, 1),), preview=False)
    again = calendar_sub_run_id("run-1", (date(2026, 11, 1),), preview=False)
    assert first == again
    assert first.startswith("run-1-calendar-") and len(first) == len("run-1-calendar-") + 12
    assert calendar_sub_run_id("run-1", (date(2026, 11, 2),), preview=False) != first
    assert calendar_sub_run_id("run-1", (date(2026, 11, 1),), preview=True) != first


def test_calendar_after_dates_never_calls_graph_and_is_idempotent(tmp_path):
    service = ReferenceApplicationService(
        live_runner=offline_runner(
            make_offline_deps(success_scripts(), rag_service=FakeRAGService(evidence_answer()))
        ),
        output_root=tmp_path,
    )
    request = UIRunRequest(query="خطة لرحلة إلى الرياض", run_id="run-cal", execution_mode=UIExecutionMode.CACHED_DEMO)
    result = service.run(request)
    assert service.graph_invocations == {}

    from tests.helpers.step7_contracts import CalendarAfterDateRequest

    calendar_request = CalendarAfterDateRequest("run-cal", (date(2026, 12, 1),), preview=False)
    view = service.create_calendar_after_dates(calendar_request)
    assert view.creation_status == "created"
    assert view.artifact_type == "calendar"
    assert view.filename == "itinerary.ics"
    assert view.download_bytes is not None
    assert service.graph_invocations == {}

    repeated = service.create_calendar_after_dates(calendar_request)
    assert repeated.download_bytes == view.download_bytes
    assert len(list(tmp_path.glob("run-cal-calendar-*"))) == 1

    different = service.create_calendar_after_dates(
        CalendarAfterDateRequest("run-cal", (date(2026, 12, 3),), preview=False)
    )
    assert different.download_bytes != view.download_bytes
    assert len(list(tmp_path.glob("run-cal-calendar-*"))) == 2


def test_calendar_after_dates_rejects_unknown_or_missing_inputs(tmp_path):
    from tests.helpers.step7_contracts import CalendarAfterDateRequest

    service = ReferenceApplicationService(output_root=tmp_path)
    service.run(UIRunRequest(query="x", run_id="run-1", execution_mode=UIExecutionMode.CACHED_DEMO))
    with pytest.raises(UnknownRunError):
        service.create_calendar_after_dates(CalendarAfterDateRequest("nope", (date(2026, 1, 1),)))
    with pytest.raises(MissingCalendarDatesError):
        service.create_calendar_after_dates(CalendarAfterDateRequest("run-1", ()))


# ---------------------------------------------------------------------------
# Artifact-button states
# ---------------------------------------------------------------------------


def test_artifact_button_state_only_created_downloads():
    created = UIArtifactView("pdf", "PDF", "itinerary.pdf", "application/pdf", 10, "abc", "created")
    skipped = UIArtifactView("calendar", "تقويم", "itinerary.ics", "text/calendar", 0, None, "skipped")
    failed = UIArtifactView("pdf", "PDF", "itinerary.pdf", "application/pdf", 0, None, "failed")
    assert artifact_button_state(created) == ("download", True)
    assert artifact_button_state(skipped) == ("skipped", False)
    assert artifact_button_state(failed) == ("failed", False)


def test_download_bytes_only_for_safe_created_artifacts(tmp_path):
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    target = run_dir / "answer.txt"
    target.write_bytes(b"data")
    inside = UIArtifactView("raw_text", "نص", "answer.txt", "text/plain", 4, None, "created")
    assert download_payload(inside, target, tmp_path) == b"data"

    outside = tmp_path.parent / "answer.txt"
    outside.write_bytes(b"leak")
    assert download_payload(inside, outside, tmp_path) is None

    skipped = UIArtifactView("calendar", "تقويم", "itinerary.ics", "text/calendar", 0, None, "skipped")
    assert download_payload(skipped, target, tmp_path) is None
    assert download_payload(inside, None, tmp_path) is None


def test_manifest_projection_omits_filesystem_paths(tmp_path):
    result = render_node(render_state(render_itinerary()), render_deps(tmp_path))
    request = UIRunRequest(query="x", run_id="step6-state-run")
    ui_result = build_result_from_state(request, result)
    assert all(isinstance(item, UIArtifactView) for item in ui_result.artifacts)
    assert all(item.download_bytes is None for item in ui_result.artifacts)
    assert any(item.artifact_type == "calendar" for item in ui_result.artifacts)


# ---------------------------------------------------------------------------
# Duplicate-run prevention
# ---------------------------------------------------------------------------


def test_artifact_manager_rejects_duplicate_run_directory(tmp_path):
    ArtifactManager(tmp_path, "run-dedup")
    with pytest.raises(ArtifactError) as error:
        ArtifactManager(tmp_path, "run-dedup")
    assert error.value.category == "duplicate_run"


def test_service_duplicate_run_reuses_result_without_rerunning_graph():
    service = ReferenceApplicationService(
        live_runner=offline_runner(
            make_offline_deps(success_scripts(), rag_service=FakeRAGService(evidence_answer()))
        )
    )
    request = UIRunRequest(query="خطة لرحلة إلى الرياض", run_id="run-dupe")
    first = service.run(request)
    second = service.run(request)
    assert service.graph_invocations["run-dupe"] == 1
    assert second is first or second == first


def test_service_rejects_active_duplicate():
    holder = {"reentrant_result": None}

    class ReentrantRunner:
        def __call__(self, request):
            service.run(request)
            raise AssertionError("reentrant run should not complete")

    service = ReferenceApplicationService(live_runner=ReentrantRunner())
    request = UIRunRequest(query="x", run_id="run-inflight")
    with pytest.raises(DuplicateRunError):
        service.run(request)
    assert holder["reentrant_result"] is None


# ---------------------------------------------------------------------------
# Session isolation
# ---------------------------------------------------------------------------


def test_separate_service_instances_are_isolated():
    first = ReferenceApplicationService(
        live_runner=offline_runner(
            make_offline_deps(success_scripts(), rag_service=FakeRAGService(evidence_answer()))
        )
    )
    second = ReferenceApplicationService()
    request = UIRunRequest(query="خطة لرحلة إلى الرياض", run_id="run-shared", execution_mode=UIExecutionMode.CACHED_DEMO)
    a = first.run(request)
    b = second.run(request)
    assert a.run_id == b.run_id == "run-shared"
    assert first.graph_invocations == {}
    assert second._snapshots and first._snapshots is not second._snapshots


# ---------------------------------------------------------------------------
# Fixture selection
# ---------------------------------------------------------------------------


def test_select_demo_fixture_only_for_explicit_demo_mode():
    assert select_demo_fixture(UIExecutionMode.CACHED_DEMO) == "CIT-DEMO01"
    assert select_demo_fixture(UIExecutionMode.LIVE) is None


def test_demo_run_is_offline_simulated_and_unmistakably_labeled(tmp_path):
    service = ReferenceApplicationService(output_root=tmp_path)
    request = UIRunRequest(
        query="رحلة", run_id="run-demo", execution_mode=UIExecutionMode.CACHED_DEMO
    )
    result = service.run(request)
    assert service.graph_invocations == {}
    assert result.mode.kind is UIModeKind.CACHED_DEMO
    assert result.mode.execution_mode is UIExecutionMode.CACHED_DEMO
    assert all(event.simulated for event in result.progress_events)
    assert result.itinerary is not None
    assert result.sources


def test_representative_fixture_is_a_valid_deterministic_demo_source():
    fixture = representative_fixture()
    again = representative_fixture()
    mapping = fixture.validate_citations()
    assert set(mapping) == {"CIT-DEMO-SPRING-001", "CIT-DEMO-MARKET-002"}
    assert {source.citation_id for source in again.sources} == {source.citation_id for source in fixture.sources}


# ---------------------------------------------------------------------------
# Sanitized errors
# ---------------------------------------------------------------------------


def test_sanitize_text_scrubs_credentials_and_bounds_length():
    scrubbed = sanitize_text("Authorization: Bearer nvapi-1234567890abcdef\napi_key = sk-live-999999\nپن لongtoken123456789012345678")
    assert "nvapi-1234567890" not in scrubbed
    assert "sk-live-999999" not in scrubbed
    assert "longtoken123456789012345678" not in scrubbed
    assert len(sanitize_text("x" * 500)) <= 320


def test_make_error_is_sanitized_and_bounded():
    error = make_error(
        "run-1",
        "retrieve",
        FailureKind.AUTH,
        f"مفتاح سري api_key = nvapi-supersecret و {('y' * 400)}",
        retryable=False,
    )
    assert error.kind == FailureKind.AUTH.value
    assert "nvapi-supersecret" not in error.message
    assert len(error.message) <= 280


def test_safe_chain_message_is_generic():
    message = safe_chain_message(RuntimeError("backend exploded with sk-1234"))
    assert "backend exploded" not in message
    assert "sk-1234" not in message
    assert "تعذّر" in message


def test_node_exception_never_leaks_raw_text_into_result(tmp_path):
    deps = make_offline_deps(success_scripts(), rag_service=ExplodingRAG())
    state = run_pipeline("خطة لرحلة إلى الرياض", dependencies=deps, run_id="run-crash")
    combined = " ".join([*state.get("warnings", []), *[e.message for e in state.get("errors", [])]])
    assert "backend exploded" not in combined
    result = build_result_from_state(
        UIRunRequest(query="خطة لرحلة إلى الرياض", run_id="run-crash"), state
    )
    assert "backend exploded" not in result.error_message
    assert result.error_message in {"", "حدث خطأ أثناء معالجة طلبك؛ يرجى المحاولة مرة أخرى."}


def test_ui_error_message_is_generic_and_empty_on_success():
    assert ui_error_message([], GraphOutcome.COMPLETED.value) == ""
    message = ui_error_message([object()], GraphOutcome.FAILED.value)
    assert "حدث خطأ" in message
    assert "AUTH" not in message and "traceback" not in message.lower()
