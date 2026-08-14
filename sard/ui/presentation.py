"""Pure Arabic RTL presentation helpers for the Step 7 Streamlit interface.

This module is the *only* place the UI builds HTML strings, applies URL
allowlisting, or maps stage/state/mode values to Arabic labels. It imports
no Streamlit code, no LangGraph, no Zvec, no NVIDIA integration, no
``sard.config``, no ``sard.rag`` and no Step 6 renderer — it is fully
testable without launching Streamlit or any provider.

The Step 7 contract types from :mod:`sard.application.contracts`
(``UIStage``, ``UIProgressState``, ``UIModeKind``, ``UIExecutionMode``,
``UISourceView``, ``UIArtifactView``, ``UIModeStatus``, ``UIModelRoute``,
``UIProgressEvent``) are consumed duck-typed. They are imported only under
``TYPE_CHECKING`` so this module works before the application package is
present; the label maps are keyed on the frozen value strings from the
shared contract. Values are normalized through ``_value_of`` so both the
enum members and their raw strings are accepted.

Security rules implemented here:

* ``is_safe_external_url`` allows only ``http``/``https`` URLs without
  credentials, a non-empty hostname and no control characters; anything
  else is rejected and rendered as plain text instead of a link.
* Every piece of text interpolated into an ``unsafe_allow_html=True``
  Streamlit block is passed through :func:`escape_html` first.
* ``answer_with_source_links`` links inline ``[CIT-...]`` references only
  to source records already present in ``UIRunResult.sources``; unknown
  references stay literal and no source is ever invented from the answer.
"""

from __future__ import annotations

import html
import math
import re
import secrets
from datetime import date as date_type
from typing import TYPE_CHECKING, Optional

from sard.url_policy import is_safe_external_url as _shared_is_safe_external_url

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from sard.application.contracts import (
        UIArtifactView,
        UIModelRoute,
        UIModeStatus,
        UIProgressEvent,
        UISourceView,
    )

# ---------------------------------------------------------------------------
# Label maps (frozen contract value strings -> concise Arabic labels)
# ---------------------------------------------------------------------------

_STAGE_LABELS: dict[str, str] = {
    "understand": "فهم الطلب",
    "plan": "وضع الخطة",
    "retrieve": "جمع المصادر",
    "compose": "صياغة الإجابة",
    "verify": "التحقق من الاستشهادات",
    "render": "تجهيز الملفات",
}

_STATE_LABELS: dict[str, str] = {
    "waiting": "في الانتظار",
    "active": "قيد التنفيذ",
    "completed": "اكتمل",
    "retried": "أُعيدت المحاولة",
    "degraded": "وضع مخفَّض",
    "failed": "فشل",
    "partially_completed": "اكتمل جزئيًا",
}

_MODE_KIND_LABELS: dict[str, str] = {
    "live": "وضع مباشر",
    "degraded_retrieval": "استرجاع مخفَّض",
    "model_fallback": "نموذج احتياطي",
    "cached_demo": "نسخة احتياطية محفوظة",
    "unavailable": "غير متاح",
}

_EXECUTION_MODE_LABELS: dict[str, str] = {
    "live": "مباشر",
    "cached_demo": "نسخة احتياطية محفوظة",
}

_RETRIEVAL_MODE_LABELS: dict[str, str] = {
    "hybrid_reranked": "هجين مع إعادة ترتيب",
    "hybrid_fused": "هجين مدمج",
    "dense_only": "بحث دلالي فقط",
    "full_text_only": "بحث نصي فقط",
    "unavailable": "غير متاح",
}

_GRAPH_OUTCOME_LABELS: dict[str, str] = {
    "completed": "اكتملت الرحلة",
    "partial": "اكتملت جزئيًا",
    "failed": "لم تكتمل",
}

_ARTIFACT_TYPE_LABELS: dict[str, str] = {
    "pdf": "برنامج الرحلة PDF",
    "calendar": "تقويم الرحلة",
    "raw_text": "الإجابة نصًا",
}

_ARTIFACT_STATUS_LABELS: dict[str, str] = {
    "created": "تم الإنشاء",
    "skipped": "تخطّي",
    "failed": "فشل الإنشاء",
}

_FALLBACK_LABELS: dict[str, str] = {
    "stage": "مرحلة غير معروفة",
    "state": "حالة غير معروفة",
    "mode_kind": "وضع غير معروف",
    "execution_mode": "وضع غير معروف",
    "retrieval_mode": "وضع استرجاع غير معروف",
    "graph_outcome": "حالة غير معروفة",
    "artifact_status": "حالة غير معروفة",
}

# ---------------------------------------------------------------------------
# Value normalization
# ---------------------------------------------------------------------------


def _value_of(value: object) -> object:
    """Return the raw string of an enum member, else the value itself."""
    return getattr(value, "value", value)


def _label(mapping: dict[str, str], value: object, fallback_key: str) -> str:
    key = _value_of(value)
    if isinstance(key, str):
        label = mapping.get(key)
        if label is not None:
            return label
    return _FALLBACK_LABELS[fallback_key]


# ---------------------------------------------------------------------------
# Arabic label accessors
# ---------------------------------------------------------------------------


def stage_label(stage: object) -> str:
    """Concise Arabic label for a UI stage (``understand`` .. ``render``)."""
    return _label(_STAGE_LABELS, stage, "stage")


def state_label(state: object) -> str:
    """Concise Arabic label for a UI progress state."""
    return _label(_STATE_LABELS, state, "state")


def mode_kind_label(kind: object) -> str:
    """Concise Arabic label for a final mode kind."""
    return _label(_MODE_KIND_LABELS, kind, "mode_kind")


def execution_mode_label(mode: object) -> str:
    """Concise Arabic label for an execution mode (live / cached_demo)."""
    return _label(_EXECUTION_MODE_LABELS, mode, "execution_mode")


def retrieval_mode_label(mode: str) -> str:
    """Concise Arabic label for a normalized retrieval mode string."""
    return _label(_RETRIEVAL_MODE_LABELS, mode, "retrieval_mode")


def graph_outcome_label(outcome: object) -> str:
    """Concise Arabic label for a graph outcome (completed/partial/failed)."""
    return _label(_GRAPH_OUTCOME_LABELS, outcome, "graph_outcome")


def artifact_type_label(artifact_type: str) -> str:
    """Arabic fallback label for an artifact type (pdf/calendar/raw_text)."""
    return _ARTIFACT_TYPE_LABELS.get(artifact_type, "ملف")


def artifact_status_label(status: str) -> str:
    """Concise Arabic label for an artifact creation status."""
    return _label(_ARTIFACT_STATUS_LABELS, status, "artifact_status")


# ---------------------------------------------------------------------------
# Arabic formatting helpers
# ---------------------------------------------------------------------------

_ARABIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")

_ARABIC_MONTHS: dict[int, str] = {
    1: "يناير",
    2: "فبراير",
    3: "مارس",
    4: "أبريل",
    5: "مايو",
    6: "يونيو",
    7: "يوليو",
    8: "أغسطس",
    9: "سبتمبر",
    10: "أكتوبر",
    11: "نوفمبر",
    12: "ديسمبر",
}


def to_arabic_digits(value: object) -> str:
    """Render a number using Arabic-Indic digits (123 -> ١٢٣)."""
    return str(value).translate(_ARABIC_DIGITS)


def format_date_arabic(value: object) -> str:
    """Format a ``date`` or ISO date string as ``١٥ فبراير ٢٠٢٦``."""
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        parsed = value
    else:
        try:
            parsed = date_type.fromisoformat(str(value))
        except (TypeError, ValueError):
            return ""
    month = _ARABIC_MONTHS.get(parsed.month, to_arabic_digits(parsed.month))
    return f"{to_arabic_digits(parsed.day)} {month} {to_arabic_digits(parsed.year)}"


def format_coverage(ratio: Optional[float]) -> str:
    """Format a coverage ratio as an Arabic percentage (0.85 -> ٨٥٪)."""
    if ratio is None:
        return ""
    try:
        value = float(ratio)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(value):
        value = 0.0
    value = max(0.0, min(1.0, value))
    return f"{to_arabic_digits(round(value * 100))}٪"


def format_size_bytes(size_bytes: object) -> str:
    """Format a byte count in Arabic-friendly units."""
    try:
        size = int(size_bytes)
    except (TypeError, ValueError):
        return ""
    size = max(0, size)
    if size < 1024:
        return f"{to_arabic_digits(size)} بايت"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} ك.ب"
    return f"{size / (1024 * 1024):.1f} م.ب"


def new_run_id(prefix: str = "step7") -> str:
    """Generate a fresh safe ASCII run ID such as ``step7-<32 hex>``.

    The result always matches the existing safe run-ID rule
    ``^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$``.
    """
    candidate = f"{prefix}-{secrets.token_hex(16)}"
    if not re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$", candidate):
        return f"step7-{secrets.token_hex(16)}"
    return candidate


# ---------------------------------------------------------------------------
# Safe URL allowlisting
# ---------------------------------------------------------------------------

def is_safe_external_url(url: object) -> bool:
    """Return whether a URL passes the shared application/UI source policy."""

    return _shared_is_safe_external_url(url)


def _markdown_url(url: str) -> str:
    """Make a URL safe to embed inside a markdown link destination."""
    return url.replace(")", "%29").replace(" ", "%20")


def safe_external_link_markdown(text: object, url: object) -> str:
    """Return ``[text](url)`` for an allowlisted URL, else plain text.

    The link text is HTML-escaped so the result is safe to render in
    Streamlit markdown; unsafe URLs degrade to escaped plain text.
    """
    safe_text = escape_html(text)
    if not is_safe_external_url(url):
        return safe_text
    return f"[{safe_text}]({_markdown_url(str(url))})"


# ---------------------------------------------------------------------------
# HTML helpers (every interpolated value must be escaped)
# ---------------------------------------------------------------------------


def escape_html(value: object) -> str:
    """Escape text for safe interpolation into ``unsafe_allow_html`` blocks."""
    return html.escape(str(value), quote=True)


def branded_header_html(title: str, subtitle: str = "", value_prop: str = "") -> str:
    """Branded Arabic hero header with title, subtitle and value proposition."""
    parts = ['<header class="sard-hero">']
    parts.append(f'<h1 class="sard-hero-title">{escape_html(title)}</h1>')
    if subtitle:
        parts.append(f'<p class="sard-hero-subtitle">{escape_html(subtitle)}</p>')
    if value_prop:
        parts.append(f'<p class="sard-hero-value">{escape_html(value_prop)}</p>')
    parts.append("</header>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Source cards and answer rendering
# ---------------------------------------------------------------------------

_CITATION_RE = re.compile(r"\[(CIT-[A-Za-z0-9_-]{3,60})\]")


def answer_with_source_links(answer: str, sources: object) -> str:
    """Link inline ``[CIT-...]`` references to known verified sources.

    Only citation IDs present in ``sources`` and pointing at an allowlisted
    URL are linked; unknown references stay literal. No source is invented.
    """
    url_by_id: dict[str, str] = {}
    for source in sources or ():
        cid = getattr(source, "citation_id", "")
        url = getattr(source, "url", "")
        if isinstance(cid, str) and cid and is_safe_external_url(url):
            url_by_id[cid] = str(url)

    def _replace(match: re.Match[str]) -> str:
        cid = match.group(1)
        url = url_by_id.get(cid)
        if url is None:
            return match.group(0)
        return f"[{cid}]({_markdown_url(url)})"

    return _CITATION_RE.sub(_replace, str(answer))


def source_card_html(source: object) -> str:
    """Build one responsive source card as escaped HTML.

    A card always shows the citation ID and title. Metadata completeness and
    verification badges reflect ``metadata_complete`` / ``citation_verified``.
    The external link is rendered only when :func:`is_safe_external_url`
    accepts the URL; otherwise a muted "no safe link" note is shown.
    """
    citation_id = escape_html(getattr(source, "citation_id", ""))
    title = escape_html(str(getattr(source, "title", "") or "").strip())
    url = getattr(source, "url", "")
    metadata_complete = bool(getattr(source, "metadata_complete", False))
    citation_verified = bool(getattr(source, "citation_verified", True))

    page = getattr(source, "page", None)
    section = getattr(source, "section", None)
    publication_date = getattr(source, "publication_date", None)

    badges = []
    badge_class = "sard-badge sard-badge-ok" if metadata_complete else "sard-badge sard-badge-warn"
    badges.append(
        f'<span class="{badge_class}">{"بيانات كاملة" if metadata_complete else "بيانات ناقصة"}</span>'
    )
    badges.append(
        f'<span class="sard-badge sard-badge-ok">{"مُتحقَّق" if citation_verified else "غير مُتحقَّق"}</span>'
    )

    meta_parts: list[str] = []
    if page is not None:
        meta_parts.append(f"صفحة {to_arabic_digits(page)}")
    if section:
        meta_parts.append(escape_html(section))
    if publication_date:
        meta_parts.append(escape_html(format_date_arabic(publication_date)))

    if is_safe_external_url(url):
        link_html = (
            f'<a class="sard-link" href="{escape_html(url)}" target="_blank" '
            'rel="noopener noreferrer">فتح المصدر</a>'
        )
    else:
        link_html = '<span class="sard-muted">لا يوجد رابط آمن</span>'

    meta_html = "".join(
        f'<p class="sard-source-meta">{", ".join(meta_parts)}</p>' if meta_parts else ""
    )
    badges_html = f'<p class="sard-source-meta">{"".join(badges)}</p>'

    return (
        '<article class="sard-source-card">'
        f'<h3 class="sard-source-title">{title}</h3>'
        f'{badges_html}'
        f'{meta_html}'
        f'<p class="sard-source-link">{link_html}</p>'
        f'<footer class="sard-source-cite">{citation_id}</footer>'
        "</article>"
    )


# ---------------------------------------------------------------------------
# Mode / route / artifact summary lines
# ---------------------------------------------------------------------------


def mode_status_line(mode_status: object) -> str:
    """One-line Arabic summary of the final UI mode status."""
    kind = getattr(mode_status, "kind", None)
    retrieval = str(getattr(mode_status, "retrieval_mode", "") or "")
    fallback_used = bool(getattr(mode_status, "model_fallback_used", False))
    execution_mode = getattr(mode_status, "execution_mode", None)

    if _value_of(execution_mode) == "cached_demo" or _value_of(kind) == "cached_demo":
        return "نسخة احتياطية محفوظة مسبقًا — لم يُنشأ المحتوى الآن"

    parts: list[str] = []
    if execution_mode is not None:
        parts.append(execution_mode_label(execution_mode))
    parts.append(mode_kind_label(kind))
    if retrieval:
        parts.append(f"الاسترجاع: {retrieval_mode_label(retrieval)}")
    if fallback_used:
        parts.append("نموذج احتياطي مُفعَّل")
    return " — ".join(parts)


def model_route_label(route: object) -> str:
    """Display the allowlisted resolved model ID (plus fallback marker)."""
    resolved = str(getattr(route, "resolved_model", "") or "")
    if not resolved:
        return ""
    if bool(getattr(route, "used_fallback", False)):
        return f"{resolved} (احتياطي)"
    return resolved


def progress_line(event: object) -> str:
    """Render one progress event as a short Arabic markdown line."""
    stage = stage_label(getattr(event, "stage", None))
    state = state_label(getattr(event, "state", None))
    line = f"**{stage}** — {state}"
    if bool(getattr(event, "simulated", False)):
        line += " · (تجريبي)"
    summary = str(getattr(event, "summary", "") or "").strip()
    if summary:
        line += f": {summary}"
    return line


def artifact_download_label(artifact: object) -> str:
    """Download-button label combining display label and byte size."""
    label = str(getattr(artifact, "display_label", "") or "")
    try:
        size_bytes = int(getattr(artifact, "size_bytes", 0))
    except (TypeError, ValueError):
        size_bytes = 0
    if size_bytes > 0:
        return f"{label} ({format_size_bytes(size_bytes)})"
    return label


def warning_messages(result: object) -> tuple[str, ...]:
    """Return bounded warnings safe for Streamlit's Markdown rendering."""

    values = getattr(result, "warnings", ())
    if not isinstance(values, tuple):
        return ()
    return tuple(
        escape_html(str(value).strip())[:320]
        for value in values
        if str(value).strip()
    )


def mode_banner_html(mode_status: object, *, demo: bool = False) -> str:
    """Safe HTML banner that unmistakably labels the active mode."""
    banner_class = "sard-mode-banner demo" if demo else "sard-mode-banner"
    body = escape_html(mode_status_line(mode_status))
    note = (
        '<span class="sard-badge sard-badge-warn">نسخة احتياطية محفوظة مسبقًا — ليست مولّدة الآن</span>'
        if demo
        else ""
    )
    return f'<div class="{banner_class}">{note}{body}</div>'
