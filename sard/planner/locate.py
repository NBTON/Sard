"""Cultural Locator for Sard's Isnād Planner.

Extracts geographic region, cultural occasion, community/lineage context,
and user stance (visitor, local, researcher, unknown) from query text.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple
from pydantic import BaseModel, Field

from sard.schemas.isnad import Region, UserStance


class CulturalLocation(BaseModel):
    """Extracted geographic and social context."""

    region: Region = "unknown"
    occasion: Optional[str] = None
    community: Optional[str] = None
    user_stance: UserStance = "unknown"
    sub_district: Optional[str] = None


# Regional pattern definitions
_REGION_PATTERNS = [
    ("najd", re.compile(r"(نجد|نجدي|الرياض|درعية|طريف|سدير|قصيم|وشم|حوطة|أثل|باب نجدي|najd|riyadh|diriyah)", re.I)),
    ("hijaz", re.compile(r"(حجاز|حجازي|جدة|بلد|رواشين|روشان|مكة|مدينة|ينبع|طائف|علا|حجر|منجور|hijaz|jeddah|makkah|madinah|alula)", re.I)),
    ("asir", re.compile(r"(عسير|عسيري|أبها|قط عسيري|سودة|رجال ألمع|نماص|تنومة|asir|abha|rijaal almaa)", re.I)),
    ("eastern", re.compile(r"(شرقية|أحساء|احساء|قطيف|تاروت|دمام|خبر|ظهران|سيهات|هفوف|مبرز|eastern province|al-ahsa|tarout|qatif)", re.I)),
    ("north", re.compile(r"(حائل|حائلي|تبوك|جوف|دومة الجندل|عرعر|قريات|تيماء|hail|tabuk|jouf)", re.I)),
    ("south", re.compile(r"(نجران|جازان|فيفاء|فرسان|أخدود|مغواة|najran|jazan|farasan)", re.I)),
    ("national", re.compile(r"(سعودي|سعودية|المملكة|يوم التأسيس|اليوم الوطني|سدو|السدو|قهوة|القهوة|saudi|national)", re.I)),
]

# User stance detection patterns
_STANCE_PATTERNS = [
    ("researcher", re.compile(r"(بحث|توثيق|دراسة|تأصيل|مرجع|أكاديمي|وثيقة|مخطوطة|research|academic|source|reference)", re.I)),
    ("visitor", re.compile(r"(سياحة|زيارة|مسافر|أول مرة|كيف أصل|تذاكر|سائح|visitor|tourist|travel|first time)", re.I)),
    ("local", re.compile(r"(عندنا|جدتي|أهلنا|ديرتنا|حارتنا|أنا من|local|our family|our tradition)", re.I)),
]


def locate_cultural_context(text: str) -> CulturalLocation:
    """Analyze query and extract regional grounding, occasion, and user stance."""
    t_norm = text.lower().strip()

    # 1. Determine region
    detected_region: Region = "unknown"
    for r_name, pattern in _REGION_PATTERNS:
        if pattern.search(t_norm):
            detected_region = r_name  # type: ignore
            break

    # 2. Determine user stance
    detected_stance: UserStance = "unknown"
    for s_name, pattern in _STANCE_PATTERNS:
        if pattern.search(t_norm):
            detected_stance = s_name  # type: ignore
            break

    # 3. Detect occasion / sub-context
    occasion = None
    if re.search(r"(عيد|فطر|أضحى|رمضان)", t_norm):
        occasion = "أعياد ومناسبات دينية"
    elif re.search(r"(عرس|زواج|خطوبة|ملكة)", t_norm):
        occasion = "أعراس ومناسبات اجتماعية"
    elif re.search(r"(بناء|نجارة|نقش|عمارة|باب|خشب|طين)", t_norm):
        occasion = "حرف وفنون العمارة التقليدية"
    elif re.search(r"(طبخ|طعام|وليمة|غداء|عشاء)", t_norm):
        occasion = "ثقافة الطعام والضيافة"

    return CulturalLocation(
        region=detected_region,
        occasion=occasion,
        user_stance=detected_stance,
    )
