from __future__ import annotations

from datetime import date

from sard.application import SardApplicationService, build_demo_result
from sard.application.contracts import (
    CalendarAfterDateRequest,
    UIExecutionMode,
    UIModeKind,
    UIProgressEvent,
    UIRunRequest,
    UIRunResult,
)
from sard.application.demo import HERO_QUERY


def test_public_service_runs_offline_demo_and_retained_calendar_without_live_dependencies(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("SARD_OUTPUT_ROOT", str(tmp_path))

    def forbidden_live_dependencies(*args, **kwargs):
        raise AssertionError("cached demo must not resolve live dependencies")

    monkeypatch.setattr(
        "sard.application.service.default_dependencies",
        forbidden_live_dependencies,
    )
    service = SardApplicationService(cached_demo_provider=build_demo_result)
    request = UIRunRequest(
        query=HERO_QUERY,
        run_id="step7-integrated-demo",
        execution_mode=UIExecutionMode.CACHED_DEMO,
    )

    items = list(service.stream_run(request))
    result = items[-1]
    assert isinstance(result, UIRunResult)
    assert result.mode.kind is UIModeKind.CACHED_DEMO
    assert all(
        isinstance(event, UIProgressEvent) and event.simulated
        for event in items[:-1]
    )
    assert all(not hasattr(artifact, "absolute_path") for artifact in result.artifacts)
    assert all(
        artifact.download_bytes
        for artifact in result.artifacts
        if artifact.creation_status == "created"
    )

    calendar_request = CalendarAfterDateRequest(
        run_id=result.run_id,
        dates=(date(2027, 1, 10), date(2027, 1, 11)),
    )
    first = service.create_calendar_after_dates(calendar_request)
    second = service.create_calendar_after_dates(calendar_request)
    assert first is second
    assert first.creation_status == "created"
    assert first.download_bytes and first.download_bytes.startswith(b"BEGIN:VCALENDAR")

    # A Streamlit rerender asking for the same run returns the retained result
    # and cannot rebuild demo artifacts or invoke a live graph.
    assert list(service.stream_run(request)) == [result]
