"""Arabic shaping and bidi helpers used exactly once at the drawing boundary."""

from __future__ import annotations

import html
import re

import arabic_reshaper
from bidi.algorithm import get_display


ARABIC_RE = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]")
PROTECTED_RE = re.compile(
    r"https?://[^\s<>]+|\[CIT-[A-Za-z0-9_-]+\]|[%+\-]\d+(?:[.,]\d+)?|"
    r"[A-Za-z0-9][A-Za-z0-9._:/?&=%+\-]*"
)


def contains_arabic(text: str) -> bool:
    return bool(ARABIC_RE.search(text))


def escape_reportlab_markup(text: str) -> str:
    """Escape every ReportLab Paragraph markup metacharacter."""

    return html.escape(text, quote=True)


def shape_rtl(text: str) -> str:
    """Shape one *logical, unshaped* line for visual RTL drawing.

    URLs and stable citation IDs are temporarily replaced by ASCII tokens so
    bidi processing cannot reverse their characters. Call this only after line
    wrapping; reshaping already transformed text would corrupt joining.
    """

    if not text or not contains_arabic(text):
        return text
    protected: list[str] = []

    def stash(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"ZXQ{len(protected) - 1}QXZ"

    logical = PROTECTED_RE.sub(stash, text)
    visual = get_display(arabic_reshaper.reshape(logical), base_dir="R")
    for index, value in enumerate(protected):
        visual = visual.replace(f"ZXQ{index}QXZ", value)
    return visual


def append_citations(text: str, citation_ids: tuple[str, ...]) -> str:
    """Append explicit IDs once while preserving IDs already inline."""

    suffix = [
        f"[{citation_id}]"
        for citation_id in citation_ids
        if f"[{citation_id}]" not in text
    ]
    return f"{text} {' '.join(suffix)}".rstrip()


def visual_runs(visual_text: str) -> list[tuple[bool, str]]:
    """Split already-shaped visual text into Arabic-font and Latin-font runs."""

    def uses_arabic_font(character: str) -> bool:
        codepoint = ord(character)
        return (
            0x0600 <= codepoint <= 0x08FF
            or 0xFB50 <= codepoint <= 0xFDFF
            or 0xFE70 <= codepoint <= 0xFEFF
        )

    runs: list[tuple[bool, str]] = []
    for character in visual_text:
        arabic = uses_arabic_font(character)
        if runs and runs[-1][0] == arabic:
            runs[-1] = (arabic, runs[-1][1] + character)
        else:
            runs.append((arabic, character))
    return runs
