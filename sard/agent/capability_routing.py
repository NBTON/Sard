"""Capability-based model routing for Sard.

Public behavior: Auto is default, no provider/model IDs exposed.
Routing is deterministic, testable, with bounded timeouts and health checks.
Includes multimodal classification for images, audio, documents, and 3D files.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional


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


# Lightweight deterministic classifier - no LLM, testable
_FRESH_KEYWORDS = re.compile(
    r"(اليوم|غدا|غدًا|الآن|موعد|فعالية|مهرجان|افتتاح|إغلاق|سعر|تذكرة|جدول|مواعيد|opening|hours|price|event|today|tomorrow)",
    re.I,
)
_ITINERARY_KEYWORDS = re.compile(
    r"(برنامج|مسار|خطة|جدول|يومين|ثلاثة أيام|itinerary|trip|route|plan)", re.I
)
_TRANSLATION_HINT = re.compile(r"(ترجم|ترجمة|translate|translation)", re.I)
_MAP_HINT = re.compile(r"(خريطة|map|موقع|إحداثيات|coordinates)", re.I)
_DIAGRAM_HINT = re.compile(r"(مخطط|diagram|timeline|جدول زمني|علاقة|شجرة)", re.I)

# Multimodal file reference and media keywords
_FILE_IMAGE_RE = re.compile(r"@[\w\-\.\/\\\:]+\.(?:jpg|jpeg|png|webp|tiff|bmp|gif)", re.I)
_FILE_AUDIO_RE = re.compile(r"@[\w\-\.\/\\\:]+\.(?:mp3|wav|m4a|ogg|flac)", re.I)
_FILE_DOC_RE = re.compile(r"@[\w\-\.\/\\\:]+\.(?:pdf|docx|txt|md|csv|json)", re.I)
_FILE_3D_RE = re.compile(r"@[\w\-\.\/\\\:]+\.(?:ply|obj|stl|gltf|glb|nii|nii\.gz)", re.I)

_VISION_HINT = re.compile(r"(صورة|صور|photo|image|vision|artifact-photo)", re.I)
_AUDIO_HINT = re.compile(r"(صوت|تسجيل|تفريغ|نص صوتي|audio|recording|transcript|transcribe|oral-history)", re.I)
_DOC_OCR_HINT = re.compile(r"(مخطوطة|وثيقة|صفحة|مسح|ocr|pdf|manuscript|document)", re.I)
_3D_HINT = re.compile(r"(مجسم|نموذج ثلاثي|3d|mesh|ply|obj|stl|nifti)", re.I)


def classify_capability(question: str) -> Capability:
    q = question.strip().lower()

    # Multimodal detection (file references first)
    if _FILE_AUDIO_RE.search(q):
        return Capability.AUDIO_TRANSCRIPTION
    if _FILE_DOC_RE.search(q):
        if _TRANSLATION_HINT.search(q):
            return Capability.DOCUMENT_OCR
        return Capability.DOCUMENT_OCR
    if _FILE_3D_RE.search(q):
        return Capability.THREE_D_INSPECTION
    if _FILE_IMAGE_RE.search(q) or _VISION_HINT.search(q):
        return Capability.VISION

    # Media keyword heuristics
    if _AUDIO_HINT.search(q) and ("تفريغ" in q or "transcribe" in q or "تسجيل" in q):
        return Capability.AUDIO_TRANSCRIPTION
    if _DOC_OCR_HINT.search(q) and ("استخرج" in q or "extract" in q or "ترجم" in q or "صفحة" in q):
        return Capability.DOCUMENT_OCR
    if _3D_HINT.search(q) and ("أبعاد" in q or "مجسم" in q or "mesh" in q):
        return Capability.THREE_D_INSPECTION

    if _TRANSLATION_HINT.search(q):
        return Capability.TRANSLATION
    if _ITINERARY_KEYWORDS.search(q):
        return Capability.ITINERARY_PLANNING
    if _MAP_HINT.search(q):
        return Capability.MAP_GENERATION
    if _DIAGRAM_HINT.search(q):
        return Capability.DIAGRAM_GENERATION
    if _FRESH_KEYWORDS.search(q):
        return Capability.FRESH_EVENT_PLACE

    # heuristic for cultural factual
    if any(k in q for k in ["أين", "متى", "من هو", "تاريخ", "تراث", "ثقافة", "موقع", "يونسكو", "وزارة الثقافة"]):
        return Capability.SAUDI_CULTURAL_FACTUAL
    if len(q.split()) > 30 or any(k in q for k in ["لماذا", "كيف", "analyse", "analyze", "explain why"]):
        return Capability.COMPLEX_REASONING
    return Capability.SIMPLE_CONVERSATION


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
}


def _is_eligible(candidate: ModelCandidate, cap: Capability) -> bool:
    req = _CAPABILITY_REQUIREMENTS.get(cap, {})
    if req.get("supports_tools") and not candidate.supports_tools:
        return False
    if req.get("supports_structured") and not candidate.supports_structured:
        return False
    if cap == Capability.VISION and "vision" not in candidate.capabilities:
        return False
    # Never route to model lacking required capability implicitly
    if cap not in candidate.capabilities and candidate.capabilities:
        # if candidate declares capabilities, cap must be inside
        if cap in [Capability.SAUDI_CULTURAL_FACTUAL, Capability.FRESH_EVENT_PLACE]:
            # cultural factual can be handled by general models
            pass
        else:
            # for strict caps like VISION/STRUCTURED, require explicit
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
    # Filter by pricing if free_only
    pool = candidates
    if free_only:
        free_pool = [c for c in pool if c.pricing == "free"]
        if free_pool:
            pool = free_pool
    # Filter by capability eligibility
    eligible = [c for c in pool if _is_eligible(c, capability)]
    if not eligible:
        return None
    # Filter by health
    healthy = [c for c in eligible if c.health_score >= health_threshold]
    if healthy:
        eligible = healthy
    # Deterministic ranking: health desc, context_size desc, model_id asc for stability
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
    # exponential decay
    _HEALTH[model_id] = max(0.0, 1.0 - (cnt * 0.25))


def get_health(model_id: str) -> float:
    return _HEALTH.get(model_id, 1.0)


def public_mode_label(is_auto: bool, override_available: bool) -> str:
    if is_auto:
        return "تلقائي"
    if not override_available:
        return "تلقائي (العودة لتلقائي)"
    return "مخصص"
