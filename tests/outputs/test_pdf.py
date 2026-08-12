from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import fitz
import pytest
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from sard.outputs.arabic import escape_reportlab_markup, shape_rtl, visual_runs
from sard.outputs.fonts import (
    ArabicFontError,
    DEFAULT_FONT_PATH,
    DEFAULT_LATIN_FONT_PATH,
    FONT_SHA256,
    LATIN_FONT_SHA256,
    require_arabic_font,
)
from sard.outputs.pdf import render_pdf, safe_pdf_filename, wrap_logical_lines
from sard.outputs.sample import representative_fixture
from sard.outputs.schemas import CitationSource, Itinerary, ItineraryDay, TextBlock


def test_arabic_shaping_changes_codepoints_and_is_rtl():
    visual = shape_rtl("مرحبا بالعالم")
    assert visual != "مرحبا بالعالم"
    assert "ﻢ" in visual or "ﻣ" in visual


def test_mixed_text_preserves_urls_and_citation_ids_exactly():
    url = "https://example.org/a?x=1&lang=ar"
    citation = "[CIT-DEMO-123]"
    visual = shape_rtl(f"راجع English {url} ثم {citation}")
    assert url in visual
    assert citation in visual


@pytest.mark.parametrize("sample", ["الأرقام ١٢٣ و123!", "اختبار: (نعم؟) +50%"])
def test_numerals_and_punctuation_survive_shaping(sample):
    visual = shape_rtl(sample)
    for expected in ("١٢٣", "123"):
        if expected in sample:
            assert expected in visual
    assert "?" not in sample or "?" in visual


def test_prefixed_numeric_tokens_keep_logical_order():
    visual = shape_rtl("الرموز %50 و+3 و-12 مستقرة")
    assert "%50" in visual
    assert "+3" in visual
    assert "-12" in visual


def test_wrapping_uses_shaped_line_widths():
    font_name = "SardTestNaskh"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, str(DEFAULT_FONT_PATH)))
    lines = wrap_logical_lines(
        "نص عربي طويل لاختبار التفاف الأسطر مع English و123 وعلامات الترقيم",
        120,
        font_name,
        12,
    )
    assert len(lines) > 1
    assert all(
        sum(
            pdfmetrics.stringWidth(run, font_name if arabic else "Helvetica", 12)
            for arabic, run in visual_runs(shape_rtl(line))
        )
        <= 120
        for line in lines
    )


def _itinerary(block: TextBlock, sources=()) -> Itinerary:
    return Itinerary(
        title="عنوان",
        summary="ملخص",
        days=(ItineraryDay("يوم", notes=(block,)),),
        sources=tuple(sources),
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_unknown_declared_and_inline_citations_are_rejected():
    with pytest.raises(ValueError, match="CIT-UNKNOWN-999"):
        _itinerary(TextBlock("نص", ("CIT-UNKNOWN-999",))).validate_citations()
    with pytest.raises(ValueError, match="CIT-UNKNOWN-888"):
        _itinerary(TextBlock("نص [CIT-UNKNOWN-888]")).validate_citations()


def test_citation_mapping_uses_only_supplied_metadata():
    source = CitationSource("CIT-SOURCE-123", "العنوان", "https://example.org")
    mapping = _itinerary(TextBlock("نص", (source.citation_id,)), (source,)).validate_citations()
    assert mapping == {source.citation_id: source}
    assert mapping[source.citation_id].page is None
    assert mapping[source.citation_id].publication_date is None


def test_reportlab_markup_is_fully_escaped():
    assert escape_reportlab_markup('<link href="x">A&B</link>') == (
        "&lt;link href=&quot;x&quot;&gt;A&amp;B&lt;/link&gt;"
    )


def test_markup_like_content_is_rendered_as_literal_text(monkeypatch, tmp_path):
    monkeypatch.setenv("SARD_PDF_OUTPUT_ROOT", str(tmp_path))
    artifact = render_pdf(_itinerary(TextBlock('<b>literal & safe</b>')), "literal.pdf")
    with fitz.open(artifact.path) as document:
        extracted = "".join(page.get_text() for page in document)
    assert "<b>literal & safe</b>" in extracted


def test_missing_configured_font_fails_without_fallback(monkeypatch, tmp_path):
    missing = tmp_path / "missing.ttf"
    monkeypatch.setenv("SARD_ARABIC_FONT_PATH", str(missing))
    with pytest.raises(ArabicFontError, match="never falls back silently"):
        require_arabic_font()


def test_safe_output_paths_and_no_overwrite(monkeypatch, tmp_path):
    root = tmp_path / "pdf-root"
    monkeypatch.setenv("SARD_PDF_OUTPUT_ROOT", str(root))
    itinerary = representative_fixture()
    with pytest.raises(ValueError, match="inside configured root"):
        render_pdf(itinerary, tmp_path / "outside.pdf")
    with pytest.raises(ValueError, match="end in .pdf"):
        render_pdf(itinerary, "not-a-pdf.txt")
    artifact = render_pdf(itinerary, "nested/sample.pdf")
    assert artifact.path.parent == root / "nested"
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        render_pdf(itinerary, "nested/sample.pdf")
    assert safe_pdf_filename("../../خطة الرحلة") == "sard-itinerary.pdf"


def test_successful_multipage_pdf(monkeypatch, tmp_path):
    monkeypatch.setenv("SARD_PDF_OUTPUT_ROOT", str(tmp_path))
    artifact = render_pdf(representative_fixture(), "fixture.pdf")
    assert artifact.mime_type == "application/pdf"
    assert artifact.filename == "fixture.pdf"
    assert artifact.size_bytes == artifact.path.stat().st_size > 10_000
    assert artifact.warnings == ()
    with fitz.open(artifact.path) as document:
        assert document.page_count >= 3
        assert all(page.rect.height > page.rect.width for page in document)
        assert all(page.get_pixmap(matrix=fitz.Matrix(0.25, 0.25)).width > 0 for page in document)
        extracted = "".join(page.get_text() for page in document)
        assert "CIT-DEMO-SPRING-001" in extracted
        assert "CIT-DEMO-MARKET-002" in extracted
        assert "https://example.org/arabic-springs?lang=ar&ref=PDF" in extracted


def test_very_long_body_splits_across_pages(monkeypatch, tmp_path):
    from dataclasses import replace

    monkeypatch.setenv("SARD_PDF_OUTPUT_ROOT", str(tmp_path))
    fixture = representative_fixture()
    long_block = TextBlock(
        text=" ".join(["نص عربي طويل مع English 123."] * 700),
        citation_ids=("CIT-DEMO-SPRING-001",),
    )
    first_stop = replace(fixture.days[0].stops[0], paragraphs=(long_block,))
    first_day = replace(
        fixture.days[0], stops=(first_stop, *fixture.days[0].stops[1:])
    )
    fixture = replace(fixture, days=(first_day, *fixture.days[1:]))
    artifact = render_pdf(fixture, "long-body.pdf")
    with fitz.open(artifact.path) as document:
        assert document.page_count >= 4
        assert all(
            0 <= block[1] and block[3] <= page.rect.height
            for page in document
            for block in page.get_text("blocks")
        )


def test_long_footer_citations_stay_inside_page(monkeypatch, tmp_path):
    from dataclasses import replace
    from sard.outputs.schemas import CitationSource

    monkeypatch.setenv("SARD_PDF_OUTPUT_ROOT", str(tmp_path))
    fixture = representative_fixture()
    original = fixture.days[0].stops[0].paragraphs[0]
    extra = tuple(
        CitationSource(
            citation_id=f"CIT-DEMO-EXTRA-{index:03d}",
            title=f"مصدر تجريبي {index}",
            url=f"https://example.org/{index}",
        )
        for index in range(12)
    )
    cited_block = replace(
        original,
        citation_ids=(*original.citation_ids, *(source.citation_id for source in extra)),
    )
    first_stop = replace(fixture.days[0].stops[0], paragraphs=(cited_block,))
    first_day = replace(
        fixture.days[0], stops=(first_stop, *fixture.days[0].stops[1:])
    )
    fixture = replace(
        fixture,
        days=(first_day, *fixture.days[1:]),
        sources=(*fixture.sources, *extra),
    )
    artifact = render_pdf(fixture, "long-footer.pdf")
    with fitz.open(artifact.path) as document:
        assert all(
            0 <= block[0] <= block[2] <= page.rect.width
            and 0 <= block[1] <= block[3] <= page.rect.height
            for page in document
            for block in page.get_text("blocks")
        )


def test_pdf_modules_do_not_import_forbidden_architecture():
    forbidden = ("langchain", "nvidia", "zvec", "sard.ui")
    for filename in ("schemas.py", "arabic.py", "fonts.py", "pdf.py"):
        path = Path("sard/outputs") / filename
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not any(name.startswith(forbidden) for name in imported)


def test_bundled_font_checksum_is_pinned():
    import hashlib

    assert hashlib.sha256(DEFAULT_FONT_PATH.read_bytes()).hexdigest() == FONT_SHA256
    assert hashlib.sha256(DEFAULT_LATIN_FONT_PATH.read_bytes()).hexdigest() == LATIN_FONT_SHA256
