"""Scope Guardrail for Sard — Saudi cultural focus.

Implements an intent gate BEFORE retrieval/generation:
- Allow Saudi culture, heritage, history, traditions, regions, cuisine, crafts,
  oral history, and closely related comparative questions.
- Politely decline clearly unrelated queries.
- Do not let nearest-neighbor retrieval override a confident out-of-scope classification.
- Support both Arabic and English responses.

Detection is bilingual, deterministic, and conservative: only mark out-of-scope
when the query is confidently foreign-only without Saudi connection.
"""

from __future__ import annotations

import re
from typing import Tuple

# Saudi / heritage keywords (Arabic + English) — Saudi-specific only, not generic heritage terms
_SAUDI_KEYWORDS = [
    # Country & regions (Arabic)
    "السعودية", "سعودي", "سعودية", "المملكة", "نجد", "الحجاز", "عسير", "القصيم", "المنطقة الشرقية",
    "الشرقية", "الأحساء", "القطيف", "تاروت", "الدرعية", "الرياض", "جدة", "مكة", "المدينة",
    "الطائف", "العلا", "تبوك", "حائل", "جازان", "نجران", "الباحة", "عرعر", "الجوف", "ينبع",
    "سدير", "وشم", "الوشم",
    # Heritage terms (Arabic — Saudi-specific)
    "وزارة الثقافة", "هيئة التراث", "اليونسكو",
    "القهوة السعودية", "السدو", "البشت", "المشلح", "العرضة", "السامري", "الخطوة", "الدحة",
    "القط العسيري", "مأكولات", "كبسة", "جريش", "سليق", "قرصان", "حنيني", "مندي", "مطازيز",
    # Regions / cities English (Saudi-specific)
    "saudi", "saudi arabia", "kingdom of saudi", "najd", "hijaz", "hejaz", "asir", "alula", "diriyah",
    "riyadh", "jeddah", "taif", "tabuk", "hail", "jazan", "najran",
    "sadu", "bisht", "qahwa",
]

# Foreign culture keywords that are out-of-scope when alone
_FOREIGN_KEYWORDS = [
    "ساموراي", "ساموراى", "نينجا", "سوشي", "اليابان", "ياباني", "يابانية", "طوكيو", "كيوتو",
    "الصين", "صيني", "كوريا", "كوري", "الهند", "هندي", "أوروبا", "أوروبي", "أمريكا", "أمريكي",
    "فرنسا", "فرنسي", "بريطانيا", "بريطاني", "ألمانيا", "ألماني",
    "مصر", "مصري",  # comparative Egyptian questions without Saudi are out-of-scope unless Saudi present
    "japan", "japanese", "samurai", "ninja", "sushi", "tokyo", "kyoto",
    "china", "chinese", "korea", "korean", "india", "indian",
    "europe", "european", "america", "american", "france", "french",
    "britain", "british", "germany", "german", "usa", "uk",
]

# Comparative connectors that indicate allowed cross-cultural question when Saudi also present
_COMPARATIVE_CONNECTORS = [
    "قارن", "مقارنة", "مقارنه", "بين", "مقارنة بين", "علاقة", "الفرق بين", "و",
    "compare", "comparison", "versus", "vs", "between", "and", "with",
]

_GREETING_PATTERN = re.compile(
    r"^(من أنت|من انت|عرفني بنفسك|عرف بنفسك|ما هو سرد|من تكون|مرحبا|أهلا|اهلا|السلام عليكم|صباح الخير|مساء الخير|هلا|أهلاً|hello|hi|who are you|introduce yourself)\s*[!؟?]*$",
    re.I,
)

def _contains_any(text_lower: str, keywords: list[str]) -> bool:
    for kw in keywords:
        if kw.lower() in text_lower:
            return True
    return False

def _is_greeting(query: str) -> bool:
    return bool(_GREETING_PATTERN.match(query.strip().lower()))

def _is_arabic(query: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", query))

def is_out_of_scope(query: str) -> Tuple[bool, str]:
    """Determine if query is confidently out-of-scope for Sard.

    Returns (is_out_of_scope, reason_code).
    - True only when foreign-only without Saudi/heritage anchor.
    - False for greetings, Saudi-related, heritage, or comparative with Saudi.
    """
    q = (query or "").strip()
    if not q:
        return False, "empty"
    if _is_greeting(q):
        return False, "greeting"
    q_low = q.lower()

    has_saudi = _contains_any(q_low, _SAUDI_KEYWORDS)
    has_foreign = _contains_any(q_low, _FOREIGN_KEYWORDS)

    # If Saudi present, never out-of-scope (comparative allowed, even if foreign also present)
    if has_saudi:
        return False, "saudi_present"

    # If foreign present and no Saudi, check if it's truly foreign-only
    if has_foreign and not has_saudi:
        # Even foreign-only could be heritage-generic without Saudi, e.g. "What is samurai?"
        # That's out-of-scope. But ensure we don't block pure heritage questions that are Saudi-implicit
        # e.g. "Tell me about coffee traditions" without Saudi word but hospitality is Saudi heritage -> allow
        # So we only block when foreign keyword is dominant and no heritage generic term implies Saudi
        # Check overlap: if query contains generic heritage words but also foreign, we still consider foreign-only out-of-scope
        # because heritage words alone don't imply Saudi.
        return True, "foreign_only"

    # No Saudi, no foreign -> could be generic question like "What is heritage?" -> allow (will be answered via RAG hedge)
    # Don't block.
    return False, "in_scope_or_generic"

def scope_guard_response(query: str, lang: str = "ar") -> str:
    """Polite scope explanation in the requested language."""
    is_ar = lang == "ar" or (_is_arabic(query) and lang not in ("en", "ar"))
    # Determine language: explicit lang takes precedence; otherwise infer from query
    if lang in ("ar", "en"):
        is_ar = lang == "ar"
    else:
        is_ar = _is_arabic(query)
    if is_ar:
        return (
            "أعتذر، أنا **سرد** متخصص في الثقافة والتراث السعودي (التاريخ، المناطق، العادات، المطبخ، الحرف، اللهجات، والضيافة).\n\n"
            "سؤالك الحالي يقع خارج نطاق تخصصي ولا أجد له سندًا في سجلات التراث السعودي المعتمدة.\n\n"
            "يمكنني مساعدتك في:\n"
            "- استكشاف تراث وهوية أي منطقة من مناطق المملكة الـ13.\n"
            "- شرح العادات والتقاليد والمأكولات والحرف اليدوية السعودية.\n"
            "- مقارنات ثقافية **تتضمن** الجانب السعودي (مثلاً: قارن بين الضيافة السعودية واليابانية).\n\n"
            "إذا كان لديك جانب سعودي تود مقارنته أو سياق سعودي مرتبط بسؤالك، يسعدني التوضيح."
        )
    else:
        return (
            "I'm **Sard**, your guide specialized in Saudi culture and heritage — history, regions, traditions, cuisine, crafts, dialects, and hospitality.\n\n"
            "Your current question is outside my curated Saudi heritage scope, and I don't have verified Saudi sources for it.\n\n"
            "I can help you with:\n"
            "- Exploring heritage and identity of any of the 13 Saudi regions.\n"
            "- Traditions, cuisine, and crafts of Saudi communities.\n"
            "- Comparative questions that **include** the Saudi side (e.g., compare Saudi and Japanese hospitality).\n\n"
            "If you'd like to add a Saudi angle or compare with a Saudi tradition, I'd be glad to elaborate."
        )

def check_scope_before_retrieval(query: str, lang: str = "ar") -> Tuple[bool, str]:
    """Convenience: return (should_block, response_text). Empty response if not blocked."""
    is_oos, reason = is_out_of_scope(query)
    if is_oos:
        return True, scope_guard_response(query, lang=lang)
    return False, ""
