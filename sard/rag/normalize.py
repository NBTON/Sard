"""Conservative Arabic text normalization.

Two distinct use cases share this module:

1. **Ingestion-time cleanup** — stripping repeated headers/footers/nav text
   and collapsing whitespace while preserving the original text untouched
   for display/citation (see :func:`clean_document_text`).
2. **Retrieval-time query normalization** — a conservative, configurable
   Arabic normalization used only to improve matching (dense + full-text).
   It never overwrites what is shown to the user (see :func:`normalize_arabic`).

Normalization version is tracked via :data:`NORMALIZATION_VERSION` and is
part of the versioned Zvec collection path, so changing these rules forces a
rebuild instead of silently mixing normalization schemes in one index.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

NORMALIZATION_VERSION = "1"

# Arabic diacritics (tashkeel) + tatweel.
_TASHKEEL_RE = re.compile(
    "[" + "".join(
        [
            "\u0610-\u061a",  # Quranic annotation marks
            "\u064b-\u065f",  # tanwin, harakat, shadda, sukun
            "\u0670",  # superscript alef
            "\u06d6-\u06dc",
            "\u06df-\u06e8",
            "\u06ea-\u06ed",
        ]
    ) + "]"
)
_TATWEEL_RE = re.compile("\u0640+")

# Alef variants -> bare alef.
_ALEF_VARIANTS_RE = re.compile("[\u0622\u0623\u0625\u0671]")
# Persian/Urdu yeh + alef maksura -> Arabic yeh (conservative: keep separate
# from alef maksura at word end is debatable; we fold both to \u064a which
# is the common IR-normalization choice for Arabic search).
_YEH_VARIANTS_RE = re.compile("[\u0649\u06cc\u06d2]")
# Persian kaf -> Arabic kaf.
_KAF_VARIANTS_RE = re.compile("[\u06a9]")
# Teh marbuta is intentionally NOT folded by default (conservative), but is
# offered as an option since some Arabic IR pipelines fold it to heh.
_TEH_MARBUTA_RE = re.compile("\u0629")

_MULTI_SPACE_RE = re.compile(r"[ \t\u00a0\u200f\u200e]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class ArabicNormalizationOptions:
    """Configurable, conservative Arabic normalization switches.

    All default to True except teh_marbuta folding, which is the most
    aggressive/lossy option and is off by default.
    """

    strip_tatweel: bool = True
    strip_diacritics: bool = True
    normalize_alef: bool = True
    normalize_yeh: bool = True
    normalize_kaf: bool = True
    normalize_spacing: bool = True
    fold_teh_marbuta: bool = False


DEFAULT_OPTIONS = ArabicNormalizationOptions()


def normalize_arabic(
    text: str, options: ArabicNormalizationOptions = DEFAULT_OPTIONS
) -> str:
    """Conservatively normalize Arabic text for retrieval matching only.

    This must NEVER be used to overwrite text shown to the user — only to
    build a matching key (query normalization, or a normalized copy of
    corpus text used solely for retrieval).
    """
    if not text:
        return text

    result = unicodedata.normalize("NFKC", text)

    if options.strip_tatweel:
        result = _TATWEEL_RE.sub("", result)
    if options.strip_diacritics:
        result = _TASHKEEL_RE.sub("", result)
    if options.normalize_alef:
        result = _ALEF_VARIANTS_RE.sub("\u0627", result)  # -> bare alef
    if options.normalize_yeh:
        result = _YEH_VARIANTS_RE.sub("\u064a", result)  # -> Arabic yeh
    if options.normalize_kaf:
        result = _KAF_VARIANTS_RE.sub("\u0643", result)  # -> Arabic kaf
    if options.fold_teh_marbuta:
        result = _TEH_MARBUTA_RE.sub("\u0647", result)  # -> heh
    if options.normalize_spacing:
        result = _MULTI_SPACE_RE.sub(" ", result)
        result = "\n".join(line.strip() for line in result.split("\n"))
        result = _MULTI_NEWLINE_RE.sub("\n\n", result)
        result = result.strip()

    return result


def clean_document_text(text: str) -> str:
    """Ingestion-time cleanup: collapse whitespace, do NOT alter characters.

    This is intentionally much lighter than :func:`normalize_arabic` — it
    must preserve the exact wording/characters of the source for citation
    and display, only tidying layout artifacts (repeated blank lines,
    trailing spaces, stray control characters introduced by PDF/HTML
    extraction).
    """
    if not text:
        return text

    # Normalize Unicode form (NFC) without altering Arabic letters/diacritics
    # semantically — this only canonicalizes composed vs. decomposed forms.
    result = unicodedata.normalize("NFC", text)
    # Drop non-printable control characters except newline/tab.
    result = "".join(
        ch for ch in result if ch in ("\n", "\t") or not unicodedata.category(ch).startswith("C")
    )
    result = _MULTI_SPACE_RE.sub(" ", result)
    lines = [line.strip() for line in result.split("\n")]
    result = "\n".join(lines)
    result = _MULTI_NEWLINE_RE.sub("\n\n", result)
    return result.strip()


def remove_repeated_lines(pages: list[str], min_repeat_ratio: float = 0.6) -> list[str]:
    """Remove lines that repeat across most pages (headers/footers/nav).

    ``pages`` is a list of page/section texts. A line that appears on at
    least ``min_repeat_ratio`` of pages (and is short — likely a
    header/footer, not a real repeated sentence) is treated as boilerplate
    and stripped from every page.
    """
    if len(pages) < 3:
        return pages

    from collections import Counter

    line_counts: Counter[str] = Counter()
    per_page_lines = []
    for page in pages:
        lines = [ln.strip() for ln in page.split("\n") if ln.strip()]
        per_page_lines.append(lines)
        for ln in set(lines):
            if len(ln) <= 120:  # headers/footers are usually short
                line_counts[ln] += 1

    threshold = max(2, int(len(pages) * min_repeat_ratio))
    boilerplate = {ln for ln, count in line_counts.items() if count >= threshold}

    cleaned_pages = []
    for lines in per_page_lines:
        kept = [ln for ln in lines if ln not in boilerplate]
        cleaned_pages.append("\n".join(kept))
    return cleaned_pages
