"""Renderer & RTL audit regressions (audit: renderer_rtl_audit_plan.md).

Covers every audit blocker/quality fix inside sard/outputs/**:
evidence-rule enforcement, canonical PDF routing/adapter, long-paragraph
splitting, PDF header/footer font correctness + footer citations,
DOCX table/OOXML/RTL fixes, PPTX image robustness/pagination/headings,
HTML direction/list/source behavior, and mixed RTL token preservation.
"""

from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree

from pypdf import PdfReader

from sard.outputs.arabic import shape_rtl
from sard.outputs.document import (
    ArtifactBlock,
    ArtifactDocument,
    ArtifactMetadata,
    ArtifactSection,
    ArtifactTheme,
)
from sard.outputs.pdf import build_pdf_from_document, clean_pdf_text
from sard.outputs.schemas import CitationSource

CID = "CIT-AUDIT-001"


def _sources() -> tuple:
    return (
        CitationSource(
            CID, "دليل الواحة", "https://example.org/oasis",
            page=4, section="المسار الشرقي",
        ),
    )


def _doc_with_evidence() -> ArtifactDocument:
    return ArtifactDocument(
        metadata=ArtifactMetadata(
            artifact_id="art-audit-1", run_id="run-audit-1",
            format="pdf", kind="document", title="تقرير التدقيق", topic="الواحة",
        ),
        sections=(
            ArtifactSection(
                section_id="s1", title="قسم",
                blocks=(
                    ArtifactBlock(block_id="cited", block_type="paragraph",
                                  text="CITED-MARKER-123 موثق", source_ids=(CID,)),
                    ArtifactBlock(block_id="plain", block_type="paragraph",
                                  text="UNVERIFIED-MARKER-456", verification_status="verified"),
                    ArtifactBlock(block_id="user", block_type="paragraph",
                                  text="USER-MARKER-789", verification_status="user_provided"),
                ),
            ),
        ),
        sources=_sources(),
    )


# --- RTL token preservation (RTL-B1, RTL-Q1, RTL-Q2) --------------------------


def test_shape_rtl_mixed_parens_and_citations():
    out = shape_rtl("قصر سلوى (Salwa Palace) [CIT-abc123] وأهميته")
    assert "(Salwa Palace)" in out
    assert out.count("(") == out.count(")")


def test_shape_rtl_year_suffix_and_numeric_citations():
    assert "1727م" in shape_rtl("سنة 1727م")
    assert "1447هـ" in shape_rtl("عام 1447هـ")
    assert "[1]" in shape_rtl("البند [1] مهم")


# --- Evidence rule across renderers (HTML-B1, DOCX-B2, PPTX-B4) ----------------


def test_evidence_rule_enforced_across_all_renderers():
    from sard.outputs.html import render_html_document
    from sard.outputs.office import PresentationGenerator
    from sard.outputs.office_docx import DocxGenerator

    doc = _doc_with_evidence()

    html = render_html_document(doc)
    assert "CITED-MARKER-123" in html
    assert "USER-MARKER-789" in html
    assert "UNVERIFIED-MARKER-456" not in html

    docx_data = DocxGenerator().build_from_document(doc)
    with zipfile.ZipFile(io.BytesIO(docx_data)) as package:
        xml_text = package.read("word/document.xml").decode("utf-8")
    assert "CITED-MARKER-123" in xml_text
    assert "UNVERIFIED-MARKER-456" not in xml_text

    pptx_data = PresentationGenerator().build_from_document(doc)
    from pptx import Presentation

    slide_text = " ".join(
        shape.text
        for slide in Presentation(io.BytesIO(pptx_data)).slides
        for shape in slide.shapes
        if shape.has_text_frame
    )
    assert "CITED-MARKER-123" in slide_text
    assert "UNVERIFIED-MARKER-456" not in slide_text

    pdf_data = build_pdf_from_document(doc)
    reader = PdfReader(io.BytesIO(pdf_data))
    pdf_text = "".join(page.extract_text() or "" for page in reader.pages)
    assert "UNVERIFIED-MARKER-456" not in pdf_text


# --- HTML (HTML-B2, HTML-Q1, HTML-Q2, HTML-Q4) ---------------------------------


def _bullets_doc() -> ArtifactDocument:
    return ArtifactDocument(
        metadata=ArtifactMetadata(
            artifact_id="a", format="html", kind="document",
            title="t", topic="tt",
        ),
        sections=(
            ArtifactSection(
                section_id="s1", title="",
                blocks=tuple(
                    ArtifactBlock(block_id=f"b{i}", block_type="bullet",
                                  text=f"بند {i}", verification_status="user_provided")
                    for i in range(3)
                ),
            ),
        ),
    )


def test_html_bullet_list_coalesced():
    from sard.outputs.html import render_html_document

    html = render_html_document(_bullets_doc())
    assert html.count('<ul class="sard-list">') == 1
    assert html.count("<li") == 3


def test_html_table_direction_follows_theme():
    from sard.outputs.html import render_html_document

    def _table_doc(direction: str) -> ArtifactDocument:
        return ArtifactDocument(
            metadata=ArtifactMetadata(
                artifact_id="a", format="html", kind="document", title="t", topic="tt",
            ),
            theme=ArtifactTheme(direction=direction, locale="en-US" if direction == "ltr" else "ar-SA"),
            sections=(
                ArtifactSection(
                    section_id="s1", title="",
                    blocks=(
                        ArtifactBlock(block_id="t1", block_type="table",
                                      text="", data={"rows": [["a", "b"], ["c", "d"]]},
                                      verification_status="user_provided"),
                    ),
                ),
            ),
        )

    assert '<div dir="ltr"><table' in render_html_document(_table_doc("ltr"))
    assert '<div dir="rtl"><table' in render_html_document(_table_doc("rtl"))


def test_html_images_require_https_csp_alignment():
    from sard.outputs.html import render_html_document

    def _img_doc(src: str) -> ArtifactDocument:
        return ArtifactDocument(
            metadata=ArtifactMetadata(
                artifact_id="a", format="html", kind="document", title="t", topic="tt",
            ),
            sections=(
                ArtifactSection(
                    section_id="s1", title="",
                    blocks=(
                        ArtifactBlock(block_id="i1", block_type="image", text="صورة",
                                      data={"src": src}, verification_status="user_provided"),
                    ),
                ),
            ),
        )

    assert "<img" in render_html_document(_img_doc("https://example.com/x.png"))
    assert "<img" not in render_html_document(_img_doc("http://example.com/x.png"))


def test_html_sources_include_metadata():
    from sard.outputs.html import render_html_document

    html = render_html_document(_doc_with_evidence())
    assert "صفحة 4" in html
    assert "المسار الشرقي" in html


# --- DOCX (DOCX-B1, DOCX-Q1, DOCX-Q2) ------------------------------------------


def _docx_root(data: bytes, part: str = "word/document.xml") -> ElementTree.Element:
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        return ElementTree.fromstring(package.read(part))


def test_docx_table_column_order_not_double_reversed():
    from sard.outputs.office_docx import DocxGenerator

    doc = ArtifactDocument(
        metadata=ArtifactMetadata(
            artifact_id="a", format="docx", kind="document", title="t", topic="tt",
        ),
        sections=(
            ArtifactSection(
                section_id="s1", title="",
                blocks=(
                    ArtifactBlock(block_id="t1", block_type="table", text="",
                                  data={"rows": [["R1C1", "R1C2"], ["R2C1", "R2C2"]]},
                                  verification_status="user_provided"),
                ),
            ),
        ),
    )
    root = _docx_root(DocxGenerator().build_from_document(doc))
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    first_row = root.findall(".//w:tr", ns)[0]
    cells = ["".join(t.text or "" for t in cell.findall(".//w:t", ns)) for cell in first_row.findall("w:tc", ns)]
    assert cells[0] == "R1C1"  # logical column 0 stays first in XML; bidiVisual flips display
    assert root.find(".//w:bidiVisual", ns) is not None


def test_docx_rpr_and_sectpr_schema_order():
    from sard.outputs.office_docx import DocxGenerator

    root = _docx_root(DocxGenerator().build_from_document(_doc_with_evidence()))
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

    def _local(tag: str) -> str:
        return tag.split("}", 1)[-1]

    for rpr in root.findall(".//w:rPr", ns):
        kinds = [_local(child.tag) for child in rpr]
        if "szCs" in kinds and "rtl" in kinds:
            assert kinds.index("szCs") < kinds.index("rtl")

    sect_pr = _docx_root(
        DocxGenerator().build_from_document(_doc_with_evidence()), "word/document.xml"
    )
    # sectPr lives in document.xml too (section properties at body end).
    for spr in root.findall(".//w:sectPr", ns):
        kinds = [_local(child.tag) for child in spr]
        if "bidi" in kinds and "docGrid" in kinds:
            assert kinds.index("bidi") < kinds.index("docGrid")
    assert sect_pr is not None


# --- PPTX (PPTX-B1/B2/B3, PPTX-Q1/Q2) ------------------------------------------


def _pptx_text(data: bytes) -> list[str]:
    from pptx import Presentation

    texts: list[str] = []
    for slide in Presentation(io.BytesIO(data)).slides:
        for shape in slide.shapes:
            if shape.has_table:
                for row in shape.table.rows:
                    for cell in row.cells:
                        texts.append(cell.text)
            elif shape.has_text_frame:
                texts.append(shape.text)
    return texts


def test_pptx_table_pagination_no_silent_truncation():
    from sard.outputs.office import PresentationGenerator

    rows = [[f"R{r}C{c}" for c in range(3)] for r in range(25)]
    doc = ArtifactDocument(
        metadata=ArtifactMetadata(
            artifact_id="a", format="pptx", kind="presentation", title="t", topic="tt",
        ),
        sections=(
            ArtifactSection(
                section_id="s1", title="جدول",
                blocks=(
                    ArtifactBlock(block_id="t1", block_type="table", text="",
                                  data={"rows": rows}, verification_status="user_provided"),
                ),
            ),
        ),
    )
    texts = _pptx_text(PresentationGenerator().build_from_document(doc))
    joined = "\n".join(texts)
    assert "R0C0" in joined and "R24C2" in joined  # first AND last rows survive
    assert any("(تابع)" in t for t in texts)  # continuation slides exist


def test_pptx_remote_image_no_crash():
    from sard.outputs.office import PresentationGenerator

    doc = ArtifactDocument(
        metadata=ArtifactMetadata(
            artifact_id="a", format="pptx", kind="presentation", title="t", topic="tt",
        ),
        sections=(
            ArtifactSection(
                section_id="s1", title="صور",
                blocks=(
                    ArtifactBlock(block_id="i1", block_type="image", text="صورة بعيدة",
                                  data={"src": "https://example.com/img.png"},
                                  verification_status="user_provided"),
                ),
            ),
        ),
    )
    data = PresentationGenerator().build_from_document(doc)
    assert data.startswith(b"PK")
    assert "https://example.com/img.png" in "\n".join(_pptx_text(data))


def test_pptx_heading_associates_with_content():
    from sard.outputs.office import PresentationGenerator

    doc = ArtifactDocument(
        metadata=ArtifactMetadata(
            artifact_id="a", format="pptx", kind="presentation", title="t", topic="tt",
        ),
        sections=(
            ArtifactSection(
                section_id="s1", title="القسم",
                blocks=(
                    ArtifactBlock(block_id="h1", block_type="heading", text="عنوان فرعي",
                                  verification_status="user_provided"),
                    ArtifactBlock(block_id="p1", block_type="paragraph", text="نص الفقرة",
                                  verification_status="user_provided"),
                ),
            ),
        ),
    )
    from pptx import Presentation

    slides = Presentation(io.BytesIO(PresentationGenerator().build_from_document(doc))).slides
    # Title slide + exactly one content slide titled by the heading.
    assert len(slides) == 2
    body_texts = [
        shape.text for shape in slides[1].shapes
        if shape.has_text_frame and shape.text_frame.paragraphs
    ]
    assert any("عنوان فرعي" in t for t in body_texts)
    assert any("نص الفقرة" in t for t in body_texts)


def test_pptx_long_bullets_paginate_and_sources_paginate():
    from sard.outputs.office import PresentationGenerator

    doc = ArtifactDocument(
        metadata=ArtifactMetadata(
            artifact_id="a", format="pptx", kind="presentation", title="t", topic="tt",
        ),
        sections=(
            ArtifactSection(
                section_id="s1", title="نقاط",
                blocks=tuple(
                    ArtifactBlock(block_id=f"b{i}", block_type="bullet",
                                  text=f"بند طويل {i} " + "نص " * 60,
                                  verification_status="user_provided")
                    for i in range(6)
                ),
            ),
        ),
        sources=tuple(
            CitationSource(f"CIT-PAGE-{i:03d}", f"مصدر {i}", "https://example.org/x")
            for i in range(8)
        ),
    )
    from pptx import Presentation

    slides = Presentation(io.BytesIO(PresentationGenerator().build_from_document(doc))).slides
    assert len(slides) >= 4  # title + 2 bullet continuations + 2 sources slides (min)
    joined = "\n".join(
        shape.text for slide in slides for shape in slide.shapes if shape.has_text_frame
    )
    assert "CIT-PAGE-007" in joined


# --- PDF (PDF-B2/B5/Q2 + long split + determinism) ------------------------------


def test_pdf_long_paragraph_no_layout_error():
    long_text = "نص طويل متدفق للاختبار " * 400  # ~8000 chars, multi-page
    doc = ArtifactDocument(
        metadata=ArtifactMetadata(
            artifact_id="a", format="pdf", kind="document", title="تقرير طويل", topic="اختبار",
        ),
        sections=(
            ArtifactSection(
                section_id="s1", title="متن",
                blocks=(
                    ArtifactBlock(block_id="p1", block_type="paragraph",
                                  text=long_text, verification_status="user_provided"),
                ),
            ),
        ),
    )
    data = build_pdf_from_document(doc)
    assert data.startswith(b"%PDF")
    assert len(PdfReader(io.BytesIO(data)).pages) >= 2


def test_pdf_footer_citations_present():
    doc = ArtifactDocument(
        metadata=ArtifactMetadata(
            artifact_id="a", format="pdf", kind="document", title="t", topic="tt",
        ),
        sections=(
            ArtifactSection(
                section_id="s1", title="",
                blocks=(
                    ArtifactBlock(block_id="p1", block_type="paragraph",
                                  text=f"حقيقة موثقة [{CID}]", source_ids=(CID,)),
                ),
            ),
        ),
        sources=_sources(),
    )
    text = "".join(
        page.extract_text() or "" for page in PdfReader(io.BytesIO(build_pdf_from_document(doc))).pages
    )
    assert CID in text


def test_pdf_hyperlink_target_preserved():
    cleaned = clean_pdf_text("راجع [الدليل](https://example.com/guide) للمزيد")
    assert "https://example.com/guide" in cleaned
    assert "الدليل" in cleaned


def test_pdf_canonical_bytes_deterministic():
    first = build_pdf_from_document(_doc_with_evidence())
    second = build_pdf_from_document(_doc_with_evidence())
    assert first == second


def test_pdf_report_no_nul_bytes_and_splits_long_text():
    from sard.outputs.pdf_report import render_cultural_pdf_report

    data = render_cultural_pdf_report(
        title="تقرير — اختبار • شامل",
        topic="اختبار",
        content_paragraphs=["فقرة طويلة " * 1500],
        region="نجد",
        summary="ملخص",
    )
    assert data.startswith(b"%PDF")
    # NUL bytes are legal inside compressed PDF streams; the audit blocker is
    # NUL *glyphs in drawn text* (missing-glyph emission), so assert on the
    # extracted text layer instead of the raw binary.
    extracted = "".join(
        page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages
    )
    assert "\x00" not in extracted
    assert "—" in extracted or "•" in extracted
    assert len(PdfReader(io.BytesIO(data)).pages) >= 2
