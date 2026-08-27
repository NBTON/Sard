"""Oral History & Family Heritage Memoir Compiler for Sard (حكواتي العائلة).

Structures conversational oral histories, childhood memories, and ancestral stories
into multi-chapter biographical booklets formatted in literary Arabic prose,
with PDF booklet and document export capabilities.
"""

from __future__ import annotations

import io
import logging
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from sard.outputs.arabic import shape_rtl
from sard.outputs.fonts import ensure_fonts_registered, require_arabic_font, require_latin_font

logger = logging.getLogger("sard.outputs.memoir")

COLOR_PAPER = colors.HexColor("#F3EEE4")
COLOR_PAPER_2 = colors.HexColor("#E8E0D2")
COLOR_INK = colors.HexColor("#141210")
COLOR_CLAY = colors.HexColor("#BE4A24")
COLOR_DATE = colors.HexColor("#6E1F1F")
COLOR_OLIVE = colors.HexColor("#4A513C")
COLOR_GOLD = colors.HexColor("#C4A46A")
COLOR_CARD = colors.HexColor("#FAF7F1")
COLOR_BORDER = colors.HexColor("#D4CBBD")
COLOR_MUTED = colors.HexColor("#8A8178")


@dataclass
class MemoirChapter:
    """Represents a chapter in the family heritage memoir."""
    chapter_number: int
    title: str
    era_or_decade: str = ""  # e.g. "خمسينيات القرن الماضي"
    location: str = ""        # e.g. "حي الظهيرة بالرياض القديمة"
    narrative_prose: str = ""  # Rich Arabic prose
    key_quotes: List[str] = field(default_factory=list)
    traditional_terms_glossary: Dict[str, str] = field(default_factory=dict)
    anecdotes: List[str] = field(default_factory=list)


@dataclass
class FamilyMemoirBooklet:
    """Represents the complete family heritage biography booklet."""
    title: str
    family_or_narrator_name: str
    narrator_title: str = "الراوي / صاحب السيرة"
    region: str = "المملكة العربية السعودية"
    origin_village_or_town: str = ""
    dedication: str = ""
    introduction_prose: str = ""
    chapters: List[MemoirChapter] = field(default_factory=list)
    ancestral_tree_summary: str = ""
    memoir_id: str = field(default_factory=lambda: f"memoir-{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MemoirCompiler:
    """Compiles interview transcripts into formatted memoirs and PDF booklets."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path("output")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def render_pdf(self, memoir: FamilyMemoirBooklet) -> bytes:
        """Draws multi-page A4 booklet with decorative headers and chapter dividers."""
        arabic_font, latin_font = ensure_fonts_registered()

        buffer = io.BytesIO()
        c = Canvas(buffer, pagesize=A4)
        w, h = A4

        # Page 1: Cover Page
        self._render_cover_page(c, memoir, w, h, arabic_font)
        c.showPage()

        # Page 2+: Chapters
        for ch in memoir.chapters:
            self._render_chapter_page(c, memoir, ch, w, h, arabic_font)
            c.showPage()

        c.save()
        return buffer.getvalue()

    def compile_pdf(self, memoir: FamilyMemoirBooklet) -> bytes:
        """Alias for render_pdf."""
        return self.render_pdf(memoir)

    def _render_cover_page(self, c: Canvas, memoir: FamilyMemoirBooklet, w: float, h: float, font: str):
        # Dark luxurious cover
        c.setFillColor(COLOR_INK)
        c.rect(0, 0, w, h, stroke=0, fill=1)

        # Gold Border
        c.setStrokeColor(COLOR_GOLD)
        c.setLineWidth(2)
        c.roundRect(30, 30, w - 60, h - 60, 20, stroke=1, fill=0)

        # Seal
        c.setFillColor(COLOR_GOLD)
        c.setFont(font, 16)
        c.drawCentredString(w / 2, h - 140, shape_rtl("✦ توثيق الموروث والتاريخ الشفوي ✦"))

        # Main Title
        c.setFillColor(COLOR_PAPER)
        c.setFont(font, 28)
        c.drawCentredString(w / 2, h - 230, shape_rtl(memoir.title))

        # Narrator
        c.setFillColor(COLOR_GOLD)
        c.setFont(font, 18)
        c.drawCentredString(w / 2, h - 290, shape_rtl(f"سيرة وذكريات: {memoir.family_or_narrator_name}"))

        # Region / Village
        if memoir.origin_village_or_town:
            c.setFillColor(COLOR_PAPER_2)
            c.setFont(font, 13)
            c.drawCentredString(w / 2, h - 330, shape_rtl(f"الجذور والموطن: {memoir.origin_village_or_town} ({memoir.region})"))

        # Dedication Box
        if memoir.dedication:
            c.setFillColor(COLOR_CARD)
            c.setStrokeColor(COLOR_GOLD)
            c.roundRect(60, 220, w - 120, 80, 10, stroke=1, fill=0)
            c.setFillColor(COLOR_GOLD)
            c.setFont(font, 11)
            c.drawCentredString(w / 2, 270, shape_rtl("« إهداء »"))
            c.setFillColor(COLOR_PAPER)
            c.setFont(font, 11.5)
            c.drawCentredString(w / 2, 245, shape_rtl(memoir.dedication[:120]))

        # Footer
        c.setFillColor(COLOR_GOLD)
        c.setFont(font, 10)
        c.drawCentredString(w / 2, 55, shape_rtl("سرد — حكواتي العائلة وتوثيق السير التراثية"))

    def _render_chapter_page(self, c: Canvas, memoir: FamilyMemoirBooklet, ch: MemoirChapter, w: float, h: float, font: str):
        # Warm paper background
        c.setFillColor(COLOR_PAPER)
        c.rect(0, 0, w, h, stroke=0, fill=1)

        margin = 35
        c.setStrokeColor(COLOR_BORDER)
        c.setLineWidth(1)
        c.rect(margin, margin, w - (2 * margin), h - (2 * margin), stroke=1, fill=0)

        # Header Block
        c.setFillColor(COLOR_INK)
        c.rect(margin, h - margin - 60, w - (2 * margin), 60, stroke=0, fill=1)
        c.setFillColor(COLOR_GOLD)
        c.rect(margin, h - margin - 60, w - (2 * margin), 3, stroke=0, fill=1)

        c.setFillColor(COLOR_GOLD)
        c.setFont(font, 12)
        c.drawRightString(w - margin - 20, h - margin - 25, shape_rtl(f"الفصل {ch.chapter_number} • {ch.era_or_decade}"))

        c.setFillColor(COLOR_PAPER)
        c.setFont(font, 18)
        c.drawRightString(w - margin - 20, h - margin - 48, shape_rtl(ch.title))

        # Chapter Narrative Prose
        y = h - margin - 95
        c.setFillColor(COLOR_INK)
        c.setFont(font, 13)

        paragraphs = ch.narrative_prose.split("\n\n") if "\n\n" in ch.narrative_prose else [ch.narrative_prose]
        for p in paragraphs:
            if not p.strip():
                continue
            words = p.split(" ")
            line = []
            for word in words:
                line.append(word)
                if len(" ".join(line)) > 55:
                    c.drawRightString(w - margin - 25, y, shape_rtl(" ".join(line)))
                    y -= 22
                    line = []
            if line:
                c.drawRightString(w - margin - 25, y, shape_rtl(" ".join(line)))
                y -= 26

        # Key Quote callout box at bottom of page
        if ch.key_quotes:
            c.setFillColor(COLOR_PAPER_2)
            c.setStrokeColor(COLOR_CLAY)
            c.roundRect(margin + 10, 50, w - (2 * margin) - 20, 55, 8, stroke=1, fill=1)
            c.setFillColor(COLOR_DATE)
            c.setFont(font, 10.5)
            quote_text = f"« {ch.key_quotes[0][:110]} »"
            c.drawCentredString(w / 2, 75, shape_rtl(quote_text))

        # Footer
        c.setFillColor(COLOR_INK)
        c.setFont(font, 9)
        c.drawRightString(w - margin, 25, shape_rtl(f"{memoir.family_or_narrator_name} — {memoir.title}"))

    def save_pdf_file(self, memoir: FamilyMemoirBooklet, filename: Optional[str] = None) -> Tuple[Path, str]:
        safe_name = filename or f"{memoir.memoir_id}.pdf"
        if not safe_name.endswith(".pdf"):
            safe_name += ".pdf"
        target_path = self.output_dir / safe_name
        data = self.render_pdf(memoir)
        target_path.write_bytes(data)
        logger.info("Saved Family Memoir PDF: %s (%d bytes)", target_path, len(data))
        return target_path, safe_name


# ---------------------------------------------------------------------------
# High-Level Helper & Guided Interview Synthesis
# ---------------------------------------------------------------------------

ORAL_HISTORY_INTERVIEW_PROMPTS = [
    {
        "stage": "origins",
        "question_ar": "حدثني عن موطن الأجداد الأول، والحي أو القرية التي نشأت فيها، وكيف كانت طبيعة البيوت والفرجان القديمة؟",
    },
    {
        "stage": "childhood",
        "question_ar": "ما هي أجمل ذكريات طفولتك في الأعياد والمناسبات، وكيف كانت احتفالات العائلة والجيران في ذلك الزمان؟",
    },
    {
        "stage": "livelihood",
        "question_ar": "ما هي المهن والحرف التي عمل بها الآباء والأجداد (مثل الغوص، الزراعة، التجارة، الرعي)، وما هي المصاعب التي واجهوها؟",
    },
    {
        "stage": "transformation",
        "question_ar": "كيف عاصرت بدايات التحول والتطور في المملكة (دخول الكهرباء، التعليم، الطرق المعبدة)، وما هو أثر ذلك على نمط الحياة؟",
    },
    {
        "stage": "wisdom",
        "question_ar": "ما هي أهم حكمة أو وصية أو مثل شعبي ورثته عن كبار السن وترغب في نقله للأبناء والأحفاد؟",
    },
]


def synthesize_memoir_from_notes(
    family_or_narrator: str = "",
    raw_notes: Optional[List[Dict[str, str]]] = None,
    origin_region: str = "المملكة العربية السعودية",
    origin_town: str = "",
    family_name: str = "",
) -> FamilyMemoirBooklet:
    """Synthesizes interview answers into structured biographical chapters."""
    name = family_or_narrator or family_name or "الأسرة الكريمة"
    notes = raw_notes or []

    memoir = FamilyMemoirBooklet(
        title=f"سفر الذكريات وأثر الأجداد: سيرة {name}",
        family_or_narrator_name=name,
        region=origin_region,
        origin_village_or_town=origin_town,
        dedication="إلى الأبناء والأحفاد، صوناً للجذور ووفاءً لمسيرة الرعيل الأول.",
    )

    for idx, note in enumerate(notes, 1):
        topic = note.get("topic", f"محطة من الذاكرة {idx}")
        narrative = note.get("content", "")
        memoir.chapters.append(
            MemoirChapter(
                chapter_number=idx,
                title=topic,
                era_or_decade=note.get("era", "الزمن الجميل"),
                location=note.get("location", origin_town or origin_region),
                narrative_prose=narrative,
                key_quotes=[note.get("quote", "")] if note.get("quote") else [],
            )
        )

    return memoir
