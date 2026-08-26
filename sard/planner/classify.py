"""Request Classifier for Sard's Isnād Planner.

Classifies incoming cultural requests into canonical categories:
- story: narratives, historical origins, folklore, epic tales
- place: heritage sites, historic districts, geography, architectural landmarks
- ritual: social occasions, Eid, weddings, hospitality practices, coffee ritual
- food: traditional cuisine, recipes, regional dishes, cooking methods
- dialect: regional terminology, idioms, proverbs, linguistic nuances
- etiquette: manners, guest protocols, dress norms, social boundaries
- object_from_image: physical artifacts, doors, textiles, crafts identified via images
- itinerary: multi-day travel plans, visit routes, schedules
- other: general cultural inquiries
"""

from __future__ import annotations

import re
from typing import Tuple
from sard.schemas.isnad import RequestClassification

# Keywords indicating request category
_CLASSIFIERS = [
    ("object_from_image", re.compile(r"(@[a-zA-Z0-9_\-\.\/\\]+\.(?:jpg|jpeg|png|webp|gif|bmp)|صورة|هذا الباب|هذه القطعة|هذا المجسم|ما هذا|هذا الشيء|photo|image|this door|artifact)", re.I)),
    ("itinerary", re.compile(r"(برنامج|مسار|جدول|رحلة|يومين|خطة سياحية|itinerary|schedule|tour plan|days)", re.I)),
    ("food", re.compile(r"(طعام|أكلة|طبخة|وصفة|روبيان|كبسة|قرصان|جريش|حنيني|سليق|مندي|مفطح|معصوب|عيش حساوي|خبز أحمر|مأكولات|food|dish|recipe|cuisine)", re.I)),
    ("ritual", re.compile(r"(طقس|عادات|تقاليد|احتفال|عيد|عرس|زواج|ضيافة|قهوة|فنجال|عرضة|دحة|خطوة|ritual|celebration|wedding|hospitality)", re.I)),
    ("dialect", re.compile(r"(لهجة|معنى كلمة|مثل|أمثال|مفردة|مفردات|مصطلح|كيف يقولون|dialect|slang|phrase|proverb)", re.I)),
    ("etiquette", re.compile(r"(إتيكيت|آداب|سلوك|واجب|كيف أتصرف|مجلس|حق الضيف|etiquette|protocol|manners|rules of conduct)", re.I)),
    ("story", re.compile(r"(قصة|رواية|حكاية|سالفة|أصل|تاريخ|من أين جاء|story|tale|origin|narrative|legend)", re.I)),
    ("place", re.compile(r"(موقع|قصر|قلعة|واحة|حي|بلد|منطقة|جبل|عين|سوق|معلم|place|palace|castle|fort|oasis|market|unesco)", re.I)),
]


def classify_request(text: str, has_media: bool = False) -> Tuple[RequestClassification, float]:
    """Classify the user query into a canonical RequestClassification."""
    if has_media or re.search(r"@[a-zA-Z0-9_\-\.\/\\]+\.(?:jpg|jpeg|png|webp|gif|bmp|ply|obj)", text, re.I):
        return "object_from_image", 0.95

    t_norm = text.lower().strip()
    for cat, pattern in _CLASSIFIERS:
        if pattern.search(t_norm):
            return cat, 0.85

    return "other", 0.50
