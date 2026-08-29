"""General Arabic RTL Cultural PDF Report Renderer for Sard.

Produces rich, publication-grade cultural reports, topic briefings, and heritage documents
styled with Sard's cultural design palette (Ink, Clay, Date, Olive, Gold, Paper) and
guaranteed Arabic right-to-left layout and typographic shaping.
"""

from __future__ import annotations

import io
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from sard.outputs.arabic import contains_arabic, shape_rtl, visual_runs
from sard.outputs.fonts import ensure_fonts_registered, require_arabic_font, require_latin_font

# ---------------------------------------------------------------------------
# Color Palette
# ---------------------------------------------------------------------------
HEX_INK = "#141210"
HEX_CLAY = "#BE4A24"
HEX_DATE = "#6E1F1F"
HEX_OLIVE = "#4A513C"
HEX_GOLD = "#C4A46A"
HEX_PAPER = "#F3EEE4"
HEX_PAPER_2 = "#E8E0D2"
HEX_CARD = "#FAF7F1"
HEX_MUTED = "#8A8178"
HEX_BORDER = "#D4CBBD"

COLOR_INK = colors.HexColor(HEX_INK)
COLOR_CLAY = colors.HexColor(HEX_CLAY)
COLOR_DATE = colors.HexColor(HEX_DATE)
COLOR_OLIVE = colors.HexColor(HEX_OLIVE)
COLOR_GOLD = colors.HexColor(HEX_GOLD)
COLOR_PAPER = colors.HexColor(HEX_PAPER)
COLOR_PAPER_2 = colors.HexColor(HEX_PAPER_2)
COLOR_CARD = colors.HexColor(HEX_CARD)
COLOR_MUTED = colors.HexColor(HEX_MUTED)
COLOR_BORDER = colors.HexColor(HEX_BORDER)


@dataclass
class ReportSection:
    title: str
    content: str
    bullets: List[str] = field(default_factory=list)
    badge: str = ""
    table_data: Optional[List[List[str]]] = None


@dataclass
class CulturalReport:
    title: str
    topic: str
    summary: str
    region: str = "المملكة العربية السعودية"
    author: str = "سرد — المستشار الثقافي المعتمد"
    sections: List[ReportSection] = field(default_factory=list)
    key_takeaways: List[str] = field(default_factory=list)
    sources: List[Dict[str, str]] = field(default_factory=list)
    report_id: str = field(default_factory=lambda: f"rep-{uuid.uuid4().hex[:8]}")


class _ArabicTextFlowable(Flowable):
    """Renders wrapped, shaped Arabic RTL text runs with mixed Latin fallback."""

    def __init__(
        self,
        text: str,
        font: str,
        latin_font: str,
        size: float = 11,
        leading: float = 16,
        color: colors.Color = COLOR_INK,
        align: str = "right",
        bullet: bool = False,
    ):
        super().__init__()
        self.text = text
        self.font = font
        self.latin_font = latin_font
        self.size = size
        self.leading = leading
        self.color = color
        self.align = align
        self.bullet = bullet
        self._wrapped_lines: List[str] = []

    def wrap(self, avail_width: float, avail_height: float) -> Tuple[float, float]:
        self.width = avail_width
        words = self.text.split()
        if not words:
            self._wrapped_lines = [""]
            self.height = self.leading
            return self.width, self.height

        lines: List[str] = []
        cur_line = ""
        bullet_prefix = "• " if self.bullet else ""
        max_w = avail_width - (14 if self.bullet else 0)

        for w in words:
            trial = f"{cur_line} {w}".strip()
            # Calculate width using visual runs
            tw = self._measure_width(trial)
            if tw <= max_w:
                cur_line = trial
            else:
                if cur_line:
                    lines.append(cur_line)
                cur_line = w
        if cur_line:
            lines.append(cur_line)

        self._wrapped_lines = lines or [""]
        self.height = len(self._wrapped_lines) * self.leading + 4
        return self.width, self.height

    def _measure_width(self, text: str) -> float:
        shaped = shape_rtl(text)
        return sum(
            pdfmetrics.stringWidth(run, self.font if is_ar else self.latin_font, self.size)
            for is_ar, run in visual_runs(shaped)
        )

    def draw(self):
        canvas: Canvas = self.canv
        canvas.saveState()
        canvas.setFillColor(self.color)

        y = self.height - self.leading
        for idx, line in enumerate(self._wrapped_lines):
            shaped = shape_rtl(line)
            runs = visual_runs(shaped)
            total_w = sum(
                pdfmetrics.stringWidth(run, self.font if is_ar else self.latin_font, self.size)
                for is_ar, run in runs
            )

            if self.align == "right":
                x = self.width - total_w
            elif self.align == "center":
                x = (self.width - total_w) / 2
            else:
                x = 0

            # Draw bullet symbol on first line if requested
            if self.bullet and idx == 0:
                canvas.setFillColor(COLOR_CLAY)
                canvas.circle(self.width - 4, y + (self.size / 3), 2.5, fill=1, stroke=0)
                canvas.setFillColor(self.color)
                x -= 12

            cur_x = x
            for is_ar, run in runs:
                fn = self.font if is_ar else self.latin_font
                canvas.setFont(fn, self.size)
                canvas.drawString(cur_x, y, run)
                cur_x += pdfmetrics.stringWidth(run, fn, self.size)

            y -= self.leading

        canvas.restoreState()


class _NumberedCanvas(Canvas):
    """Adds header, footer, and page numbering to the document."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_header_footer(num_pages)
            super().showPage()
        super().save()

    def draw_header_footer(self, total_pages: int):
        self.saveState()
        ar_font, lat_font = ensure_fonts_registered()

        # Header (pages > 1)
        if self._pageNumber > 1:
            self.setStrokeColor(COLOR_BORDER)
            self.setLineWidth(0.75)
            self.line(40, 800, 555, 800)

            self.setFont(ar_font, 8.5)
            self.setFillColor(COLOR_MUTED)
            hdr_text = shape_rtl("سرد — التوثيق والمعرفة الثقافية السعودية")
            self.drawRightString(555, 806, hdr_text)

        # Footer (all pages)
        self.setStrokeColor(COLOR_BORDER)
        self.setLineWidth(0.75)
        self.line(40, 45, 555, 45)

        self.setFont(ar_font, 8)
        self.setFillColor(COLOR_MUTED)
        brand = shape_rtl("وزارة الثقافة 2026 • منظومة سرد للذكاء الاصطناعي")
        self.drawString(40, 32, brand)

        pg_text = shape_rtl(f"صفحة {self._pageNumber} من {total_pages}")
        self.drawRightString(555, 32, pg_text)

        self.restoreState()


def render_cultural_pdf_report(
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
    """Builds a multi-page Arabic RTL cultural report PDF and returns bytes."""
    ar_font, lat_font = ensure_fonts_registered()

    stream = io.BytesIO()
    doc = SimpleDocTemplate(
        stream,
        pagesize=A4,
        leftMargin=40,
        rightMargin=40,
        topMargin=50,
        bottomMargin=55,
    )

    story: List[Flowable] = []
    content_width = 515

    # 1. Header Banner
    story.append(Spacer(1, 10))
    story.append(
        _ArabicTextFlowable(
            text="المملكة العربية السعودية • وزارة الثقافة",
            font=ar_font,
            latin_font=lat_font,
            size=9,
            leading=12,
            color=COLOR_CLAY,
            align="right",
        )
    )
    story.append(Spacer(1, 4))
    story.append(
        _ArabicTextFlowable(
            text=title or f"تقرير ثقافي: {topic}",
            font=ar_font,
            latin_font=lat_font,
            size=20,
            leading=26,
            color=COLOR_INK,
            align="right",
        )
    )
    story.append(Spacer(1, 4))
    story.append(
        _ArabicTextFlowable(
            text=f"المنطقة المستهدفة: {region} | إعداد وتوثيق: سرد (Sard Cultural Agent)",
            font=ar_font,
            latin_font=lat_font,
            size=9.5,
            leading=14,
            color=COLOR_MUTED,
            align="right",
        )
    )
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=1.5, color=COLOR_GOLD, spaceBefore=4, spaceAfter=14))

    # 2. Executive Summary Callout Box
    if summary or (content_paragraphs and len(content_paragraphs) > 0):
        sum_text = summary or content_paragraphs[0]
        summary_flow = _ArabicTextFlowable(
            text=sum_text,
            font=ar_font,
            latin_font=lat_font,
            size=10.5,
            leading=16.5,
            color=COLOR_INK,
            align="right",
        )
        title_flow = _ArabicTextFlowable(
            text="ملخص التوثيق والأصالة الثقافية",
            font=ar_font,
            latin_font=lat_font,
            size=11,
            leading=15,
            color=COLOR_CLAY,
            align="right",
        )

        box_table = Table(
            [[title_flow], [summary_flow]],
            colWidths=[content_width],
        )
        box_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), COLOR_CARD),
                ("BOX", (0, 0), (-1, -1), 1.5, COLOR_GOLD),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                ("LEFTPADDING", (0, 0), (-1, -1), 14),
                ("RIGHTPADDING", (0, 0), (-1, -1), 14),
            ])
        )
        story.append(box_table)
        story.append(Spacer(1, 16))

    # 3. Main Paragraphs
    if content_paragraphs:
        start_idx = 1 if (not summary and len(content_paragraphs) > 1) else 0
        for p in content_paragraphs[start_idx:]:
            if p.strip():
                story.append(
                    _ArabicTextFlowable(
                        text=p.strip(),
                        font=ar_font,
                        latin_font=lat_font,
                        size=10.5,
                        leading=17,
                        color=COLOR_INK,
                        align="right",
                    )
                )
                story.append(Spacer(1, 10))

    # 4. Structured Sections
    if sections:
        for sec in sections:
            sec_title = sec.get("title", "")
            sec_content = sec.get("content", "")
            sec_bullets = sec.get("bullets", [])
            sec_badge = sec.get("badge", "")

            story.append(Spacer(1, 12))
            header_text = f"◆ {sec_title}" if not sec_badge else f"◆ {sec_title} ({sec_badge})"
            story.append(
                _ArabicTextFlowable(
                    text=header_text,
                    font=ar_font,
                    latin_font=lat_font,
                    size=13,
                    leading=18,
                    color=COLOR_DATE,
                    align="right",
                )
            )
            story.append(HRFlowable(width="100%", thickness=0.8, color=COLOR_BORDER, spaceBefore=4, spaceAfter=8))

            if sec_content:
                story.append(
                    _ArabicTextFlowable(
                        text=sec_content,
                        font=ar_font,
                        latin_font=lat_font,
                        size=10.5,
                        leading=16.5,
                        color=COLOR_INK,
                        align="right",
                    )
                )
                story.append(Spacer(1, 6))

            for b in sec_bullets:
                story.append(
                    _ArabicTextFlowable(
                        text=b,
                        font=ar_font,
                        latin_font=lat_font,
                        size=10,
                        leading=15,
                        color=COLOR_INK,
                        align="right",
                        bullet=True,
                    )
                )
                story.append(Spacer(1, 4))

    # 5. Key Takeaways Card
    if key_takeaways:
        story.append(Spacer(1, 14))
        takeaway_flowables = [
            _ArabicTextFlowable(
                text="النتائج والخلاصات المعرفية الرئيسية:",
                font=ar_font,
                latin_font=lat_font,
                size=11,
                leading=15,
                color=COLOR_OLIVE,
                align="right",
            ),
            Spacer(1, 6),
        ]
        for kt in key_takeaways:
            takeaway_flowables.append(
                _ArabicTextFlowable(
                    text=kt,
                    font=ar_font,
                    latin_font=lat_font,
                    size=10,
                    leading=15,
                    color=COLOR_INK,
                    align="right",
                    bullet=True,
                )
            )
            takeaway_flowables.append(Spacer(1, 4))

        tk_table = Table([[takeaway_flowables]], colWidths=[content_width])
        tk_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), COLOR_PAPER),
                ("BOX", (0, 0), (-1, -1), 1, COLOR_OLIVE),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ])
        )
        story.append(KeepTogether(tk_table))

    # 6. Citations and Sources
    if sources:
        story.append(Spacer(1, 16))
        story.append(
            _ArabicTextFlowable(
                text="المراجع والمصادر التوثيقية المعتمدة:",
                font=ar_font,
                latin_font=lat_font,
                size=11,
                leading=15,
                color=COLOR_CLAY,
                align="right",
            )
        )
        story.append(HRFlowable(width="100%", thickness=0.8, color=COLOR_BORDER, spaceBefore=4, spaceAfter=6))

        for s in sources:
            s_title = s.get("title") or s.get("source_name") or s.get("id", "")
            s_url = s.get("url") or s.get("source_url") or ""
            ref_text = f"{s_title} — {s_url}" if s_url else s_title
            story.append(
                _ArabicTextFlowable(
                    text=ref_text,
                    font=ar_font,
                    latin_font=lat_font,
                    size=8.5,
                    leading=13,
                    color=COLOR_MUTED,
                    align="right",
                    bullet=True,
                )
            )
            story.append(Spacer(1, 3))

    doc.build(story, canvasmaker=_NumberedCanvas)
    pdf_data = stream.getvalue()

    if output_path:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(pdf_data)

    return pdf_data
