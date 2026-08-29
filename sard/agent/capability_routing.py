"""Capability-based model routing and structured intent classification for Sard.

Public behavior: Auto is default, no provider/model IDs exposed.
Routing is deterministic, testable, with bounded timeouts and health checks.
Includes multimodal classification for images, audio, documents, and 3D files.
Includes structured intent classification separating domain capability, requested artifact
formats, input modalities, and interactive modes.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple


class Capability(str, Enum):
    SIMPLE_CONVERSATION = "simple_conversation"
    SAUDI_CULTURAL_FACTUAL = "saudi_cultural_factual"
    FRESH_EVENT_PLACE = "fresh_event_place"
    COMPLEX_REASONING = "complex_reasoning"
    ITINERARY_PLANNING = "itinerary_planning"
    TRANSLATION = "translation"
    VISION = "vision"
    AUDIO_TRANSCRIPTION = "audio_transcription"
    DOCUMENT_OCR = "document_ocr"
    THREE_D_INSPECTION = "3d_inspection"
    MULTIMODAL_GROUNDING = "multimodal_grounding"
    DOCUMENT_ANALYSIS = "document_analysis"
    STRUCTURED_OUTPUT = "structured_output"
    GENERATIVE_UI = "generative_ui"
    MAP_GENERATION = "map_generation"
    DIAGRAM_GENERATION = "diagram_generation"
    PRESENTATION_DECK = "presentation_deck"
    RECIPE_CARD = "recipe_card"
    CALENDAR_SYNC = "calendar_sync"
    ETIQUETTE_SIMULATOR = "etiquette_simulator"
    ORAL_HISTORY = "oral_history"
    DIALECT_PROVERB = "dialect_proverb"
    ARTISAN_CRAFT = "artisan_craft"
    GREETING_CARD = "greeting_card"
    VERIFIED_RESEARCH = "verified_research"


@dataclass(frozen=True)
class StructuredIntent:
    """Structured user intent capturing domain, requested formats, and modalities."""
    domain_capability: Capability
    requested_formats: tuple[str, ...] = ("text",)
    input_modalities: tuple[str, ...] = ("text",)
    interactive_mode: bool = False
    explicit_artifact_request: bool = False
    extracted_topic: str = ""
    region: str = ""


@dataclass(frozen=True)
class ModelCandidate:
    model_id: str
    provider: str
    capabilities: frozenset
    context_size: int
    pricing: str  # "free" or "paid"
    supports_tools: bool
    supports_structured: bool
    health_score: float = 1.0  # 0-1


# Lightweight deterministic classifiers
_FORMAT_PDF_RE = re.compile(
    r"(?:\.pdf\b|(?:\b|و)pdf\b|بي\s*دي\s*إف|بي\s*دي\s*اف|ملف\s*pdf|بصيغة\s*pdf|مستند\s*pdf|تقرير\s*pdf|بطاقة\s*pdf|كتاب\s*pdf|كتيب\s*pdf|وثيقة\s*pdf)",
    re.I,
)
_FORMAT_DOCX_RE = re.compile(
    r"(?:\.docx\b|\.doc\b|(?:\b|و)docx\b|(?:\b|و)doc\b|وورد|دوكس|ملف\s*docx|تقرير\s*docx|مستند\s*docx|word\s*doc|word\s*document)",
    re.I,
)
_FORMAT_PPTX_RE = re.compile(
    r"(?:\.pptx\b|\.ppt\b|(?:\b|و)pptx\b|(?:\b|و)ppt\b|بوربوينت|باوربوينت|عرض\s*تقديمي|شرائح|سلايدات|برزنتيشن|presentation|powerpoint|slides)",
    re.I,
)
_FORMAT_ICS_RE = re.compile(
    r"(?:\.ics\b|(?:\b|و)ics\b|تقويم|روزنامة|مزامنة\s*التقويم|أضف\s*للتقويم|calendar|ical|icalendar|google\s*calendar)",
    re.I,
)
_FORMAT_SVG_RE = re.compile(
    r"(?:\.svg\b|(?:\b|و)svg\b|مخطط\s*svg|رسم\s*بياني|flowchart|vector)",
    re.I,
)
_FORMAT_PNG_RE = re.compile(
    r"(?:\.png\b|(?:\b|و)png\b|\.jpg\b|\.jpeg\b|صورة|بطاقة\s*صورة)",
    re.I,
)

_PRESENTATION_HINT = re.compile(
    r"(عرض تقديمي|شرائح|بوربوينت|pptx|presentation|slides|سلايدات|برزنتيشن|إيجاز ثقافي|powerpoint)",
    re.I,
)
_RECIPE_HINT = re.compile(
    r"(وصفة|طبخة|طريقة عمل|طريقة تحضير|مقادير|جريش|سليق|كبسة|حنيني|مرقوق|مطازيز|recipe|dish|cooking)",
    re.I,
)
_CALENDAR_HINT = re.compile(
    r"(تقويم|روزنامة|مزامنة|أضف للتقويم|سهيل|المربعانية|الوسم|مواسم فلكية|calendar|ics|google calendar|مهرجان|موسم الرياض|العلا)",
    re.I,
)
_ETIQUETTE_HINT = re.compile(
    r"(إتيكيت|اتيكيت|آداب|بروتوكول|ضيافة|صب القهوة|مسك الدلة|هز الفنجان|مجلس|مفاوضات|etiquette|protocol)",
    re.I,
)
_ORAL_HISTORY_HINT = re.compile(
    r"(تاريخ شفوي|حكواتي|سيرة عائلة|ذكريات قديمة|توثيق الموروث|شجرة العائلة|memoir|oral history|biography)",
    re.I,
)
_DIALECT_HINT = re.compile(
    r"(لهجة|مثل|أمثال|سالفة المثل|أبشر بسعدك|دحين|مرحبا ألوف|يا بعد حيي|عامية|مصطلحات|proverb|dialect|slang)",
    re.I,
)
_ARTISAN_HINT = re.compile(
    r"(حرفة|حرف تقليدية|سدو|بشت|مشلح|ورد طائفي|قط عسيري|فخار|حرفيين|craft|artisan|sadu|bisht)",
    re.I,
)
_GREETING_HINT = re.compile(
    r"(بطاقة تهنئة|تهنئة|كارت|معايدة|يوم التأسيس|اليوم الوطني|عيد الفطر|عيد الأضحى|رمضان مبارك|greeting card|card)",
    re.I,
)
_RESEARCH_HINT = re.compile(
    r"(توثيق معتمد|بحث أكاديمي|مراجع رسمية|دارة الملك عبد العزيز|هيئة التراث|توثيق تاريخي|research|citation|bibliography)",
    re.I,
)

_FRESH_KEYWORDS = re.compile(
    r"(اليوم|غدا|غدًا|الآن|موعد|فعالية|مهرجان|افتتاح|إغلاق|سعر|تذكرة|جدول|مواعيد|opening|hours|price|event|today|tomorrow)",
    re.I,
)
_ITINERARY_KEYWORDS = re.compile(
    r"(برنامج|مسار|خطة|جدول|يومين|ثلاثة أيام|itinerary|trip|route|plan)", re.I
)
_TRANSLATION_HINT = re.compile(r"(ترجم|ترجمة|translate|translation)", re.I)
_MAP_HINT = re.compile(r"(خريطة|map|موقع|إحداثيات|coordinates)", re.I)
_DIAGRAM_HINT = re.compile(r"(مخطط|diagram|timeline|جدول زمني|علاقة|شجرة|flowchart)", re.I)

# Multimodal file reference and media keywords
_FILE_IMAGE_RE = re.compile(r"@[\w\-\.\/\\\:]+\.(?:jpg|jpeg|png|webp|tiff|bmp|gif)", re.I)
_FILE_AUDIO_RE = re.compile(r"@[\w\-\.\/\\\:]+\.(?:mp3|wav|m4a|ogg|flac)", re.I)
_FILE_DOC_RE = re.compile(r"@[\w\-\.\/\\\:]+\.(?:pdf|docx|txt|md|csv|json)", re.I)
_FILE_3D_RE = re.compile(r"@[\w\-\.\/\\\:]+\.(?:ply|obj|stl|gltf|glb|nii|nii\.gz)", re.I)

_VISION_HINT = re.compile(r"(صورة|صور|photo|image|vision|artifact-photo)", re.I)
_AUDIO_HINT = re.compile(r"(صوت|تسجيل|تفريغ|نص صوتي|audio|recording|transcript|transcribe|oral-history)", re.I)
_DOC_OCR_HINT = re.compile(r"(مخطوطة|وثيقة|صفحة|مسح|ocr|pdf|manuscript|document)", re.I)
_3D_HINT = re.compile(r"(مجسم|نموذج ثلاثي|3d|mesh|ply|obj|stl|nifti)", re.I)


def extract_requested_formats(query: str) -> List[str]:
    """Detect explicitly requested output file formats in Arabic and English queries."""
    formats: List[str] = []
    q = query.strip()

    if _FORMAT_PDF_RE.search(q):
        formats.append("pdf")
    if _FORMAT_DOCX_RE.search(q):
        formats.append("docx")
    if _FORMAT_PPTX_RE.search(q):
        formats.append("pptx")
    if _FORMAT_ICS_RE.search(q):
        formats.append("ics")
    if _FORMAT_SVG_RE.search(q):
        formats.append("svg")
    if _FORMAT_PNG_RE.search(q) and not _FILE_IMAGE_RE.search(q):
        # Only add png if not solely referencing an input @file.png
        if any(w in q.lower() for w in ["png", "بصيغة png", "بطاقة صورة", "بطاقة"]):
            formats.append("png")

    return formats


def extract_input_modalities(query: str, attachments: Optional[Sequence[Any]] = None) -> List[str]:
    """Determine input modalities from textual query and attachment metadata."""
    modalities: Set[str] = set()
    q = query.strip().lower()

    if attachments:
        for att in attachments:
            mime = getattr(att, "mime_type", "") or (att.get("mime_type", "") if isinstance(att, dict) else "")
            fn = getattr(att, "filename", "") or (att.get("filename", "") if isinstance(att, dict) else "")
            ext = os.path.splitext(fn)[1].lower() if fn else ""

            if mime.startswith("image/") or ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"]:
                modalities.add("image")
            elif mime.startswith("audio/") or ext in [".mp3", ".wav", ".m4a", ".ogg", ".flac"]:
                modalities.add("audio")
            elif "pdf" in mime or "document" in mime or ext in [".pdf", ".docx", ".txt", ".md"]:
                modalities.add("document")
            elif ext in [".ply", ".obj", ".stl", ".glb", ".gltf", ".nii", ".nii.gz"]:
                modalities.add("3d")

    if _FILE_AUDIO_RE.search(q) or (_AUDIO_HINT.search(q) and any(k in q for k in ["تفريغ", "transcribe", "تسجيل"])):
        modalities.add("audio")
    if _FILE_3D_RE.search(q) or (_3D_HINT.search(q) and any(k in q for k in ["أبعاد", "مجسم", "mesh"])):
        modalities.add("3d")
    if _FILE_DOC_RE.search(q) or (_DOC_OCR_HINT.search(q) and any(k in q for k in ["استخرج", "extract", "ترجم", "مخطوطة"])):
        modalities.add("document")
    if _FILE_IMAGE_RE.search(q) or _VISION_HINT.search(q):
        modalities.add("image")

    if not modalities:
        modalities.add("text")
    return sorted(list(modalities))


def classify_intent(
    query: str,
    messages: Optional[List[Dict[str, Any]]] = None,
    attachments: Optional[Sequence[Any]] = None,
) -> StructuredIntent:
    """Extract structured intent distinguishing domain, formats, and modalities."""
    q = query.strip()
    q_lower = q.lower()

    # 1. Requested Formats
    req_formats = extract_requested_formats(q)
    explicit_artifact = len(req_formats) > 0

    # 2. Input Modalities
    modalities = extract_input_modalities(q, attachments)

    # 3. Domain Capability
    domain_cap: Capability

    # Multimodal detection (file references & inputs take precedence)
    if "audio" in modalities and (_FILE_AUDIO_RE.search(q_lower) or "تفريغ" in q_lower or "transcribe" in q_lower):
        domain_cap = Capability.AUDIO_TRANSCRIPTION
    elif "3d" in modalities and (_FILE_3D_RE.search(q_lower) or "أبعاد" in q_lower or "mesh" in q_lower):
        domain_cap = Capability.THREE_D_INSPECTION
    elif "document" in modalities and (_FILE_DOC_RE.search(q_lower) or "مخطوطة" in q_lower):
        domain_cap = Capability.DOCUMENT_OCR
    elif "image" in modalities and (_FILE_IMAGE_RE.search(q_lower) or "صورة" in q_lower):
        domain_cap = Capability.VISION

    # Specialized tool heuristics
    elif _PRESENTATION_HINT.search(q):
        domain_cap = Capability.PRESENTATION_DECK
        if "pptx" not in req_formats:
            req_formats.append("pptx")
            explicit_artifact = True
    elif _GREETING_HINT.search(q) and any(k in q_lower for k in ["بطاقة", "كارت", "تهنئة", "معايدة"]):
        domain_cap = Capability.GREETING_CARD
        if not req_formats:
            req_formats.extend(["pdf", "svg"])
            explicit_artifact = True
    elif _RECIPE_HINT.search(q) and any(k in q_lower for k in ["وصفة", "طريقة", "مقادير", "طبخة", "recipe"]):
        domain_cap = Capability.RECIPE_CARD
        if "pdf" in req_formats or "بطاقة" in q_lower:
            if "pdf" not in req_formats:
                req_formats.append("pdf")
            explicit_artifact = True
    elif _CALENDAR_HINT.search(q) and any(k in q_lower for k in ["تقويم", "مزامنة", "روزنامة", "calendar", "سهيل", "المربعانية"]) and not _ITINERARY_KEYWORDS.search(q):
        domain_cap = Capability.CALENDAR_SYNC
        if "ics" not in req_formats:
            req_formats.append("ics")
            explicit_artifact = True
    elif _ETIQUETTE_HINT.search(q):
        domain_cap = Capability.ETIQUETTE_SIMULATOR
        if "svg" not in req_formats:
            req_formats.append("svg")
            explicit_artifact = True
    elif _ORAL_HISTORY_HINT.search(q) and any(k in q_lower for k in ["شفوي", "حكواتي", "سيرة", "ذكريات", "memoir"]):
        domain_cap = Capability.ORAL_HISTORY
        if "pdf" not in req_formats:
            req_formats.append("pdf")
            explicit_artifact = True
    elif _DIALECT_HINT.search(q) and any(k in q_lower for k in ["لهجة", "مثل", "أمثال", "سالفة", "معنى", "مصطلح", "proverb"]):
        domain_cap = Capability.DIALECT_PROVERB
    elif _ARTISAN_HINT.search(q) and any(k in q_lower for k in ["حرفة", "سدو", "بشت", "ورد", "قط", "أصالة", "craft"]):
        domain_cap = Capability.ARTISAN_CRAFT
    elif _RESEARCH_HINT.search(q):
        domain_cap = Capability.VERIFIED_RESEARCH

    elif _ITINERARY_KEYWORDS.search(q):
        domain_cap = Capability.ITINERARY_PLANNING
        # If user mentioned itinerary, formats like PDF / ICS may be requested
        if "pdf" in req_formats or "ics" in req_formats:
            explicit_artifact = True
    elif _TRANSLATION_HINT.search(q):
        domain_cap = Capability.TRANSLATION
    elif _MAP_HINT.search(q):
        domain_cap = Capability.MAP_GENERATION
    elif _DIAGRAM_HINT.search(q):
        domain_cap = Capability.DIAGRAM_GENERATION
        if "svg" not in req_formats:
            req_formats.append("svg")
            explicit_artifact = True
    elif _FRESH_KEYWORDS.search(q):
        domain_cap = Capability.FRESH_EVENT_PLACE
    elif any(k in q_lower for k in ["أين", "متى", "من هو", "تاريخ", "تراث", "ثقافة", "موقع", "يونسكو", "وزارة الثقافة", "نجد", "الحجاز", "عسير", "العلا", "الدرعية", "history", "heritage", "culture", "alula", "diriyah", "riyadh", "saudi", "briefing"]):
        domain_cap = Capability.SAUDI_CULTURAL_FACTUAL
    elif len(q.split()) > 30 or any(k in q_lower for k in ["لماذا", "كيف", "analyse", "analyze", "explain why"]):
        domain_cap = Capability.COMPLEX_REASONING
    else:
        domain_cap = Capability.SIMPLE_CONVERSATION

    # Extract topic heuristics
    clean_topic = re.sub(r"(أنشئ لي|أعطني|صمم|جهز|أريد|تقرير|ملف|بصيغة|عرض|برزنتيشن|pdf|docx|pptx|ics|عن|حول|please create a|make a|generate a|about)\s*", "", q, flags=re.I).strip(" :،.؟?")

    # Extract region heuristics
    region = ""
    for reg in ["نجد", "الرياض", "الدرعية", "الحجاز", "مكة", "المدينة", "جدة", "الطائف", "العلا", "عسير", "أبها", "المنطقة الشرقية", "الأحساء", "القطيف", "حائل", "تبوك", "جازان", "نجران"]:
        if reg in q:
            region = reg
            break

    formats_tuple = tuple(req_formats) if req_formats else ("text",)

    return StructuredIntent(
        domain_capability=domain_cap,
        requested_formats=formats_tuple,
        input_modalities=tuple(modalities),
        interactive_mode=domain_cap in (Capability.ETIQUETTE_SIMULATOR, Capability.DIALECT_PROVERB),
        explicit_artifact_request=explicit_artifact,
        extracted_topic=clean_topic[:100] if clean_topic else q[:50],
        region=region or "المملكة العربية السعودية",
    )


def classify_capability(question: str) -> Capability:
    """Classify capability for backward compatibility."""
    intent = classify_intent(question)
    return intent.domain_capability


# Capability -> required flags
_CAPABILITY_REQUIREMENTS: Dict[Capability, Dict[str, bool]] = {
    Capability.SIMPLE_CONVERSATION: {},
    Capability.SAUDI_CULTURAL_FACTUAL: {"supports_tools": False},
    Capability.FRESH_EVENT_PLACE: {"supports_tools": True},
    Capability.COMPLEX_REASONING: {},
    Capability.ITINERARY_PLANNING: {"supports_structured": True},
    Capability.TRANSLATION: {},
    Capability.VISION: {"supports_tools": False},
    Capability.AUDIO_TRANSCRIPTION: {"supports_tools": False},
    Capability.DOCUMENT_OCR: {"supports_tools": False},
    Capability.THREE_D_INSPECTION: {"supports_tools": False},
    Capability.MULTIMODAL_GROUNDING: {"supports_tools": True},
    Capability.DOCUMENT_ANALYSIS: {"supports_tools": True},
    Capability.STRUCTURED_OUTPUT: {"supports_structured": True},
    Capability.GENERATIVE_UI: {"supports_structured": True},
    Capability.MAP_GENERATION: {"supports_structured": True},
    Capability.DIAGRAM_GENERATION: {"supports_structured": True},
    Capability.PRESENTATION_DECK: {"supports_tools": True},
    Capability.RECIPE_CARD: {"supports_tools": True},
    Capability.CALENDAR_SYNC: {"supports_tools": True},
    Capability.ETIQUETTE_SIMULATOR: {"supports_tools": True},
    Capability.ORAL_HISTORY: {"supports_tools": True},
    Capability.DIALECT_PROVERB: {"supports_tools": True},
    Capability.ARTISAN_CRAFT: {"supports_tools": True},
    Capability.GREETING_CARD: {"supports_tools": True},
    Capability.VERIFIED_RESEARCH: {"supports_tools": True},
}


def _is_eligible(candidate: ModelCandidate, cap: Capability) -> bool:
    req = _CAPABILITY_REQUIREMENTS.get(cap, {})
    if req.get("supports_tools") and not candidate.supports_tools:
        return False
    if req.get("supports_structured") and not candidate.supports_structured:
        return False
    if cap == Capability.VISION and "vision" not in candidate.capabilities:
        return False
    if cap not in candidate.capabilities and candidate.capabilities:
        if cap in [Capability.SAUDI_CULTURAL_FACTUAL, Capability.FRESH_EVENT_PLACE]:
            pass
        else:
            if cap in (Capability.VISION, Capability.STRUCTURED_OUTPUT):
                return False
    return True


def select_route(
    capability: Capability,
    candidates: List[ModelCandidate],
    health_threshold: float = 0.3,
    free_only: bool = True,
) -> Optional[ModelCandidate]:
    """Deterministic selection: filter -> rank by health -> latency proxy -> cost."""
    pool = candidates
    if free_only:
        free_pool = [c for c in pool if c.pricing == "free"]
        if free_pool:
            pool = free_pool
    eligible = [c for c in pool if _is_eligible(c, capability)]
    if not eligible:
        return None
    healthy = [c for c in eligible if c.health_score >= health_threshold]
    if healthy:
        eligible = healthy
    eligible.sort(key=lambda c: (-c.health_score, -c.context_size, c.model_id))
    return eligible[0]


# Simple in-memory circuit breaker health tracker
_HEALTH: Dict[str, float] = {}
_FAILURE_COUNTS: Dict[str, int] = {}


def record_success(model_id: str):
    _HEALTH[model_id] = min(1.0, _HEALTH.get(model_id, 1.0) + 0.1)
    _FAILURE_COUNTS[model_id] = 0


def record_failure(model_id: str):
    cnt = _FAILURE_COUNTS.get(model_id, 0) + 1
    _FAILURE_COUNTS[model_id] = cnt
    _HEALTH[model_id] = max(0.0, 1.0 - (cnt * 0.25))


def get_health(model_id: str) -> float:
    return _HEALTH.get(model_id, 1.0)


def public_mode_label(is_auto: bool, override_available: bool) -> str:
    if is_auto:
        return "تلقائي"
    if not override_available:
        return "تلقائي (العودة لتلقائي)"
    return "مخصص"
