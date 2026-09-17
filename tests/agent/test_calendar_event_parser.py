"""DG-3: explicit dated-event extraction for chat-driven ICS calendars.

The parser converts user-stated dates/times into the explicit-events ICS
schema. It must never invent dates, times, or time zones: undated, untimed,
and invalid entries are skipped with warnings so the caller falls through
to the curated heritage lookup (honest ``no_match`` for unknown topics).
"""

from __future__ import annotations

from sard.agent.calendar_event_parser import extract_calendar_events


def test_iso_date_with_range_yields_structured_event():
    events, warnings = extract_calendar_events(
        "Create an ICS calendar for my trip: Riyadh visit on 2026-03-05 10:00-11:00",
        fallback_title="trip",
    )
    assert warnings == []
    assert len(events) == 1
    event = events[0]
    assert event["start_date"] == "2026-03-05"
    assert event["start_time"] == "10:00"
    assert event["end_time"] == "11:00"
    assert "Riyadh" in event["title"]


def test_two_dates_get_their_own_ranges():
    events, warnings = extract_calendar_events(
        "Diriyah tour on 2026-03-05 09:00 to 10:30 and museum on 6 March 2026 14:00-15:00"
    )
    assert warnings == []
    assert len(events) == 2
    by_date = {e["start_date"]: e for e in events}
    assert by_date["2026-03-05"]["start_time"] == "09:00"
    assert by_date["2026-03-05"]["end_time"] == "10:30"
    assert by_date["2026-03-06"]["start_time"] == "14:00"
    assert by_date["2026-03-06"]["end_time"] == "15:00"


def test_date_without_time_range_is_skipped_with_warning():
    events, warnings = extract_calendar_events("Calendar for 2026-03-05 without times")
    assert events == []
    assert any("2026-03-05" in w for w in warnings)


def test_end_before_start_is_skipped_with_warning():
    events, warnings = extract_calendar_events("Add event on 2026-03-05 14:00-11:00")
    assert events == []
    assert any("2026-03-05" in w for w in warnings)


def test_yearless_date_is_not_invented():
    events, warnings = extract_calendar_events("Trip on March 5 10:00-11:00")
    assert events == []


def test_bare_topic_yields_no_events():
    events, warnings = extract_calendar_events("Just tell me about heritage festivals")
    assert events == []
    assert warnings == []


def test_bad_input_never_raises():
    assert extract_calendar_events("") == ([], [])
    assert extract_calendar_events(None) == ([], [])


def test_explicit_events_render_parseable_ics(tmp_path):
    """Orchestrator explicit-events stack honors parser output incl. warnings."""
    from icalendar import Calendar

    from sard.outputs.orchestrator import (
        ArtifactOrchestrator,
        ArtifactRequest,
        FileSystemArtifactStore,
    )

    events, _ = extract_calendar_events("Visit AlUla on 2026-11-20 09:00-10:00")
    assert len(events) == 1
    store = FileSystemArtifactStore(tmp_path)
    orch = ArtifactOrchestrator(store)
    res = orch.generate_artifact(ArtifactRequest(
        format="ics", kind="calendar", title="trip", topic="AlUla trip",
        raw_text="Visit AlUla on 2026-11-20 09:00-10:00",
        content_data={"events": events, "warnings": ["caller note"]},
        metadata={"run_id": "run-ics-1", "version": 1},
    ))
    assert res.status == "created"
    assert res.download_url is not None
    data, _, _ = store.get_bytes(res.filename)
    cal = Calendar.from_ical(data)
    vevents = [c for c in cal.walk() if c.name == "VEVENT"]
    assert len(vevents) == 1
    assert vevents[0].get("dtstart").dt.isoformat() == "2026-11-20T09:00:00+03:00"
    assert vevents[0].get("dtend").dt.isoformat() == "2026-11-20T10:00:00+03:00"
    assert res.preview is not None
    assert res.preview.get("events_count") == 1
    assert "caller note" in (res.preview.get("warnings") or [])
