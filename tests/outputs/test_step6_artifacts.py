"""Offline acceptance tests for deterministic Step 6 artifact rendering."""

from __future__ import annotations

import ast
import importlib
from dataclasses import replace
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import fitz
import pytest
from icalendar import Calendar

from sard.agent.state import ClaimRecord, ClaimStatus, VerificationResult
from sard.outputs.artifacts import ArtifactError, ArtifactManager
from sard.outputs.calendar import CalendarRenderError, render_calendar
from sard.outputs.raw import render_raw_text
from sard.outputs.schemas import (
    CitationSource,
    Coordinates,
    FieldSupport,
    Itinerary,
    ItineraryDay,
    ItineraryStop,
    TextBlock,
)
from sard.outputs.validation import CitationValidationError, build_verified_render_input


render_module = importlib.import_module("sard.agent.nodes.render")
render_node = render_module.render


CID_ONE = "CIT-STEP6-001"
CID_TWO = "CIT-STEP6-002"


def _sources() -> tuple[CitationSource, ...]:
    return (
        CitationSource(
            CID_ONE,
            "دليل الواحة",
            r"https://example.org/a,b;section?x=1\\2",
            page=4,
            section="المسار؛ الشرقي",
        ),
        CitationSource(CID_TWO, "دليل السوق", "https://example.com/market?lang=ar"),
        CitationSource("CIT-UNUSED-003", "مصدر غير مستخدم", "https://unused.example/"),
    )


def _stop(
    citation_id: str = CID_ONE,
    *,
    start: time | None = time(9, 0),
    end: time | None = time(10, 0),
    time_text: str = "09:00 - 10:00",
    stop_id: str = "stop-one",
    unsupported_optional: bool = False,
) -> ItineraryStop:
    description = TextBlock(
        "وصف موثق؛ يتضمن فاصلة، وفاصلة منقوطة؛ ومسار\\خاص\nوسطرًا ثانياً "
        f"[{citation_id}]",
        (citation_id,),
    )
    practical = TextBlock("ملاحظة عملية موثقة، احفظ هذه الجملة.", (citation_id,))
    accessibility = TextBlock("معلومة إتاحة موثقة.", (citation_id,))
    supports = [
        FieldSupport("title", (citation_id,)),
        FieldSupport("location", (citation_id,)),
        FieldSupport("time", (citation_id,)),
        FieldSupport("description", (citation_id,)),
        FieldSupport("practical_notes", (citation_id,)),
        FieldSupport("accessibility_notes", (citation_id,)),
    ]
    if not unsupported_optional:
        supports.extend(
            [
                FieldSupport("address", (citation_id,)),
                FieldSupport("coordinates", (citation_id,)),
            ]
        )
    return ItineraryStop(
        time=time_text,
        title="المحطة الأولى",
        location="الموقع، شارع؛ مبنى\\1",
        paragraphs=(description,),
        bullets=(practical,),
        notes=(),
        stop_id=stop_id,
        start_time=start,
        end_time=end,
        location_name="الموقع، شارع؛ مبنى\\1",
        address="العنوان المدعوم، مبنى ١",
        coordinates=Coordinates(24.7136, 46.6753),
        description=(description,),
        practical_notes=(practical,),
        accessibility_notes=(accessibility,),
        citation_ids=(citation_id,),
        field_support=tuple(supports),
    )


def _itinerary(
    *,
    dated: bool = True,
    explicit_dates: tuple[date, ...] = (),
    second_stop: ItineraryStop | None = None,
    unsupported_optional: bool = False,
) -> Itinerary:
    first = _stop(unsupported_optional=unsupported_optional)
    second = second_stop or replace(
        _stop(
            CID_TWO,
            start=time(11, 0),
            end=time(12, 0),
            time_text="11:00 - 12:00",
            stop_id="stop-two",
        ),
        title="المحطة الثانية",
        location="السوق القديم",
        location_name="السوق القديم",
    )
    day = ItineraryDay(
        "اليوم الأول",
        date(2026, 11, 1) if dated else None,
        (first, second),
        notes=(TextBlock("ملاحظة اليوم موثقة.", (CID_ONE,)),),
        relative_day_number=1,
        field_support=(FieldSupport("title", (CID_ONE,)), FieldSupport("date", provenance="user_provided")),
    )
    return Itinerary(
        title="رحلة التحقق العربية",
        summary="ملخص موثق للرحلة [CIT-STEP6-001]",
        days=(day,),
        sources=_sources(),
        generated_at=datetime(2026, 8, 13, 9, 30, tzinfo=ZoneInfo("Asia/Riyadh")),
        notes=(),
        run_id="step6-test-run",
        explicit_dates=explicit_dates,
        citation_ids=(),
        field_support=(
            FieldSupport("title", provenance="user_provided"),
            FieldSupport("summary", (CID_ONE,)),
        ),
    )


def _state(
    itinerary: Itinerary | None = None,
    *,
    answer: str = "هذه إجابة عربية موثقة [CIT-STEP6-001] و[CIT-STEP6-002].",
    run_id: str = "step6-state-run",
    caller_dates: list[str] | None = None,
    fallback_events: tuple = (),
    verification_passed: bool = True,
) -> dict:
    return {
        "run_id": run_id,
        "final_answer": answer,
        "draft": answer,
        "itinerary": itinerary,
        "sources": list(_sources()),
        "atomic_claims": [
            ClaimRecord("claim-1", "حقيقة أولى", (CID_ONE,), status=ClaimStatus.SUPPORTED),
            ClaimRecord("claim-2", "حقيقة ثانية", (CID_TWO,), status=ClaimStatus.SUPPORTED),
        ],
        "verification_result": VerificationResult(verification_passed, ("claim-1", "claim-2"), (), ""),
        "verification_exhausted": not verification_passed,
        "retrieval_mode": "hybrid_reranked",
        "fallback_events": list(fallback_events),
        "model_fallback_used": bool(fallback_events),
        "warnings": [],
        "caller_dates": caller_dates or [],
        "travel_dates": [],
    }


def _deps(tmp_path: Path, **kwargs):
    values = {
        "render_artifacts": True,
        "output_root": str(tmp_path),
        "render_checksums": True,
        "preview_calendar": False,
    }
    values.update(kwargs)
    return SimpleNamespace(**values)


def _created(result: dict, artifact_type: str):
    return next(item for item in result["rendered_artifacts"] if item.artifact_type == artifact_type)


def test_complete_three_artifact_success_manifest_and_pdf_mapping(tmp_path):
    result = render_node(
        _state(_itinerary()),
        _deps(tmp_path),
    )

    assert result["graph_outcome"] == "completed"
    assert {item.artifact_type for item in result["rendered_artifacts"]} == {"raw_text", "pdf", "calendar"}
    for item in result["rendered_artifacts"]:
        assert item.creation_status == "created"
        assert Path(item.absolute_path).is_file()
        assert item.path == item.absolute_path
        assert item.size_bytes == Path(item.absolute_path).stat().st_size > 0
        assert len(item.checksum or "") == 64

    pdf = _created(result, "pdf")
    with fitz.open(pdf.absolute_path) as document:
        extracted = "".join(page.get_text() for page in document)
        assert "CIT-STEP6-001" in extracted
        assert "CIT-STEP6-002" in extracted
        assert "https://example.org/a,b;section?x=1" in extracted
        assert all(
            0 <= block[0] <= block[2] <= page.rect.width
            and 0 <= block[1] <= block[3] <= page.rect.height
            for page in document
            for block in page.get_text("blocks")
        )


def test_pdf_bytes_are_deterministic_for_identical_verified_input(monkeypatch, tmp_path):
    from sard.outputs.pdf import render_pdf

    fixture = _itinerary()
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    monkeypatch.setenv("SARD_PDF_OUTPUT_ROOT", str(first_root))
    render_pdf(fixture, "same.pdf")
    monkeypatch.setenv("SARD_PDF_OUTPUT_ROOT", str(second_root))
    render_pdf(fixture, "same.pdf")
    assert (first_root / "same.pdf").read_bytes() == (second_root / "same.pdf").read_bytes()


def test_calendar_parsing_arabic_timezone_uids_and_source_urls(tmp_path):
    itinerary = _itinerary()
    first = render_calendar(itinerary)
    second = render_calendar(itinerary)
    parsed = Calendar.from_ical(first.data)
    events = [component for component in parsed.subcomponents if component.name == "VEVENT"]

    assert len(events) == 2
    assert first.event_uids == second.event_uids
    assert len(set(first.event_uids)) == 2
    assert any("رحلة" in str(parsed.get("x-wr-calname")) for _ in [0])
    for event in events:
        assert event["DTSTART"].params["TZID"] == "Asia/Riyadh"
        assert str(event.decoded("dtstart").tzinfo) == "Asia/Riyadh"
        assert "CIT-STEP6-00" in str(event.decoded("description"))
        assert "https://" in str(event.decoded("description"))
        assert any(character in str(event.decoded("summary")) for character in "المحطة")

    wire = first.data.decode("utf-8")
    assert "\\," in wire
    assert "\\;" in wire
    assert "\\\\" in wire
    assert "\\n" in wire

    duplicate_stop_ids = replace(
        itinerary,
        days=(replace(itinerary.days[0], stops=tuple(replace(stop, stop_id="same-id") for stop in itinerary.days[0].stops)),),
    )
    duplicate_ids = render_calendar(duplicate_stop_ids).event_uids
    assert len(duplicate_ids) == len(set(duplicate_ids))


def test_calendar_dates_are_required_and_relative_labels_are_not_dates():
    undated = _itinerary(dated=False)
    with pytest.raises(CalendarRenderError) as error:
        render_calendar(undated)
    assert error.value.category == "missing_dates"

    dated_by_caller = _itinerary(dated=False, explicit_dates=(date(2026, 12, 1),))
    result = render_calendar(dated_by_caller)
    parsed = Calendar.from_ical(result.data)
    event = next(component for component in parsed.subcomponents if component.name == "VEVENT")
    assert event.decoded("dtstart").date() == date(2026, 12, 1)

    preview = render_calendar(dated_by_caller, preview=True)
    preview_calendar = Calendar.from_ical(preview.data)
    assert str(preview_calendar.get("x-wr-calname")).startswith("معاينة:")

    overridden = render_calendar(_itinerary(explicit_dates=(date(2027, 1, 5),)))
    overridden_event = next(
        component for component in Calendar.from_ical(overridden.data).subcomponents if component.name == "VEVENT"
    )
    assert overridden_event.decoded("dtstart").date() == date(2027, 1, 5)


def test_invalid_times_are_rejected_and_overlaps_warn():
    invalid = _itinerary(
        second_stop=replace(
            _stop(CID_TWO, start=time(12), end=time(11), time_text="12:00 - 11:00", stop_id="bad-time"),
            title="وقت غير صالح",
        )
    )
    result = render_calendar(invalid)
    parsed = Calendar.from_ical(result.data)
    events = [component for component in parsed.subcomponents if component.name == "VEVENT"]
    assert len(events) == 1
    assert any("النهاية" in warning for warning in result.warnings)

    all_invalid = replace(
        invalid,
        days=(replace(invalid.days[0], stops=(invalid.days[0].stops[1],)),),
    )
    with pytest.raises(CalendarRenderError, match="موقّتة"):
        render_calendar(all_invalid)

    overlap = _itinerary(
        second_stop=replace(
            _stop(CID_TWO, start=time(9, 30), end=time(11), time_text="09:30 - 11:00", stop_id="overlap"),
            title="محطة متداخلة",
        )
    )
    result = render_calendar(overlap)
    assert any("تداخل" in warning for warning in result.warnings)


def test_raw_utf8_sources_status_and_no_hidden_reasoning():
    result = render_raw_text(
        "إجابة عربية آمنة [CIT-STEP6-001].",
        _sources()[:2],
        verification_status="evidence_limited",
        retrieval_mode="dense_only",
        degraded_notice="تم استخدام وضع محدود بالأدلة.",
    )
    text = result.data.decode("utf-8")
    assert "إجابة عربية" in text
    assert "CIT-STEP6-001" in text
    assert "https://example.org" in text
    assert "evidence_limited" in text
    assert "chain-of-thought" not in text.lower()
    assert "system prompt" not in text.lower()
    assert "api_key" not in text.lower()

    with pytest.raises(ValueError, match="CIT-UNKNOWN-999"):
        render_raw_text("نص [CIT-UNKNOWN-999]", _sources()[:2])


def test_citation_validation_rejects_unknown_and_removes_unsupported_fields():
    state = _state(_itinerary(unsupported_optional=True), answer="معلومة [CIT-STEP6-001].")
    verified = build_verified_render_input(state)
    assert verified.itinerary is not None
    stop = verified.itinerary.days[0].stops[0]
    assert stop.address is None
    assert stop.coordinates is None
    assert stop.effective_description
    assert all(source.citation_id != "CIT-UNUSED-003" for source in verified.sources)

    unknown = _state(_itinerary(), answer="معلومة [CIT-UNKNOWN-999].")
    with pytest.raises(CitationValidationError):
        build_verified_render_input(unknown, allow_partial=False)

    partial = build_verified_render_input(unknown, allow_partial=True)
    assert "CIT-UNKNOWN-999" not in partial.final_answer
    assert partial.validation_error


def test_artifact_manager_safe_names_atomic_writes_and_checksums(tmp_path):
    manager = ArtifactManager(tmp_path, "safe-run", checksums=True)
    result = manager.write_bytes(
        "محتوى عربي".encode("utf-8"),
        filename="answer.txt",
        artifact_type="raw_text",
        display_label="إجابة",
        mime_type="text/plain; charset=utf-8",
    )
    assert result.creation_status == "created"
    assert result.absolute_path == str(tmp_path / "safe-run" / "answer.txt")
    assert result.checksum
    assert not list((tmp_path / "safe-run").glob("*.tmp"))
    with pytest.raises(ArtifactError, match="overwrite"):
        manager.write_bytes(b"new", filename="answer.txt", artifact_type="raw_text", display_label="x", mime_type="text/plain")
    with pytest.raises(ArtifactError, match="safe ASCII"):
        manager.write_bytes(b"x", filename="../escape.txt", artifact_type="raw_text", display_label="x", mime_type="text/plain")
    with pytest.raises(ArtifactError, match="safe ASCII"):
        ArtifactManager(tmp_path, "../outside")
    assert not (tmp_path / "escape.txt").exists()


def test_missing_dates_allow_pdf_and_raw_but_skip_calendar(tmp_path):
    result = render_node(_state(_itinerary(dated=False)), _deps(tmp_path))
    assert result["graph_outcome"] == "partial"
    assert _created(result, "pdf").creation_status == "created"
    assert _created(result, "raw_text").creation_status == "created"
    calendar = next(item for item in result["rendered_artifacts"] if item.artifact_type == "calendar")
    assert calendar.creation_status == "skipped"
    assert calendar.error_category == "missing_dates"
    assert any("لم تُخترع" in warning for warning in calendar.warnings)


def test_pdf_failure_does_not_prevent_raw_or_calendar(tmp_path, monkeypatch):
    def fail_pdf(*args, **kwargs):
        raise RuntimeError("test pdf failure")

    monkeypatch.setattr(render_module, "render_pdf", fail_pdf)
    result = render_node(_state(_itinerary()), _deps(tmp_path))
    assert result["graph_outcome"] == "partial"
    assert _created(result, "raw_text").creation_status == "created"
    assert _created(result, "calendar").creation_status == "created"
    pdf = _created(result, "pdf")
    assert pdf.creation_status == "failed"
    assert pdf.error_category == "runtimeerror"


def test_calendar_failure_does_not_prevent_pdf_or_raw(tmp_path, monkeypatch):
    def fail_calendar(*args, **kwargs):
        raise CalendarRenderError("test_calendar", "calendar failed")

    monkeypatch.setattr(render_module, "render_calendar", fail_calendar)
    result = render_node(_state(_itinerary()), _deps(tmp_path))
    assert result["graph_outcome"] == "partial"
    assert _created(result, "pdf").creation_status == "created"
    assert _created(result, "raw_text").creation_status == "created"
    calendar = _created(result, "calendar")
    assert calendar.creation_status == "skipped"
    assert calendar.error_category == "test_calendar"


def test_pdf_only_success_is_reported_when_other_artifacts_fail(tmp_path, monkeypatch):
    def fail_raw(*args, **kwargs):
        raise RuntimeError("test raw failure")

    def fail_calendar(*args, **kwargs):
        raise CalendarRenderError("test_calendar", "calendar failed")

    monkeypatch.setattr(render_module, "render_raw_text", fail_raw)
    monkeypatch.setattr(render_module, "render_calendar", fail_calendar)
    result = render_node(_state(_itinerary()), _deps(tmp_path))
    assert result["graph_outcome"] == "partial"
    assert _created(result, "pdf").creation_status == "created"
    assert _created(result, "raw_text").creation_status == "failed"
    assert _created(result, "calendar").creation_status == "skipped"


def test_degraded_mode_and_model_fallback_are_disclosed(tmp_path):
    class Fallback:
        degraded = True

    result = render_node(
        _state(_itinerary(), fallback_events=(Fallback(),), verification_passed=False),
        _deps(tmp_path),
    )
    assert result["graph_outcome"] == "partial"
    assert result["itinerary"].verification_status.value == "evidence_limited"
    assert result["itinerary"].model_fallback_used is True
    assert result["itinerary"].degraded_notice
    raw_path = _created(result, "raw_text").absolute_path
    assert "evidence_limited" in Path(raw_path).read_text(encoding="utf-8")
    with fitz.open(_created(result, "pdf").absolute_path) as document:
        extracted = "".join(page.get_text() for page in document)
        import arabic_reshaper

        assert arabic_reshaper.reshape("تنبيه") in extracted


def test_renderers_have_no_provider_or_hidden_reasoning_dependencies():
    forbidden = ("langchain", "nvidia", "zvec", "sard.ui", "retriever", "reranker")
    for filename in ("artifacts.py", "calendar.py", "raw.py", "schemas.py", "validation.py"):
        tree = ast.parse((Path("sard/outputs") / filename).read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name.lower() for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module.lower())
        assert not any(any(token in name for token in forbidden) for name in imported)


def test_rendered_artifacts_use_explicit_mime_types_and_safe_absolute_paths(tmp_path):
    result = render_node(_state(_itinerary()), _deps(tmp_path))
    run_dir = (tmp_path / "step6-state-run").resolve()
    expected = {
        "pdf": "application/pdf",
        "calendar": "text/calendar; charset=utf-8",
        "raw_text": "text/plain; charset=utf-8",
    }
    for item in result["rendered_artifacts"]:
        assert item.mime_type == expected[item.artifact_type]
        assert Path(item.absolute_path).resolve().parent == run_dir
