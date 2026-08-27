"""Tests for Sard Unified Agentic Cultural Tools.

Validates the tool wrappers for presentation generation, recipe card creation,
calendar sync, etiquette protocol simulation, dialect/proverb lore decoder,
artisan craft concierge, greeting card studio, oral history memoir compiler,
and verified heritage research citations.
"""

from __future__ import annotations

import pytest
from sard.agent.tools.cultural_agentic_tools import (
    tool_generate_presentation,
    tool_generate_recipe_or_craft_card,
    tool_sync_heritage_calendar,
    tool_simulate_etiquette_protocol,
    tool_decode_dialect_or_proverb,
    tool_advise_artisan_craft,
    tool_create_greeting_card,
    tool_compile_oral_history_memoir,
    tool_conduct_verified_research,
)


def test_tool_generate_presentation():
    res = tool_generate_presentation(topic="تراث الدرعية التاريخي")
    assert res["success"] is True
    assert res["filename"].endswith(".pptx")
    assert "slides" in res
    assert len(res["slides"]) >= 3


def test_tool_generate_recipe_or_craft_card():
    res = tool_generate_recipe_or_craft_card(item_name="الجريش")
    assert res["success"] is True
    assert res["filename"].endswith(".pdf")
    assert "card_data" in res
    assert "ingredients_or_materials" in res["card_data"]


def test_tool_sync_heritage_calendar():
    res = tool_sync_heritage_calendar(query="المربعانية")
    assert res["success"] is True
    assert res["filename"].endswith(".ics")
    assert len(res["events"]) >= 1
    assert "google_calendar_url" in res["events"][0]


def test_tool_simulate_etiquette_protocol():
    res = tool_simulate_etiquette_protocol(scenario_type="majlis")
    assert res["success"] is True
    assert "<svg" in res["diagram_svg"]
    assert "diagram_data" in res


def test_tool_decode_dialect_or_proverb():
    res = tool_decode_dialect_or_proverb(phrase_or_proverb="أبشر بسعدك", dialect_region="najdi")
    assert res["success"] is True
    assert "سالفة" in res["lore_story_ar"] or "قصة" in res["lore_story_ar"] or len(res["lore_story_ar"]) > 5
    assert res["meaning_ar"]


def test_tool_advise_artisan_craft():
    res = tool_advise_artisan_craft(craft_name="sadu")
    assert res["success"] is True
    assert len(res["authentication_checklist"]) >= 3
    assert res["care_instructions"]


def test_tool_create_greeting_card():
    res = tool_create_greeting_card(occasion="foundation_day", recipient_name="سارة")
    assert res["success"] is True
    assert "<svg" in res["svg_markup"]
    assert res["filename"].endswith(".pdf")


def test_tool_compile_oral_history_memoir():
    notes = [
        {"topic": "أيام الصبا", "content": "كنا نلعب في سكيك القرية عند الغروب.", "era": "الماضي"}
    ]
    res = tool_compile_oral_history_memoir(family_name="العتيبي", raw_notes=notes)
    assert res["success"] is True
    assert res["filename"].endswith(".pdf")
    assert len(res["chapters"]) == 1


def test_tool_conduct_verified_research():
    res = tool_conduct_verified_research(topic="توثيق قصر المربع")
    assert res["success"] is True
    assert len(res["bibliography"]) >= 3
    assert "<svg" in res["timeline_svg"]
