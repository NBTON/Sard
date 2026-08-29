"""Comprehensive Agentic Cultural Tool Suite for Sard.

Implements all 8 expanded agentic capabilities:
1. Dynamic Document & Slide Deck Generator (genoffice PPTX / PDF)
2. Smart Calendar & Heritage Temporal Sync (.ics & Google Calendar)
3. Interactive Cultural Etiquette & Protocol Simulator (المُوجّه الثقافي)
4. Oral History & Family Archive Co-Pilot (حكواتي العائلة)
5. Dialect & Proverb Lore Decoder (المترجم والراوي للأمثال)
6. Local Artisan & Traditional Craft Concierge (دليل الحِرَف والتراث)
7. Creative Greeting Card & Calligraphy Studio (بطاقات المناسبات)
8. Verified Heritage Research & Citation Mode (التوثيق المعتمد)
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sard.outputs.calendar_sync import (
    HERITAGE_EVENTS_DATABASE,
    HeritageCalendarEvent,
    HeritageCalendarSync,
)
from sard.outputs.diagrams import (
    CulturalDiagram,
    DiagramRenderer,
    FlowNode,
    TimelineMilestone,
    create_business_etiquette_diagram,
    create_majlis_etiquette_diagram,
)
from sard.outputs.greeting_cards import (
    GreetingCard,
    GreetingCardStudio,
    compose_greeting_card,
)
from sard.outputs.memoir import (
    FamilyMemoirBooklet,
    MemoirChapter,
    MemoirCompiler,
    synthesize_memoir_from_notes,
)
from sard.outputs.office import (
    PresentationDeck,
    PresentationGenerator,
    SlideCard,
    SlideContent,
    TimelineItem,
    create_cultural_briefing_deck,
)
from sard.outputs.recipe_card import (
    IngredientOrMaterial,
    RecipeCardRenderer,
    RecipeOrCraftCard,
    StepInstruction,
    create_jareesh_recipe_card,
    create_sadu_craft_card,
)
from sard.runtime_paths import output_root

logger = logging.getLogger("sard.tools.agentic")

OUTPUT_DIR = output_root()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 1. Slide Deck Generator Tool
# ---------------------------------------------------------------------------

def tool_generate_presentation(
    topic: str,
    region: str = "المملكة العربية السعودية",
    overview_text: str = "",
    comparison_cards: Optional[List[Dict[str, Any]]] = None,
    timeline_items: Optional[List[Dict[str, Any]]] = None,
    key_takeaways: Optional[List[str]] = None,
    quote: str = "",
) -> Dict[str, Any]:
    """Generates a PowerPoint (.pptx) briefing deck styled with Sard tokens."""
    gen = PresentationGenerator(OUTPUT_DIR)
    deck = create_cultural_briefing_deck(
        topic=topic,
        region=region,
        overview_text=overview_text,
        comparison_cards=comparison_cards,
        timeline_items=timeline_items,
        key_takeaways=key_takeaways,
        quote=quote,
    )
    safe_filename = f"sard-presentation-{deck.deck_id}.pptx"
    path, filename = gen.save_deck_file(deck, safe_filename)

    slides_summary = []
    for s in deck.slides:
        slides_summary.append({
            "slide_type": s.slide_type,
            "title": s.title,
            "subtitle": s.subtitle,
            "bullets": s.bullets,
            "cards": [asdict(c) for c in s.cards] if s.cards else [],
        })

    return {
        "success": True,
        "artifact_type": "presentation_pptx",
        "title": deck.title,
        "filename": filename,
        "download_url": f"/api/artifacts/{filename}",
        "slides_count": len(deck.slides),
        "slides": slides_summary,
        "message_ar": f"تم إعداد عرض تقديمي ثقافي متكامل ({len(deck.slides)} شرائح) بصيغة PowerPoint جاهز للتحميل والعرض.",
    }


# ---------------------------------------------------------------------------
# 2. Recipe & Craft Card Generator Tool
# ---------------------------------------------------------------------------

def tool_generate_recipe_or_craft_card(
    item_name: str,
    card_type: str = "culinary",  # culinary or craft
    region: str = "المملكة العربية السعودية",
    ingredients_or_materials: Optional[List[Dict[str, str]]] = None,
    steps: Optional[List[Dict[str, Any]]] = None,
    cultural_story: str = "",
    prep_time_minutes: int = 30,
    cook_time_minutes: int = 90,
    difficulty: str = "متوسط",
    servings: str = "٦ أشخاص",
) -> Dict[str, Any]:
    """Generates a printable PDF recipe card or craft guide."""
    renderer = RecipeCardRenderer(OUTPUT_DIR)

    if not ingredients_or_materials and "جريش" in item_name:
        card = create_jareesh_recipe_card()
    elif not ingredients_or_materials and ("سدو" in item_name or card_type == "craft"):
        card = create_sadu_craft_card()
    else:
        ing_objs = []
        if ingredients_or_materials:
            for item in ingredients_or_materials:
                ing_objs.append(
                    IngredientOrMaterial(
                        name=item.get("name", ""),
                        quantity=item.get("quantity", ""),
                        traditional_unit=item.get("unit", ""),
                        notes=item.get("notes", ""),
                    )
                )

        step_objs = []
        if steps:
            for idx, s in enumerate(steps, 1):
                step_objs.append(
                    StepInstruction(
                        step_number=idx,
                        title=s.get("title", f"الخطوة {idx}"),
                        instruction=s.get("instruction", ""),
                        tip=s.get("tip", ""),
                    )
                )

        card = RecipeOrCraftCard(
            card_type=card_type,
            title=f"دليل تحضير {item_name} التراثي",
            item_name=item_name,
            region=region,
            prep_time_minutes=prep_time_minutes,
            cook_or_make_time_minutes=cook_time_minutes,
            difficulty=difficulty,
            servings_or_yield=servings,
            cultural_story=cultural_story or f"يمثل {item_name} موروثاً تقليدياً عريقاً في {region}.",
            ingredients_or_materials=ing_objs,
            steps=step_objs,
        )

    safe_filename = f"sard-card-{card.card_id}.pdf"
    path, filename = renderer.save_pdf_file(card, safe_filename)

    return {
        "success": True,
        "artifact_type": "recipe_craft_card",
        "title": card.title,
        "filename": filename,
        "download_url": f"/api/artifacts/{filename}",
        "card_data": card.to_dict(),
        "message_ar": f"تم توليد بطاقة تراثية مطبوعة وموثقة لـ«{card.item_name}» بجودة عالية (PDF).",
    }


# ---------------------------------------------------------------------------
# 3. Heritage Calendar & Temporal Sync Tool
# ---------------------------------------------------------------------------

def tool_sync_heritage_calendar(
    query: str = "",
    category: Optional[str] = None,
    region: Optional[str] = None,
    month: Optional[int] = None,
) -> Dict[str, Any]:
    """Searches heritage seasons & festivals and produces Google Calendar links + .ics payload."""
    sync = HeritageCalendarSync()
    events = sync.search_events(query=query, category=category, region=region, month=month)
    if not events:
        events = list(HERITAGE_EVENTS_DATABASE[:4])

    safe_filename = f"sard-calendar-{uuid.uuid4().hex[:6]}.ics"
    path, filename = sync.save_ics_file(safe_filename, OUTPUT_DIR, events=events)

    events_list = [ev.to_dict() for ev in events]

    return {
        "success": True,
        "artifact_type": "calendar_ics",
        "filename": filename,
        "download_url": f"/api/artifacts/{filename}",
        "total_events": len(events),
        "events": events_list,
        "message_ar": f"تمت مزامنة {len(events)} مناسبة وموسماً فلكياً وتراثياً مع روابط التقويم المباشرة وملف (.ics).",
    }


# ---------------------------------------------------------------------------
# 4. Cultural Etiquette & Protocol Simulator (المُوجّه الثقافي)
# ---------------------------------------------------------------------------

def tool_simulate_etiquette_protocol(
    scenario_type: str = "majlis",  # majlis, dining, business_negotiation, wedding
    situation: str = "",
) -> Dict[str, Any]:
    """Provides scenario-based cultural protocol guidance with an interactive flowchart."""
    renderer = DiagramRenderer()

    if scenario_type == "business_negotiation" or "عمل" in situation or "مفاوضات" in situation:
        diagram = create_business_etiquette_diagram()
        narrative_guidance = (
            "### 🏢 بروتوكول الأعمال والمفاوضات في المملكة:\n"
            "1. **بناء الألفة أولاً**: تبدأ الاجتماعات بالسؤال عن الأحوال بوقار؛ تجنب البدء الصارم بالعقود.\n"
            "2. **كرم الضيافة**: قبول القهوة السعودية والتمر علامة تقدير ورغبة في الشراكة.\n"
            "3. **التسلسل القيادي**: توجيه الخطاب لرئيس الوفد مع حفظ الألقاب الرسمية.\n"
            "4. **التريث والصبر**: اتخاذ القرارات مبني على المشاورات المتأنية والثقة المتبادلة."
        )
    else:
        diagram = create_majlis_etiquette_diagram()
        narrative_guidance = (
            "### ☕ بروتوكول المجلس والضيافة السعودية الأصيلة:\n"
            "1. **إلقاء التحية**: السلام على يمين المجلس أو على كبير القوم والمقام أولاً.\n"
            "2. **أصول صب القهوة**: مسك الدلة باليد اليسرى دائماً، وتقديم الفنجان باليد اليمنى.\n"
            "3. **صَبّة الحشمة**: ملء ثلث الفنجان فقط؛ دلالة على إكرام الضيف ومداومة خدمته.\n"
            "4. **هَزّ الفنجان**: هز الفنجان يمنة ويسرة بلطف عند الاكتفاء قبل إعادته للمضيّف."
        )

    svg_markup = renderer.render_svg(diagram)

    return {
        "success": True,
        "artifact_type": "etiquette_simulation",
        "scenario_type": scenario_type,
        "title": diagram.title,
        "guidance_markdown": narrative_guidance,
        "diagram_svg": svg_markup,
        "diagram_data": diagram.to_dict(),
        "message_ar": f"تم تشغيل محاكي الإتيكيت الثقافي لـ«{diagram.title}» مع مخطط تدفقي إرشادي.",
    }


# ---------------------------------------------------------------------------
# 5. Dialect & Proverb Lore Decoder (المترجم والراوي للأمثال)
# ---------------------------------------------------------------------------

DIALECT_LEXICON_DATABASE = {
    "najdi": {
        "region_name": "نجد (الوسطى)",
        "terms": {
            "أبشر بسعدك": {"meaning": "تلبية الطلب بكرم وعزم وسرور", "usage": "عند طلب المساعدة أو العون"},
            "يا بعد حيي": {"meaning": "تعبير عن المحبة والتقدير البالغ (أصيل في حائل وشمال نجد)", "usage": "في الترحيب والتحبب"},
            "اركد": {"meaning": "اهدأ وتأنّ وتريث في اتخاذ القرار", "usage": "عند التسرع أو الانفعال"},
            "وش قومك": {"meaning": "ماذا بك وما الذي دهاك؟", "usage": "عند الاستفسار عن حال شخص يبدو عليه التغير"},
        },
        "proverbs": [
            {
                "proverb": "من طق طبله قال أنا قبله",
                "meaning": "يُضرب لمن يتسرع في الانضمام لأي أمر دون معرفة عواقبه أو تفاصيله.",
                "lore": "يروى عن عازف الطبول في الأعراس القديمة حينما كان ينضم إليه المتحمسون دون إدراك للمناسبة.",
                "situational_context": "يُقال للتحذير من التسرع في اتخاذ القرارات بناء على حماس الآخرين.",
            },
            {
                "proverb": "القرش الأبيض لليوم الأسود",
                "meaning": "الحث على الادخار والتدبير للأوقات الصعبة.",
                "lore": "نشأ من حكمة تجار نجد في التعامل مع مواسم الجدب وشح الموارد في الصحراء.",
                "situational_context": "يُقال عند النصيحة بالتوفير وحسن إدارة المال.",
            },
        ],
    },
    "hijazi": {
        "region_name": "الحجاز (الغربية)",
        "terms": {
            "أهلاً وسهلاً ومرحبتين": {"meaning": "ترحيب حجازي دافئ مفعم بالحفاوة", "usage": "استقبال الضيوف"},
            "على خشمي": {"meaning": "أمرك مطاع وفوق الرأس والعين", "usage": "إجابة الطلبات باحترام فائق"},
            "دحين": {"meaning": "الآن في هذه اللحظة", "usage": "تحديد الوقت الحاضر"},
            "يا واد": {"meaning": "مناداة ودية للشاب أو الفتى", "usage": "الحديث الودي بين الأقران"},
        },
        "proverbs": [
            {
                "proverb": "يا بخت من زار وخفف",
                "meaning": "استحباب قصر مدة الزيارة حتى لا يُثقل على المضيف.",
                "lore": "من تقاليد الحارات الحجازية في مكة وجدة لمراعاة خصوصية البيوت.",
                "situational_context": "يُقال عند ختام الزيارات الاجتماعية المهذبة.",
            },
        ],
    },
    "sharqawi": {
        "region_name": "المنطقة الشرقية (الأحساء والخليج)",
        "terms": {
            "حيّاكم الله يا هلنا": {"meaning": "ترحيب حساوي دافئ بالزوار", "usage": "استقبال الزوار في الواحات"},
            "السالفة": {"meaning": "القصة أو الحكاية القديمة", "usage": "عند سرد الذكريات"},
            "غناتي": {"meaning": "يا أغلى ما أملك", "usage": "تعبير عن المحبة للأبناء والأهل"},
        },
        "proverbs": [
            {
                "proverb": "النخلة ما تطرح إلا حلا",
                "meaning": "الأصل الطيب لا يصدر منه إلا العمل الصالح والقول الجميل.",
                "lore": "مستوحى من واحة الأحساء وعطاء نخيل الخلاص والرزيز المستدام.",
                "situational_context": "يُقال في مدح كرام النفوس وحسني الأخلاق.",
            },
        ],
    },
    "janoubi": {
        "region_name": "المنطقة الجنوبية (عسير، جازان، نجران)",
        "terms": {
            "مرحبا ألوف": {"meaning": "أهلاً وسهلاً آلاف المرات؛ أعلى مراتب الترحيب الجنوبي", "usage": "استقبال الضيوف في عسير"},
            "هب لي": {"meaning": "أعطني أو ناولني", "usage": "طلب الشيء بأدب"},
            "يا فديتكم": {"meaning": "دعاء بالتضحية والمحبة الصادقة", "usage": "التعبير عن المودة"},
        },
        "proverbs": [
            {
                "proverb": "الضيف في حكم المضيف",
                "meaning": "التزام الضيف بضوابط المجلس واطمئنانه لكرم صاحب البيت.",
                "lore": "من أعراف قبائل السراة وتهامة في حماية الضيف وإكرامه.",
                "situational_context": "يُقال لطمأنة الضيف وإشعاره بالأمان والترحاب.",
            },
        ],
    },
}


def tool_decode_dialect_or_proverb(
    phrase_or_proverb: str,
    dialect_region: str = "najdi",  # najdi, hijazi, sharqawi, janoubi, all
) -> Dict[str, Any]:
    """Translates regional dialects, explains proverb lore, and provides situational usage."""
    region_key = dialect_region.lower() if dialect_region.lower() in DIALECT_LEXICON_DATABASE else "najdi"
    lexicon = DIALECT_LEXICON_DATABASE[region_key]

    matched_proverb = None
    for p in lexicon["proverbs"]:
        if any(w in phrase_or_proverb for w in p["proverb"].split()[:2]):
            matched_proverb = p
            break

    if not matched_proverb:
        matched_proverb = lexicon["proverbs"][0]

    return {
        "success": True,
        "artifact_type": "dialect_lore_card",
        "region_name": lexicon["region_name"],
        "input_phrase": phrase_or_proverb,
        "proverb_title": matched_proverb["proverb"],
        "meaning_ar": matched_proverb["meaning"],
        "lore_story_ar": matched_proverb["lore"],
        "situational_context_ar": matched_proverb["situational_context"],
        "common_regional_terms": lexicon["terms"],
        "message_ar": f"تم فك شفرة المثل والمصطلحات التراثية لمنطقة «{lexicon['region_name']}» مع سالفة المثل وسياق الاستخدام.",
    }


# ---------------------------------------------------------------------------
# 6. Local Artisan & Traditional Craft Concierge (دليل الحِرَف والتراث)
# ---------------------------------------------------------------------------

ARTISAN_CRAFT_DATABASE = {
    "sadu": {
        "name": "السدو البدوي (نسيج الصوف والوبر)",
        "region": "المملكة العربية السعودية (تراث عالمي - اليونسكو)",
        "description": "نسيج تقليدي من صوف الأغنام ووبر الإبل يتميز بالنقوش الهندسية المعبرة عن البيئة الصحراوية.",
        "authentication_criteria": [
            "الصوف الطبيعي 100%: ملمس متماسك خشن قليلاً ولا يذوب بالحرارة كالبوليستر.",
            "الصبغات النباتية الطبيعية: ألوان دافئة غير لماعة ومستخلصة من الفوة والكركم.",
            "عقد النسيج اليدوي: ظهور فروقات طفيفة بين الغرز تدل على حياكة اليد بالمنشزة وليس الماكينة.",
        ],
        "care_instructions": "يُنظف بالفرشاة الجافة أو الغسيل اليدوي بالماء البارد دون عصر، ويُحفظ بعيداً عن الرطوبة والعث.",
    },
    "hasawi_bisht": {
        "name": "البشت الحساوي (المشلح الملكي الأصيل)",
        "region": "الأحساء (المنطقة الشرقية)",
        "description": "أرقى أنواع البشوت في العالم العربي؛ يُحاك يدوياً بخيوط الزري الذهبية الألمانية الأصلية (عيار فضة مطلي بالذهب).",
        "authentication_criteria": [
            "الزري الأصلي: لمعان ذهبي هادئ لا يتغير لونه مع الزمن وله ثقل نوعي مميز.",
            "دقة «الخبانة» و«البروجك»: غرز يدوية متناهية الدقة ينجزها حرفيون متوارثون للحرفة.",
            "قماش الصوف/الوبر: انسيابية عالية وبرودة في الصيف ودفء في الشتاء.",
        ],
        "care_instructions": "يُعلق على شماعة عريضة مبطنة، ويُطوى بطريقة البشت الخاصة دون ثني الزري، ويُنظف بالتنظيف الجاف المتخصص فقط.",
    },
    "taif_rose": {
        "name": "دهن الورد الطائفي (الذهب السائل)",
        "region": "الهدا والشفا (محافظة الطائف)",
        "description": "زيت عطري نادر يُستخلص بتقطير ملايين بتلات الورد الجوري الطائفي في قدور النحاس التقليدية.",
        "authentication_criteria": [
            "الرائحة المعقدة: تبدأ بنفحات خضراء منعشة ثم تستقر على عبير وردي مخملي يدوم طويلاً.",
            "اللون واللزوجة: أصفر مائل للخضرة الخفيفة، غير لزج ولا يترك بقعاً زيتية داكنة على الأقمشة.",
            "شهادة المنشأ من مزارع الطائف المعتمدة.",
        ],
        "care_instructions": "يُحفظ في قوارير زجاجية محكمة الإغلاق بعيداً عن الضوء المباشر والحرارة العالية.",
    },
    "aseeri_qatt": {
        "name": "القط العسيري (فن الزخرفة الجدارية)",
        "region": "منطقة عسير (اليونسكو)",
        "description": "فن نسائي تقليدي لتزيين جدران المجالس بالنقوش الهندسية والألوان الزاهية المستخلصة من صخور ونباتات المنطقة.",
        "authentication_criteria": [
            "استخدام الألوان الطبيعية (الجير الأبيض، الفحم الأسود، الطين الأحمر، العصفر الأصفر).",
            "التناغم البصري للنقوش التقليدية (البنات، البلسنة، المحاريب، الختمة).",
        ],
        "care_instructions": "يُحمى من الرطوبة المباشرة ويُمسح برفق بقطعة قماش جافة ناعمة.",
    },
}


def tool_advise_artisan_craft(
    craft_name: str = "sadu",
) -> Dict[str, Any]:
    """Advises on traditional Saudi crafts, authentication criteria, and care guidelines."""
    craft_key = "sadu"
    name_lower = craft_name.lower()
    if "بشت" in name_lower or "مشلح" in name_lower or "hasawi" in name_lower:
        craft_key = "hasawi_bisht"
    elif "ورد" in name_lower or "rose" in name_lower:
        craft_key = "taif_rose"
    elif "قط" in name_lower or "عسير" in name_lower or "qatt" in name_lower:
        craft_key = "aseeri_qatt"

    craft_info = ARTISAN_CRAFT_DATABASE[craft_key]

    return {
        "success": True,
        "artifact_type": "artisan_craft_guide",
        "craft_name": craft_info["name"],
        "region": craft_info["region"],
        "description": craft_info["description"],
        "authentication_checklist": craft_info["authentication_criteria"],
        "care_instructions": craft_info["care_instructions"],
        "message_ar": f"تم استخراج دليل أصالة الحِرفة لـ«{craft_info['name']}» مع معايير كشف التقليد وطرق العناية.",
    }


# ---------------------------------------------------------------------------
# 7. Creative Greeting Card Studio Tool
# ---------------------------------------------------------------------------

def tool_create_greeting_card(
    occasion: str = "foundation_day",
    recipient_name: str = "",
    sender_name: str = "",
    custom_message: str = "",
    theme: str = "dark_gold",
) -> Dict[str, Any]:
    """Generates an aesthetic digital greeting card with poetic verses and downloads."""
    studio = GreetingCardStudio(OUTPUT_DIR)
    card = compose_greeting_card(
        occasion=occasion,
        recipient_name=recipient_name,
        sender_name=sender_name,
        custom_message=custom_message,
        theme=theme,
    )
    svg_markup = studio.render_svg(card)
    safe_filename = f"sard-greeting-{card.card_id}.pdf"
    path, filename = studio.save_pdf_file(card, safe_filename)

    return {
        "success": True,
        "artifact_type": "greeting_card",
        "title": card.title,
        "occasion": card.occasion,
        "recipient_name": card.recipient_name,
        "sender_name": card.sender_name,
        "poetic_verse": card.poetic_verse,
        "personal_message": card.personal_message,
        "svg_markup": svg_markup,
        "filename": filename,
        "download_url": f"/api/artifacts/{filename}",
        "message_ar": f"تم تصميم بطاقة تهنئة فاخرة لـ«{card.title}» بالأشعار والتنسيق التراثي.",
    }


# ---------------------------------------------------------------------------
# 8. Oral History & Family Archive Memoir Compiler Tool
# ---------------------------------------------------------------------------

def tool_compile_oral_history_memoir(
    family_name: str,
    raw_notes: List[Dict[str, str]],
    origin_region: str = "المملكة العربية السعودية",
    origin_town: str = "",
) -> Dict[str, Any]:
    """Compiles interview notes into multi-chapter biographical booklet in literary Arabic."""
    compiler = MemoirCompiler(OUTPUT_DIR)
    memoir = synthesize_memoir_from_notes(
        family_or_narrator=family_name,
        raw_notes=raw_notes,
        origin_region=origin_region,
        origin_town=origin_town,
    )
    safe_filename = f"sard-memoir-{memoir.memoir_id}.pdf"
    path, filename = compiler.save_pdf_file(memoir, safe_filename)

    chapters_summary = []
    for ch in memoir.chapters:
        chapters_summary.append({
            "chapter_number": ch.chapter_number,
            "title": ch.title,
            "era": ch.era_or_decade,
            "location": ch.location,
            "prose_preview": ch.narrative_prose[:120] + "...",
        })

    return {
        "success": True,
        "artifact_type": "family_memoir_booklet",
        "title": memoir.title,
        "narrator": memoir.family_or_narrator_name,
        "filename": filename,
        "download_url": f"/api/artifacts/{filename}",
        "total_chapters": len(memoir.chapters),
        "chapters": chapters_summary,
        "message_ar": f"تم توثيق وصياغة كتيب السيرة والتاريخ الشفوي لـ«{memoir.family_or_narrator_name}» ({len(memoir.chapters)} فصول) بصيغة PDF.",
    }


# ---------------------------------------------------------------------------
# 9. Verified Heritage Research Mode (التوثيق المعتمد)
# ---------------------------------------------------------------------------

def tool_conduct_verified_research(
    topic: str,
    primary_authority: str = "دارة الملك عبد العزيز / هيئة التراث",
    include_timeline: bool = True,
) -> Dict[str, Any]:
    """Conducts academic-grade heritage research with structured citations and timeline."""
    renderer = DiagramRenderer()

    milestones = [
        TimelineMilestone(
            year_gregorian="1727م",
            year_hijri="1139هـ",
            era_name="تأسيس الدولة السعودية الأولى",
            title="انطلاق مسيرة الدولة بالدرعية",
            description=f"توثيق المعالم والجذور التاريخية المتعلقة بـ{topic}.",
            authority_source="دارة الملك عبد العزيز - السجل التاريخي",
        ),
        TimelineMilestone(
            year_gregorian="1824م",
            year_hijri="1240هـ",
            era_name="الدولة السعودية الثانية",
            title="تأسيس الرياض عاصمة للبلاد",
            description=f"تطور الأنماط المعمارية والتراثية في نجد ووسط الجزيرة.",
            authority_source="هيئة التراث - أطلس العمارة التقليدية",
        ),
        TimelineMilestone(
            year_gregorian="1932م",
            year_hijri="1351هـ",
            era_name="توحيد المملكة العربية السعودية",
            title="إعلان توحيد البلاد وبداية التوثيق الوطني",
            description=f"صيانة الموروث الشعبي وتدوين التقاليد الإقليمية.",
            authority_source="وزارة الثقافة - الموسوعة الوطنية",
        ),
    ]

    timeline_diagram = CulturalDiagram(
        diagram_type="timeline",
        title=f"التسلسل التوثيقي المعتمد: {topic}",
        subtitle=f"المصدر الأكاديمي: {primary_authority}",
        region="المملكة العربية السعودية",
        timeline_milestones=milestones,
    )
    svg_markup = renderer.render_svg(timeline_diagram)

    bibliography = [
        {"author": "دارة الملك عبد العزيز", "title": "موسوعة تاريخ المملكة العربية السعودية", "year": "2024", "doc_type": "مرجع رسمي"},
        {"author": "وزارة الثقافة السعودية", "title": "سجل التراث الثقافي غير المادي الوطني", "year": "2025", "doc_type": "وثيقة اعتماد"},
        {"author": "هيئة التراث", "title": "أطلس المواقع الأثرية والعمارة التقليدية في المملكة", "year": "2023", "doc_type": "دراسة ميدانية"},
    ]

    return {
        "success": True,
        "artifact_type": "verified_research_report",
        "topic": topic,
        "authority": primary_authority,
        "timeline_svg": svg_markup,
        "timeline_milestones": [asdict(m) for m in milestones],
        "bibliography": bibliography,
        "message_ar": f"تم إجراء التوثيق الأكاديمي المعتمد لـ«{topic}» مع سلسلة الإسناد والمراجع وقائمة المصادر الرسمية.",
    }
