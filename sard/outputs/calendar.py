"""Offline RFC 5545 calendar rendering from verified itinerary fields."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time as time_type
from typing import Optional
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event

from sard.outputs.schemas import Itinerary, ItineraryDay, ItineraryStop


TIMEZONE = "Asia/Riyadh"
MIME_TYPE = "text/calendar; charset=utf-8"
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_TIME_RE = re.compile(
    r"(?P<h>\d{1,2})\s*[:٫.]\s*(?P<m>\d{2})"
    r"(?:\s*(?P<ampm>صباحًا|صباحاً|صباحا|مساءً|مساءاً|مساء|AM|PM))?",
    re.I,
)
_RANGE_RE = re.compile(r"^\s*(?P<start>.+?)\s*(?:-|–|—|إلى|الى)\s*(?P<end>.+?)\s*$", re.I)


class CalendarRenderError(Exception):
    def __init__(self, category: str, message: str, warnings: tuple[str, ...] = ()):
        super().__init__(message)
        self.category = category
        self.warnings = warnings


@dataclass(frozen=True)
class CalendarRenderResult:
    data: bytes
    warnings: tuple[str, ...]
    event_uids: tuple[str, ...]

    def __bytes__(self) -> bytes:
        return self.data


def _parse_time(value: str | time_type | None) -> Optional[time_type]:
    if isinstance(value, time_type):
        return value.replace(tzinfo=None)
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.translate(_ARABIC_DIGITS).strip()
    match = _TIME_RE.search(value)
    if not match:
        return None
    hour = int(match.group("h"))
    minute = int(match.group("m"))
    ampm = (match.group("ampm") or "").lower()
    if ampm in {"مساء", "مساءً", "مساءاً", "pm"} and hour < 12:
        hour += 12
    if ampm in {"صباحا", "صباحًا", "صباحاً", "am"} and hour == 12:
        hour = 0
    try:
        return time_type(hour, minute)
    except ValueError:
        return None


def stop_interval(stop: ItineraryStop) -> tuple[Optional[time_type], Optional[time_type], Optional[str]]:
    start = _parse_time(stop.start_time)
    end = _parse_time(stop.end_time)
    if start is not None or end is not None:
        return start, end, None
    raw = (stop.time or "").translate(_ARABIC_DIGITS)
    range_match = _RANGE_RE.match(raw)
    if not range_match:
        return None, None, "invalid_time" if raw.strip() else "missing_time"
    start = _parse_time(range_match.group("start"))
    end = _parse_time(range_match.group("end"))
    if start is None or end is None:
        return None, None, "invalid_time"
    return start, end, None


def _day_date(itinerary: Itinerary, day: ItineraryDay, index: int) -> Optional[date]:
    number = day.relative_day_number or index
    if itinerary.explicit_dates and 0 < number <= len(itinerary.explicit_dates):
        return itinerary.explicit_dates[number - 1]
    return day.date


def _stop_text(stop: ItineraryStop, source_map: dict[str, object]) -> str:
    parts = [stop.effective_location_name]
    if stop.address:
        parts.append(f"العنوان: {stop.address}")
    for block in (*stop.effective_description, *stop.effective_practical_notes, *stop.effective_accessibility_notes, *stop.notes):
        parts.append(block.text)
    citation_ids = list(stop.citation_ids)
    for support in stop.field_support:
        citation_ids.extend(support.citation_ids)
    citation_ids = list(dict.fromkeys(cid for cid in citation_ids if cid))
    if citation_ids:
        parts.append("الاستشهادات: " + " ".join(f"[{cid}]" for cid in citation_ids))
        urls = [getattr(source_map.get(cid), "url", "") for cid in citation_ids]
        urls = list(dict.fromkeys(url for url in urls if url))
        if urls:
            parts.append("المصادر: " + "\n".join(urls))
    return "\n".join(part for part in parts if part)


def render_calendar(itinerary: Itinerary, *, preview: bool = False) -> CalendarRenderResult:
    """Return deterministic UTF-8 iCalendar bytes; never calls the graph."""

    if itinerary.timezone != TIMEZONE:
        raise CalendarRenderError("unsupported_timezone", f"Only {TIMEZONE} is supported.")
    timezone = ZoneInfo(TIMEZONE)
    candidates: list[tuple[datetime, datetime, ItineraryDay, ItineraryStop, int, int]] = []
    warnings: list[str] = []
    all_days_undated = True
    for day_index, day in enumerate(itinerary.days, start=1):
        day_date = _day_date(itinerary, day, day_index)
        if day_date is not None:
            all_days_undated = False
        for stop_index, stop in enumerate(day.stops, start=1):
            if day_date is None:
                warnings.append(f"تم تخطي المحطة {day_index}.{stop_index}: التاريخ غير محدد.")
                continue
            start_time, end_time, time_error = stop_interval(stop)
            if time_error:
                warnings.append(f"تم تخطي المحطة {day_index}.{stop_index}: الوقت {time_error}.")
                continue
            if start_time is None or end_time is None:
                warnings.append(f"تم تخطي المحطة {day_index}.{stop_index}: وقت البداية والنهاية مطلوبان.")
                continue
            start = datetime.combine(day_date, start_time, tzinfo=timezone)
            end = datetime.combine(day_date, end_time, tzinfo=timezone)
            if end <= start:
                warnings.append(f"تم تخطي المحطة {day_index}.{stop_index}: وقت النهاية يسبق البداية.")
                continue
            candidates.append((start, end, day, stop, day_index, stop_index))

    if all_days_undated:
        raise CalendarRenderError("missing_dates", "لا يمكن إنشاء تقويم دون تواريخ صريحة.", tuple(warnings))
    if not candidates:
        raise CalendarRenderError("no_valid_events", "لا توجد محطات مؤرخة وموقّتة صالحة.", tuple(warnings))

    by_day: dict[date, list[tuple[datetime, datetime, int, int]]] = {}
    for start, end, _day, _stop, day_index, stop_index in candidates:
        by_day.setdefault(start.date(), []).append((start, end, day_index, stop_index))
    for intervals in by_day.values():
        intervals.sort()
        for previous, current in zip(intervals, intervals[1:]):
            if current[0] < previous[1]:
                warnings.append(f"يوجد تداخل زمني بين المحطتين {previous[2]}.{previous[3]} و{current[2]}.{current[3]}.")

    calendar = Calendar()
    calendar.add("prodid", "-//Sard//Arabic Itinerary Step 6//EN")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("x-wr-calname", f"معاينة: {itinerary.title}" if preview else itinerary.title)
    calendar.add("x-wr-timezone", TIMEZONE)
    generated_at = itinerary.generated_at.astimezone(UTC)
    uids: list[str] = []
    source_map = {source.citation_id: source for source in itinerary.sources}
    for start, end, day, stop, day_index, stop_index in candidates:
        identity = f"day-{day_index}-stop-{stop_index}|{stop.stop_id or 'anonymous'}"
        uid = f"{uuid.uuid5(uuid.NAMESPACE_URL, f'{itinerary.run_id}|{identity}')}@sard.local"
        if uid in uids:
            raise CalendarRenderError("duplicate_uid", f"Duplicate UID for {identity}.", tuple(warnings))
        uids.append(uid)
        event = Event()
        event.add("uid", uid)
        event.add("dtstamp", generated_at)
        event.add("dtstart", start)
        event.add("dtend", end)
        event.add("summary", stop.title or "محطة")
        event.add("location", stop.effective_location_name)
        event.add("description", _stop_text(stop, source_map))
        calendar.add_component(event)
    calendar.add_missing_timezones()
    missing = calendar.get_missing_tzids()
    if missing:
        raise CalendarRenderError("missing_timezone_definition", str(sorted(missing)), tuple(warnings))
    data = calendar.to_ical()
    parsed = Calendar.from_ical(data)
    parsed_events = [component for component in parsed.subcomponents if component.name == "VEVENT"]
    if len(parsed_events) != len(candidates) or len({event.get("uid").to_ical().decode() for event in parsed_events}) != len(parsed_events):
        raise CalendarRenderError("calendar_validation", "Calendar round-trip validation failed.", tuple(warnings))
    return CalendarRenderResult(data=data, warnings=tuple(warnings), event_uids=tuple(uids))
