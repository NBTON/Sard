"""Extract explicit dated events from a chat query for ICS rendering.

Converts user-stated dates/times into the ``content_data.events`` schema
consumed by the explicit-events ICS stack
(``ArtifactGeneratorRegistry._render_ics_from_explicit_events``), where each
event carries ``{title, start_date, start_time, end_time, location?,
description?}``.

Fail-closed contract (never invent schedule facts):

- A date without a year is skipped (the year is not assumed).
- A date without an explicit start/end time *range* is skipped (no default
  durations are invented).
- Invalid entries (impossible dates, bad times, end before start) are
  skipped with a warning naming the problem.
- No time zone is ever attached here; times are naive local times and the
  calendar renderer pins the ``Asia/Riyadh`` zone.
- When nothing valid remains, ``([], warnings)`` is returned so the caller
  falls through to the curated heritage lookup (which fails honestly with
  ``no_match`` for unknown topics).
"""

from __future__ import annotations

import re
from datetime import date as _date
from typing import Any, Dict, List, Tuple

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

_MONTHS = {
    "january": 1, "jan": 1,
    "\u064a\u0646\u0627\u064a\u0631": 1,
    "\u0641\u0628\u0631\u0627\u064a\u0631": 2,
    "\u0645\u0627\u0631\u0633": 3,
    "\u0623\u0628\u0631\u064a\u0644": 4, "\u0627\u0628\u0631\u064a\u0644": 4,
    "\u0645\u0627\u064a\u0648": 5,
    "\u064a\u0648\u0646\u064a\u0648": 6, "\u064a\u0648\u0646\u064a\u0647": 6,
    "\u064a\u0648\u0644\u064a\u0648": 7,
    "\u0623\u063a\u0633\u0637\u0633": 8, "\u0627\u063a\u0633\u0637\u0633": 8,
    "\u0633\u0628\u062a\u0645\u0628\u0631": 9,
    "\u0623\u0643\u062a\u0648\u0628\u0631": 10, "\u0627\u0643\u062a\u0648\u0628\u0631": 10,
    "\u0646\u0648\u0641\u0645\u0628\u0631": 11,
    "\u062f\u064a\u0633\u0645\u0628\u0631": 12,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

_ISO_DATE_RE = re.compile(r"\b(?P<y>\d{4})[/-](?P<m>\d{1,2})[/-](?P<d>\d{1,2})\b")
_DMY_ALPHA_RE = re.compile(
    r"\b(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+(?P<mon>[A-Za-z\u0600-\u06FF]+)\s+(?P<y>\d{4})\b"
)
_MDY_ALPHA_RE = re.compile(
    r"\b(?P<mon>[A-Za-z\u0600-\u06FF]+)\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<y>\d{4})\b"
)
_RANGE_SEP_RE = re.compile(
    r"\s*(?:-|\u2013|\u2014|\u0625\u0644\u0649|\u0627\u0644\u0649|\bto\b|\buntil\b)\s*",
    re.I,
)

_WINDOW = 80


def _month_number(name: str) -> int | None:
    key = (name or "").strip().lower()
    if key in _MONTHS:
        return _MONTHS[key]
    return None


def _to_24(hour: int, minute: int, ampm: str) -> Tuple[int, int] | None:
    """Apply an am/pm marker; bare hours are 24h clock. None when invalid."""
    if minute > 59:
        return None
    marker = (ampm or "").strip()
    if marker:
        low = marker.lower()
        is_pm = low == "pm" or marker in ("\u0645", "\u0645\u0633\u0627\u0621")
        is_am = low == "am" or marker in ("\u0635", "\u0635\u0628\u0627\u062d\u0627")
        if not (is_pm or is_am):
            return None
        if hour < 1 or hour > 12:
            return None
        if is_pm and hour < 12:
            hour += 12
        if is_am and hour == 12:
            hour = 0
    if hour > 23:
        return None
    return hour, minute


_RANGE_RE = re.compile(
    r"(?P<sh>\d{1,2})\s*[:\u066b.]\s*(?P<sm>\d{2})"
    r"(?:\s*(?P<sap>AM|PM|am|pm|\u0635|\u0645|\u0635\u0628\u0627\u062d\u0627|\u0645\u0633\u0627\u0621))?"
    r"\s*(?:-|\u2013|\u2014|\u0625\u0644\u0649|\u0627\u0644\u0649|\bto\b|\buntil\b)\s*"
    r"(?P<eh>\d{1,2})\s*[:\u066b.]\s*(?P<em>\d{2})"
    r"(?:\s*(?P<eap>AM|PM|am|pm|\u0635|\u0645|\u0635\u0628\u0627\u062d\u0627|\u0645\u0633\u0627\u0621))?",
    re.I,
)


def _clean_title(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip(" ,.\u060c\u061b:;!?"))
    cleaned = re.sub(
        r"^(please\s+|create\s+(an?\s+)?(ics\s+)?(calendar\s+)?(for\s+)?(my\s+)?(trip\s+)?"
        r"|(my\s+)?(trip\s+)?(schedule\s+)?(events?\s+)?(on\s+)?)+",
        "",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(
        r"\s+(on|for|at|in|\u0645\u0646|\u0641\u064a|\u0628\u062a\u0627\u0631\u064a\u062e)$",
        "",
        cleaned.strip(),
        flags=re.I,
    )
    return cleaned.strip()[:80]


def extract_calendar_events(
    query: str, fallback_title: str = ""
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Extract explicit dated events from ``query``.

    Returns ``(events, warnings)``. ``events`` entries match the
    explicit-events ICS schema; ``warnings`` explains every skipped date in
    plain language. Each time range is attached to its nearest date (ties
    resolve to the later date); a range is never shared. Never raises on
    bad input.
    """
    events: List[Dict[str, Any]] = []
    warnings: List[str] = []
    if not isinstance(query, str) or not query.strip():
        return events, warnings
    text = query.translate(_ARABIC_DIGITS)

    dates: List[Tuple[int, int, _date]] = []

    def _add(year: int, month: int, day: int, start: int, end: int) -> None:
        try:
            parsed = _date(year, month, day)
        except ValueError:
            warnings.append(f"Skipped invalid date {year}-{month:02d}-{day:02d}.")
            return
        if not 1900 <= parsed.year <= 2100:
            warnings.append(f"Skipped out-of-range date {parsed.isoformat()}.")
            return
        if any(s < end and start < e for s, e, _ in dates):
            return
        dates.append((start, end, parsed))

    for match in _ISO_DATE_RE.finditer(text):
        _add(int(match.group("y")), int(match.group("m")),
             int(match.group("d")), match.start(), match.end())
    for pattern in (_DMY_ALPHA_RE, _MDY_ALPHA_RE):
        for match in pattern.finditer(text):
            month = _month_number(match.group("mon"))
            if month is None:
                continue
            _add(int(match.group("y")), month,
                 int(match.group("d")), match.start(), match.end())

    if not dates:
        return events, warnings
    dates.sort()

    ranges: List[Tuple[int, int, Any]] = [
        (match.start(), match.end(), match) for match in _RANGE_RE.finditer(text)
    ]
    owned: Dict[int, List[Tuple[int, int, Any]]] = {i: [] for i in range(len(dates))}
    for item in ranges:
        rs, re_ = item[0], item[1]
        best: int | None = None
        best_gap: int | None = None
        for i, (ds, de, _) in enumerate(dates):
            gap = rs - de if rs >= de else ds - re_
            if gap < 0:
                continue
            if best_gap is None or gap < best_gap:
                best, best_gap = i, gap
        if best is not None:
            owned[best].append(item)

    for index, (start, end, day) in enumerate(dates):
        prev_end = dates[index - 1][1] if index > 0 else 0
        next_start = dates[index + 1][0] if index + 1 < len(dates) else len(text)
        # Title seed: neighboring text minus the date and its owned ranges.
        seed_parts: List[str] = []
        cursor = prev_end
        for rs, re_, _ in sorted(owned[index]):
            if prev_end <= rs and re_ <= next_start and rs >= cursor:
                seed_parts.append(text[cursor:rs])
                cursor = re_
        seed_parts.append(text[cursor:next_start])
        seed = " ".join(seed_parts).replace(text[start:end], " ", 1)
        title = _clean_title(seed) or (fallback_title or "event").strip()[:80]
        made = False
        saw_range = False
        for _, _, match in owned[index]:
            saw_range = True
            start_t = _to_24(int(match.group("sh")), int(match.group("sm")),
                             match.group("sap") or "")
            end_t = _to_24(int(match.group("eh")), int(match.group("em")),
                           match.group("eap") or "")
            if start_t is None or end_t is None:
                continue
            if end_t <= start_t:
                continue
            made = True
            events.append({
                "title": title,
                "start_date": day.isoformat(),
                "start_time": f"{start_t[0]:02d}:{start_t[1]:02d}",
                "end_time": f"{end_t[0]:02d}:{end_t[1]:02d}",
                "location": "",
                "description": "",
            })
        if not made:
            if saw_range:
                warnings.append(
                    f"Skipped {day.isoformat()}: nearby time range is not usable "
                    "(end must be after start)."
                )
            else:
                warnings.append(
                    f"Skipped {day.isoformat()}: a start and end time range is required."
                )
    return events, warnings
