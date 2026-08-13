"""Offline determinism, fixture-selection, and isolation tests for the cached demo.

These tests prove Agent C's contract:

- the demo is deterministic (same run ID + dates => identical events, citations,
  itinerary, answer, and byte-for-byte artifact bytes/checksums across roots);
- fixture selection serves only the exact hero query and fabricates nothing else;
- the cached-demo mode and simulated progress are unmistakable;
- artifact download bytes exist only for ``created`` items after a path
  containment check;
- importing the demo never pulls in live graph/RAG/config/UI clients.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from icalendar import Calendar

import sard.application.demo as demo
from sard.application.contracts import UIExecutionMode, UIModeKind, UIProgressState, UIRunRequest, UIStage
from sard.application.demo import DEMO_DEFAULT_DATES, DEMO_WARNING, HERO_QUERY, DemoQueryUnavailable
from sard.outputs.artifacts import failed_artifact, skipped_artifact
from sard.outputs.schemas import CITATION_ID_RE

SAFE_RUN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _request(
    *,
    run_id: str = "demo-abc123",
    query: str = HERO_QUERY,
    trip_dates: tuple[date, ...] = (),
    preferences: tuple[str, ...] = (),
    render_artifacts: bool = True,
) -> UIRunRequest:
    return UIRunRequest(
        query=query,
        run_id=run_id,
        trip_dates=trip_dates,
        preferences=preferences,
        execution_mode=UIExecutionMode.CACHED_DEMO,
        render_artifacts=render_artifacts,
    )


# --------------------------------------------------------------------------- fixture selection


def test_is_hero_query_matches_exact_only():
    assert demo.is_hero_query(HERO_QUERY)
    assert demo.is_hero_query(f"  {HERO_QUERY}  ")
    assert demo.is_hero_query("أنشئ برنامجًا   سياحيًا تراثيًا لمدة يومين في المنطقة الشرقية")
    assert not demo.is_hero_query("")
    assert not demo.is_hero_query("أنشئ برنامجًا سياحيًا لمدة ثلاثة أيام في المنطقة الشرقية")
    assert not demo.is_hero_query("ما هي أفضل المطاعم في الرياض؟")


def test_make_demo_run_id_deterministic_and_safe():
    first = demo.make_demo_run_id()
    second = demo.make_demo_run_id()
    assert first == second
    assert first.startswith("demo-")
    assert SAFE_RUN_RE.fullmatch(first)
    dated = demo.make_demo_run_id(trip_dates=(date(2026, 11, 1),))
    assert dated != first
    assert demo.make_demo_run_id(trip_dates=(date(2026, 11, 1),)) == dated


def test_demo_fixture_deterministic_and_validated():
    first = demo.demo_fixture(run_id="demo-abc123")
    second = demo.demo_fixture(run_id="demo-abc123")
    assert first == second
    assert first.itinerary == second.itinerary
    first.itinerary.validate_citations()
    assert first.retrieval_mode == "hybrid_reranked"
    assert first.coverage_ratio == 1.0
    known = {source.citation_id for source in first.sources}
    assert all(CITATION_ID_RE.fullmatch(cid) for cid in known)
    assert set(first.itinerary.all_citation_ids()) <= known


# --------------------------------------------------------------------------- offline determinism


def test_build_demo_result_offline_determinism(tmp_path):
    first = demo.build_demo_result(_request(), output_root=tmp_path / "a")
    second = demo.build_demo_result(_request(), output_root=tmp_path / "b")
    assert first == second
    assert first.run_id == "demo-abc123"
    assert first.graph_outcome == "completed"
    assert first.error_message == ""


def test_artifact_bytes_stable_across_roots(tmp_path):
    first = demo.build_demo_result(_request(), output_root=tmp_path / "one")
    second = demo.build_demo_result(_request(), output_root=tmp_path / "two")
    for a, b in zip(first.artifacts, second.artifacts):
        assert a.download_bytes == b.download_bytes
        assert a.checksum == b.checksum


def test_dates_change_calendar_and_pdf_but_not_raw_text(tmp_path):
    default = demo.build_demo_result(_request(), output_root=tmp_path / "a")
    changed = demo.build_demo_result(
        _request(trip_dates=(date(2026, 12, 20), date(2026, 12, 21))),
        output_root=tmp_path / "b",
    )

    def by_type(result, artifact_type):
        return next(a for a in result.artifacts if a.artifact_type == artifact_type)

    assert by_type(default, "raw_text").download_bytes == by_type(changed, "raw_text").download_bytes
    assert by_type(default, "calendar").download_bytes != by_type(changed, "calendar").download_bytes
    assert by_type(default, "pdf").download_bytes != by_type(changed, "pdf").download_bytes


# --------------------------------------------------------------------------- cached-demo / simulated mode


def test_progress_events_all_simulated_and_well_formed():
    events = demo.build_demo_progress("demo-abc123")
    assert events
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert all(event.run_id == "demo-abc123" for event in events)
    assert all(event.simulated for event in events)
    assert all(event.stage in UIStage for event in events)
    assert all(event.state in UIProgressState for event in events)
    assert all(event.event_kind in {"waiting", "started", "completed", "graph_completed"} for event in events)
    assert all(event.summary.strip() for event in events)
    for event in events:
        parsed = datetime.fromisoformat(event.timestamp)
        assert parsed.tzinfo is not None
    assert events[0].stage is UIStage.UNDERSTAND
    assert events[0].state is UIProgressState.WAITING
    assert events[-1].stage is UIStage.RENDER
    assert events[-1].state is UIProgressState.COMPLETED
    assert {event.stage for event in events} == set(UIStage)
    assert demo.build_demo_progress("demo-abc123") == events


def test_mode_is_cached_demo_unmistakable(tmp_path):
    result = demo.build_demo_result(_request(), output_root=tmp_path)
    assert result.mode.kind is UIModeKind.CACHED_DEMO
    assert result.mode.execution_mode is UIExecutionMode.CACHED_DEMO
    assert result.mode.model_fallback_used is False
    assert result.mode.retrieval_mode == "hybrid_reranked"
    assert result.mode.model_routes
    assert all(route.used_fallback is False for route in result.mode.model_routes)
    assert all(route.resolved_model.startswith("demo-") for route in result.mode.model_routes)
    assert DEMO_WARNING in result.warnings
    assert all(event.simulated for event in result.progress_events)
    assert "تجريبي" in result.final_answer or DEMO_WARNING in result.warnings


def test_sources_stable_citations_and_metadata(tmp_path):
    result = demo.build_demo_result(_request(), output_root=tmp_path)
    assert len(result.sources) == 4
    assert [source.citation_id for source in result.sources] == [
        "CIT-DEMO-EAST-01",
        "CIT-DEMO-EAST-02",
        "CIT-DEMO-EAST-03",
        "CIT-DEMO-EAST-04",
    ]
    assert all(source.metadata_complete for source in result.sources)
    assert all(source.citation_verified for source in result.sources)
    assert all(source.title.strip() for source in result.sources)
    assert all(source.url.startswith("https://example.org/offline-demo/") for source in result.sources)


def test_itinerary_verified_with_default_dates(tmp_path):
    result = demo.build_demo_result(_request(), output_root=tmp_path)
    itinerary = result.itinerary
    assert itinerary is not None
    itinerary.validate_citations()
    assert itinerary.verification_status.value == "verified"
    assert itinerary.explicit_dates == DEMO_DEFAULT_DATES
    assert len(itinerary.days) == 2
    assert all(day.date is not None for day in itinerary.days)
    assert itinerary.run_id == "demo-abc123"


def test_requested_dates_override_demo_dates(tmp_path):
    dates = (date(2026, 12, 20), date(2026, 12, 21))
    result = demo.build_demo_result(_request(trip_dates=dates), output_root=tmp_path)
    assert result.itinerary.explicit_dates == dates


# --------------------------------------------------------------------------- artifacts


def test_artifacts_created_with_download_bytes(tmp_path):
    result = demo.build_demo_result(_request(), output_root=tmp_path)
    assert len(result.artifacts) == 3
    by_type = {artifact.artifact_type: artifact for artifact in result.artifacts}
    assert set(by_type) == {"raw_text", "pdf", "calendar"}
    for artifact in result.artifacts:
        assert artifact.creation_status == "created"
        assert artifact.download_bytes is not None
        assert artifact.size_bytes == len(artifact.download_bytes)
        assert SHA256_RE.fullmatch(artifact.checksum or "")
        assert artifact.error_category is None
    assert by_type["raw_text"].filename == "answer.txt"
    assert by_type["pdf"].filename == "itinerary.pdf"
    assert by_type["calendar"].filename == "itinerary.ics"


def test_raw_text_contains_verified_answer(tmp_path):
    result = demo.build_demo_result(_request(), output_root=tmp_path)
    raw = next(artifact for artifact in result.artifacts if artifact.artifact_type == "raw_text")
    text = raw.download_bytes.decode("utf-8")
    assert "المنطقة الشرقية" in text
    assert "[CIT-DEMO-EAST-01]" in text
    assert "حالة التحقق: verified" in text
    assert DEMO_WARNING in text


def test_calendar_parses_with_four_events(tmp_path):
    result = demo.build_demo_result(_request(), output_root=tmp_path)
    calendar_artifact = next(artifact for artifact in result.artifacts if artifact.artifact_type == "calendar")
    parsed = Calendar.from_ical(calendar_artifact.download_bytes)
    events = [component for component in parsed.subcomponents if component.name == "VEVENT"]
    assert len(events) == 4
    uids = {event.get("uid").to_ical().decode() for event in events}
    assert len(uids) == 4


def test_pdf_has_pdf_header(tmp_path):
    result = demo.build_demo_result(_request(), output_root=tmp_path)
    pdf = next(artifact for artifact in result.artifacts if artifact.artifact_type == "pdf")
    assert pdf.download_bytes.startswith(b"%PDF-")


def test_pdf_creation_date_is_pinned_to_demo_timestamp(tmp_path):
    result = demo.build_demo_result(_request(), output_root=tmp_path)
    pdf = next(artifact for artifact in result.artifacts if artifact.artifact_type == "pdf")
    pinned = b"/CreationDate (D:20260813100000+00'00')"
    assert pdf.download_bytes.count(pinned) == 1
    assert pdf.download_bytes.count(b"/ModDate (D:20260813100000+00'00')") == 1


def test_only_expected_files_written_offline(tmp_path):
    demo.build_demo_result(_request(), output_root=tmp_path)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["demo-abc123"]
    run_dir = tmp_path / "demo-abc123"
    assert sorted(path.name for path in run_dir.iterdir()) == ["answer.txt", "itinerary.ics", "itinerary.pdf"]


def test_download_bytes_only_for_created(tmp_path):
    failed = demo._artifact_view(
        failed_artifact(
            artifact_type="pdf",
            display_label="برنامج الرحلة PDF",
            filename="itinerary.pdf",
            mime_type="application/pdf",
            category="test_failure",
            warning="فشل تجريبي.",
        ),
        tmp_path,
    )
    skipped = demo._artifact_view(
        skipped_artifact(
            artifact_type="calendar",
            display_label="تقويم الرحلة",
            filename="itinerary.ics",
            mime_type="text/calendar; charset=utf-8",
            category="missing_dates",
            warning="تواريخ ناقصة.",
        ),
        tmp_path,
    )
    assert failed.creation_status == "failed"
    assert skipped.creation_status == "skipped"
    assert failed.download_bytes is None
    assert skipped.download_bytes is None
    assert failed.error_category == "test_failure"
    assert skipped.error_category == "missing_dates"


def test_contained_bytes_rejects_path_escape(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    inside = run_dir / "answer.txt"
    inside.write_bytes(b"ok")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"secret")
    assert demo._read_contained_bytes(str(inside), run_dir) == b"ok"
    assert demo._read_contained_bytes(str(outside), run_dir) is None
    assert demo._read_contained_bytes(None, run_dir) is None


# --------------------------------------------------------------------------- refusal and streaming


def test_non_hero_query_rejected_without_writes(tmp_path):
    with pytest.raises(DemoQueryUnavailable):
        demo.build_demo_result(_request(query="ماذا تفعل في الرياض؟"), output_root=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_live_execution_mode_rejected(tmp_path):
    request = UIRunRequest(query=HERO_QUERY, run_id="demo-abc123", execution_mode=UIExecutionMode.LIVE)
    with pytest.raises(ValueError):
        demo.build_demo_result(request, output_root=tmp_path)


def test_stream_demo_yields_events_then_result(tmp_path):
    items = list(demo.stream_demo(_request(), output_root=tmp_path))
    result = items[-1]
    yielded_events = items[:-1]
    assert yielded_events
    assert yielded_events == list(result.progress_events)
    assert result.mode.kind is UIModeKind.CACHED_DEMO
    assert result.mode.execution_mode is UIExecutionMode.CACHED_DEMO


# --------------------------------------------------------------------------- isolation from live clients


def test_no_live_dependencies_imported():
    code = (
        "import sys\n"
        "import sard.application.demo\n"
        "bad = [m for m in sys.modules "
        "if m.startswith(('sard.rag', 'sard.config', 'sard.agent', 'sard.ui'))]\n"
        "assert not bad, bad\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
