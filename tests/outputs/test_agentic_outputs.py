"""Tests for Sard Agentic Cultural Output Engines.

Validates PPTX slide deck generation, visual SVG diagram rendering,
calendar sync (.ics + Google URLs), printable recipe/craft cards,
greeting cards, and oral history memoirs.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import pytest

from sard.outputs.office import (
    PresentationDeck,
    SlideContent,
    SlideCard,
    TimelineItem,
    PresentationGenerator,
    create_cultural_briefing_deck,
)
from sard.outputs.diagrams import (
    CulturalDiagram,
    DiagramRenderer,
    create_majlis_etiquette_diagram,
    create_business_etiquette_diagram,
)
from sard.outputs.calendar_sync import (
    HeritageCalendarSync,
    HERITAGE_EVENTS_DATABASE,
)
from sard.outputs.recipe_card import (
    RecipeOrCraftCard,
    RecipeCardRenderer,
    create_jareesh_recipe_card,
    create_sadu_craft_card,
)
from sard.outputs.greeting_cards import (
    GreetingCardStudio,
    compose_greeting_card,
)
from sard.outputs.memoir import (
    FamilyMemoirBooklet,
    MemoirChapter,
    MemoirCompiler,
    synthesize_memoir_from_notes,
)


def test_pptx_deck_generation(tmp_path: Path):
    """Test generating a 16:9 widescreen PowerPoint deck with GenOffice architecture."""
    deck = create_cultural_briefing_deck(
        topic="يوم التأسيس السعودي",
        region="نجد والدرعية",
        overview_text="مناسبة وطنية تحتفي بذكرى تأسيس الدولة السعودية الأولى عام 1727م.",
        comparison_cards=[
            {"title": "العمارة التقليدية", "bullets": ["استخدام الطين واللبن", "الرواشن والمثلثات النجدية"]},
            {"title": "الأزياء التراثية", "bullets": ["المردون والدقلة", "الغبانة والشيلة"]},
        ],
        timeline_items=[
            {"era": "1727م", "title": "تأسيس الدولة الأولى", "description": "تولي الإمام محمد بن سعود الحكم في الدرعية"},
            {"era": "1824م", "title": "الدولة السعودية الثانية", "description": "إعادة التأسيس وعاصمتها الرياض"},
            {"era": "1902م", "title": "استرداد الرياض", "description": "انطلاق مسيرة التوحيد المباركة"},
        ],
        key_takeaways=["عمق تاريخي ممتد لثلاثة قرون", "أصالة التقاليد النجدية والعربية"],
    )

    out_file = tmp_path / "briefing.pptx"
    gen = PresentationGenerator(tmp_path)
    pptx_bytes = gen.build_pptx(deck)
    out_file.write_bytes(pptx_bytes)

    assert out_file.exists()
    assert out_file.stat().st_size > 1000
    assert len(deck.slides) == 5


def test_diagram_svg_rendering():
    """Test generating structured visual SVGs using diagram-design schemas."""
    majlis_diag = create_majlis_etiquette_diagram()
    renderer = DiagramRenderer()
    svg_out = renderer.render_svg(majlis_diag)

    assert "<svg" in svg_out
    assert "المجلس" in svg_out
    assert "direction:rtl" in svg_out
    assert "</svg>" in svg_out

    biz_diag = create_business_etiquette_diagram()
    biz_svg = renderer.render_svg(biz_diag)
    assert "<svg" in biz_svg
    assert "المفاوضات" in biz_svg


def test_calendar_sync_and_ics(tmp_path: Path):
    """Test heritage calendar queries, Google Calendar URLs, and .ics export."""
    sync_engine = HeritageCalendarSync()

    # Query events
    events = sync_engine.search_events(query="سهيل")
    assert len(events) >= 1
    suhail = events[0]
    assert "سهيل" in suhail.title_ar

    # Google Calendar URL check
    gcal_url = suhail.google_calendar_url()
    assert "calendar.google.com/calendar/render" in gcal_url
    assert "action=TEMPLATE" in gcal_url

    # Export .ics
    ics_file = tmp_path / "heritage_events.ics"
    ics_text = sync_engine.export_ics(events)
    ics_file.write_text(ics_text, encoding="utf-8")
    assert ics_file.exists()

    content = ics_file.read_text(encoding="utf-8")
    assert "BEGIN:VCALENDAR" in content
    assert "VERSION:2.0" in content
    assert "SUMMARY:" in content
    assert "END:VCALENDAR" in content


def test_recipe_and_craft_card_pdf(tmp_path: Path):
    """Test generating printable recipe and craft card PDFs with ReportLab."""
    renderer = RecipeCardRenderer(tmp_path)

    # Recipe card
    jareesh = create_jareesh_recipe_card()
    jareesh_pdf = tmp_path / "jareesh.pdf"
    jareesh_pdf.write_bytes(renderer.render_pdf(jareesh))
    assert jareesh_pdf.exists()
    assert jareesh_pdf.stat().st_size > 1000

    # Craft card
    sadu = create_sadu_craft_card()
    sadu_pdf = tmp_path / "sadu.pdf"
    sadu_pdf.write_bytes(renderer.render_pdf(sadu))
    assert sadu_pdf.exists()
    assert sadu_pdf.stat().st_size > 1000


def test_greeting_card_studio(tmp_path: Path):
    """Test creating cultural greeting cards with poetic meters and SVGs."""
    studio = GreetingCardStudio(tmp_path)
    card = compose_greeting_card(
        occasion="foundation_day",
        recipient_name="عبد العزيز",
        sender_name="نواف",
        theme="dark_gold",
    )

    svg = studio.render_svg_card(card)
    assert "<svg" in svg
    assert "يوم التأسيس" in svg

    pdf_file = tmp_path / "greeting_card.pdf"
    pdf_file.write_bytes(studio.render_pdf_card(card))
    assert pdf_file.exists()
    assert pdf_file.stat().st_size > 1000


def test_oral_history_memoir_compiler(tmp_path: Path):
    """Test compiling multi-turn oral history notes into chaptered PDF booklet."""
    raw_notes = [
        {
            "topic": "النشأة في الأحساء وأيام النخيل",
            "content": "كنا نستيقظ مع بزوغ الفجر في الهفوف، ونسمع هدير عيون الماء وسواقي النخيل.",
            "era": "السبعينيات الميلادية",
        },
        {
            "topic": "رحلات الغوص وصيد اللؤلؤ",
            "content": "كان النوخذة يقود السفينة في مياه الخليج، والغيص ينزل إلى القاع بحبل الديين.",
            "era": "الستينيات الميلادية",
        },
    ]

    booklet = synthesize_memoir_from_notes(
        family_name="آل مبارك",
        raw_notes=raw_notes,
        origin_region="المنطقة الشرقية",
        origin_town="الأحساء",
    )

    assert len(booklet.chapters) == 2
    assert booklet.family_or_narrator_name == "آل مبارك"

    compiler = MemoirCompiler(tmp_path)
    out_pdf = tmp_path / "memoir.pdf"
    out_pdf.write_bytes(compiler.compile_pdf(booklet))
    assert out_pdf.exists()
    assert out_pdf.stat().st_size > 1000
