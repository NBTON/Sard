"""Cultural DOCX generator for Sard, built on python-docx.

Generates standards-compliant Word (.docx) documents from the canonical
:class:`ArtifactDocument <sard.outputs.document.ArtifactDocument>` (or the
legacy ``render_cultural_docx_report`` kwargs): heading/paragraph styles,
numbering, tables, images, sources, Arabic RTL properties, fonts, margins,
headers/footers.

RTL POLICY: native Unicode + RTL properties (``w:bidi`` on paragraphs,
``w:rtl`` on runs, complex-script typefaces).  NO pre-reshaping — Word
shapes Arabic natively.

FONTS: rendering fails loudly when content is missing; partial files are
deleted on failure.
"""

from __future__ import annotations

import io
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Pt, RGBColor, Inches


logger = logging.getLogger("sard.outputs.office_docx")

MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

FONT_ARABIC = "Noto Naskh Arabic"
FONT_BODY = "IBM Plex Sans Arabic"
FONT_FALLBACK = "Arial"

COLOR_INK = RGBColor(0x14, 0x12, 0x10)
COLOR_CLAY = RGBColor(0xBE, 0x4A, 0x24)
COLOR_DATE = RGBColor(0x6E, 0x1F, 0x1F)
COLOR_OLIVE = RGBColor(0x4A, 0x51, 0x3C)
COLOR_GOLD = RGBColor(0xC4, 0xA4, 0x6A)
COLOR_MUTED = RGBColor(0x8A, 0x81, 0x78)


class DocxRenderError(ValueError):
    """DOCX rendering failed loudly (missing content, bad path)."""


@dataclass
class DocxSection:
    title: str
    content: str
    bullets: List[str] = field(default_factory=list)
    badge: str = ""
    table_data: Optional[List[List[str]]] = None


@dataclass
class CulturalDocxDocument:
    title: str
    topic: str
    summary: str = ""
    region: str = "المملكة العربية السعودية"
    author: str = "سرد — المستشار الثقافي المعتمد"
    paragraphs: List[str] = field(default_factory=list)
    sections: List[DocxSection] = field(default_factory=list)
    key_takeaways: List[str] = field(default_factory=list)
    sources: List[Dict[str, str]] = field(default_factory=list)
    doc_id: str = field(default_factory=lambda: f"doc-{uuid.uuid4().hex[:8]}")


def _set_run_rtl(run, *, size: Optional[Pt] = None, bold: bool = False, color=None, font: Optional[str] = None) -> None:
    """Apply complex-script font + RTL marker to a python-docx run (native Unicode)."""

    run.font.name = font or FONT_BODY
    if size is not None:
        run.font.size = size
    run.font.bold = bold or None
    if color is not None:
        run.font.color.rgb = color
    r = run._r
    rPr = r.get_or_add_rPr()
    rtl = OxmlElement("w:rtl")
    rPr.append(rtl)
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    rFonts.set(qn("w:cs"), FONT_ARABIC)
    rFonts.set(qn("w:ascii"), font or FONT_BODY)
    rFonts.set(qn("w:hAnsi"), font or FONT_BODY)
    szCs = OxmlElement("w:szCs")
    szCs.set(qn("w:val"), str(int((size.pt if size else 11) * 2)))
    rPr.append(szCs)


def _set_paragraph_rtl(paragraph, *, align: WD_ALIGN_PARAGRAPH = WD_ALIGN_PARAGRAPH.RIGHT) -> None:
    """Right-align + ``w:bidi`` so Word lays out mixed Arabic/Latin correctly."""

    paragraph.alignment = align
    pPr = paragraph._p.get_or_add_pPr()
    if pPr.find(qn("w:bidi")) is None:
        pPr.append(OxmlElement("w:bidi"))


def _add_paragraph(doc: Document, text: str, *, size: float = 11, bold: bool = False, color=None, font: Optional[str] = None, style: Optional[str] = None, space_after: float = 6) -> Any:
    paragraph = doc.add_paragraph(style=style) if style else doc.add_paragraph()
    _set_paragraph_rtl(paragraph)
    paragraph.paragraph_format.space_after = Pt(space_after)
    run = paragraph.add_run(str(text or ""))
    _set_run_rtl(run, size=Pt(size), bold=bold, color=color, font=font)
    return paragraph


def _add_bullet(doc: Document, text: str, *, style: str = "List Bullet") -> Any:
    try:
        paragraph = doc.add_paragraph(style=style)
    except (KeyError, ValueError):
        paragraph = doc.add_paragraph()
        paragraph.style = doc.styles["Normal"]
    _set_paragraph_rtl(paragraph)
    run = paragraph.add_run(str(text or ""))
    _set_run_rtl(run, size=Pt(11))
    return paragraph


def _add_table(doc: Document, rows: Sequence[Sequence[str]]) -> Any:
    """Add an RTL table (logical-first column renders rightmost via tblBidiVisual)."""

    cleaned = [[str(c or "") for c in row] for row in rows if row]
    if not cleaned:
        raise DocxRenderError("Cannot render an empty table.")
    width = max(len(row) for row in cleaned)
    normalized = [row + [""] * (width - len(row)) for row in cleaned]
    # RTL visual order: reverse columns so logical-first is rightmost.
    rtl_rows = [list(reversed(row)) for row in normalized]
    table = doc.add_table(rows=len(rtl_rows), cols=width)
    table.style = "Light Grid Accent 1"
    try:
        tbl = table._tbl
        tblPr = tbl.tblPr
        bidi = OxmlElement("w:bidiVisual")
        tblPr.append(bidi)
    except Exception as exc:  # Non-fatal: table still renders LTR-grid.
        logger.debug("Could not set tblBidiVisual: %s", type(exc).__name__)
    for row_idx, row in enumerate(rtl_rows):
        for col_idx, cell_text in enumerate(row):
            cell = table.cell(row_idx, col_idx)
            cell.text = ""
            paragraph = cell.paragraphs[0]
            _set_paragraph_rtl(paragraph)
            run = paragraph.add_run(cell_text)
            _set_run_rtl(
                run,
                size=Pt(11 if row_idx == 0 else 10),
                bold=row_idx == 0,
                color=COLOR_DATE if row_idx == 0 else None,
            )
            if row_idx == 0:
                shading = OxmlElement("w:shd")
                shading.set(qn("w:val"), "clear")
                shading.set(qn("w:fill"), "E8E0D2")
                cell._tc.get_or_add_tcPr().append(shading)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)
    return table


def _add_image(doc: Document, image_path: str, *, width_in: float = 5.5) -> bool:
    """Best-effort local image; returns False (renders nothing) when unavailable."""

    candidate = Path(str(image_path or "").strip())
    if not candidate.is_file():
        return False
    try:
        paragraph = doc.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.add_run().add_picture(str(candidate), width=Inches(width_in))
        return True
    except Exception as exc:
        logger.debug("Skipping unreadable image %s: %s", candidate, type(exc).__name__)
        return False


def _apply_page_setup(doc: Document) -> None:
    for section in doc.sections:
        section.top_margin = Inches(1.0)
        section.bottom_margin = Inches(1.0)
        section.left_margin = Inches(1.0)
        section.right_margin = Inches(1.0)
        sectPr = section._sectPr
        bidi = OxmlElement("w:bidi")
        sectPr.append(bidi)


def _apply_header_footer(doc: Document, author: str) -> None:
    try:
        section = doc.sections[0]
        header = section.header
        h_para = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
        _set_paragraph_rtl(h_para)
        run = h_para.add_run("المملكة العربية السعودية • وزارة الثقافة — سرد")
        _set_run_rtl(run, size=Pt(8), color=COLOR_MUTED)
        footer = section.footer
        f_para = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        _set_paragraph_rtl(f_para)
        run = f_para.add_run(f"سرد — المستشار الثقافي | {author}")
        _set_run_rtl(run, size=Pt(8), color=COLOR_MUTED)
    except Exception as exc:
        logger.debug("Header/footer setup skipped: %s", type(exc).__name__)


class DocxGenerator:
    """Generates standard OOXML .docx files via python-docx with Arabic RTL support."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path("output")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # -- canonical ArtifactDocument entry point ---------------------------

    def build_from_document(self, doc) -> bytes:
        """Build DOCX bytes from a canonical ArtifactDocument (artifact agent shape)."""

        from sard.outputs.document import ArtifactDocument as _ArtifactDocument

        if not isinstance(doc, _ArtifactDocument):
            raise DocxRenderError("build_from_document requires an ArtifactDocument.")
        meta = doc.metadata
        title = (meta.title or "").strip()
        topic = (meta.topic or "").strip()
        if not title:
            raise DocxRenderError("Artifact title is required.")
        if not topic:
            raise DocxRenderError("Artifact topic is required.")
        document = Document()
        _apply_page_setup(document)
        _apply_header_footer(document, meta.region or "")
        _add_paragraph(document, "المملكة العربية السعودية • وزارة الثقافة (سرد)", size=9, bold=True, color=COLOR_CLAY, space_after=2)
        _add_paragraph(document, title, size=20, bold=True, color=COLOR_INK, font=FONT_ARABIC, space_after=2)
        _add_paragraph(document, f"الموضوع: {topic} | المنطقة: {meta.region}", size=9, color=COLOR_MUTED, space_after=8)
        if meta.warnings:
            for warning in meta.warnings:
                if str(warning or "").strip():
                    _add_paragraph(document, f"تنبيه: {warning}", size=9, color=COLOR_CLAY, space_after=2)
        for section in doc.sections:
            if section.title.strip():
                _add_paragraph(document, section.title.strip(), size=15, bold=True, color=COLOR_DATE, font=FONT_ARABIC, space_after=4)
            for block in section.blocks:
                self._render_block(document, block)
        if doc.sources:
            _add_paragraph(document, "المراجع والتوثيق المعتمد:", size=12, bold=True, color=COLOR_CLAY, space_after=4)
            for source in doc.sources:
                label = f"[{source.citation_id}] {source.title}"
                if source.url:
                    label += f" ({source.url})"
                _add_bullet(document, label)
        stream = io.BytesIO()
        document.save(stream)
        return stream.getvalue()

    def _render_block(self, document: Document, block) -> None:
        btype = str(getattr(block, "block_type", "") or "").lower()
        text = getattr(block, "text", "") or ""
        data = getattr(block, "data", None) if isinstance(getattr(block, "data", None), dict) else {}
        if btype == "heading":
            level = data.get("level", 2) if data else 2
            try:
                level = int(level)
            except (TypeError, ValueError):
                level = 2
            heading = document.add_heading(level=min(max(level, 1), 3))
            _set_paragraph_rtl(heading)
            run = heading.add_run(text)
            _set_run_rtl(run, size=Pt(16), bold=True, color=COLOR_DATE, font=FONT_ARABIC)
        elif btype in {"bullet", "item", "point", "takeaway"}:
            _add_bullet(document, text)
        elif btype in {"quote", "callout", "note"}:
            prefix = "«" if btype == "quote" else ""
            suffix = "»" if btype == "quote" else ""
            _add_paragraph(document, f"{prefix}{text}{suffix}", size=11, bold=btype != "quote", color=COLOR_DATE if btype == "quote" else None)
        elif btype == "code":
            para = document.add_paragraph()
            para.alignment = WD_ALIGN_PARAGRAPH.LEFT
            run = para.add_run(text)
            run.font.name = "Consolas"
            run.font.size = Pt(9)
        elif btype in {"table", "table_row", "row"}:
            rows = data.get("rows") or data.get("table_data") or data.get("table")
            if isinstance(rows, (list, tuple)) and rows:
                _add_table(document, rows)
            elif text.strip():
                _add_paragraph(document, text)
        elif btype in {"image", "diagram", "card"}:
            src = str(data.get("src") or data.get("url") or "").strip()
            rendered = _add_image(document, src) if src else False
            if not rendered and text.strip():
                _add_paragraph(document, text)
        elif btype == "attachment":
            return  # preview metadata, not visible content
        elif btype in {"slide", "event", "calendar_event"}:
            _add_paragraph(document, text, size=11, bold=True)
        else:
            if text.strip():
                _add_paragraph(document, text)

    # -- legacy entry point (orchestrator-compatible) ----------------------

    def build_docx(self, doc: CulturalDocxDocument) -> bytes:
        """Construct a valid .docx package in memory and return bytes."""

        title = (doc.title or "").strip()
        topic = (doc.topic or "").strip()
        if not title:
            raise DocxRenderError("DOCX title is required; refusing to render filler.")
        if not topic:
            raise DocxRenderError("DOCX topic is required; refusing to render filler.")
        paragraphs = [p for p in (doc.paragraphs or []) if str(p or "").strip()]
        if not paragraphs and not doc.sections and not doc.key_takeaways and not doc.summary.strip():
            raise DocxRenderError("DOCX content is missing; refusing to render filler.")

        document = Document()
        _apply_page_setup(document)
        _apply_header_footer(document, doc.author)

        _add_paragraph(document, "المملكة العربية السعودية • وزارة الثقافة (سرد 2026)", size=9, bold=True, color=COLOR_CLAY, space_after=2)
        _add_paragraph(document, title, size=20, bold=True, color=COLOR_INK, font=FONT_ARABIC, space_after=2)
        _add_paragraph(document, f"المنطقة: {doc.region} | التوثيق والمعتمد: {doc.author}", size=9, color=COLOR_MUTED, space_after=8)

        summary_text = doc.summary or (paragraphs[0] if paragraphs else "")
        if summary_text.strip():
            _add_paragraph(document, "ملخص التقرير والأصالة الثقافية", size=12, bold=True, color=COLOR_CLAY, space_after=2)
            _add_paragraph(document, summary_text.strip(), size=11, space_after=8)

        start = 1 if (not doc.summary.strip() and len(paragraphs) > 1) else 0
        for para in paragraphs[start:]:
            _add_paragraph(document, para.strip(), space_after=6)

        for section in doc.sections:
            badge_suffix = f" ({section.badge})" if section.badge else ""
            _add_paragraph(document, f"◆ {section.title}{badge_suffix}", size=14, bold=True, color=COLOR_DATE, font=FONT_ARABIC, space_after=4)
            if section.content.strip():
                _add_paragraph(document, section.content.strip(), space_after=6)
            for bullet in section.bullets:
                if str(bullet or "").strip():
                    _add_bullet(document, str(bullet).strip())
            if section.table_data:
                _add_table(document, section.table_data)

        if doc.key_takeaways:
            _add_paragraph(document, "الخلاصات المعرفية والأصالة التراثية:", size=12, bold=True, color=COLOR_OLIVE, space_after=4)
            for item in doc.key_takeaways:
                if str(item or "").strip():
                    _add_bullet(document, str(item).strip())

        if doc.sources:
            _add_paragraph(document, "المراجع والتوثيق المعتمد:", size=12, bold=True, color=COLOR_CLAY, space_after=4)
            for source in doc.sources:
                if not isinstance(source, Mapping):
                    continue
                source_title = source.get("title") or source.get("source_name") or source.get("id", "")
                source_url = source.get("url") or source.get("source_url") or ""
                ref_text = f"{source_title} ({source_url})" if source_url else str(source_title)
                if ref_text.strip():
                    _add_bullet(document, ref_text.strip())

        core = document.core_properties
        core.title = title
        core.author = doc.author
        core.comments = f"Sard cultural document {doc.doc_id}"

        stream = io.BytesIO()
        document.save(stream)
        return stream.getvalue()


def render_cultural_docx_report(
    title: str,
    topic: str,
    content_paragraphs: Optional[List[str]] = None,
    sections: Optional[List[Dict[str, Any]]] = None,
    key_takeaways: Optional[List[str]] = None,
    sources: Optional[List[Dict[str, str]]] = None,
    region: str = "المملكة العربية السعودية",
    summary: str = "",
    output_path: Optional[Path] = None,
) -> bytes:
    """Build an Arabic RTL cultural Word (.docx) document and return bytes."""

    sec_objs: List[DocxSection] = []
    if sections:
        for item in sections:
            if not isinstance(item, Mapping):
                continue
            sec_objs.append(
                DocxSection(
                    title=str(item.get("title") or ""),
                    content=str(item.get("content") or ""),
                    bullets=[str(b) for b in (item.get("bullets") or [])],
                    badge=str(item.get("badge") or ""),
                    table_data=item.get("table_data"),
                )
            )

    doc = CulturalDocxDocument(
        title=title or f"تقرير ثقافي: {topic}",
        topic=topic,
        summary=summary,
        region=region,
        paragraphs=content_paragraphs or [],
        sections=sec_objs,
        key_takeaways=key_takeaways or [],
        sources=sources or [],
    )

    gen = DocxGenerator()
    data = gen.build_docx(doc)

    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_bytes(data)
        except Exception:
            path.unlink(missing_ok=True)
            raise

    return data
