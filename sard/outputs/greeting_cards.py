"""Creative Cultural Greeting Card & Calligraphy Studio for Sard.

Generates beautifully formatted cultural greeting cards for Saudi & Arab occasions
(Eids, National Day, Foundation Day, Ramadan, Weddings) with poetic verses,
customizable Arabic typography, Islamic geometric arches, and Sard cultural colors.
"""

from __future__ import annotations

import html
import io
import logging
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfgen.canvas import Canvas

from sard.outputs.arabic import shape_rtl
from sard.outputs.fonts import ensure_fonts_registered, require_arabic_font, require_latin_font

logger = logging.getLogger("sard.outputs.greeting_cards")

COLOR_PAPER = "#F3EEE4"
COLOR_PAPER_2 = "#E8E0D2"
COLOR_INK = "#141210"
COLOR_CLAY = "#BE4A24"
COLOR_DATE = "#6E1F1F"
COLOR_OLIVE = "#4A513C"
COLOR_GOLD = "#C4A46A"
COLOR_CARD = "#FAF7F1"
COLOR_MUTED = "#8A8178"


@dataclass
class GreetingCard:
    """Represents a personalized cultural greeting card."""
    occasion: str  # foundation_day, national_day, eid_fitr, eid_adha, ramadan, wedding, celebration
    title: str
    recipient_name: str = ""
    sender_name: str = ""
    poetic_verse: str = ""
    poetic_meter: str = "فصحى / نبطي"
    personal_message: str = ""
    theme: str = "dark_gold"  # dark_gold, royal_green, warm_clay, heritage_paper
    card_id: str = field(default_factory=lambda: f"card-{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class GreetingCardStudio:
    """Renders digital greeting cards as styled SVGs and downloadable PDFs."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path("output")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def render_svg(self, card: GreetingCard, width: int = 800, height: int = 500) -> str:
        """Generates a responsive, ultra-luxurious SVG greeting card."""
        bg_color = COLOR_INK if card.theme == "dark_gold" else COLOR_OLIVE if card.theme == "royal_green" else COLOR_DATE if card.theme == "warm_clay" else COLOR_CARD
        text_primary = COLOR_PAPER if card.theme in ["dark_gold", "royal_green", "warm_clay"] else COLOR_INK
        accent_color = COLOR_GOLD

        svg_lines = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" height="100%" style="font-family:\'IBM Plex Sans Arabic\', sans-serif; direction:rtl; text-anchor:middle;">',
            '<defs>',
            '  <filter id="card-shadow" x="-5%" y="-5%" width="110%" height="110%" filterUnits="userSpaceOnUse">',
            '    <feDropShadow dx="0" dy="6" stdDeviation="10" flood-color="#000000" flood-opacity="0.25"/>',
            '  </filter>',
            '  <linearGradient id="gold-grad" x1="0%" y1="0%" x2="100%" y2="100%">',
            '    <stop offset="0%" stop-color="#DFBA75"/>',
            '    <stop offset="50%" stop-color="#C4A46A"/>',
            '    <stop offset="100%" stop-color="#9C7E47"/>',
            '  </linearGradient>',
            '</defs>',
            # Card Base
            f'<rect x="15" y="15" width="{width-30}" height="{height-30}" rx="20" fill="{bg_color}" filter="url(#card-shadow)"/>',
            # Outer Gold Border
            f'<rect x="25" y="25" width="{width-50}" height="{height-50}" rx="16" fill="none" stroke="url(#gold-grad)" stroke-width="1.8"/>',
            # Inner Subtle Border
            f'<rect x="33" y="33" width="{width-66}" height="{height-66}" rx="12" fill="none" stroke="url(#gold-grad)" stroke-width="0.8" stroke-dasharray="8,4"/>',
            # Top Cultural Arch Ornament
            f'<path d="M {width//2 - 60} 45 Q {width//2} 28 {width//2 + 60} 45" fill="none" stroke="url(#gold-grad)" stroke-width="2"/>',
            f'<circle cx="{width//2}" cy="37" r="4" fill="url(#gold-grad)"/>',
        ]

        # Occasion Header
        svg_lines.append(
            f'<text x="{width//2}" y="85" fill="{accent_color}" font-size="14" font-weight="700" letter-spacing="1">✦ {html.escape(card.title)} ✦</text>'
        )

        # Recipient Name (if provided)
        y_pos = 135
        if card.recipient_name:
            svg_lines.append(
                f'<text x="{width//2}" y="{y_pos}" fill="{text_primary}" font-size="20" font-weight="700" font-family="Noto Naskh Arabic, serif">إلى الكريم/ة: {html.escape(card.recipient_name)}</text>'
            )
            y_pos += 45

        # Poetic Verse (Calligraphy feel)
        if card.poetic_verse:
            verses = card.poetic_verse.splitlines()
            for v in verses:
                if v.strip():
                    svg_lines.append(
                        f'<text x="{width//2}" y="{y_pos}" fill="url(#gold-grad)" font-size="18" font-weight="700" font-family="Noto Naskh Arabic, serif">« {html.escape(v.strip())} »</text>'
                    )
                    y_pos += 32

        # Personal Message
        if card.personal_message:
            y_pos += 10
            msg_lines = card.personal_message.splitlines()
            for line in msg_lines[:3]:
                if line.strip():
                    svg_lines.append(
                        f'<text x="{width//2}" y="{y_pos}" fill="{text_primary}" font-size="14" font-weight="400">{html.escape(line.strip())}</text>'
                    )
                    y_pos += 26

        # Sender Signoff
        if card.sender_name:
            y_pos += 15
            svg_lines.append(
                f'<text x="{width//2}" y="{y_pos}" fill="{accent_color}" font-size="14" font-weight="600">من: {html.escape(card.sender_name)}</text>'
            )

        # Bottom Palm & Swords Emblem Indicator
        svg_lines.append(
            f'<path d="M {width//2 - 40} {height - 45} Q {width//2} {height - 30} {width//2 + 40} {height - 45}" fill="none" stroke="url(#gold-grad)" stroke-width="1.5"/>'
            f'<circle cx="{width//2}" cy="{height - 37}" r="3" fill="url(#gold-grad)"/>'
            f'<text x="{width//2}" y="{height - 20}" fill="{COLOR_MUTED}" font-size="10">سرد • بطاقات المناسبات التراثية</text>'
            '</svg>'
        )
        return "\n".join(svg_lines)

    def render_pdf(self, card: GreetingCard) -> bytes:
        """Draws landscape greeting card PDF."""
        arabic_font, latin_font = ensure_fonts_registered()

        buffer = io.BytesIO()
        # Landscape Letter size
        pagesize = landscape(letter)
        c = Canvas(buffer, pagesize=pagesize)
        w, h = pagesize

        # Background
        c.setFillColor(colors.HexColor(COLOR_INK))
        c.rect(0, 0, w, h, stroke=0, fill=1)

        # Gold Border
        c.setStrokeColor(colors.HexColor(COLOR_GOLD))
        c.setLineWidth(2)
        c.roundRect(30, 30, w - 60, h - 60, 16, stroke=1, fill=0)

        # Title
        c.setFillColor(colors.HexColor(COLOR_GOLD))
        c.setFont(arabic_font, 22)
        c.drawCentredString(w / 2, h - 90, shape_rtl(f"✦ {card.title} ✦"))

        # Recipient
        y = h - 150
        if card.recipient_name:
            c.setFillColor(colors.HexColor(COLOR_PAPER))
            c.setFont(arabic_font, 18)
            c.drawCentredString(w / 2, y, shape_rtl(f"إلى: {card.recipient_name}"))
            y -= 45

        # Verse
        if card.poetic_verse:
            c.setFillColor(colors.HexColor(COLOR_GOLD))
            c.setFont(arabic_font, 16)
            for v in card.poetic_verse.splitlines():
                if v.strip():
                    c.drawCentredString(w / 2, y, shape_rtl(f"« {v.strip()} »"))
                    y -= 30

        # Message
        if card.personal_message:
            y -= 10
            c.setFillColor(colors.HexColor(COLOR_PAPER))
            c.setFont(arabic_font, 13)
            for line in card.personal_message.splitlines():
                if line.strip():
                    c.drawCentredString(w / 2, y, shape_rtl(line.strip()))
                    y -= 24

        # Sender
        if card.sender_name:
            y -= 15
            c.setFillColor(colors.HexColor(COLOR_GOLD))
            c.setFont(arabic_font, 14)
            c.drawCentredString(w / 2, y, shape_rtl(f"من: {card.sender_name}"))

        # Footer
        c.setFillColor(colors.HexColor(COLOR_MUTED))
        c.setFont(arabic_font, 9)
        c.drawCentredString(w / 2, 45, shape_rtl("سرد — استوديو البطاقات التراثية والخط العربي"))

        c.showPage()
        c.save()
        return buffer.getvalue()

    def render_svg_card(self, card: GreetingCard) -> str:
        return self.render_svg(card)

    def render_pdf_card(self, card: GreetingCard) -> bytes:
        return self.render_pdf(card)

    def save_pdf_file(self, card: GreetingCard, filename: Optional[str] = None) -> Tuple[Path, str]:
        safe_name = filename or f"{card.card_id}.pdf"
        if not safe_name.endswith(".pdf"):
            safe_name += ".pdf"
        target_path = self.output_dir / safe_name
        data = self.render_pdf(card)
        target_path.write_bytes(data)
        logger.info("Saved Greeting Card PDF: %s (%d bytes)", target_path, len(data))
        return target_path, safe_name


# ---------------------------------------------------------------------------
# High-Level Helper Presets & Poetry Engine
# ---------------------------------------------------------------------------

OCCASION_POETRY_PRESETS = {
    "foundation_day": {
        "title": "تهنئة يوم التأسيس السعودي (يوم بدينا)",
        "verse": "ثلاثةُ قرونٍ والمجدُ يَسري في دمِنا ... جذورٌ رسَتْ وعزٌّ بنا تَسامى",
        "default_msg": "أرفع أسمى آيات التهاني والتبريكات بمناسبة ذكرى يوم التأسيس، سائلاً المولى أن يديم على وطننا الغالي عزه وأمنه وازدهاره.",
    },
    "national_day": {
        "title": "تهنئة اليوم الوطني السعودي (نحلم ونحقق)",
        "verse": "دُمتَ يا وطني مناراً للهدى ... وملاذاً للعلا طابَ مقاما",
        "default_msg": "كل عام ووطننا المعطاء في قمة المجد والعلياء، حاملاً راية التوحيد والعزم نحو المستقبل المشرق.",
    },
    "eid_fitr": {
        "title": "تهنئة عيد الفطر المبارك",
        "verse": "أتاكَ العيدُ بالبُشرى وسارَا ... وألبسَكَ المسرّةَ والفخارَا",
        "default_msg": "تقبل الله منا ومنكم صالح الأعمال، وأعاده علينا وعليكم باليمن والبركات والمسرات وكل عام وأنتم بخير.",
    },
    "eid_adha": {
        "title": "تهنئة عيد الأضحى المبارك",
        "verse": "عِيدٌ أَطَلَّ عَلَى الأحِبَّةِ بَاسِمَا ... يَهْدِي القُلُوبَ سَعَادَةً وَمَغَانِمَا",
        "default_msg": "أضحى مبارك، جعله الله عيداً مليئاً بالخير والسلام والبركات، وتقبل الله طاعاتكم وأدام بهجتكم.",
    },
    "ramadan": {
        "title": "تهنئة حلول شهر رمضان المبارك",
        "verse": "هَلَّ الهِلالُ فَرَحَّبَتْ أَرْوَاحُنَا ... شَهْرُ الصِّيَامِ وَمَنْبَعُ الإِحْسَانِ",
        "default_msg": "مبارك عليكم شهر رمضان الفضيل، نسأل الله أن يوفقنا وإياكم لصيامه وقيامه وعتقائه من النيران.",
    },
}


def compose_greeting_card(
    occasion: str,
    recipient_name: str = "",
    sender_name: str = "",
    custom_message: str = "",
    theme: str = "dark_gold",
) -> GreetingCard:
    """Composes a complete greeting card using authentic presets or custom notes."""
    preset = OCCASION_POETRY_PRESETS.get(occasion.lower(), {
        "title": "بطاقة تهنئة ومودة",
        "verse": "دامتْ مسرّاتُكم بالخيرِ زاهيةً ... وطابَ سعيُكم في كلِّ مكرُمَةِ",
        "default_msg": "أطيب التهاني وأصدق التمنيات بدوام الصحة والتوفيق والمسرات.",
    })

    return GreetingCard(
        occasion=occasion,
        title=preset["title"],
        recipient_name=recipient_name,
        sender_name=sender_name,
        poetic_verse=preset["verse"],
        personal_message=custom_message or preset["default_msg"],
        theme=theme,
    )
