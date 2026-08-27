"""Printable Traditional Culinary & Craft Card Generator for Sard.

Renders rich visual recipe cards and artisanal craft step-by-step guides
formatted for high-quality printing (PDF) and interactive frontend rendering.
Adheres strictly to Sard's warm cultural palette and Arabic typography.
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

from sard.outputs.arabic import shape_rtl, visual_runs
from sard.outputs.fonts import ensure_fonts_registered, require_arabic_font, require_latin_font

logger = logging.getLogger("sard.outputs.recipe_card")

COLOR_PAPER = colors.HexColor("#F3EEE4")
COLOR_PAPER_2 = colors.HexColor("#E8E0D2")
COLOR_INK = colors.HexColor("#141210")
COLOR_CLAY = colors.HexColor("#BE4A24")
COLOR_DATE = colors.HexColor("#6E1F1F")
COLOR_OLIVE = colors.HexColor("#4A513C")
COLOR_GOLD = colors.HexColor("#C4A46A")
COLOR_CARD = colors.HexColor("#FAF7F1")
COLOR_BORDER = colors.HexColor("#D4CBBD")


@dataclass
class IngredientOrMaterial:
    name: str
    quantity: str = ""
    traditional_unit: str = ""  # صاع, مد, فنجان, أوقية, غرام
    notes: str = ""


@dataclass
class StepInstruction:
    step_number: int
    title: str
    instruction: str
    duration_minutes: Optional[int] = None
    tip: str = ""


@dataclass
class RecipeOrCraftCard:
    """Represents a traditional dish recipe or artisan craft guide."""
    card_type: str  # culinary or craft
    title: str
    item_name: str
    region: str = "المملكة العربية السعودية"
    category: str = "أطباق رئيسية / حرف يدوية"
    prep_time_minutes: int = 30
    cook_or_make_time_minutes: int = 90
    difficulty: str = "متوسط"  # سهل, متوسط, متقدم / يحتاج مهارة حرفية
    servings_or_yield: str = "٦ أشخاص"
    cultural_story: str = ""  # سالفة الطبخة أو الحرفة وأصل التسمية
    ingredients_or_materials: List[IngredientOrMaterial] = field(default_factory=list)
    steps: List[StepInstruction] = field(default_factory=list)
    pro_tips: List[str] = field(default_factory=list)
    authenticity_checklist: List[str] = field(default_factory=list)
    card_id: str = field(default_factory=lambda: f"card-{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RecipeCardRenderer:
    """Renders printable PDF cards and data schemas."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path("output")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def render_pdf(self, card: RecipeOrCraftCard) -> bytes:
        """Draws an elegant A4 printable cultural card."""
        arabic_font, latin_font = ensure_fonts_registered()

        buffer = io.BytesIO()
        c = Canvas(buffer, pagesize=A4)
        width, height = A4  # 595.27 x 841.89 pt

        # 1. Background
        c.setFillColor(COLOR_PAPER)
        c.rect(0, 0, width, height, stroke=0, fill=1)

        # 2. Border & Header Card
        margin = 28
        c.setStrokeColor(COLOR_BORDER)
        c.setLineWidth(1.5)
        c.roundRect(margin, margin, width - (2 * margin), height - (2 * margin), 16, stroke=1, fill=0)

        # Header Block
        c.setFillColor(COLOR_INK)
        c.roundRect(margin + 6, height - margin - 85, width - (2 * margin) - 12, 79, 12, stroke=0, fill=1)

        # Header Gold Line
        c.setFillColor(COLOR_GOLD)
        c.rect(margin + 6, height - margin - 85, width - (2 * margin) - 12, 4, stroke=0, fill=1)

        # Title & Region
        c.setFillColor(COLOR_PAPER)
        c.setFont(arabic_font, 22)
        shaped_title = shape_rtl(card.title)
        c.drawRightString(width - margin - 24, height - margin - 42, shaped_title)

        c.setFillColor(COLOR_GOLD)
        c.setFont(arabic_font, 12)
        sub_text = f"✦ {card.region} • {card.category} | {card.servings_or_yield}"
        c.drawRightString(width - margin - 24, height - margin - 65, shape_rtl(sub_text))

        # 3. Metadata Metrics Bar
        y_metrics = height - margin - 125
        c.setFillColor(COLOR_CARD)
        c.setStrokeColor(COLOR_BORDER)
        c.roundRect(margin + 6, y_metrics, width - (2 * margin) - 12, 32, 8, stroke=1, fill=1)

        c.setFillColor(COLOR_INK)
        c.setFont("NotoNaskhArabic-Regular", 10.5)
        metrics_text = f"وقت الإعداد: {card.prep_time_minutes} دقيقة | وقت التحضير: {card.cook_or_make_time_minutes} دقيقة | الصعوبة: {card.difficulty}"
        c.drawRightString(width - margin - 20, y_metrics + 10, shape_rtl(metrics_text))

        # 4. Cultural Backstory Box (سالفة الطبخة / الحرفة)
        y_story = y_metrics - 68
        if card.cultural_story:
            c.setFillColor(COLOR_CARD)
            c.setStrokeColor(COLOR_GOLD)
            c.setLineWidth(1)
            c.roundRect(margin + 6, y_story, width - (2 * margin) - 12, 58, 8, stroke=1, fill=1)

            c.setFillColor(COLOR_DATE)
            c.setFont("NotoNaskhArabic-Regular", 11)
            c.drawRightString(width - margin - 18, y_story + 40, shape_rtl("📜 الأصل والتراث:"))

            c.setFillColor(COLOR_INK)
            c.setFont("NotoNaskhArabic-Regular", 9.5)
            # Clip story text to fit
            story_clean = card.cultural_story[:170]
            c.drawRightString(width - margin - 18, y_story + 18, shape_rtl(story_clean))
        else:
            y_story = y_metrics

        # 5. Two-column Layout: Right = Ingredients, Left = Steps
        col_gap = 14
        col_width = (width - (2 * margin) - 12 - col_gap) / 2
        right_x = margin + 6 + col_width + col_gap
        left_x = margin + 6
        content_y_start = y_story - 20

        # --- Right Column: Ingredients / Materials ---
        c.setFillColor(COLOR_CLAY)
        c.setFont("NotoNaskhArabic-Regular", 13)
        ing_header = "المقادير والمكونات الأصيلة" if card.card_type == "culinary" else "الأدوات والمواد الخام"
        c.drawRightString(right_x + col_width - 8, content_y_start, shape_rtl(f"✦ {ing_header}"))

        ing_y = content_y_start - 20
        c.setFont("NotoNaskhArabic-Regular", 9.5)
        for ing in card.ingredients_or_materials[:10]:
            c.setFillColor(COLOR_CARD)
            c.setStrokeColor(COLOR_PAPER_2)
            c.roundRect(right_x, ing_y - 4, col_width, 22, 6, stroke=1, fill=1)

            c.setFillColor(COLOR_INK)
            qty_str = f" ({ing.quantity} {ing.traditional_unit})".strip() if ing.quantity else ""
            ing_line = f"• {ing.name}{qty_str}"
            c.drawRightString(right_x + col_width - 10, ing_y + 2, shape_rtl(ing_line))
            ing_y -= 26

        # --- Left Column: Step-by-Step Instructions ---
        c.setFillColor(COLOR_OLIVE)
        c.setFont("NotoNaskhArabic-Regular", 13)
        c.drawRightString(left_x + col_width - 8, content_y_start, shape_rtl("✦ خطوات التحضير والعمل"))

        step_y = content_y_start - 20
        for step in card.steps[:6]:
            c.setFillColor(COLOR_CARD)
            c.setStrokeColor(COLOR_BORDER)
            c.roundRect(left_x, step_y - 18, col_width, 42, 6, stroke=1, fill=1)

            c.setFillColor(COLOR_CLAY)
            c.setFont("NotoNaskhArabic-Regular", 10.5)
            step_title_line = f"{step.step_number}. {step.title}"
            c.drawRightString(left_x + col_width - 10, step_y + 8, shape_rtl(step_title_line))

            c.setFillColor(COLOR_INK)
            c.setFont("NotoNaskhArabic-Regular", 8.5)
            c.drawRightString(left_x + col_width - 10, step_y - 8, shape_rtl(step.instruction[:65]))
            step_y -= 48

        # 6. Pro Tips & Footer
        footer_y = margin + 18
        c.setFillColor(COLOR_INK)
        c.setFont("NotoNaskhArabic-Regular", 8.5)
        c.drawRightString(width - margin - 16, footer_y, shape_rtl("سرد — دليل التراث والطهي والحِرف السعودية | توثيق معتمد"))

        c.showPage()
        c.save()
        return buffer.getvalue()

    def save_pdf_file(self, card: RecipeOrCraftCard, filename: Optional[str] = None) -> Tuple[Path, str]:
        safe_name = filename or f"{card.card_id}.pdf"
        if not safe_name.endswith(".pdf"):
            safe_name += ".pdf"
        target_path = self.output_dir / safe_name
        data = self.render_pdf(card)
        target_path.write_bytes(data)
        logger.info("Saved Recipe/Craft Card PDF: %s (%d bytes)", target_path, len(data))
        return target_path, safe_name


# ---------------------------------------------------------------------------
# Presets / Samples for Traditional Saudi Dishes & Crafts
# ---------------------------------------------------------------------------

def create_jareesh_recipe_card() -> RecipeOrCraftCard:
    """Creates traditional Najdi Jareesh recipe card."""
    return RecipeOrCraftCard(
        card_type="culinary",
        title="الجريش النجدي الأصيل (سيد المائدة)",
        item_name="جريش",
        region="منطقة الرياض / نجد",
        category="أطباق رئيسية تقليدية",
        prep_time_minutes=20,
        cook_or_make_time_minutes=120,
        difficulty="متوسط",
        servings_or_yield="٨ أشخاص",
        cultural_story="الجريش هو الطبق الوطني الرسمي للمملكة العربية السعودية بحسب تصنيف هيئة فنون الطهي 2023؛ يرمز للكرم والضيافة في احتفالات الأعياد والأعراس.",
        ingredients_or_materials=[
            IngredientOrMaterial(name="حب جريش قمح لقيمي مجروش", quantity="٢", traditional_unit="كوب"),
            IngredientOrMaterial(name="أرز مصري", quantity="نصف", traditional_unit="كوب"),
            IngredientOrMaterial(name="لبن رائب طازج كامل الدسم", quantity="٤", traditional_unit="أكواب"),
            IngredientOrMaterial(name="مرق دجاج أو لحم بلدي", quantity="٤", traditional_unit="أكواب"),
            IngredientOrMaterial(name="بصل مفروم ناعم (للحمسة)", quantity="٢", traditional_unit="حبة كبيرة"),
            IngredientOrMaterial(name="سمن بري غنمي أصيل", quantity="٣", traditional_unit="ملاعق كبيرة"),
            IngredientOrMaterial(name="ليمون أسود مطحون (لومي)", quantity="١", traditional_unit="ملعقة صغيرة"),
            IngredientOrMaterial(name="كمون وفلفل أسود وملح بحري", quantity="حسب الرغبة"),
        ],
        steps=[
            StepInstruction(
                step_number=1,
                title="غسل ونقع الحبوب",
                instruction="يُغسل الجريش والأرز جيداً وينقع في ماء دافئ لمدة ساعة على الأقل.",
            ),
            StepInstruction(
                step_number=2,
                title="طبخ الجريش بالمرق",
                instruction="يُضاف الجريش للمرق في قدر ثقيل القاعدة ويُترك يغلي على نار هادئة مع التحريك المستمر.",
            ),
            StepInstruction(
                step_number=3,
                title="إضافة اللبن والمضرب",
                instruction="يُسكب اللبن الرائب تدريجياً ويُضرب الجريش بـ«المضرابة» الخشبية حتى يصبح ناعماً وكريمياً.",
            ),
            StepInstruction(
                step_number=4,
                title="إعداد الكشنة والتزيين",
                instruction="يُحمّس البصل بالسمن البري واللومي الأسود حتى يكتسب لوناً ذهبياً عطراً، ويوضع على وجه الطبق.",
            ),
        ],
        pro_tips=[
            "استخدام قدر الفخار أو قدر الضغط الثقيل يمنح الجريش قواماً متجانساً وعمقاً في النكهة.",
            "الضرب بالمضرابة الخشبية التقليدية هو سر نعومة القوام النجدي الأصيل.",
        ],
        authenticity_checklist=[
            "استخدام السمن البري البلدي بدلاً من الزيوت النباتية.",
            "استخدام اللومي الأسود الحساوي في الكشنة العلوية.",
        ],
    )


def create_sadu_craft_card() -> RecipeOrCraftCard:
    """Creates traditional Sadu weaving artisan craft card."""
    return RecipeOrCraftCard(
        card_type="craft",
        title="حرفة نسج السدو البدوي الأصيل",
        item_name="نسيج السدو",
        region="المملكة العربية السعودية (اليونسكو)",
        category="حِرف ونسيج تقليدي",
        prep_time_minutes=60,
        cook_or_make_time_minutes=240,
        difficulty="يحتاج مهارة حرفية",
        servings_or_yield="قطعة نسيج تراثية",
        cultural_story="أُدرج السدو في القائمة التمثيلية للتراث الثقافي غير المادي لليونسكو عام 2020؛ يجسد براعة المرأة البدوية في تحويل صوف الأغنام ووبر الإبل إلى سجاد وبيوت شعر ذات نقوش هندسية ساحرة.",
        ingredients_or_materials=[
            IngredientOrMaterial(name="صوف أغنام ووبر إبل طبيعي مغسول وممشوق", quantity="١", traditional_unit="كغم"),
            IngredientOrMaterial(name="المغزل الخشبي اليدوي", quantity="١", traditional_unit="قطعة"),
            IngredientOrMaterial(name="النول الأرضي التقليدي (الميشع والمنشزة)", quantity="١", traditional_unit="طقم"),
            IngredientOrMaterial(name="صبغات نباتية طبيعية (كركم، فوه، لومي، نيلة)", quantity="متعدد"),
            IngredientOrMaterial(name="المدرة أو القرن لرص خيوط اللحمة", quantity="١", traditional_unit="أداة"),
        ],
        steps=[
            StepInstruction(
                step_number=1,
                title="جز الصوف وتنظيفه وغزله",
                instruction="يُنظف الصوف من الشوائب ويُغزل بالمغزل اليدوي لتكوين خيوط قوية ومستدامة.",
            ),
            StepInstruction(
                step_number=2,
                title="الصباغة بالمواد الطبيعية",
                instruction="تُصبغ الخيوط بالنباتات الصحراوية (الأحمر من الفوة، والأصفر من العصفر والكركم).",
            ),
            StepInstruction(
                step_number=3,
                title="تسدية النول الأرضي",
                instruction="تُمَد خيوط السدى على أوتاد النول الأرضي المشدود بدقة متناهية.",
            ),
            StepInstruction(
                step_number=4,
                title="النسج وتشكيل النقوش الهندسية",
                instruction="تُنسج النقوش التراثية كـ«الشجرة»، «الحبوب»، و«العويرجان» بالمنشزة والمدرة.",
            ),
        ],
        pro_tips=[
            "الحفاظ على شد الخيوط بانتظام يضمن استواء سطح السدو وعدم تموجه.",
            "الصبغات الطبيعية تحافظ على رونقها وتزداد جمالاً مع تقادم الزمن.",
        ],
        authenticity_checklist=[
            "ملمس خشن متماسك من الصوف الطبيعي 100%.",
            "تناسق النقوش الهندسية البدوية الكلاسيكية دون استخدام خيوط البوليستر الاصطناعية.",
        ],
    )
