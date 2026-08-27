"""Small, dependency-free helpers shared by agent nodes.

Kept deliberately thin: JSON extraction from model replies, conservative
Arabic deterministic extraction used when the model path degrades, and
numeric coercion.  No provider or SDK is imported here.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

_JSON_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def extract_json_object(text: str) -> Optional[dict]:
    """Extract the first balanced JSON object from a model reply.

    Tolerates code fences, surrounding prose and trailing commas.  Returns
    ``None`` (never raises) when no valid JSON object can be parsed.
    """
    if not text:
        return None

    fenced = _JSON_CODE_FENCE_RE.search(text)
    candidate = fenced.group(1) if fenced else text

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    payload = candidate[start : end + 1]
    try:
        parsed = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def coerce_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        cleaned = value.replace("،", "").replace(",", "").strip()
        digits = {
            "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
            "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
        }
        cleaned = "".join(digits.get(ch, ch) for ch in cleaned)
        try:
            return int(cleaned)
        except ValueError:
            return default
    return default


def _clean_word(value: str) -> str:
    return re.sub(r"[\u060c\u061b\u061f\u002e\u0021\u2026\s]+$", "", value).strip()


_AUDIENCE_KEYWORDS = {
    "عائلة": "عائلة",
    "أطفال": "أطفال",
    "شهر العسل": "شهر عسل",
    "أصدقاء": "أصدقاء",
    "رحلة عمل": "رحلة عمل",
    "رحلة تطوعية": "رحلة تطوعية",
}

_INTEREST_KEYWORDS = {
    "طعام": "طعام",
    "أكل": "طعام",
    "مطاعم": "طعام",
    "أسواق": "أسواق",
    "تسوق": "تسوق",
    "طبيعة": "طبيعة",
    "حديقة": "طبيعة",
    "متاحف": "متاحف",
    "تاريخ": "تاريخ",
    "شواطئ": "شواطئ",
    "بحر": "شواطئ",
    "جبال": "جبال",
    "صحاري": "صحاري",
    "رياضة": "رياضة",
    "مغامرات": "مغامرات",
    "ثقافة": "ثقافة",
    "فنون": "ثقافة",
    "صحراء": "صحاري",
    "ينابيع": "طبيعة",
}

_TIMING_KEYWORDS = (
    "صيف", "شتاء", "ربيع", "خريف",
    "رمضان", "عيد الفطر", "عيد الأضحى", "عطلة نهاية الأسبوع",
    "ديسمبر", "يناير", "فبراير", "مارس", "أبريل", "مايو",
    "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر",
)


def deterministic_extraction(text: str) -> dict:
    """Very conservative Arabic extraction of basic travel fields.

    Used only when semantic extraction is unavailable/degraded.  Never claims
    a value that is not plainly present in the request text.
    """
    items: dict[str, Any] = {}
    request = text or ""

    destination = None
    for marker in ("إلى ", "الى ", "في "):
        match = re.search(marker + r"([\u0600-\u06FF][\u0600-\u06FF\s]{1,40}?)" + r"(?=[،.؛؟\n]|$)", request)
        if match:
            candidate = _clean_word(match.group(1))
            if candidate and any("\u0600" <= ch <= "\u06FF" for ch in candidate):
                destination = candidate
                break
    items["destination"] = destination

    duration = None
    match = re.search(r"(\d+)\s*(ي|أ)و?م", request)
    if not match:
        match = re.search(r"(\d+)\s*ليل[ةه]", request)
    days = coerce_int(match.group(1) if match else None)
    if days:
        duration = days
    items["duration_days"] = duration

    timing = None
    for keyword in _TIMING_KEYWORDS:
        if keyword in request:
            timing = keyword
            break
    items["timing"] = timing
    items["travel_dates"] = []
    items["timing_constraints"] = []
    items["accessibility_needs"] = []
    items["budget"] = None

    audience = []
    for keyword, label in _AUDIENCE_KEYWORDS.items():
        if keyword in request:
            audience.append(label)
    items["audience"] = audience

    interests = []
    for keyword, label in _INTEREST_KEYWORDS.items():
        if keyword in request:
            if label not in interests:
                interests.append(label)
    items["interests"] = interests

    items["user_facts"] = []

    missing = []
    if not destination:
        missing.append("وجهة السفر غير محددة")
    if not duration:
        missing.append("مدة الرحلة غير محددة")
    if not timing:
        missing.append("تاريخ السفر غير محدد")
    if not audience:
        missing.append("عدد وطبيعة المسافرين غير محددة")
    items["missing_constraints"] = missing

    assumptions = []
    if not duration:
        assumptions.append("سنفترض رحلة قصيرة (ليلة واحدة) ما لم يُحدد المستخدم خلاف ذلك")
    if not audience:
        assumptions.append("سنفترض مسافرًا بالغًا واحدًا ما لم يُحدد المستخدم خلاف ذلك")
    if not timing:
        assumptions.append("سنفترض إمكانية السفر في أي وقت ما لم يحدد المستخدم خلاف ذلك")
    items["assumptions"] = assumptions

    items["intent"] = "travel_planning"
    return items


def pick_allowed(items: dict, allowed: tuple[str, ...]) -> dict:
    return {key: value for key, value in items.items() if key in allowed}


def sanitize_cultural_output(text: str) -> str:
    """Sanitize and polish AI response text before sending to UI.

    1. Removes bracketed internal developer tokens like:
       - [RAG: ...] or 【RAG: ...】
       - [CIT-...] or 【CIT-...】
       - [Web: ...] or 【Web: ...】
       - [Media: ...] or 【Media: ...】
    2. Converts raw HTML breaks like <br>, <br/>, <br /> into newlines.
    3. Cleans up formatting artifacts, double spaces, and excess blank lines.
    """
    if not text:
        return ""

    # 1. Normalize line break tags to markdown newlines
    cleaned = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.IGNORECASE)

    # 2. Strip internal citation markers: [RAG: ...], 【RAG: ...】, [Web: ...], 【Web: ...】, [Media: ...], 【Media: ...】, [CIT-...]
    cleaned = re.sub(
        r"[\[【]\s*(?:RAG|Web|Media|CIT|cit)[\s:-][^\]】]*?[\]】]",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\[\s*CIT-[A-Za-z0-9_-]+\s*\]", "", cleaned, flags=re.IGNORECASE)

    # 3. Clean up whitespace within lines
    lines = [re.sub(r"[ \t]{2,}", " ", line).rstrip() for line in cleaned.split("\n")]

    # 4. Rejoin with newlines and clean up excess blank lines
    result = "\n".join(lines)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()

    return result