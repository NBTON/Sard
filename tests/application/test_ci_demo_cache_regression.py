"""CI regression tests for demo-cache line-ending resilience, cryptographic integrity rejection, and retained calendar generation."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from sard.application import SardApplicationService, build_demo_result
from sard.application.contracts import (
    CalendarAfterDateRequest,
    UIExecutionMode,
    UIModeKind,
    UIRunRequest,
    UIRunResult,
)
from sard.application.demo import (
    DEMO_CACHE_ROOT,
    HERO_QUERY,
    DemoCacheUnavailable,
    load_precached_artifacts,
)


def _setup_simulated_cache(
    root: Path,
    *,
    answer_bytes: bytes | None = None,
    calendar_bytes: bytes | None = None,
) -> dict:
    manifest_data = json.loads((DEMO_CACHE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    (root / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")

    if answer_bytes is None:
        # Simulate Windows git checkout: answer.txt with CRLF line endings
        raw_lf = (DEMO_CACHE_ROOT / "answer.txt").read_bytes().replace(b"\r\n", b"\n")
        answer_bytes = raw_lf.replace(b"\n", b"\r\n")
    (root / "answer.txt").write_bytes(answer_bytes)

    if calendar_bytes is None:
        # Simulate Linux git checkout of git blob: itinerary.ics with LF line endings
        cal_crlf = (DEMO_CACHE_ROOT / "itinerary.ics").read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        calendar_bytes = cal_crlf.replace(b"\r\n", b"\n")
    (root / "itinerary.ics").write_bytes(calendar_bytes)

    (root / "itinerary.pdf").write_bytes((DEMO_CACHE_ROOT / "itinerary.pdf").read_bytes())
    return manifest_data


def test_demo_cache_line_endings_cross_platform_resilience(tmp_path: Path):
    """Verify that load_precached_artifacts succeeds regardless of git line ending translations."""
    manifest_data = _setup_simulated_cache(tmp_path)

    artifacts = load_precached_artifacts(tmp_path)
    assert len(artifacts) == 3
    by_type = {a.artifact_type: a for a in artifacts}
    assert set(by_type) == {"raw_text", "pdf", "calendar"}

    raw = by_type["raw_text"]
    assert raw.creation_status == "created"
    assert raw.size_bytes == 2206
    assert raw.checksum == manifest_data["artifacts"]["raw_text"]["sha256"]
    assert b"\r\n" not in (raw.download_bytes or b"")

    cal = by_type["calendar"]
    assert cal.creation_status == "created"
    assert cal.size_bytes == 3885
    assert cal.checksum == manifest_data["artifacts"]["calendar"]["sha256"]
    assert cal.download_bytes and cal.download_bytes.startswith(b"BEGIN:VCALENDAR\r\n")
    assert cal.download_bytes.endswith(b"END:VCALENDAR\r\n")


def test_demo_cache_substantive_mutation_rejected_by_integrity_check(tmp_path: Path):
    """Verify that substantive content mutations fail integrity checks and raise DemoCacheUnavailable."""
    # 1. Mutate answer.txt substantive content with same length (preserves byte count, changes SHA-256)
    raw_crlf = (DEMO_CACHE_ROOT / "answer.txt").read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    mutated_raw = raw_crlf.replace(b"\xd9\x87", b"\xd9\x88", 1)
    assert len(mutated_raw) == len(raw_crlf)
    assert mutated_raw != raw_crlf

    path_raw_mutated = tmp_path / "raw_mutated"
    path_raw_mutated.mkdir()
    _setup_simulated_cache(path_raw_mutated, answer_bytes=mutated_raw)

    with pytest.raises(DemoCacheUnavailable, match=r"packaged raw_text integrity check failed"):
        load_precached_artifacts(path_raw_mutated)

    # 2. Mutate itinerary.ics substantive content with same length (preserves byte count, changes SHA-256)
    cal_lf = (DEMO_CACHE_ROOT / "itinerary.ics").read_bytes().replace(b"\r\n", b"\n")
    mutated_cal = cal_lf.replace(b"SUMMARY:", b"XUMMARY:", 1)
    assert len(mutated_cal) == len(cal_lf)
    assert mutated_cal != cal_lf

    path_cal_mutated = tmp_path / "cal_mutated"
    path_cal_mutated.mkdir()
    _setup_simulated_cache(path_cal_mutated, calendar_bytes=mutated_cal)

    with pytest.raises(DemoCacheUnavailable, match=r"packaged calendar integrity check failed"):
        load_precached_artifacts(path_cal_mutated)


def test_retained_calendar_offline_generation(monkeypatch, tmp_path: Path):
    """Verify that cached demo retained run can generate calendar without live dependencies."""
    monkeypatch.setenv("SARD_OUTPUT_ROOT", str(tmp_path))

    def forbidden_live(*args, **kwargs):
        raise AssertionError("live dependencies must not be invoked for cached demo")

    monkeypatch.setattr("sard.application.service.default_dependencies", forbidden_live)

    service = SardApplicationService(cached_demo_provider=build_demo_result)
    request = UIRunRequest(
        query=HERO_QUERY,
        run_id="ci-demo-retained-test",
        execution_mode=UIExecutionMode.CACHED_DEMO,
    )

    items = list(service.stream_run(request))
    result = items[-1]
    assert isinstance(result, UIRunResult)
    assert result.mode.kind is UIModeKind.CACHED_DEMO
    assert result.graph_outcome == "completed"

    calendar_request = CalendarAfterDateRequest(
        run_id=result.run_id,
        dates=(date(2027, 2, 1), date(2027, 2, 2)),
    )
    first = service.create_calendar_after_dates(calendar_request)
    second = service.create_calendar_after_dates(calendar_request)
    assert first is second
    assert first.creation_status == "created"
    assert first.download_bytes and first.download_bytes.startswith(b"BEGIN:VCALENDAR")
