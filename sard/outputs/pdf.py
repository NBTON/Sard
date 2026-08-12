"""Arabic RTL itinerary PDF renderer, isolated from UI/RAG/model code."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Flowable, PageBreak, SimpleDocTemplate, Spacer

from sard.outputs.arabic import append_citations, contains_arabic, shape_rtl, visual_runs
from sard.outputs.fonts import require_arabic_font, require_latin_font
from sard.outputs.schemas import INLINE_CITATION_RE, CitationSource, Itinerary, TextBlock


MIME_TYPE = "application/pdf"
DEFAULT_OUTPUT_ROOT = Path("output/pdf")
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class RenderedArtifact:
    filename: str
    path: Path
    mime_type: str
    size_bytes: int
    warnings: tuple[str, ...] = ()


def safe_pdf_filename(value: str) -> str:
    """Create a conservative ASCII filename; Arabic titles use a stable default."""

    stem = _SAFE_FILENAME_RE.sub("-", Path(value).stem).strip("-._") or "sard-itinerary"
    filename = f"{stem[:80]}.pdf"
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if stem.upper() in reserved:
        return f"sard-{filename}"
    return filename


def _resolve_output_path(output_path: str | Path) -> tuple[Path, Path]:
    root = Path(os.environ.get("SARD_PDF_OUTPUT_ROOT", DEFAULT_OUTPUT_ROOT)).resolve()
    requested = Path(output_path)
    if requested.suffix.lower() != ".pdf":
        raise ValueError("PDF output path must end in .pdf")
    if requested.name != safe_pdf_filename(requested.name):
        raise ValueError(
            "PDF filename must use only safe ASCII letters, digits, dots, "
            "dashes, or underscores."
        )
    candidate = requested.resolve() if requested.is_absolute() else (root / requested).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Output path must remain inside configured root: {root}") from exc
    if candidate == root:
        raise ValueError("Output path must name a PDF file.")
    if candidate.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {candidate}")
    return root, candidate


def _visual_width(text: str, font: str, size: float, latin_font: str = "Helvetica") -> float:
    return sum(
        pdfmetrics.stringWidth(run, font if arabic else latin_font, size)
        for arabic, run in visual_runs(shape_rtl(text))
    )


def _split_token(
    token: str, max_width: float, font: str, size: float, latin_font: str
) -> list[str]:
    if _visual_width(token, font, size, latin_font) <= max_width:
        return [token]
    parts: list[str] = []
    current = ""
    for character in token:
        trial = current + character
        if current and _visual_width(trial, font, size, latin_font) > max_width:
            parts.append(current)
            current = character
        else:
            current = trial
    if current:
        parts.append(current)
    return parts


def wrap_logical_lines(
    text: str,
    max_width: float,
    font: str,
    size: float,
    latin_font: str = "Helvetica",
) -> list[str]:
    """Wrap logical text before shaping so joining is correct on every line."""

    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        words: list[str] = []
        for token in paragraph.split():
            words.extend(_split_token(token, max_width, font, size, latin_font))
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if _visual_width(trial, font, size, latin_font) <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


class _TextFlowable(Flowable):
    def __init__(
        self,
        text: str,
        *,
        font: str,
        latin_font: str,
        size: float,
        leading: float,
        color=colors.HexColor("#263238"),
        rtl: bool | None = None,
        citation_ids: Sequence[str] = (),
        top_padding: float = 0,
        bottom_padding: float = 0,
    ) -> None:
        super().__init__()
        self.text = text
        self.font = font
        self.latin_font = latin_font
        self.size = size
        self.leading = leading
        self.color = color
        self.rtl = contains_arabic(text) if rtl is None else rtl
        self.citation_ids = tuple(citation_ids)
        self.top_padding = top_padding
        self.bottom_padding = bottom_padding
        self.lines: list[str] = []

    def wrap(self, availWidth, availHeight):  # noqa: N802 - ReportLab API
        self.lines = wrap_logical_lines(
            self.text, availWidth, self.font, self.size, self.latin_font
        )
        self.width = availWidth
        self.height = self.top_padding + len(self.lines) * self.leading + self.bottom_padding
        return self.width, self.height

    def draw(self) -> None:
        canvas = self.canv
        if hasattr(canvas, "register_citations"):
            canvas.register_citations(self.citation_ids)
        canvas.setFillColor(self.color)
        y = self.height - self.top_padding - self.size
        for logical in self.lines:
            visual = shape_rtl(logical) if self.rtl else logical
            runs = visual_runs(visual)
            total_width = sum(
                pdfmetrics.stringWidth(run, self.font if arabic else self.latin_font, self.size)
                for arabic, run in runs
            )
            x = self.width - total_width if self.rtl else 0
            for arabic, run in runs:
                run_font = self.font if arabic else self.latin_font
                canvas.setFont(run_font, self.size)
                canvas.drawString(x, y, run)
                x += pdfmetrics.stringWidth(run, run_font, self.size)
            y -= self.leading


class _FooterCanvas(Canvas):
    def __init__(self, *args, arabic_font: str, latin_font: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.arabic_font = arabic_font
        self.latin_font = latin_font
        self._page_citations: set[str] = set()

    def register_citations(self, citation_ids: Iterable[str]) -> None:
        self._page_citations.update(citation_ids)

    def showPage(self) -> None:  # noqa: N802 - ReportLab API
        width, _ = A4
        self.saveState()
        self.setStrokeColor(colors.HexColor("#D7CCC8"))
        self.line(42, 42, width - 42, 42)
        self.setFillColor(colors.HexColor("#6D4C41"))
        ids = ", ".join(sorted(self._page_citations)) or "لا توجد إحالات"
        note = f"المصادر في هذه الصفحة: {ids}"
        for text, y in ((note, 28), (f"الصفحة {self.getPageNumber()}", 14)):
            runs = visual_runs(shape_rtl(text))
            total = sum(
                pdfmetrics.stringWidth(run, self.arabic_font if ar else self.latin_font, 8)
                for ar, run in runs
            )
            x = (width - 42 - total) if y == 28 else (width - total) / 2
            for arabic, run in runs:
                run_font = self.arabic_font if arabic else self.latin_font
                self.setFont(run_font, 8)
                self.drawString(x, y, run)
                x += pdfmetrics.stringWidth(run, run_font, 8)
        self.restoreState()
        self._page_citations.clear()
        super().showPage()


def _block_flowable(
    block: TextBlock, font: str, latin_font: str, prefix: str = ""
) -> _TextFlowable:
    text = append_citations(prefix + block.text, block.citation_ids)
    page_citations = tuple(
        dict.fromkeys((*block.citation_ids, *INLINE_CITATION_RE.findall(text)))
    )
    return _TextFlowable(
        text,
        font=font,
        latin_font=latin_font,
        size=11,
        leading=18,
        citation_ids=page_citations,
        bottom_padding=5,
    )


def _source_metadata(source: CitationSource) -> str:
    values: list[str] = []
    if source.page is not None:
        values.append(f"الصفحة {source.page}")
    if source.section:
        values.append(f"القسم: {source.section}")
    if source.publication_date:
        values.append(f"تاريخ النشر: {source.publication_date.isoformat()}")
    return " | ".join(values)


def _build_story(itinerary: Itinerary, font: str, latin_font: str) -> list[Flowable]:
    story: list[Flowable] = [
        _TextFlowable(
            itinerary.title,
            font=font,
            latin_font=latin_font,
            size=24,
            leading=32,
            color=colors.HexColor("#7A3E20"),
            bottom_padding=10,
        ),
        _TextFlowable(
            itinerary.summary,
            font=font,
            latin_font=latin_font,
            size=13,
            leading=21,
            color=colors.HexColor("#455A64"),
            bottom_padding=8,
        ),
        _TextFlowable(
            f"أُنشئ في: {itinerary.generated_at.isoformat()}",
            font=font,
            latin_font=latin_font,
            size=9,
            leading=14,
            color=colors.HexColor("#78909C"),
            bottom_padding=12,
        ),
    ]
    for block in itinerary.notes:
        story.append(_block_flowable(block, font, latin_font, "ملاحظة عامة: "))

    for day_index, day in enumerate(itinerary.days, 1):
        if day_index > 1:
            story.append(PageBreak())
        date_suffix = f" - {day.date.isoformat()}" if day.date else ""
        story.append(
            _TextFlowable(
                f"اليوم {day_index}: {day.title}{date_suffix}",
                font=font,
                latin_font=latin_font,
                size=18,
                leading=25,
                color=colors.HexColor("#00695C"),
                bottom_padding=10,
            )
        )
        for stop in day.stops:
            story.extend(
                [
                    _TextFlowable(
                        f"{stop.time} | {stop.title}",
                        font=font,
                        latin_font=latin_font,
                        size=14,
                        leading=21,
                        color=colors.HexColor("#37474F"),
                        top_padding=5,
                    ),
                    _TextFlowable(
                        f"الموقع: {stop.location}",
                        font=font,
                        latin_font=latin_font,
                        size=10,
                        leading=16,
                        color=colors.HexColor("#607D8B"),
                        bottom_padding=5,
                    ),
                ]
            )
            story.extend(
                _block_flowable(block, font, latin_font) for block in stop.paragraphs
            )
            story.extend(
                _block_flowable(block, font, latin_font, "• ") for block in stop.bullets
            )
            story.extend(
                _block_flowable(block, font, latin_font, "ملاحظة: ")
                for block in stop.notes
            )
            story.append(Spacer(1, 7))
        story.extend(
            _block_flowable(block, font, latin_font, "ملاحظة اليوم: ")
            for block in day.notes
        )

    story.extend(
        [
            PageBreak(),
            _TextFlowable(
                "المصادر",
                font=font,
                latin_font=latin_font,
                size=20,
                leading=28,
                color=colors.HexColor("#7A3E20"),
                bottom_padding=10,
            ),
        ]
    )
    for source in itinerary.sources:
        story.append(
            _TextFlowable(
                f"[{source.citation_id}] {source.title}",
                font=font,
                latin_font=latin_font,
                size=12,
                leading=19,
                citation_ids=(source.citation_id,),
            )
        )
        story.append(
            _TextFlowable(
                source.url,
                font=font,
                latin_font=latin_font,
                size=8.5,
                leading=13,
                color=colors.HexColor("#1565C0"),
                rtl=False,
            )
        )
        metadata = _source_metadata(source)
        if metadata:
            story.append(
                _TextFlowable(
                    metadata,
                    font=font,
                    latin_font=latin_font,
                    size=9,
                    leading=14,
                    color=colors.HexColor("#607D8B"),
                )
            )
        story.append(Spacer(1, 10))
    return story


def render_pdf(itinerary: Itinerary, output_path: str | Path) -> RenderedArtifact:
    """Render a validated itinerary beneath ``SARD_PDF_OUTPUT_ROOT``.

    Existing files are never overwritten. No network, UI, RAG, LangChain,
    NVIDIA, or Zvec code is imported or called by this module.
    """

    itinerary.validate_citations()
    font_path = require_arabic_font()
    latin_font_path = require_latin_font()
    root, destination = _resolve_output_path(output_path)
    root.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)

    font_name = "SardNotoNaskhArabic"
    latin_font_name = "SardNotoSans"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
    if latin_font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(latin_font_name, str(latin_font_path)))

    document = SimpleDocTemplate(
        str(destination),
        pagesize=A4,
        rightMargin=50,
        leftMargin=50,
        topMargin=48,
        bottomMargin=58,
        title=itinerary.title,
        author="Sard",
    )
    try:
        document.build(
            _build_story(itinerary, font_name, latin_font_name),
            canvasmaker=lambda *args, **kwargs: _FooterCanvas(
                *args, arabic_font=font_name, latin_font=latin_font_name, **kwargs
            ),
        )
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return RenderedArtifact(
        filename=destination.name,
        path=destination,
        mime_type=MIME_TYPE,
        size_bytes=destination.stat().st_size,
        warnings=(),
    )
