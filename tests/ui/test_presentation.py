"""Pure tests for :mod:`sard.ui.presentation`.

These tests never import Streamlit, ``sard.application`` (which does not
exist on this branch yet), LangGraph, Zvec, NVIDIA, ``sard.config`` or the
Step 6 renderers — they exercise the presentation helpers directly with
duck-typed contract fixtures.
"""

from __future__ import annotations

import re
from datetime import date
from types import SimpleNamespace

import pytest

from sard.ui import presentation as ui

# Frozen contract value sets (mirror of the Step 7 shared contract).
CONTRACT_STAGES = {"understand", "plan", "retrieve", "compose", "verify", "render"}
CONTRACT_STATES = {
    "waiting",
    "active",
    "completed",
    "retried",
    "degraded",
    "failed",
    "partially_completed",
}
CONTRACT_MODE_KINDS = {
    "live",
    "degraded_retrieval",
    "model_fallback",
    "cached_demo",
    "unavailable",
}
CONTRACT_EXECUTION_MODES = {"live", "cached_demo"}
CONTRACT_RETRIEVAL_MODES = {
    "hybrid_reranked",
    "hybrid_fused",
    "dense_only",
    "full_text_only",
    "unavailable",
}
CONTRACT_GRAPH_OUTCOMES = {"completed", "partial", "failed"}
CONTRACT_ARTIFACT_STATUSES = {"created", "skipped", "failed"}


def _source(**overrides) -> SimpleNamespace:
    fields = {
        "citation_id": "CIT-TEST-0001",
        "title": "مصدر اختبار",
        "url": "https://example.org/source?lang=ar",
        "page": None,
        "section": None,
        "publication_date": None,
        "metadata_complete": False,
        "citation_verified": True,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _event(**overrides) -> SimpleNamespace:
    fields = {
        "stage": "understand",
        "state": "active",
        "summary": "",
        "simulated": False,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _mode_status(**overrides) -> SimpleNamespace:
    fields = {
        "kind": "live",
        "retrieval_mode": "hybrid_reranked",
        "model_fallback_used": False,
        "execution_mode": "live",
        "model_routes": (),
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _artifact(**overrides) -> SimpleNamespace:
    fields = {
        "artifact_type": "pdf",
        "display_label": "برنامج الرحلة PDF",
        "filename": "itinerary.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 2048,
        "creation_status": "created",
        "download_bytes": b"%PDF-1.7",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


# ---------------------------------------------------------------------------
# Label maps cover every frozen contract value
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", sorted(CONTRACT_STAGES))
def test_stage_label_covers_all_contract_stages(value: str) -> None:
    label = ui.stage_label(value)
    assert label and label != value
    assert "غير معروفة" not in label


def test_stage_label_accepts_enum_member() -> None:
    enum_member = SimpleNamespace(value="render")
    assert ui.stage_label(enum_member) == ui.stage_label("render")


def test_stage_label_unknown_falls_back() -> None:
    assert ui.stage_label("mystery_node") == "مرحلة غير معروفة"
    assert ui.stage_label(None) == "مرحلة غير معروفة"


@pytest.mark.parametrize("value", sorted(CONTRACT_STATES))
def test_state_label_covers_all_contract_states(value: str) -> None:
    label = ui.state_label(value)
    assert label and label != value
    assert "غير معروفة" not in label


def test_state_label_unknown_falls_back() -> None:
    assert ui.state_label("unknown_state") == "حالة غير معروفة"


@pytest.mark.parametrize("value", sorted(CONTRACT_MODE_KINDS))
def test_mode_kind_label_covers_all_contract_kinds(value: str) -> None:
    label = ui.mode_kind_label(value)
    assert label and label != value
    assert "غير معروف" not in label


def test_mode_kind_label_unknown_falls_back() -> None:
    assert ui.mode_kind_label("magic") == "وضع غير معروف"


@pytest.mark.parametrize("value", sorted(CONTRACT_EXECUTION_MODES))
def test_execution_mode_label_covers_all_contract_modes(value: str) -> None:
    label = ui.execution_mode_label(value)
    assert label and label != value
    assert "غير معروف" not in label


def test_execution_mode_label_unknown_falls_back() -> None:
    assert ui.execution_mode_label("magic") == "وضع غير معروف"


@pytest.mark.parametrize("value", sorted(CONTRACT_RETRIEVAL_MODES))
def test_retrieval_mode_label_covers_all_contract_modes(value: str) -> None:
    label = ui.retrieval_mode_label(value)
    assert label and label != value
    assert "غير معروف" not in label


def test_retrieval_mode_label_unknown_falls_back() -> None:
    assert ui.retrieval_mode_label("spooky") == "وضع استرجاع غير معروف"


@pytest.mark.parametrize("value", sorted(CONTRACT_GRAPH_OUTCOMES))
def test_graph_outcome_label_covers_all_contract_outcomes(value: str) -> None:
    label = ui.graph_outcome_label(value)
    assert label and label != value
    assert "غير معروفة" not in label


def test_graph_outcome_label_unknown_falls_back() -> None:
    assert ui.graph_outcome_label("exploded") == "حالة غير معروفة"


@pytest.mark.parametrize("value", sorted(CONTRACT_ARTIFACT_STATUSES))
def test_artifact_status_label_covers_all_contract_statuses(value: str) -> None:
    label = ui.artifact_status_label(value)
    assert label and label != value
    assert "غير معروفة" not in label


# ---------------------------------------------------------------------------
# Safe URL allowlisting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/source?lang=ar&ref=1#sec",
        "http://example.com",
        "https://example.org:8080/path",
        "HTTPS://EXAMPLE.ORG/x",
        "https://sub.domain.example.co/travel/riyadh",
    ],
)
def test_is_safe_external_url_accepts_http_https(url: str) -> None:
    assert ui.is_safe_external_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
        "ftp://example.com/file",
        "https://user:pass@example.org",
        "https://user@example.org",
        "https://example.com@evil.org",
        "http://",  # empty host
        "https://",
        "not a url",
        "//example.org/path",  # scheme-less protocol-relative
        "https://example.org\n",  # control char
        "https://example.org:99999/",  # invalid port
        "https://example.org/private/token/abc",
        "https://example.org/path?sig=abc",
        "https://example.org/path?X-Amz-Signature=abc",
        "https://example.org/path#authorization-secret",
        "https://example.org/path/abcdefghijklmnopqrstuvwx",
        "x" * 2049,  # over length bound
        "",
        None,
        12345,
    ],
)
def test_is_safe_external_url_rejects_unsafe(url: object) -> None:
    assert ui.is_safe_external_url(url) is False


def test_safe_external_link_markdown_allows_safe_url() -> None:
    assert ui.safe_external_link_markdown("فتح المصدر", "https://example.org/s") == (
        "[فتح المصدر](https://example.org/s)"
    )


def test_safe_external_link_markdown_degrades_unsafe_url_to_text() -> None:
    assert ui.safe_external_link_markdown("فتح المصدر", "javascript:alert(1)") == (
        "فتح المصدر"
    )
    assert ui.safe_external_link_markdown("فتح المصدر", "https://user@example.org") == (
        "فتح المصدر"
    )


def test_safe_external_link_markdown_escapes_link_text() -> None:
    out = ui.safe_external_link_markdown("نص <script>x</script>", "https://example.org")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


# ---------------------------------------------------------------------------
# HTML escaping
# ---------------------------------------------------------------------------


def test_escape_html_escapes_quotes_and_ampersands() -> None:
    out = ui.escape_html('<a href="x">&"')
    assert out == "&lt;a href=&quot;x&quot;&gt;&amp;&quot;"


def test_branded_header_html_escapes_all_fields() -> None:
    out = ui.branded_header_html(
        title="<script>alert(1)</script>",
        subtitle='سرد & "قيمة"',
        value_prop="<b>غامق</b>",
    )
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "&amp;" in out
    assert 'class="sard-hero"' in out
    assert 'class="sard-hero-title"' in out
    assert 'class="sard-hero-subtitle"' in out
    assert 'class="sard-hero-value"' in out


def test_branded_header_html_omits_empty_optional_fields() -> None:
    out = ui.branded_header_html("سرد")
    assert 'sard-hero-subtitle' not in out
    assert 'sard-hero-value' not in out


# ---------------------------------------------------------------------------
# Source cards
# ---------------------------------------------------------------------------


def test_source_card_html_escapes_title_and_links_safe_url() -> None:
    source = _source(title="مصدر <script>x</script>")
    out = ui.source_card_html(source)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert 'href="https://example.org/source?lang=ar"' in out
    assert 'rel="noopener noreferrer"' in out
    assert 'target="_blank"' in out
    assert "CIT-TEST-0001" in out


def test_source_card_html_shows_no_link_for_unsafe_url() -> None:
    source = _source(url="javascript:alert(1)")
    out = ui.source_card_html(source)
    assert "فتح المصدر" not in out
    assert "لا يوجد رابط آمن" in out


def test_source_card_html_badges_toggle_on_metadata_and_verification() -> None:
    out = ui.source_card_html(_source(metadata_complete=True, citation_verified=True))
    assert "بيانات كاملة" in out
    assert "مُتحقَّق" in out
    assert "بيانات ناقصة" not in out

    out = ui.source_card_html(_source(metadata_complete=False, citation_verified=False))
    assert "بيانات ناقصة" in out
    assert "غير مُتحقَّق" in out
    assert "بيانات كاملة" not in out


def test_source_card_html_renders_optional_metadata() -> None:
    source = _source(
        page=12,
        section="وصف الموقع",
        publication_date=date(2026, 2, 15),
    )
    out = ui.source_card_html(source)
    assert "صفحة ١٢" in out
    assert "وصف الموقع" in out
    assert "١٥ فبراير ٢٠٢٦" in out


# ---------------------------------------------------------------------------
# Answer rendering
# ---------------------------------------------------------------------------


def test_answer_links_only_known_citations() -> None:
    sources = (_source(citation_id="CIT-TEST-0001"),)
    out = ui.answer_with_source_links(
        "استكشف الرياض [CIT-TEST-0001] ولا تنسَ [CIT-UNKNOWN].",
        sources,
    )
    assert "[CIT-TEST-0001](https://example.org/source?lang=ar)" in out
    assert "[CIT-UNKNOWN]" in out
    assert out.count("[") == out.count("]")  # balanced


def test_answer_keeps_unknown_citation_literal_when_url_unsafe() -> None:
    sources = (_source(citation_id="CIT-TEST-0001", url="javascript:alert(1)"),)
    out = ui.answer_with_source_links("نص [CIT-TEST-0001]", sources)
    assert "(javascript:" not in out
    assert "[CIT-TEST-0001]" in out


def test_answer_does_not_invent_citations_from_plain_text() -> None:
    out = ui.answer_with_source_links("لا استشهادات هنا.", ())
    assert out == "لا استشهادات هنا."


def test_answer_encodes_parenthesis_in_markdown_url() -> None:
    sources = (_source(citation_id="CIT-TEST-0001", url="https://example.org/a)2"),)
    out = ui.answer_with_source_links("نص [CIT-TEST-0001]", sources)
    assert "(https://example.org/a%292)" in out


# ---------------------------------------------------------------------------
# Mode / route / progress / artifact summaries
# ---------------------------------------------------------------------------


def test_mode_status_line_live() -> None:
    line = ui.mode_status_line(_mode_status())
    assert "مباشر" in line
    assert "الاسترجاع: هجين مع إعادة ترتيب" in line
    assert "نموذج احتياطي مُفعَّل" not in line


def test_mode_status_line_reports_fallback() -> None:
    line = ui.mode_status_line(_mode_status(model_fallback_used=True))
    assert "نموذج احتياطي مُفعَّل" in line


def test_mode_status_line_marks_cached_demo_as_simulated_without_live_routes() -> None:
    line = ui.mode_status_line(
        _mode_status(
            kind="cached_demo",
            execution_mode="cached_demo",
            retrieval_mode="hybrid_reranked",
            model_fallback_used=True,
        )
    )
    assert line == "عرض تجريبي محفوظ — مراحل ومخرجات محاكاة ثابتة"
    assert "الاسترجاع" not in line
    assert "نموذج احتياطي" not in line


def test_mode_status_line_handles_unknown_kind_gracefully() -> None:
    line = ui.mode_status_line(_mode_status(kind="mystery"))
    assert "وضع غير معروف" in line


def test_model_route_label_shows_resolved_model() -> None:
    route = SimpleNamespace(use_case="compose", resolved_model="gpt-4o", used_fallback=False)
    assert ui.model_route_label(route) == "gpt-4o"


def test_model_route_label_marks_fallback() -> None:
    route = SimpleNamespace(use_case="compose", resolved_model="gpt-4o", used_fallback=True)
    assert ui.model_route_label(route) == "gpt-4o (احتياطي)"


def test_progress_line_uses_arabic_labels() -> None:
    line = ui.progress_line(_event(stage="understand", state="active", summary="قراءة الطلب"))
    assert "**فهم الطلب** — قيد التنفيذ" in line
    assert "قراءة الطلب" in line


def test_progress_line_marks_simulated_events() -> None:
    line = ui.progress_line(_event(stage="render", state="completed", simulated=True))
    assert "(تجريبي)" in line


def test_artifact_download_label_combines_label_and_size() -> None:
    assert ui.artifact_download_label(_artifact()) == "برنامج الرحلة PDF (2.0 ك.ب)"


def test_artifact_download_label_with_zero_size() -> None:
    artifact = _artifact(size_bytes=0)
    assert ui.artifact_download_label(artifact) == "برنامج الرحلة PDF"


def test_mode_banner_html_marks_demo() -> None:
    out = ui.mode_banner_html(_mode_status(kind="cached_demo"), demo=True)
    assert "عرض تجريبي" in out
    assert 'class="sard-mode-banner demo"' in out


def test_mode_banner_html_sanitizes_unknown_fields() -> None:
    status = _mode_status(kind="<b>x</b>", retrieval_mode="<img src=x onerror=1>")
    out = ui.mode_banner_html(status)
    assert "<b>" not in out
    assert "<img" not in out
    assert "غير معروف" in out


# ---------------------------------------------------------------------------
# Arabic formatting
# ---------------------------------------------------------------------------


def test_to_arabic_digits() -> None:
    assert ui.to_arabic_digits(123) == "١٢٣"
    assert ui.to_arabic_digits("2026") == "٢٠٢٦"


def test_format_date_arabic_accepts_date_and_iso_string() -> None:
    assert ui.format_date_arabic(date(2026, 2, 15)) == "١٥ فبراير ٢٠٢٦"
    assert ui.format_date_arabic("2026-02-15") == "١٥ فبراير ٢٠٢٦"
    assert ui.format_date_arabic("garbage") == ""
    assert ui.format_date_arabic(None) == ""


def test_format_coverage() -> None:
    assert ui.format_coverage(0.85) == "٨٥٪"
    assert ui.format_coverage(0.0) == "٠٪"
    assert ui.format_coverage(1.5) == "١٠٠٪"  # clamped
    assert ui.format_coverage(-0.5) == "٠٪"  # clamped
    assert ui.format_coverage(None) == ""
    assert ui.format_coverage("nope") == ""


def test_format_size_bytes() -> None:
    assert ui.format_size_bytes(0) == "٠ بايت"
    assert ui.format_size_bytes(512) == "٥١٢ بايت"
    assert ui.format_size_bytes(1536) == "1.5 ك.ب"
    assert ui.format_size_bytes(2 * 1024 * 1024) == "2.0 م.ب"
    assert ui.format_size_bytes("junk") == ""


def test_new_run_id_is_safe_ascii() -> None:
    run_id = ui.new_run_id()
    assert re.fullmatch(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$", run_id)
    assert run_id.startswith("step7-")
    assert run_id != ui.new_run_id()
