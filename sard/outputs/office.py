"""GenOffice-inspired PowerPoint (.pptx) and Office document generator for Sard.

Provides automated authoring of cultural presentation decks with Sard's
cultural design palette (paper, ink, clay, date, olive, gold, card) and
native Arabic right-to-left layout conventions.
"""

from __future__ import annotations

import io
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

logger = logging.getLogger("sard.outputs.office")

# ---------------------------------------------------------------------------
# Sard Cultural Color Palette
# ---------------------------------------------------------------------------
COLOR_PAPER = RGBColor(0xF3, 0xEE, 0xE4)      # #F3EEE4
COLOR_PAPER_2 = RGBColor(0xE8, 0xE0, 0xD2)    # #E8E0D2
COLOR_INK = RGBColor(0x14, 0x12, 0x10)        # #141210
COLOR_CLAY = RGBColor(0xBE, 0x4A, 0x24)       # #BE4A24
COLOR_DATE = RGBColor(0x6E, 0x1F, 0x1F)       # #6E1F1F
COLOR_OLIVE = RGBColor(0x4A, 0x51, 0x3C)      # #4A513C
COLOR_GOLD = RGBColor(0xC4, 0xA4, 0x6A)       # #C4A46A
COLOR_CARD = RGBColor(0xFA, 0xF7, 0xF1)       # #FAF7F1
COLOR_WHITE = RGBColor(0xFF, 0xFF, 0xFF)
COLOR_MUTED = RGBColor(0x8A, 0x81, 0x78)

FONT_HEADING = "Noto Naskh Arabic"
FONT_BODY = "IBM Plex Sans Arabic"
FONT_FALLBACK = "Arial"

# Pagination caps (layout, not content caps): at most this many items per
# slide; overflow auto-paginates into "(تابع)" continuation slides.  Content
# is NEVER dropped to fit — it continues.  Minimum readable body size is
# Pt(13); renderers must paginate instead of shrinking text below it.
MAX_CARDS_PER_SLIDE = 3
MAX_TIMELINE_PER_SLIDE = 4
MAX_BULLETS_PER_SLIDE = 6
MAX_CHARS_PER_SLIDE = 900
MAX_TABLE_ROWS_PER_SLIDE = 12
MAX_SOURCES_PER_SLIDE = 6
MIN_BODY_PT = 13


class DeckBuildError(ValueError):
    """Raised when a deck has no real content; renderers fail visibly instead
    of hallucinating filler bullets, summaries, or quotes."""


def _chunk_items(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)] or [[]]


def _chunk_bullets(
    bullets: list[str],
    max_count: int = MAX_BULLETS_PER_SLIDE,
    max_chars: int = MAX_CHARS_PER_SLIDE,
) -> list[list[str]]:
    """Chunk bullets by count AND character budget, never dropping text."""

    chunks: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    for bullet in bullets:
        if current and (len(current) >= max_count or current_len + len(bullet) > max_chars):
            chunks.append(current)
            current, current_len = [], 0
        current.append(bullet)
        current_len += len(bullet)
    if current:
        chunks.append(current)
    return chunks or [[]]


def _chunk_paragraphs(paragraphs: list[str], max_chars: int = MAX_CHARS_PER_SLIDE) -> list[list[str]]:
    """Split long body text across slides by character budget, never dropping text."""

    chunks: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    for para in paragraphs:
        if current and current_len + len(para) > max_chars:
            chunks.append(current)
            current, current_len = [], 0
        current.append(para)
        current_len += len(para)
    if current:
        chunks.append(current)
    return chunks or [[]]


def _set_paragraph_rtl(paragraph) -> None:
    """Set base direction RTL + complex-script typeface for Arabic paragraphs.

    PowerPoint needs a:pPr rtl="1" (not just right alignment) for correct
    bidi ordering of mixed Arabic/Latin runs.
    """
    try:
        paragraph.alignment = PP_ALIGN.RIGHT
        pPr = paragraph._p.get_or_add_pPr()
        pPr.set("rtl", "1")
        # Complex-script + east-asian typefaces fall back to Arabic fonts
        for tag in ("a:cs", "a:ea"):
            try:
                el = pPr.find(f"{{http://schemas.openxmlformats.org/drawingml/2006/main}}{tag[2:]}")
                if el is None:
                    from pptx.oxml.ns import qn as _qn

                    from lxml import etree as _etree

                    el = _etree.SubElement(pPr, _qn("a:cs") if tag == "a:cs" else _qn("a:ea"))
                el.set("typeface", FONT_BODY)
            except Exception as exc:
                logger.debug("Suppressed boundary exception in office.py: %s", type(exc).__name__)
                continue
    except Exception:
        try:
            paragraph.alignment = PP_ALIGN.RIGHT
        except Exception as exc:
            logger.debug("Suppressed boundary exception in office.py: %s", type(exc).__name__)


@dataclass
class SlideCard:
    """Represents a structured card within a comparison or multi-column slide."""
    title: str
    subtitle: str = ""
    bullets: List[str] = field(default_factory=list)
    badge: str = ""
    accent_color: str = "clay"  # clay, gold, olive, date


@dataclass
class TimelineItem:
    """Represents a milestone in a chronological timeline slide."""
    year_or_era: str
    hijri_year: str = ""
    title: str = ""
    description: str = ""


@dataclass
class SlideContent:
    """Represents the semantic content of a single slide."""
    slide_type: str  # title, briefing, content, section, comparison, timeline, table, image, sources, key_points, summary
    title: str
    subtitle: str = ""
    body_paragraphs: List[str] = field(default_factory=list)
    bullets: List[str] = field(default_factory=list)
    cards: List[SlideCard] = field(default_factory=list)
    timeline: List[TimelineItem] = field(default_factory=list)
    table_rows: List[List[str]] = field(default_factory=list)
    quote: str = ""
    quote_author: str = ""
    footer_text: str = "سرد — المستشار الثقافي للمملكة العربية السعودية"
    region_badge: str = ""


@dataclass
class PresentationDeck:
    """Represents an entire cultural briefing presentation."""
    title: str
    topic: str
    region: str = "المملكة العربية السعودية"
    author: str = "سرد (Sard Cultural Agent)"
    slides: List[SlideContent] = field(default_factory=list)
    deck_id: str = field(default_factory=lambda: f"deck-{uuid.uuid4().hex[:8]}")


class PresentationGenerator:
    """Generates PowerPoint (.pptx) decks adhering strictly to Sard cultural design tokens."""

    def __init__(self, default_output_dir: Optional[Path] = None):
        self.output_dir = default_output_dir or Path("output")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _get_accent_rgb(self, name: str) -> RGBColor:
        mapping = {
            "clay": COLOR_CLAY,
            "gold": COLOR_GOLD,
            "olive": COLOR_OLIVE,
            "date": COLOR_DATE,
            "ink": COLOR_INK,
        }
        return mapping.get(name.lower(), COLOR_CLAY)

    def build_pptx(self, deck: PresentationDeck) -> bytes:
        """Constructs the presentation in 16:9 widescreen layout and returns bytes."""
        prs = Presentation()
        # 16:9 widescreen dimensions (13.333 x 7.5 inches)
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)

        try:
            blank_slide_layout = prs.slide_layouts[6]  # Completely blank layout
        except IndexError:
            blank_slide_layout = prs.slide_layouts[-1]

        slides = list(deck.slides or [])
        if not slides:
            # Empty deck guard: always emit at least a title slide so the
            # package contains ppt/slides/slide1.xml and validates.
            slides = [
                SlideContent(
                    slide_type="title",
                    title=getattr(deck, "title", "عرض ثقافي"),
                    subtitle="",
                )
            ]

        # Auto-paginate: overflow content continues on "(تابع)" slides instead
        # of being silently capped (previous cards[:3]/timeline[:4] behavior).
        slides = self._expand_pagination(slides)

        for slide_data in slides:
            slide = prs.slides.add_slide(blank_slide_layout)
            self._render_slide(prs, slide, slide_data, deck)

        stream = io.BytesIO()
        prs.save(stream)
        return stream.getvalue()

    def _expand_pagination(self, slides: list) -> list:
        """Expand over-cap slides into continuation slides; never drop content."""

        from dataclasses import replace

        expanded: list = []
        for slide_data in slides:
            if slide_data.slide_type == "comparison" and len(slide_data.cards) > MAX_CARDS_PER_SLIDE:
                for idx, chunk in enumerate(_chunk_items(list(slide_data.cards), MAX_CARDS_PER_SLIDE)):
                    title = slide_data.title if idx == 0 else f"{slide_data.title} (تابع)"
                    expanded.append(replace(slide_data, title=title, cards=chunk))
            elif slide_data.slide_type == "timeline" and len(slide_data.timeline) > MAX_TIMELINE_PER_SLIDE:
                for idx, chunk in enumerate(_chunk_items(list(slide_data.timeline), MAX_TIMELINE_PER_SLIDE)):
                    title = slide_data.title if idx == 0 else f"{slide_data.title} (تابع)"
                    expanded.append(replace(slide_data, title=title, timeline=chunk))
            elif slide_data.slide_type == "table" and len(slide_data.table_rows) > MAX_TABLE_ROWS_PER_SLIDE:
                # PPTX-B2: paginate wide tables instead of silently truncating.
                header = slide_data.table_rows[:1]
                body = slide_data.table_rows[1:]
                for idx, chunk in enumerate(_chunk_items(body, MAX_TABLE_ROWS_PER_SLIDE - 1) or [[]]):
                    title = slide_data.title if idx == 0 else f"{slide_data.title} (تابع)"
                    table_slide = replace(slide_data, title=title, table_rows=header + chunk)
                    expanded.append(table_slide)
            elif slide_data.slide_type == "sources" and len(slide_data.bullets) > MAX_SOURCES_PER_SLIDE:
                # PPTX-Q2: paginate long bibliographies instead of overflowing.
                for idx, chunk in enumerate(_chunk_items(list(slide_data.bullets), MAX_SOURCES_PER_SLIDE)):
                    title = slide_data.title if idx == 0 else f"{slide_data.title} (تابع)"
                    expanded.append(replace(slide_data, title=title, bullets=chunk))
            elif slide_data.slide_type in {"briefing", "summary", "content"} and (
                len(slide_data.bullets) > MAX_BULLETS_PER_SLIDE
                or sum(len(p) for p in (*slide_data.body_paragraphs, *slide_data.bullets, slide_data.quote)) > MAX_CHARS_PER_SLIDE
            ):
                bullet_chunks = _chunk_bullets(list(slide_data.bullets)) or [[]]
                para_chunks = _chunk_paragraphs(list(slide_data.body_paragraphs)) or [[]]
                # Zip chunks: first slide keeps body+bullets head, rest continue.
                total = max(len(bullet_chunks), len(para_chunks))
                for idx in range(total):
                    chunk_bullets = bullet_chunks[idx] if idx < len(bullet_chunks) else []
                    chunk_paras = para_chunks[idx] if idx < len(para_chunks) else []
                    if idx > 0 and not chunk_bullets and not chunk_paras:
                        continue
                    title = slide_data.title if idx == 0 else f"{slide_data.title} (تابع)"
                    quote = slide_data.quote if idx == total - 1 else ""
                    expanded.append(
                        replace(slide_data, title=title, bullets=chunk_bullets, body_paragraphs=chunk_paras, quote=quote)
                    )
            else:
                expanded.append(slide_data)
        return expanded

    def _set_background(self, slide, color: RGBColor = COLOR_PAPER):
        """Sets a full-slide solid background shape."""
        bg = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(13.333), Inches(7.5)
        )
        bg.fill.solid()
        bg.fill.fore_color.rgb = color
        bg.line.fill.background()
        return bg

    def build_from_document(self, doc) -> bytes:
        """Build a deck from a canonical ArtifactDocument (artifact agent shape).

        Mapping: title slide (metadata title/topic) -> one briefing slide per
        section (paragraphs + bullets, auto-paginated) -> table slides for
        table blocks -> image slides for local image blocks -> sources slide.
        Long content summarizes nothing away: it continues on "(تابع)" slides.
        """

        from sard.outputs.document import ArtifactDocument as _ArtifactDocument

        if not isinstance(doc, _ArtifactDocument):
            raise DeckBuildError("build_from_document requires an ArtifactDocument.")
        meta = doc.metadata
        title = (meta.title or "").strip()
        topic = (meta.topic or "").strip()
        if not title:
            raise DeckBuildError("Artifact title is required.")
        if not topic and not doc.sections:
            raise DeckBuildError("Artifact content is missing; refusing to render filler.")
        deck = PresentationDeck(title=title, topic=topic or title, region=meta.region)
        deck.slides.append(
            SlideContent(slide_type="title", title=title, subtitle=topic if topic != title else "")
        )
        for section in doc.sections:
            paras: list[str] = []
            bullets: list[str] = []
            tables: list[list[list[str]]] = []
            images: list[str] = []
            quote = ""
            # PPTX-B3: a heading titles the upcoming content group instead of
            # emitting an empty slide; consecutive headings keep the last one.
            pending_heading = ""
            # PPTX-B4: honor the fail-closed evidence rule like the preview.
            for block in section.renderable_blocks():
                btype = str(getattr(block, "block_type", "") or "").lower()
                text = (getattr(block, "text", "") or "").strip()
                data = getattr(block, "data", None)
                data = data if isinstance(data, dict) else {}
                if btype == "heading":
                    if paras or bullets or quote:
                        deck.slides.append(
                            SlideContent(
                                slide_type="briefing",
                                title=pending_heading or section.title or title,
                                body_paragraphs=paras,
                                bullets=bullets,
                                quote=quote,
                                region_badge=meta.region,
                            )
                        )
                        paras, bullets, quote = [], [], ""
                    pending_heading = text or pending_heading
                elif btype in {"bullet", "item", "point", "takeaway"}:
                    if text:
                        bullets.append(text)
                elif btype == "quote":
                    quote = text or quote
                elif btype in {"table", "table_row", "row"}:
                    rows = data.get("rows") or data.get("table_data") or data.get("table")
                    if isinstance(rows, (list, tuple)) and rows:
                        tables.append([[str(c or "") for c in r] if isinstance(r, (list, tuple)) else [str(r)] for r in rows])
                    elif text:
                        paras.append(text)
                elif btype in {"image", "diagram"}:
                    src = str(data.get("src") or data.get("url") or "").strip()
                    if src:
                        images.append(src)
                    elif text:
                        paras.append(text)
                elif btype == "attachment":
                    continue
                elif text:
                    paras.append(text)
            if paras or bullets or quote:
                deck.slides.append(
                    SlideContent(
                        slide_type="briefing",
                        title=pending_heading or section.title or title,
                        body_paragraphs=paras,
                        bullets=bullets,
                        quote=quote,
                        region_badge=meta.region,
                    )
                )
            elif pending_heading:
                # A trailing heading with no body still yields its titled
                # slide rather than vanishing silently.
                deck.slides.append(
                    SlideContent(
                        slide_type="briefing",
                        title=pending_heading,
                        region_badge=meta.region,
                    )
                )
            for table_rows in tables:
                deck.slides.append(
                    SlideContent(
                        slide_type="table",
                        title=section.title or title,
                        body_paragraphs=[],
                        bullets=[],
                        region_badge=meta.region,
                    )
                )
                deck.slides[-1].cards = []
                deck.slides[-1].table_rows = table_rows
            for src in images:
                deck.slides.append(
                    SlideContent(
                        slide_type="image",
                        title=section.title or title,
                        body_paragraphs=[src],
                        region_badge=meta.region,
                    )
                )
        if doc.sources:
            deck.slides.append(
                SlideContent(
                    slide_type="sources",
                    title="المراجع والتوثيق",
                    bullets=[
                        f"[{s.citation_id}] {s.title}" + (f" — {s.url}" if s.url else "")
                        for s in doc.sources
                    ],
                    region_badge=meta.region,
                )
            )
        if len(deck.slides) <= 1:
            raise DeckBuildError("Artifact content is missing; refusing to render filler.")
        return self.build_pptx(deck)

    def _render_slide(self, prs, slide, data: SlideContent, deck: PresentationDeck):
        if data.slide_type == "title":
            self._set_background(slide, COLOR_INK)
            self._render_title_slide(slide, data, deck)
        elif data.slide_type == "comparison":
            self._set_background(slide, COLOR_PAPER)
            self._render_comparison_slide(slide, data)
        elif data.slide_type == "timeline":
            self._set_background(slide, COLOR_PAPER)
            self._render_timeline_slide(slide, data)
        elif data.slide_type == "table":
            self._set_background(slide, COLOR_PAPER)
            self._render_table_slide(slide, data)
        elif data.slide_type == "image":
            self._set_background(slide, COLOR_PAPER)
            self._render_image_slide(slide, data)
        elif data.slide_type == "sources":
            self._set_background(slide, COLOR_PAPER_2)
            self._render_sources_slide(slide, data)
        elif data.slide_type == "summary":
            self._set_background(slide, COLOR_PAPER_2)
            self._render_summary_slide(slide, data)
        else:
            self._set_background(slide, COLOR_PAPER)
            self._render_briefing_slide(slide, data)

    def _render_title_slide(self, slide, data: SlideContent, deck: PresentationDeck):
        """Renders dark-ink title cover slide with gold & clay accents."""
        top_bar = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(1.0), Inches(0.8), Inches(11.333), Inches(0.08)
        )
        top_bar.fill.solid()
        top_bar.fill.fore_color.rgb = COLOR_GOLD
        top_bar.line.fill.background()

        badge_box = slide.shapes.add_textbox(Inches(1.0), Inches(1.2), Inches(11.333), Inches(0.6))
        tf_badge = badge_box.text_frame
        tf_badge.word_wrap = True
        p_b = tf_badge.paragraphs[0]
        _set_paragraph_rtl(p_b)
        run_b = p_b.add_run()
        run_b.text = f"✦ {deck.region} • توثيق ثقافي معتمد"
        run_b.font.name = FONT_BODY
        run_b.font.size = Pt(14)
        run_b.font.color.rgb = COLOR_GOLD
        run_b.font.bold = True

        title_box = slide.shapes.add_textbox(Inches(1.0), Inches(2.2), Inches(11.333), Inches(2.0))
        tf = title_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        _set_paragraph_rtl(p)
        run = p.add_run()
        run.text = data.title
        run.font.name = FONT_HEADING
        run.font.size = Pt(40)
        run.font.color.rgb = COLOR_PAPER
        run.font.bold = True

        if data.subtitle:
            p2 = tf.add_paragraph()
            _set_paragraph_rtl(p2)
            p2.space_before = Pt(14)
            run2 = p2.add_run()
            run2.text = data.subtitle
            run2.font.name = FONT_BODY
            run2.font.size = Pt(20)
            run2.font.color.rgb = COLOR_GOLD

        mid_bar = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(8.333), Inches(5.0), Inches(4.0), Inches(0.05)
        )
        mid_bar.fill.solid()
        mid_bar.fill.fore_color.rgb = COLOR_CLAY
        mid_bar.line.fill.background()

        footer_box = slide.shapes.add_textbox(Inches(1.0), Inches(5.8), Inches(11.333), Inches(0.8))
        tf_foot = footer_box.text_frame
        p_f = tf_foot.paragraphs[0]
        _set_paragraph_rtl(p_f)
        r_f = p_f.add_run()
        r_f.text = f"إعداد: {deck.author} | مبادرة التوثيق الثقافي"
        r_f.font.name = FONT_BODY
        r_f.font.size = Pt(13)
        r_f.font.color.rgb = COLOR_MUTED

    def _render_briefing_slide(self, slide, data: SlideContent):
        """Renders standard briefing slide with header bar and styled paragraphs."""
        self._add_slide_header(slide, data.title, data.region_badge)

        card_bg = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.8), Inches(1.8), Inches(11.733), Inches(4.8)
        )
        card_bg.fill.solid()
        card_bg.fill.fore_color.rgb = COLOR_CARD
        card_bg.line.color.rgb = COLOR_PAPER_2
        card_bg.line.width = Pt(1)

        content_box = slide.shapes.add_textbox(Inches(1.2), Inches(2.1), Inches(10.933), Inches(4.2))
        tf = content_box.text_frame
        tf.word_wrap = True

        first = True
        for p_text in data.body_paragraphs:
            p = tf.paragraphs[0] if first else tf.add_paragraph()
            first = False
            _set_paragraph_rtl(p)
            p.space_after = Pt(12)
            run = p.add_run()
            run.text = p_text
            run.font.name = FONT_BODY
            run.font.size = Pt(16)
            run.font.color.rgb = COLOR_INK

        for bullet in data.bullets:
            p = tf.add_paragraph()
            _set_paragraph_rtl(p)
            p.space_after = Pt(8)
            run_icon = p.add_run()
            run_icon.text = "✦  "
            run_icon.font.name = FONT_BODY
            run_icon.font.size = Pt(14)
            run_icon.font.color.rgb = COLOR_CLAY
            run_icon.font.bold = True

            run_b = p.add_run()
            run_b.text = bullet
            run_b.font.name = FONT_BODY
            run_b.font.size = Pt(15)
            run_b.font.color.rgb = COLOR_INK

        if data.quote:
            p_q = tf.add_paragraph()
            _set_paragraph_rtl(p_q)
            p_q.space_before = Pt(14)
            r_q = p_q.add_run()
            r_q.text = f"«{data.quote}»"
            r_q.font.name = FONT_HEADING
            r_q.font.size = Pt(16)
            r_q.font.color.rgb = COLOR_DATE
            r_q.font.italic = True

        self._add_slide_footer(slide, data.footer_text)

    def _render_comparison_slide(self, slide, data: SlideContent):
        """Renders multi-column comparison cards (e.g. Najdi vs Hejazi vs Asiri)."""
        self._add_slide_header(slide, data.title, data.region_badge or "مقارنة تراثية")

        cards = data.cards or []
        # Pagination is handled upstream (_expand_pagination); render all cards given.
        num_cards = max(1, len(cards))
        gap = Inches(0.4)
        total_width = Inches(11.733)
        card_width = (total_width - (gap * (num_cards - 1))) / num_cards
        start_x = Inches(0.8)

        for idx, card in enumerate(cards):
            x = start_x + (idx * (card_width + gap))
            y = Inches(1.8)
            h = Inches(4.8)

            c_shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, card_width, h)
            c_shape.fill.solid()
            c_shape.fill.fore_color.rgb = COLOR_CARD
            c_shape.line.color.rgb = COLOR_PAPER_2
            c_shape.line.width = Pt(1)

            accent_rgb = self._get_accent_rgb(card.accent_color)
            top_bar = slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE, x, y, card_width, Inches(0.12)
            )
            top_bar.fill.solid()
            top_bar.fill.fore_color.rgb = accent_rgb
            top_bar.line.fill.background()

            tb = slide.shapes.add_textbox(x + Inches(0.2), y + Inches(0.25), card_width - Inches(0.4), h - Inches(0.5))
            tf = tb.text_frame
            tf.word_wrap = True

            p_t = tf.paragraphs[0]
            _set_paragraph_rtl(p_t)
            r_t = p_t.add_run()
            r_t.text = card.title
            r_t.font.name = FONT_HEADING
            r_t.font.size = Pt(19)
            r_t.font.bold = True
            r_t.font.color.rgb = accent_rgb

            if card.subtitle:
                p_sub = tf.add_paragraph()
                _set_paragraph_rtl(p_sub)
                p_sub.space_after = Pt(8)
                r_sub = p_sub.add_run()
                r_sub.text = card.subtitle
                r_sub.font.name = FONT_BODY
                r_sub.font.size = Pt(13)
                r_sub.font.color.rgb = COLOR_MUTED

            for b in card.bullets:
                p_b = tf.add_paragraph()
                _set_paragraph_rtl(p_b)
                p_b.space_after = Pt(6)
                r_dot = p_b.add_run()
                r_dot.text = "• "
                r_dot.font.color.rgb = accent_rgb
                r_dot.font.bold = True
                r_text = p_b.add_run()
                r_text.text = b
                r_text.font.name = FONT_BODY
                r_text.font.size = Pt(14)
                r_text.font.color.rgb = COLOR_INK

        self._add_slide_footer(slide, data.footer_text)

    def _render_timeline_slide(self, slide, data: SlideContent):
        """Renders chronological timeline slide."""
        self._add_slide_header(slide, data.title, data.region_badge or "تسلسل تاريخي")

        timeline = data.timeline or []
        # Pagination is handled upstream (_expand_pagination); render all items given.
        num_steps = max(1, len(timeline))
        step_width = Inches(11.733) / num_steps
        start_x = Inches(0.8)

        line = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(0.8), Inches(3.4), Inches(11.733), Inches(0.06)
        )
        line.fill.solid()
        line.fill.fore_color.rgb = COLOR_GOLD
        line.line.fill.background()

        for idx, item in enumerate(timeline):
            x = start_x + (idx * step_width)

            circle = slide.shapes.add_shape(
                MSO_SHAPE.OVAL, x + (step_width / 2) - Inches(0.2), Inches(3.28), Inches(0.4), Inches(0.4)
            )
            circle.fill.solid()
            circle.fill.fore_color.rgb = COLOR_CLAY
            circle.line.color.rgb = COLOR_PAPER
            circle.line.width = Pt(2)

            tb_year = slide.shapes.add_textbox(x + Inches(0.1), Inches(2.2), step_width - Inches(0.2), Inches(1.0))
            tf_y = tb_year.text_frame
            tf_y.word_wrap = True
            p_y = tf_y.paragraphs[0]
            p_y.alignment = PP_ALIGN.CENTER
            r_y = p_y.add_run()
            r_y.text = item.year_or_era
            r_y.font.name = FONT_HEADING
            r_y.font.size = Pt(18)
            r_y.font.bold = True
            r_y.font.color.rgb = COLOR_DATE

            if item.hijri_year:
                p_h = tf_y.add_paragraph()
                p_h.alignment = PP_ALIGN.CENTER
                r_h = p_h.add_run()
                r_h.text = item.hijri_year
                r_h.font.name = FONT_BODY
                r_h.font.size = Pt(12)
                r_h.font.color.rgb = COLOR_GOLD

            tb_desc = slide.shapes.add_textbox(x + Inches(0.1), Inches(3.9), step_width - Inches(0.2), Inches(2.7))
            tf_d = tb_desc.text_frame
            tf_d.word_wrap = True
            p_dt = tf_d.paragraphs[0]
            p_dt.alignment = PP_ALIGN.CENTER
            r_dt = p_dt.add_run()
            r_dt.text = item.title
            r_dt.font.name = FONT_BODY
            r_dt.font.size = Pt(15)
            r_dt.font.bold = True
            r_dt.font.color.rgb = COLOR_INK

            if item.description:
                p_dd = tf_d.add_paragraph()
                p_dd.alignment = PP_ALIGN.CENTER
                p_dd.space_before = Pt(6)
                r_dd = p_dd.add_run()
                r_dd.text = item.description
                r_dd.font.name = FONT_BODY
                r_dd.font.size = Pt(13)
                r_dd.font.color.rgb = COLOR_INK

        self._add_slide_footer(slide, data.footer_text)

    def _render_table_slide(self, slide, data: SlideContent):
        """Renders an RTL data table slide (logical-first column rightmost)."""

        self._add_slide_header(slide, data.title, data.region_badge or "جدول")
        rows = [list(r) for r in (data.table_rows or []) if r]
        if not rows:
            raise DeckBuildError(f"Table slide '{data.title}' has no rows; refusing filler.")
        width = max(len(r) for r in rows)
        normalized = [r + [""] * (width - len(r)) for r in rows]
        # PPTX tables are LTR-positioned grids; _expand_pagination() splits
        # overflow onto "(تابع)" slides, so render every row (never truncate).
        rtl_rows = [list(reversed(r)) for r in normalized]
        left, top = Inches(0.8), Inches(1.8)
        table_width, table_height = Inches(11.733), Inches(4.6)
        shape = slide.shapes.add_table(len(rtl_rows), width, left, top, table_width, table_height)
        table = shape.table
        for row_idx, row in enumerate(rtl_rows):
            for col_idx, cell_text in enumerate(row):
                cell = table.cell(row_idx, col_idx)
                cell.text = ""
                para = cell.text_frame.paragraphs[0]
                _set_paragraph_rtl(para)
                run = para.add_run()
                run.text = cell_text
                run.font.name = FONT_BODY
                run.font.size = Pt(14 if row_idx == 0 else MIN_BODY_PT)
                run.font.bold = row_idx == 0
                run.font.color.rgb = COLOR_DATE if row_idx == 0 else COLOR_INK
                if row_idx == 0:
                    cell.fill.solid()
                    cell.fill.fore_color.rgb = COLOR_PAPER_2
        self._add_slide_footer(slide, data.footer_text)

    def _render_image_slide(self, slide, data: SlideContent):
        """Renders a full-bleed-ish image slide; fails visibly when missing."""

        from pathlib import Path as _Path

        self._add_slide_header(slide, data.title, data.region_badge or "صورة")
        src = (data.body_paragraphs[0] if data.body_paragraphs else "").strip()
        if src and "://" in src:
            # PPTX-B1: remote image URLs are not local files — render a styled
            # reference card instead of crashing the whole deck.
            self._render_remote_image_card(slide, data.title, src)
            self._add_slide_footer(slide, data.footer_text)
            return
        candidate = _Path(src)
        if not src or not candidate.is_file():
            raise DeckBuildError(f"Image slide '{data.title}' has no readable image; refusing filler.")
        try:
            slide.shapes.add_picture(str(candidate), Inches(1.5), Inches(1.8), width=Inches(10.333), height=Inches(4.6))
        except Exception as exc:
            raise DeckBuildError(f"Image slide '{data.title}' could not embed image.") from exc
        self._add_slide_footer(slide, data.footer_text)

    def _render_remote_image_card(self, slide, title: str, url: str) -> None:
        """Styled placeholder card for web-hosted images (no download)."""
        card = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE, Inches(1.5), Inches(1.8), Inches(10.333), Inches(4.6)
        )
        card.fill.solid()
        card.fill.fore_color.rgb = COLOR_CARD
        card.line.color.rgb = COLOR_GOLD
        card.line.width = Pt(1.5)
        box = slide.shapes.add_textbox(Inches(1.9), Inches(2.2), Inches(9.533), Inches(3.8))
        tf = box.text_frame
        tf.word_wrap = True
        first = True
        for line in (title, url):
            para = tf.paragraphs[0] if first else tf.add_paragraph()
            first = False
            _set_paragraph_rtl(para)
            run = para.add_run()
            run.text = line
            run.font.name = FONT_BODY
            run.font.size = Pt(16 if line == title else 12)
            run.font.bold = line == title
            run.font.color.rgb = COLOR_INK if line == title else COLOR_MUTED

    def _render_sources_slide(self, slide, data: SlideContent):
        """Renders the bibliography slide from caller-supplied sources only."""

        self._add_slide_header(slide, data.title, "المراجع")
        if not data.bullets:
            raise DeckBuildError("Sources slide has no sources; refusing filler.")
        box = slide.shapes.add_textbox(Inches(0.8), Inches(1.8), Inches(11.733), Inches(4.8))
        tf = box.text_frame
        tf.word_wrap = True
        first = True
        for source in data.bullets:
            para = tf.paragraphs[0] if first else tf.add_paragraph()
            first = False
            _set_paragraph_rtl(para)
            para.space_after = Pt(6)
            run = para.add_run()
            run.text = source
            run.font.name = FONT_BODY
            run.font.size = Pt(13)
            run.font.color.rgb = COLOR_MUTED
        self._add_slide_footer(slide, data.footer_text)

    def _render_summary_slide(self, slide, data: SlideContent):
        """Renders closing / takeaways slide with highlighted takeaway card."""
        self._add_slide_header(slide, data.title, "خلاصة وتوثيق")

        card_bg = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE, Inches(1.5), Inches(1.8), Inches(10.333), Inches(4.8)
        )
        card_bg.fill.solid()
        card_bg.fill.fore_color.rgb = COLOR_CARD
        card_bg.line.color.rgb = COLOR_GOLD
        card_bg.line.width = Pt(1.5)

        tb = slide.shapes.add_textbox(Inches(1.9), Inches(2.1), Inches(9.533), Inches(4.2))
        tf = tb.text_frame
        tf.word_wrap = True

        first = True
        for b in data.bullets:
            p = tf.paragraphs[0] if first else tf.add_paragraph()
            first = False
            _set_paragraph_rtl(p)
            p.space_after = Pt(12)
            r_i = p.add_run()
            r_i.text = "✦  "
            r_i.font.color.rgb = COLOR_CLAY
            r_i.font.bold = True
            r_i.font.size = Pt(16)

            r_t = p.add_run()
            r_t.text = b
            r_t.font.name = FONT_BODY
            r_t.font.size = Pt(16)
            r_t.font.color.rgb = COLOR_INK

        if data.quote:
            p_q = tf.add_paragraph()
            p_q.alignment = PP_ALIGN.CENTER
            p_q.space_before = Pt(16)
            r_q = p_q.add_run()
            r_q.text = f"«{data.quote}»"
            r_q.font.name = FONT_HEADING
            r_q.font.size = Pt(17)
            r_q.font.color.rgb = COLOR_DATE
            r_q.font.italic = True

        self._add_slide_footer(slide, data.footer_text)

    def _add_slide_header(self, slide, title: str, badge: str = ""):
        """Renders standard top bar with title and cultural badge."""
        stripe = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(0.8), Inches(0.5), Inches(11.733), Inches(0.04)
        )
        stripe.fill.solid()
        stripe.fill.fore_color.rgb = COLOR_CLAY
        stripe.line.fill.background()

        h_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.65), Inches(8.5), Inches(0.9))
        tf = h_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        _set_paragraph_rtl(p)
        r = p.add_run()
        r.text = title
        r.font.name = FONT_HEADING
        r.font.size = Pt(24)
        r.font.bold = True
        r.font.color.rgb = COLOR_INK

        if badge:
            b_box = slide.shapes.add_textbox(Inches(9.5), Inches(0.65), Inches(3.0), Inches(0.6))
            tf_b = b_box.text_frame
            p_b = tf_b.paragraphs[0]
            p_b.alignment = PP_ALIGN.LEFT
            r_b = p_b.add_run()
            r_b.text = f"[{badge}]"
            r_b.font.name = FONT_BODY
            r_b.font.size = Pt(13)
            r_b.font.color.rgb = COLOR_OLIVE
            r_b.font.bold = True

    def _add_slide_footer(self, slide, footer_text: str):
        """Renders discrete footer with branding."""
        f_box = slide.shapes.add_textbox(Inches(0.8), Inches(6.8), Inches(11.733), Inches(0.4))
        tf = f_box.text_frame
        p = tf.paragraphs[0]
        _set_paragraph_rtl(p)
        r = p.add_run()
        r.text = footer_text
        r.font.name = FONT_BODY
        r.font.size = Pt(10)
        r.font.color.rgb = COLOR_MUTED

    def save_deck_file(self, deck: PresentationDeck, filename: Optional[str] = None) -> Tuple[Path, str]:
        """Builds and writes presentation to a .pptx file."""
        safe_name = filename or f"{deck.deck_id}.pptx"
        if not safe_name.endswith(".pptx"):
            safe_name += ".pptx"
        target_path = self.output_dir / safe_name
        data = self.build_pptx(deck)
        target_path.write_bytes(data)
        logger.info("Saved PowerPoint presentation deck: %s (%d bytes)", target_path, len(data))
        return target_path, safe_name


# ---------------------------------------------------------------------------
# High-Level Templates & Presets
# ---------------------------------------------------------------------------

def create_cultural_briefing_deck(
    topic: str,
    region: str = "المملكة العربية السعودية",
    overview_text: str = "",
    overview_bullets: Optional[List[str]] = None,
    comparison_cards: Optional[List[Dict[str, Any]]] = None,
    timeline_items: Optional[List[Dict[str, Any]]] = None,
    key_takeaways: Optional[List[str]] = None,
    quote: str = "",
    quote_author: str = "",
) -> PresentationDeck:
    """Creates a cultural briefing deck from caller-supplied content ONLY.

    No filler is invented: overview bullets default to [] (not hallucinated
    heritage claims), the summary slide is emitted only when ``key_takeaways``
    are supplied, and ``quote`` renders only when provided.  Raises
    :class:`DeckBuildError` when there is nothing real to render.
    """

    clean_topic = (topic or "").strip()
    if not clean_topic:
        raise DeckBuildError("Deck topic is required; refusing to render filler.")
    deck = PresentationDeck(
        title=f"الإيجاز الثقافي: {clean_topic}",
        topic=clean_topic,
        region=region,
    )

    # 1. Title Slide
    deck.slides.append(
        SlideContent(
            slide_type="title",
            title=clean_topic,
            subtitle=f"إيجاز توثيقي حول {clean_topic} في {region}" if overview_text else "",
            region_badge=region,
        )
    )

    has_content = bool(
        overview_text.strip()
        or (overview_bullets or [])
        or (comparison_cards or [])
        or (timeline_items or [])
        or (key_takeaways or [])
        or quote.strip()
    )
    if not has_content:
        raise DeckBuildError(
            "Deck has no content (overview, cards, timeline, takeaways, quote); refusing filler."
        )

    # 2. Overview Slide (caller content only)
    if overview_text.strip() or (overview_bullets or []):
        deck.slides.append(
            SlideContent(
                slide_type="briefing",
                title=f"مدخل إلى {clean_topic}",
                body_paragraphs=[overview_text.strip()] if overview_text.strip() else [],
                bullets=[b for b in (overview_bullets or []) if str(b or "").strip()],
                quote=quote.strip(),
                quote_author=quote_author,
                region_badge=region,
            )
        )

    # 3. Comparative Analysis Slide (if provided)
    if comparison_cards:
        cards_objs = []
        for c in comparison_cards:
            cards_objs.append(
                SlideCard(
                    title=c.get("title", ""),
                    subtitle=c.get("subtitle", ""),
                    bullets=c.get("bullets", []),
                    badge=c.get("badge", ""),
                    accent_color=c.get("accent_color", "clay"),
                )
            )
        deck.slides.append(
            SlideContent(
                slide_type="comparison",
                title=f"الخصائص والأنماط الإقليمية: {topic}",
                cards=cards_objs,
                region_badge=region,
            )
        )

    # 4. Timeline Slide (if provided)
    if timeline_items:
        t_objs = []
        for t in timeline_items:
            t_objs.append(
                TimelineItem(
                    year_or_era=t.get("year_or_era", ""),
                    hijri_year=t.get("hijri_year", ""),
                    title=t.get("title", ""),
                    description=t.get("description", ""),
                )
            )
        deck.slides.append(
            SlideContent(
                slide_type="timeline",
                title=f"المحطات التاريخية والتحولات: {topic}",
                timeline=t_objs,
                region_badge=region,
            )
        )

    # 5. Summary / Takeaways Slide (caller takeaways only; no invented bullets/quote)
    if key_takeaways:
        deck.slides.append(
            SlideContent(
                slide_type="summary",
                title="الخلاصة والتوصيات",
                bullets=[t for t in key_takeaways if str(t or "").strip()],
                region_badge=region,
            )
        )

    return deck
