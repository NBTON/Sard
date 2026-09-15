"""Centralized RTL policy + shaping/bidi helpers for ``sard/outputs``.

RTL POLICY (binding on every renderer in this package):

* HTML/browser output: native Unicode ONLY.  Emit logical text with
  ``dir="rtl"`` / ``direction: rtl`` and logical CSS properties
  (``margin-inline-start``, ``padding-inline-end``, ``text-align: start``).
  NEVER call :func:`shape_rtl` for browser output — browsers shape Arabic
  natively and pre-shaped (presentation-form) text renders corrupted.
* ReportLab PDF: keep text LOGICAL through measurement and wrapping
  (``wrap_logical_lines`` on logical text), then call :func:`shape_rtl`
  ONLY at the draw boundary (inside ``Flowable.draw``).  Keep
  :func:`visual_runs` font-splitting so Arabic runs use the Arabic font
  and Latin/digit runs use the Latin companion font.
* DOCX/PPTX: native Unicode + RTL properties (``w:bidi``/``w:rtl``,
  ``a:pPr rtl="1"``).  No pre-reshaping — Word/PowerPoint shape natively.

Shaping helpers below are therefore PDF-draw-boundary-only.
"""

from __future__ import annotations

import html
import re

import arabic_reshaper
from bidi.algorithm import get_display


ARABIC_RE = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]")
# Atomic LTR units stashed before bidi so python-bidi cannot tear brackets or
# swap year/era-suffix positions. Order matters: specific shapes first.
# - URLs, CIT-* IDs, numeric citations ([1], [12])
# - parenthesized Latin phrases: "(Salwa Palace)" stays one run so both
#   parens survive shaping (RTL-B1)
# - Hijri/Gregorian years with era suffix: "1727م" / "1447هـ" (RTL-Q1)
PROTECTED_RE = re.compile(
    r"https?://[^\s<>]+|\[CIT-[A-Za-z0-9_-]+\]|\[\d+\]|"
    r"\([A-Za-z0-9\s.,_/\-]{1,80}\)|"
    r"\d+\s*(?:م|هـ)|[%+\-]\d+(?:[.,]\d+)?|"
    r"[A-Za-z0-9][A-Za-z0-9._:/?&=%+\-]*"
)


def contains_arabic(text: str) -> bool:
    return bool(ARABIC_RE.search(text or ""))


def needs_rtl(text: str) -> bool:
    """Alias expressing renderer intent: True when the run needs RTL base direction."""

    return contains_arabic(text or "")


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
