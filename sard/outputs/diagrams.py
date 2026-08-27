"""Diagram-Design inspired visual diagram and flowchart generator for Sard.

Generates structured JSON schemas and beautiful, scalable SVG diagrams
for cultural etiquette flowcharts, heritage timelines, craft process flows,
and comparison matrices with Sard's cultural color palette and typography.
"""

from __future__ import annotations

import html
import io
import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("sard.outputs.diagrams")

# Cultural design tokens
COLOR_PAPER = "#F3EEE4"
COLOR_PAPER_2 = "#E8E0D2"
COLOR_INK = "#141210"
COLOR_CLAY = "#BE4A24"
COLOR_DATE = "#6E1F1F"
COLOR_OLIVE = "#4A513C"
COLOR_GOLD = "#C4A46A"
COLOR_CARD = "#FAF7F1"
COLOR_BORDER = "#D4CBBD"
COLOR_MUTED = "#8A8178"


@dataclass
class FlowNode:
    """Represents a node/step in an etiquette flowchart or process flow."""
    id: str
    title: str
    description: str = ""
    node_type: str = "step"  # start, step, decision, tip, warning, finish
    do_action: str = ""      # What to do (المستحسن)
    dont_action: str = ""    # What to avoid (المحظور / ما يُكره)
    children_ids: List[str] = field(default_factory=list)
    badge: str = ""
    accent: str = "clay"     # clay, gold, olive, date


@dataclass
class TimelineMilestone:
    """Represents a point on a cultural heritage timeline."""
    year_gregorian: str
    year_hijri: str = ""
    era_name: str = ""
    title: str = ""
    description: str = ""
    location: str = ""
    significance_points: List[str] = field(default_factory=list)
    authority_source: str = ""


@dataclass
class ComparisonAspect:
    """Represents a dimension in a regional comparison matrix."""
    dimension_name: str  # e.g. مواد البناء, النقوش والزخارف, الضيافة
    values_by_region: Dict[str, str] = field(default_factory=dict)  # e.g. {"نجد": "...", "الحجاز": "..."}


@dataclass
class CulturalDiagram:
    """Unified diagram container supporting multiple visual types."""
    diagram_type: str  # etiquette_flow, timeline, craft_process, comparison_matrix, genealogy
    title: str
    subtitle: str = ""
    region: str = "المملكة العربية السعودية"
    nodes: List[FlowNode] = field(default_factory=list)
    timeline_milestones: List[TimelineMilestone] = field(default_factory=list)
    comparison_regions: List[str] = field(default_factory=list)
    comparison_aspects: List[ComparisonAspect] = field(default_factory=list)
    footer_note: str = "سرد — المستشار الثقافي السعودي"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DiagramRenderer:
    """Renders structured CulturalDiagram instances into responsive SVGs and structured JSON."""

    def render_svg(self, diagram: CulturalDiagram, width: int = 900) -> str:
        """Renders diagram as clean, styled SVG with RTL text layout."""
        if diagram.diagram_type == "etiquette_flow" or diagram.diagram_type == "craft_process":
            return self._render_flow_svg(diagram, width)
        elif diagram.diagram_type == "timeline":
            return self._render_timeline_svg(diagram, width)
        elif diagram.diagram_type == "comparison_matrix":
            return self._render_matrix_svg(diagram, width)
        else:
            return self._render_flow_svg(diagram, width)

    def _render_flow_svg(self, diagram: CulturalDiagram, width: int = 900) -> str:
        """Renders an interactive-looking vertical process / etiquette flowchart."""
        nodes = diagram.nodes or []
        card_height = 140
        gap = 40
        header_height = 100
        footer_height = 50
        total_height = header_height + (len(nodes) * (card_height + gap)) + footer_height

        svg_parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {total_height}" width="100%" height="100%" style="background:{COLOR_PAPER}; font-family:\'IBM Plex Sans Arabic\', sans-serif; direction:rtl; text-anchor:end;">',
            '<defs>',
            '  <filter id="shadow" x="-5%" y="-5%" width="110%" height="115%" filterUnits="userSpaceOnUse">',
            '    <feDropShadow dx="0" dy="3" stdDeviation="4" flood-color="#141210" flood-opacity="0.08"/>',
            '  </filter>',
            '</defs>',
        ]

        # Header
        svg_parts.append(
            f'<rect x="20" y="20" width="{width-40}" height="70" rx="12" fill="{COLOR_INK}"/>'
            f'<text x="{width-45}" y="52" fill="{COLOR_PAPER}" font-size="20" font-weight="700" font-family="Noto Naskh Arabic, serif">{html.escape(diagram.title)}</text>'
            f'<text x="{width-45}" y="74" fill="{COLOR_GOLD}" font-size="13">{html.escape(diagram.subtitle or diagram.region)}</text>'
        )

        y = header_height + 20
        card_w = width - 80
        card_x = 40

        for i, node in enumerate(nodes):
            # Connecting line to next node
            if i < len(nodes) - 1:
                svg_parts.append(
                    f'<line x1="{width//2}" y1="{y + card_height}" x2="{width//2}" y2="{y + card_height + gap}" stroke="{COLOR_CLAY}" stroke-width="3" stroke-dasharray="6,4"/>'
                )

            # Node card box
            accent_color = COLOR_CLAY if node.accent == "clay" else COLOR_GOLD if node.accent == "gold" else COLOR_OLIVE
            svg_parts.append(
                f'<rect x="{card_x}" y="{y}" width="{card_w}" height="{card_height}" rx="14" fill="{COLOR_CARD}" stroke="{COLOR_BORDER}" stroke-width="1.2" filter="url(#shadow)"/>'
                f'<rect x="{card_x + card_w - 8}" y="{y}" width="8" height="{card_height}" rx="4" fill="{accent_color}"/>'
            )

            # Step number badge
            badge_text = f"الخطوة {i+1}" if not node.badge else node.badge
            svg_parts.append(
                f'<rect x="{card_x + 20}" y="{y + 16}" width="90" height="24" rx="12" fill="{COLOR_PAPER_2}"/>'
                f'<text x="{card_x + 65}" y="{y + 32}" fill="{COLOR_INK}" font-size="12" font-weight="700" text-anchor="middle">{html.escape(badge_text)}</text>'
            )

            # Title
            svg_parts.append(
                f'<text x="{card_x + card_w - 28}" y="{y + 36}" fill="{COLOR_INK}" font-size="17" font-weight="700" font-family="Noto Naskh Arabic, serif">{html.escape(node.title)}</text>'
            )

            # Description
            if node.description:
                svg_parts.append(
                    f'<text x="{card_x + card_w - 28}" y="{y + 64}" fill="{COLOR_INK}" font-size="13.5">{html.escape(node.description[:90])}</text>'
                )

            # Do / Don't pills
            pill_y = y + 90
            if node.do_action:
                svg_parts.append(
                    f'<rect x="{card_x + card_w - 380}" y="{pill_y}" width="350" height="30" rx="8" fill="rgba(74, 81, 60, 0.12)" stroke="{COLOR_OLIVE}" stroke-width="0.8"/>'
                    f'<text x="{card_x + card_w - 20}" y="{pill_y + 20}" fill="{COLOR_OLIVE}" font-size="12.5" font-weight="600">✓ {html.escape(node.do_action[:50])}</text>'
                )
            if node.dont_action:
                svg_parts.append(
                    f'<rect x="{card_x + 20}" y="{pill_y}" width="320" height="30" rx="8" fill="rgba(190, 74, 36, 0.10)" stroke="{COLOR_CLAY}" stroke-width="0.8"/>'
                    f'<text x="{card_x + 330}" y="{pill_y + 20}" fill="{COLOR_CLAY}" font-size="12.5" font-weight="600">✕ {html.escape(node.dont_action[:45])}</text>'
                )

            y += card_height + gap

        # Footer
        svg_parts.append(
            f'<text x="{width//2}" y="{total_height - 18}" fill="{COLOR_MUTED}" font-size="11.5" text-anchor="middle">{html.escape(diagram.footer_note)}</text>'
            '</svg>'
        )
        return "\n".join(svg_parts)

    def _render_timeline_svg(self, diagram: CulturalDiagram, width: int = 900) -> str:
        """Renders a chronological timeline with milestone cards."""
        milestones = diagram.timeline_milestones or []
        card_height = 110
        gap = 36
        header_height = 90
        total_height = header_height + (len(milestones) * (card_height + gap)) + 60

        svg_parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {total_height}" width="100%" height="100%" style="background:{COLOR_PAPER}; font-family:\'IBM Plex Sans Arabic\', sans-serif; direction:rtl; text-anchor:end;">',
            '<defs>',
            '  <filter id="shadow" x="-5%" y="-5%" width="110%" height="115%" filterUnits="userSpaceOnUse">',
            '    <feDropShadow dx="0" dy="2" stdDeviation="3" flood-color="#141210" flood-opacity="0.08"/>',
            '  </filter>',
            '</defs>',
            f'<rect x="20" y="16" width="{width-40}" height="60" rx="10" fill="{COLOR_INK}"/>',
            f'<text x="{width-45}" y="44" fill="{COLOR_PAPER}" font-size="19" font-weight="700" font-family="Noto Naskh Arabic, serif">{html.escape(diagram.title)}</text>',
            f'<text x="{width-45}" y="63" fill="{COLOR_GOLD}" font-size="12">{html.escape(diagram.subtitle or "التسلسل الزمني التاريخي المعتمد")}</text>',
        ]

        # Central Vertical Spine
        spine_x = width - 130
        svg_parts.append(
            f'<line x1="{spine_x}" y1="{header_height + 10}" x2="{spine_x}" y2="{total_height - 50}" stroke="{COLOR_GOLD}" stroke-width="4"/>'
        )

        y = header_height + 20
        for m in milestones:
            # Circle marker on spine
            svg_parts.append(
                f'<circle cx="{spine_x}" cy="{y + 35}" r="10" fill="{COLOR_CLAY}" stroke="{COLOR_PAPER}" stroke-width="3"/>'
            )

            # Year badge
            year_label = m.year_hijri if m.year_hijri else m.year_gregorian
            svg_parts.append(
                f'<text x="{width - 30}" y="{y + 40}" fill="{COLOR_DATE}" font-size="15" font-weight="700">{html.escape(year_label)}</text>'
            )

            # Content card on the left
            c_x = 40
            c_w = spine_x - 70
            svg_parts.append(
                f'<rect x="{c_x}" y="{y}" width="{c_w}" height="{card_height}" rx="12" fill="{COLOR_CARD}" stroke="{COLOR_BORDER}" stroke-width="1" filter="url(#shadow)"/>'
                f'<rect x="{c_x + c_w - 6}" y="{y}" width="6" height="{card_height}" rx="3" fill="{COLOR_CLAY}"/>'
                f'<text x="{c_x + c_w - 20}" y="{y + 30}" fill="{COLOR_INK}" font-size="16" font-weight="700" font-family="Noto Naskh Arabic, serif">{html.escape(m.title)}</text>'
                f'<text x="{c_x + c_w - 20}" y="{y + 54}" fill="{COLOR_INK}" font-size="13">{html.escape(m.description[:85])}</text>'
            )

            if m.authority_source:
                svg_parts.append(
                    f'<text x="{c_x + c_w - 20}" y="{y + 86}" fill="{COLOR_OLIVE}" font-size="11.5" font-weight="600">📜 المصدر: {html.escape(m.authority_source)}</text>'
                )

            y += card_height + gap

        svg_parts.append('</svg>')
        return "\n".join(svg_parts)

    def _render_matrix_svg(self, diagram: CulturalDiagram, width: int = 900) -> str:
        """Renders side-by-side comparative table / matrix."""
        regions = diagram.comparison_regions or ["نجد", "الحجاز", "عسير"]
        aspects = diagram.comparison_aspects or []
        num_cols = len(regions) + 1
        col_width = (width - 60) / num_cols
        row_height = 80
        total_height = 100 + ((len(aspects) + 1) * row_height) + 40

        svg_parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {total_height}" width="100%" height="100%" style="background:{COLOR_PAPER}; font-family:\'IBM Plex Sans Arabic\', sans-serif; direction:rtl; text-anchor:end;">',
            f'<rect x="20" y="16" width="{width-40}" height="60" rx="10" fill="{COLOR_INK}"/>',
            f'<text x="{width-45}" y="44" fill="{COLOR_PAPER}" font-size="19" font-weight="700" font-family="Noto Naskh Arabic, serif">{html.escape(diagram.title)}</text>',
            f'<text x="{width-45}" y="63" fill="{COLOR_GOLD}" font-size="12">{html.escape(diagram.subtitle or "مقارنة الخصائص والأنماط الإقليمية")}</text>',
        ]

        # Table Header
        header_y = 90
        svg_parts.append(
            f'<rect x="30" y="{header_y}" width="{width-60}" height="45" rx="8" fill="{COLOR_PAPER_2}" stroke="{COLOR_BORDER}"/>'
            f'<text x="{width - 45}" y="{header_y + 28}" fill="{COLOR_INK}" font-size="14" font-weight="700">عنصر المقارنة</text>'
        )

        for col_idx, reg in enumerate(regions):
            rx = width - 30 - ((col_idx + 2) * col_width)
            svg_parts.append(
                f'<text x="{rx + col_width - 15}" y="{header_y + 28}" fill="{COLOR_DATE}" font-size="14" font-weight="700" font-family="Noto Naskh Arabic, serif">منطقة {html.escape(reg)}</text>'
            )

        # Rows
        y = header_y + 55
        for asp in aspects:
            svg_parts.append(
                f'<rect x="30" y="{y}" width="{width-60}" height="{row_height-10}" rx="8" fill="{COLOR_CARD}" stroke="{COLOR_BORDER}"/>'
                f'<text x="{width - 45}" y="{y + 35}" fill="{COLOR_INK}" font-size="13.5" font-weight="700">{html.escape(asp.dimension_name)}</text>'
            )

            for col_idx, reg in enumerate(regions):
                val = asp.values_by_region.get(reg, "-")
                rx = width - 30 - ((col_idx + 2) * col_width)
                svg_parts.append(
                    f'<text x="{rx + col_width - 15}" y="{y + 35}" fill="{COLOR_INK}" font-size="12.5">{html.escape(val[:40])}</text>'
                )

            y += row_height

        svg_parts.append('</svg>')
        return "\n".join(svg_parts)


# ---------------------------------------------------------------------------
# High-Level Helper Functions / Presets
# ---------------------------------------------------------------------------

def create_majlis_etiquette_diagram() -> CulturalDiagram:
    """Creates the standard authentic Saudi Majlis & Hospitality flowchart."""
    return CulturalDiagram(
        diagram_type="etiquette_flow",
        title="دليل آداب وبروتوكول المجلس والضيافة السعودية",
        subtitle="الأصول التراثية المعتمدة لتقديم القهوة والترحيب بالضيوف",
        region="المملكة العربية السعودية",
        nodes=[
            FlowNode(
                id="greet",
                title="الدخول وإلقاء التحية",
                description="ابدأ بالسلام على يمين المجلس أو على كبير القوم والمقام أولاً، ثم صافح الحضور بحفاوة.",
                badge="المرحلة الأولى",
                do_action="المصافحة باليد اليمنى والابتسام مع عبارات الترحيب",
                dont_action="تخطي الكبار أو الجلوس قبل رد التحية",
                accent="clay",
            ),
            FlowNode(
                id="coffee_hold",
                title="أصول مسك الدلة والفنجان",
                description="تُمسك الدلة باليد اليسرى دائماً، ويُقدّم الفنجان باليد اليمنى للمتلقي.",
                badge="المرحلة الثانية",
                do_action="مسك الدلة باليسرى وتقديم الفنجان باليمنى",
                dont_action="تقديم الفنجان باليد اليسرى أو ملؤه حتى الحافة",
                accent="gold",
            ),
            FlowNode(
                id="coffee_amount",
                title="صب القهوة (صَبّة الحشمة)",
                description="يُملأ ثلث الفنجان فقط (صَبّة الحشمة) دلالة على استمرار الترحيب والخدمة.",
                badge="المرحلة الثالثة",
                do_action="صب القهوة إلى ثلث الفنجان فقط بحرارة مناسبة",
                dont_action="ملء الفنجان كاملاً (يُعدّ ذلك تعجيلاً لرحيل الضيف)",
                accent="olive",
            ),
            FlowNode(
                id="shake_cup",
                title="هَزّ الفنجان للاكتفاء",
                description="عند الاكتفاء من شرب القهوة، يُهزّ الفنجان خفيفاً باليد اليمنى قبل إعادته للمضيّف.",
                badge="المرحلة الرابعة",
                do_action="هز الفنجان يمنة ويسرة بلطف عند الاكتفاء",
                dont_action="وضع الفنجان على الأرض أو تركه دون تنبيه المضيّف",
                accent="date",
            ),
        ],
    )


def create_business_etiquette_diagram() -> CulturalDiagram:
    """Creates the Saudi business negotiation & formal meeting protocol diagram."""
    return CulturalDiagram(
        diagram_type="etiquette_flow",
        title="دليل بروتوكول وآداب الأعمال والمفاوضات في المملكة",
        subtitle="بناء العلاقات المؤسسية والاجتماعات الرسمية وفق الأعراف السعودية",
        region="المملكة العربية السعودية",
        nodes=[
            FlowNode(
                id="biz_greeting",
                title="المجاملة وبناء الألفة (Small Talk)",
                description="تبدأ الاجتماعات بالسؤال عن الحال والأهل والأعمال بوقار، وتجنب الدخول المفاجئ في صلب المفاوضات.",
                badge="الخطوة 1",
                do_action="تبادل التحايا والحديث الودي اللبق قبل بنود الاجتماع",
                dont_action="استعجال توقيع العقود في الدقائق الأولى",
                accent="clay",
            ),
            FlowNode(
                id="biz_coffee",
                title="قبول واجب الضيافة",
                description="تُقدّم القهوة السعودية والتمر في بداية كل لقاء عمل؛ وقبولها يعكس التقدير والاحترام.",
                badge="الخطوة 2",
                do_action="شرب فنجان واحد على الأقل تقديراً لكرم المضيف",
                dont_action="الرفض القاطع للمشروبات الترحيبية",
                accent="gold",
            ),
            FlowNode(
                id="biz_hierarchy",
                title="احترام التراتبية الإدارية",
                description="توجيه الحديث والمخاطبة لرئيس الوفد مع حفظ الألقاب الرسمية.",
                badge="الخطوة 3",
                do_action="استخدام الألقاب الرسمية (سعادة، معالي، الأستاذ)",
                dont_action="مقاطعة كبار المسؤولين أثناء الحديث",
                accent="olive",
            ),
            FlowNode(
                id="biz_patience",
                title="المرونة والصبر في اتخاذ القرار",
                description="تتسم المفاوضات بالعمق والتريث؛ والقرارات تتطلب مشاورات متأنية.",
                badge="الخطوة 4",
                do_action="إظهار المرونة والمتابعة الرسمية المهذبة",
                dont_action="الضغط على الطرف الآخر للحصول على إجابة فورية",
                accent="date",
            ),
        ],
    )
