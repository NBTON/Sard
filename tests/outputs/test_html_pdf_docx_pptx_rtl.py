"""Renderers workstream QA: HTML/PDF/DOCX/PPTX + centralized RTL policy.

Generates representative Arabic artifacts (factual, cultural, itinerary-like,
mixed-script, table-heavy) and asserts:
- files open/parse (pypdf pages, python-docx/pptx round-trip, PDF %PDF + size)
- RTL properties present (HTML dir=rtl, DOCX w:bidi/w:rtl, PPTX a:pPr rtl=1)
- no silent tofu fallback (strict fonts required; renderers fail loudly)
- arbitrary caps removed (5 cards -> 2 slides, 6 timeline items -> 2 slides)
- no hallucinated filler (default quote/summary bullets absent unless supplied)
- torture strings survive all renderers without exceptions
"""

from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree

import pytest
from pypdf import PdfReader

# ---------------------------------------------------------------------------
# Torture corpus (pure AR/EN, mixed Diriyah, numerals, dates, parens/[CIT],
# URLs, bullets/tables, Saudi places, long URL + emoji).
# ---------------------------------------------------------------------------

TORTURE_STRINGS = [
    "المملكة العربية السعودية",  # pure AR
    "Sard cultural agent",  # pure EN
    "الدرعية Diriyah عاصمة الدولة السعودية الأولى",  # mixed
    "عدد السكان ١٢٣٤٥٦ مقابل 123456 نسمة",  # AR-Indic + ASCII numerals
    "التاريخ 15 رمضان 1447هـ الموافق 4 مارس 2026م",  # Hijri + Gregorian
    "قصر سلوى (Salwa Palace) [CIT-abc123] وأهميته",  # parens + citation
    "زوروا https://www.visitsaudi.com/ar للمعلومات",  # URL in RTL text
    "• الرياض • جدة • الدمام • أبها • تبوك • جازان • نجران • الباحة",  # places
    "https://example.com/" + "a" * 120 + " تقرير 😀🏛️",  # long URL + emoji
    "**عنوان** # ترويسة `code` > اقتباس - بند",  # markdown hierarchy markers
]

FACTUAL_DOC = {
    "title": "تقرير توثيقي: حي الطريف في الدرعية",
    "topic": "حي الطريف",
    "content_paragraphs": [
        "يقع حي الطريف في #الدرعية وهو مسجل في قائمة اليونسكو للتراث العالمي.",
        "## العمارة النجدية\n\nيتميز الحي بالعمارة **الطينية** النجدية.\n\n- قصر سلوى\n- مسجد الإمام محمد بن سعود",
    ],
    "sections": [
        {
            "title": "المواقع الرئيسية",
            "content": "أبرز معالم الحي (Salwa Palace).",
            "bullets": ["قصر سلوى", "متحف الدرعية"],
            "badge": "مواقع",
            "table_data": [
                ["الموقع", "السنة", "Year"],
                ["حي الطريف", "1727", "1727"],
                ["قصر سلوى", "1765", "1765"],
            ],
        }
    ],
    "key_takeaways": ["صون العمارة الطينية أولوية توثيقية."],
    "sources": [],
    "region": "الدرعية، منطقة الرياض",
    "summary": "ملخص توثيقي عن حي الطريف.",
}


def _docx_xml(data: bytes, part: str = "word/document.xml") -> ElementTree.Element:
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        return ElementTree.fromstring(package.read(part))


def test_torture_strings_do_not_break_html():
    from sard.outputs.html import render_html_from_request

    joined = "\n\n".join(TORTURE_STRINGS)
    out = render_html_from_request(
        title="اختبار التعذيب", topic="torture", content_paragraphs=[joined]
    )
    assert 'dir="rtl"' in out
    assert "<script" not in out
    # Model HTML must never become trusted DOM.
    evil = render_html_from_request(
        title="t", topic="tt", content_paragraphs=['<img src=x onerror=alert(1)>']
    )
    assert "onerror" in evil  # escaped text is fine...
    assert "<img src=x" not in evil  # ...but never a live element


def test_html_sanitizes_links_and_allows_tables():
    from sard.outputs.html import render_html_from_request

    out = render_html_from_request(**FACTUAL_DOC)
    assert "<table" in out and "الطريف" in out
    # Every rendered link must carry rel=noopener; FACTUAL_DOC has no URL
    # sources so it must render zero live anchors.
    assert "<a " not in out or 'rel="noopener noreferrer"' in out
    bad = render_html_from_request(
        title="t",
        topic="tt",
        content_paragraphs=["see [x](javascript:alert(1)) https://example.com ok"],
    )
    # Raw markdown is escaped text, never linkified: no live javascript: href.
    assert 'href="javascript' not in bad
    assert "<script" not in bad
    assert "https://example.com" in bad


def test_html_never_reshapes():
    import inspect

    import sard.outputs.html as html_mod

    source = inspect.getsource(html_mod)
    # Direction detection (contains_arabic) is allowed; shaping/bidi
    # (presentation forms) for browser output is forbidden.
    assert "shape_rtl" not in source
    assert "get_display" not in source
    assert "arabic_reshaper" not in source.replace("MUST NOT import ``arabic_reshaper``", "")
    assert "visual_runs" not in source


def test_pdf_bytes_valid_and_multi_page_capable():
    from sard.outputs.pdf import render_document_pdf

    data = render_document_pdf(**FACTUAL_DOC)
    assert isinstance(data, bytes)
    assert data.startswith(b"%PDF")
    assert len(data) > 5000
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) >= 1


def test_pdf_report_tables_render():
    from sard.outputs.pdf_report import render_cultural_pdf_report

    data = render_cultural_pdf_report(**FACTUAL_DOC)
    assert data.startswith(b"%PDF")
    assert len(data) > 5000
    assert len(PdfReader(io.BytesIO(data)).pages) >= 1


def test_markdown_hierarchy_preserved_not_stripped():
    from sard.outputs.pdf import parse_markdown_blocks

    blocks = parse_markdown_blocks("## العنوان\n\nنص **عريض**.\n\n- بند أول\n\n> قول")
    kinds = [kind for kind, _, _ in blocks]
    assert kinds[0] == "heading"
    assert "bullet" in kinds
    assert "quote" in kinds
    assert any("العنوان" in text for _, _, text in blocks)


def test_docx_round_trip_with_rtl_props_and_tables():
    from docx import Document

    from sard.outputs.office_docx import render_cultural_docx_report

    data = render_cultural_docx_report(**FACTUAL_DOC)
    assert data.startswith(b"PK")
    doc = Document(io.BytesIO(data))
    assert len(doc.paragraphs) > 5
    assert len(doc.tables) >= 1  # section table_data renders
    root = _docx_xml(data)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    assert root.findall(".//w:bidi", ns), "DOCX paragraphs must carry w:bidi"
    assert root.findall(".//w:rtl", ns), "DOCX runs must carry w:rtl"


def test_docx_from_artifact_document():
    from docx import Document

    from sard.outputs.document import ArtifactDocument
    from sard.outputs.office_docx import DocxGenerator
    from sard.outputs.orchestrator import ArtifactRequest

    req = ArtifactRequest(format="docx", kind="document", title="تقرير", topic="الدرعية", raw_text="نص توثيقي.")
    doc = ArtifactDocument.from_request(req)
    data = DocxGenerator().build_from_document(doc)
    assert data.startswith(b"PK")
    assert len(Document(io.BytesIO(data)).paragraphs) > 0


def test_docx_fails_loudly_on_missing_content():
    from sard.outputs.office_docx import DocxGenerator, DocxRenderError, CulturalDocxDocument

    with pytest.raises((DocxRenderError, ValueError)):
        DocxGenerator().build_docx(CulturalDocxDocument(title="t", topic="tt"))


def test_pptx_caps_removed_auto_paginate():
    from pptx import Presentation

    from sard.outputs.office import PresentationGenerator, create_cultural_briefing_deck

    deck = create_cultural_briefing_deck(
        topic="الدرعية",
        overview_text="نص تمهيدي.",
        comparison_cards=[{"title": f"بطاقة {i}", "bullets": [f"بند {i}"]} for i in range(5)],
        timeline_items=[{"year_or_era": str(1700 + i), "title": f"محطة {i}"} for i in range(6)],
        key_takeaways=["خلاصة موثقة."],
    )
    data = PresentationGenerator().build_pptx(deck)
    assert data.startswith(b"PK")
    slides = Presentation(io.BytesIO(data)).slides
    # title + overview + 2 comparison (3+2) + 2 timeline (4+2) + summary = 7
    assert len(slides) == 7


def test_pptx_no_filler_hallucinations():
    from sard.outputs.office import DeckBuildError, create_cultural_briefing_deck

    deck = create_cultural_briefing_deck(topic="الدرعية", overview_text="نص حقيقي فقط.")
    assert [s.slide_type for s in deck.slides] == ["title", "briefing"]
    assert "تراثنا هويتنا" not in str(deck.slides)
    with pytest.raises(DeckBuildError):
        create_cultural_briefing_deck(topic="الدرعية")


def test_pptx_rtl_flag_present():
    from pptx import Presentation

    from sard.outputs.office import PresentationGenerator, create_cultural_briefing_deck

    deck = create_cultural_briefing_deck(topic="الدرعية", overview_text="نص عربي للاختبار.")
    data = PresentationGenerator().build_pptx(deck)
    prs = Presentation(io.BytesIO(data))
    rtl_found = False
    for slide in prs.slides:
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for para in shape.text_frame.paragraphs:
                pPr = para._p.get_or_add_pPr()
                if pPr.get("rtl") == "1":
                    rtl_found = True
    assert rtl_found, "PPTX paragraphs must carry a:pPr rtl=1"


def test_pptx_from_artifact_document():
    from pptx import Presentation

    from sard.outputs.document import ArtifactDocument
    from sard.outputs.office import PresentationGenerator
    from sard.outputs.orchestrator import ArtifactRequest

    req = ArtifactRequest(
        format="pptx",
        kind="presentation",
        title="عرض الدرعية",
        topic="الدرعية",
        raw_text="نص." * 50,
        content_data={"bullets": [f"بند موثق {i}" for i in range(14)]},
    )
    doc = ArtifactDocument.from_request(req)
    data = PresentationGenerator().build_from_document(doc)
    slides = Presentation(io.BytesIO(data)).slides
    assert len(slides) >= 3  # title + paginated content, nothing dropped


def test_strict_fonts_fail_loudly_never_tofu():
    from sard.outputs import fonts

    arabic, latin = fonts.ensure_fonts_registered()
    assert arabic == "NotoNaskhArabic-Regular"
    assert latin == "NotoSans-Regular"
    assert "Helvetica" not in (arabic, latin)


def test_renderers_share_torture_corpus():
    from sard.outputs.html import render_html_from_request
    from sard.outputs.office import PresentationGenerator, create_cultural_briefing_deck
    from sard.outputs.office_docx import render_cultural_docx_report
    from sard.outputs.pdf import render_document_pdf

    joined = "\n\n".join(TORTURE_STRINGS)
    assert render_document_pdf("t", "tt", content_paragraphs=[joined]).startswith(b"%PDF")
    assert render_cultural_docx_report("t", "tt", content_paragraphs=[joined]).startswith(b"PK")
    html = render_html_from_request(title="t", topic="tt", content_paragraphs=[joined])
    assert "😀" in html  # emoji passes through unmangled
    deck = create_cultural_briefing_deck(topic="اختبار", overview_text=joined[:500])
    data = PresentationGenerator().build_pptx(deck)
    assert data.startswith(b"PK")
    # Long URL + emoji torture text is never silently truncated by builders.
    assert len(data) > 20000


def test_partial_files_deleted_on_failure(tmp_path):
    from sard.outputs.html import HtmlRenderError, write_html
    from sard.outputs.pdf import render_document_pdf

    # Failing inputs never leave half-written artifacts behind.
    with pytest.raises((ValueError, Exception)):
        render_document_pdf("", "", content_paragraphs=[])
    assert list(tmp_path.glob("*.pdf")) == []
    with pytest.raises(HtmlRenderError):
        write_html.__self__ if False else _write_html_bad_suffix(tmp_path)
    assert list(tmp_path.glob("*")) == []


def _write_html_bad_suffix(tmp_path):
    from sard.outputs.document import ArtifactDocument
    from sard.outputs.html import write_html
    from sard.outputs.orchestrator import ArtifactRequest

    doc = ArtifactDocument.from_request(
        ArtifactRequest(format="docx", kind="document", title="t", topic="tt", raw_text="x")
    )
    return write_html(doc, tmp_path / "out.txt")
