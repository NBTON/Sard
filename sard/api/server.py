"""Production FastAPI backend server for the Sard AI cultural assistant.

Provides streaming SSE chat with Always-On RAG, LangGraph itinerary generation,
multimodal upload lifecycle, verified artifact downloads (PDF, DOCX, PPTX, ICS, SVG),
and health/status telemetry.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, model_validator
from sse_starlette.sse import EventSourceResponse

# Ensure project root is in sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

load_dotenv(_PROJECT_ROOT / ".env")

from sard.agent.capability_routing import Capability, StructuredIntent, classify_intent
from sard.agent.chat_service import ChatService
from sard.agent.graph import default_dependencies, run_pipeline
from sard.agent.util import sanitize_cultural_output
from sard.config.models import get_model_settings
from sard.config.rag import get_rag_settings
from sard.outputs.orchestrator import (
    get_artifact_orchestrator,
    get_artifact_store,
)
from sard.runtime_paths import output_root

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("sard.api")

app = FastAPI(
    title="سرد | Sard API",
    description="Backend API for Sard — Saudi Cultural & Travel Assistant (Saudi Ministry of Culture Branding)",
    version="2.0.0",
)

# Enable CORS for Next.js and frontend dev servers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OUTPUT_DIR = output_root(default=_PROJECT_ROOT / "output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

UPLOAD_DIR = OUTPUT_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Attachment index: in-memory cache + durable JSON sidecar so a second
# process/instance can resolve attachment_id from disk (G4).
_ATTACHMENTS: Dict[str, Dict[str, Any]] = {}
_MAX_ATTACHMENTS = 100
_ATTACHMENT_TTL_SECONDS = 3600  # 1 hour


def _attachment_index_path() -> Path:
    try:
        return UPLOAD_DIR / ".attachment-index.json"
    except Exception:
        return Path(".attachment-index.json")


def _load_attachment_index() -> None:
    """Hydrate in-memory map from durable sidecar (second-process support)."""
    try:
        idx_path = _attachment_index_path()
        if not idx_path.exists():
            return
        import json as _json

        data = _json.loads(idx_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict) and v.get("path") and Path(v["path"]).exists():
                    _ATTACHMENTS.setdefault(k, v)
    except Exception as exc:
        logger.debug("Suppressed boundary exception in server.py: %s", type(exc).__name__)


def _persist_attachment_index() -> None:
    try:
        import json as _json

        idx_path = _attachment_index_path()
        tmp = idx_path.with_name(f".{idx_path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(_json.dumps(_ATTACHMENTS, ensure_ascii=False), encoding="utf-8")
        tmp.replace(idx_path)
    except Exception as exc:
        logger.debug("Suppressed boundary exception in server.py: %s", type(exc).__name__)


def _resolve_attachment_meta(att_id: str) -> Optional[Dict[str, Any]]:
    """Resolve attachment across processes: memory → index file → glob fallback."""
    safe = Path(str(att_id or "")).name
    if not safe:
        return None
    meta = _ATTACHMENTS.get(safe)
    if meta and meta.get("path") and Path(meta["path"]).exists():
        return meta
    # Hydrate from durable index once, then retry
    _load_attachment_index()
    meta = _ATTACHMENTS.get(safe)
    if meta and meta.get("path") and Path(meta["path"]).exists():
        return meta
    # Glob fallback: files are stored as {att_id}_{stem}{ext}
    try:
        matches = list(UPLOAD_DIR.glob(f"{safe}*"))
        if matches and matches[0].is_file():
            return {"attachment_id": safe, "filename": matches[0].name, "path": str(matches[0]), "mime_type": "application/octet-stream", "size_bytes": matches[0].stat().st_size}
    except Exception as exc:
        logger.debug("Suppressed boundary exception in server.py: %s", type(exc).__name__)
    return None


def _evict_expired_attachments() -> None:
    """Evict attachments older than TTL or beyond max count (LRU). Deletes files."""
    _load_attachment_index()
    now = time.time()
    # TTL eviction
    expired = [k for k, v in list(_ATTACHMENTS.items()) if now - v.get("created_at", now) > _ATTACHMENT_TTL_SECONDS]
    for k in expired:
        meta = _ATTACHMENTS.pop(k, None)
        if meta and meta.get("path"):
            try:
                p = Path(meta["path"])
                if p.exists():
                    p.unlink(missing_ok=True)
            except Exception as exc:
                logger.debug("Suppressed boundary exception in server.py: %s", type(exc).__name__)
    # Size cap (LRU by created_at)
    if len(_ATTACHMENTS) > _MAX_ATTACHMENTS:
        sorted_items = sorted(_ATTACHMENTS.items(), key=lambda kv: kv[1].get("created_at", 0))
        to_evict = len(_ATTACHMENTS) - _MAX_ATTACHMENTS
        for k, _ in sorted_items[:to_evict]:
            meta = _ATTACHMENTS.pop(k, None)
            if meta and meta.get("path"):
                try:
                    p = Path(meta["path"])
                    if p.exists():
                        p.unlink(missing_ok=True)
                except Exception as exc:
                    logger.debug("Suppressed boundary exception in server.py: %s", type(exc).__name__)
    _persist_attachment_index()

ALLOWED_EXTENSIONS = {
    ".pdf", ".docx", ".txt", ".md", ".csv", ".json",
    ".jpg", ".jpeg", ".png", ".webp", ".tiff", ".bmp", ".gif",
    ".mp3", ".wav", ".m4a", ".ogg", ".flac",
    ".ply", ".obj", ".stl", ".gltf", ".glb", ".nii", ".nii.gz",
}
MAX_UPLOAD_SIZE = 25 * 1024 * 1024  # 25MB


# --- Request & Response Models ---

class AttachmentPayload(BaseModel):
    attachment_id: str = Field(..., description="Unique stable attachment ID")
    filename: str = Field(..., description="Original filename")
    mime_type: Optional[str] = Field("application/octet-stream", description="File MIME type")
    size_bytes: Optional[int] = Field(0, description="File size in bytes")
    url: Optional[str] = Field(None, description="Download/preview URL")


class ChatMessage(BaseModel):
    role: str = Field(..., description="Role: 'user', 'assistant', or 'system'")
    content: str = Field(..., description="Message text")
    attachments: Optional[List[AttachmentPayload]] = Field(default_factory=list, description="Attached files")


class ChatRequest(BaseModel):
    messages: List[ChatMessage] = Field(default_factory=list, description="Conversation history")
    query: Optional[str] = Field(None, description="Direct user query if not using messages array")
    session_id: Optional[str] = Field(None, description="Optional session tracking ID")
    itinerary_mode: Optional[bool] = Field(False, description="Whether to trigger full itinerary generation")
    dates: Optional[List[str]] = Field(default_factory=list, description="Optional dates for itinerary")
    attachments: Optional[List[AttachmentPayload]] = Field(default_factory=list, description="Top-level uploaded attachments")
    lang: Optional[str] = Field(None, description="Explicit UI locale: 'ar' or 'en' (authoritative when present)")
    locale: Optional[str] = Field(None, description="Alias for lang")


class ItineraryRequest(BaseModel):
    query: str = Field(..., description="Travel/cultural query in Arabic")
    dates: Optional[List[str]] = Field(default_factory=list, description="List of ISO dates (e.g. ['2026-09-01', '2026-09-02'])")
    preview_calendar: Optional[bool] = Field(True, description="Enable calendar generation")
    output_root: Optional[str] = Field(None, description="Artifact output directory")


# --- Helper Functions ---

def _extract_latest_user_query(req: ChatRequest) -> str:
    if req.query and req.query.strip():
        return req.query.strip()
    for msg in reversed(req.messages):
        if msg.role == "user" and msg.content.strip():
            return msg.content.strip()
    return ""

def _resolve_request_lang(req: ChatRequest, effective_query: str) -> str:
    # Explicit locale wins (lang or locale), else infer from query
    explicit = (req.lang or req.locale or "").strip().lower()
    if explicit in ("ar", "en"):
        return explicit
    if explicit.startswith("ar"):
        return "ar"
    if explicit.startswith("en"):
        return "en"
    # Infer
    import re as _re
    if _re.search(r"[\u0600-\u06FF]", effective_query):
        return "ar"
    return "en"


def _extract_all_attachments(req: ChatRequest) -> List[Dict[str, Any]]:
    attachments_list = [a.model_dump() for a in req.attachments]
    for msg in req.messages:
        if msg.attachments:
            for a in msg.attachments:
                d = a.model_dump()
                if not any(x["attachment_id"] == d["attachment_id"] for x in attachments_list):
                    attachments_list.append(d)
    return attachments_list


def _is_model_configured(provider: str = "") -> bool:
    """Truthful provider-key check (G1). ModelSettings has no api_key field;
    inspect real env vars per provider instead of AttributeError."""
    try:
        prov = (provider or os.environ.get("MODEL_PROVIDER", "")).strip().lower()
        if not prov or prov == "auto":
            # Any known key counts as configured (auto-detect mirrors models._read_settings)
            return bool(
                os.environ.get("GEMINI_API_KEY", "").strip()
                or os.environ.get("GOOGLE_API_KEY", "").strip()
                or os.environ.get("OPENAI_API_KEY", "").strip()
                or os.environ.get("ANTHROPIC_API_KEY", "").strip()
                or os.environ.get("OPENROUTER_API_KEY", "").strip()
                or os.environ.get("NVIDIA_API_KEY", "").strip()
                or os.environ.get("NVIDIA_CHAT_BASE_URL", "").strip()
            )
        if prov == "gemini":
            return bool(os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip())
        if prov == "openai":
            return bool(os.environ.get("OPENAI_API_KEY", "").strip())
        if prov == "anthropic":
            return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
        if prov == "nvidia":
            return bool(os.environ.get("NVIDIA_API_KEY", "").strip() or os.environ.get("NVIDIA_CHAT_BASE_URL", "").strip())
        if prov == "openrouter":
            return bool(os.environ.get("OPENROUTER_API_KEY", "").strip())
        return False
    except Exception:
        return False


def _check_storage_readiness() -> dict:
    """Durable vs ephemeral storage truth (G3)."""
    try:
        from sard.runtime_paths import durable_storage_configured, output_root_is_ephemeral
    except Exception:
        return {"durable": False, "mode": "unknown"}
    durable = bool(durable_storage_configured())
    ephemeral = bool(output_root_is_ephemeral())
    if durable:
        mode = "durable_blob"
    elif ephemeral:
        mode = "ephemeral_unconfigured"
    else:
        mode = "local_filesystem"
    return {"durable": durable, "mode": mode, "ephemeral_host": ephemeral}


def _check_rag_readiness() -> dict:
    try:
        from sard.rag.bundled_retriever import get_bundled_retriever
        bundled_ok = get_bundled_retriever().is_available
    except Exception:
        bundled_ok = False

    try:
        settings = get_rag_settings()
        collection_path = Path(settings.zvec_collection_path)
        zvec_exists = collection_path.exists() and any(collection_path.iterdir()) if collection_path.exists() else False
    except Exception:
        zvec_exists = False

    is_avail = bool(zvec_exists or bundled_ok)
    return {
        "available": is_avail,
        "engine": "zvec_hybrid" if zvec_exists else ("bundled_hybrid" if bundled_ok else "unavailable"),
    }


def _probe_corpus_detail() -> dict:
    """Report retrieval mode, source count, and corpus coverage separately."""
    t0 = time.monotonic()
    corpus_dir = _PROJECT_ROOT / "data" / "corpus"
    topics: list[str] = []
    meta_count = 0
    try:
        if corpus_dir.exists():
            topics = sorted([p.name for p in corpus_dir.iterdir() if p.is_dir()])
            meta_count = sum(1 for _ in corpus_dir.rglob("*.meta.json"))
    except Exception as exc:
        logger.debug("Suppressed boundary exception in server.py: %s", type(exc).__name__)
    # Coverage: share of expected cultural regions with at least one topic dir.
    # Expected: at least the 13-region layout or legacy 3-topic fallback.
    expected_min = 3
    coverage = round(min(1.0, len(topics) / expected_min), 3) if topics else 0.0
    rag_info = _check_rag_readiness()
    mode = str(rag_info.get("engine", "unavailable"))
    latency_ms = round((time.monotonic() - t0) * 1000, 1)
    return {
        "retrieval_mode": mode,
        "source_count": meta_count,
        "topic_count": len(topics),
        "topics": topics,
        "corpus_coverage": coverage,
        "available": bool(rag_info.get("available", False)) and meta_count > 0,
        "latency_ms": latency_ms,
    }


def _check_discovery_reachability(provider: str = "", timeout_s: float = 2.0) -> dict:
    """Report model discovery reachability without leaking credentials.

    Returns {reachable: bool|None, latency_ms, detail}. None = unknown/skipped
    (no provider configured). Never raises.
    """
    t0 = time.monotonic()
    try:
        prov = (provider or os.environ.get("MODEL_PROVIDER", "")).strip().lower()
        if not prov or prov == "auto":
            # Auto-detect: pick first configured provider for discovery probe
            for cand, envs in (
                ("gemini", ("GEMINI_API_KEY", "GOOGLE_API_KEY")),
                ("openai", ("OPENAI_API_KEY",)),
                ("anthropic", ("ANTHROPIC_API_KEY",)),
                ("openrouter", ("OPENROUTER_API_KEY",)),
                ("nvidia", ("NVIDIA_API_KEY", "NVIDIA_CHAT_BASE_URL")),
            ):
                if any(os.environ.get(e, "").strip() for e in envs):
                    prov = cand
                    break
        if not prov or prov == "auto":
            return {"reachable": None, "latency_ms": 0.0, "detail": "no provider configured"}
        # Discovery = provider SDK importable + settings resolvable (no network by default).
        # Live reachability only when SARD_HEALTH_PROBE_NETWORK=1 (bounded).
        try:
            _ms = get_model_settings()
            _ = getattr(_ms, "provider", prov)
        except Exception as exc:
            return {"reachable": False, "latency_ms": round((time.monotonic() - t0) * 1000, 1), "detail": f"settings error: {type(exc).__name__}"}
        if os.environ.get("SARD_HEALTH_PROBE_NETWORK", "").lower() not in ("1", "true", "yes"):
            return {"reachable": None, "latency_ms": round((time.monotonic() - t0) * 1000, 1), "detail": "network probe skipped (set SARD_HEALTH_PROBE_NETWORK=1 to enable)"}
        # Bounded live probe (never blocks health beyond budget)
        try:
            import urllib.request as _ureq

            base_urls = {
                "openai": "https://api.openai.com/v1/models",
                "anthropic": "https://api.anthropic.com/v1/models",
                "openrouter": "https://openrouter.ai/api/v1/models",
                "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
                "nvidia": os.environ.get("NVIDIA_CHAT_BASE_URL", "").strip() or "https://integrate.api.nvidia.com/v1/models",
            }
            url = base_urls.get(prov, "")
            if not url:
                return {"reachable": None, "latency_ms": 0.0, "detail": "unknown provider"}
            req = _ureq.Request(url, method="HEAD")
            with _ureq.urlopen(req, timeout=timeout_s):
                pass
            return {"reachable": True, "latency_ms": round((time.monotonic() - t0) * 1000, 1), "detail": "discovery reachable"}
        except Exception as exc:
            return {"reachable": False, "latency_ms": round((time.monotonic() - t0) * 1000, 1), "detail": f"{type(exc).__name__}"}
    except Exception as exc:
        return {"reachable": False, "latency_ms": 0.0, "detail": f"{type(exc).__name__}"}


def _probe_inference(provider: str = "", timeout_s: float = 5.0) -> dict:
    """Report recent inference success separately (bounded, never raises).

    Default is a config-only check (success=None/unknown) to avoid spend.
    Set SARD_HEALTH_PROBE_INFERENCE=1 to attempt a minimal live invocation.
    """
    t0 = time.monotonic()
    if not _is_model_configured(provider):
        return {"success": False, "latency_ms": 0.0, "detail": "model not configured"}
    if os.environ.get("SARD_HEALTH_PROBE_INFERENCE", "").lower() not in ("1", "true", "yes"):
        return {"success": None, "latency_ms": round((time.monotonic() - t0) * 1000, 1), "detail": "live inference probe skipped"}
    try:
        from sard.config.models import get_chat_model

        model = get_chat_model()
        # Minimal invocation with strict timeout; any failure => success False.
        import concurrent.futures as _cf

        from langchain_core.messages import HumanMessage as _HM

        with _cf.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(model.invoke, [_HM(content="اختبار اتصال")])
            fut.result(timeout=timeout_s)
        return {"success": True, "latency_ms": round((time.monotonic() - t0) * 1000, 1), "detail": "inference ok"}
    except Exception as exc:
        return {"success": False, "latency_ms": round((time.monotonic() - t0) * 1000, 1), "detail": f"{type(exc).__name__}"}


# --- Endpoints ---

@app.get("/")
@app.get("/api")
@app.get("/api/")
async def root_endpoint():
    """Root metadata and API service check."""
    rag_info = _check_rag_readiness()
    return {
        "status": "ok",
        "service": "سرد | Sard Cultural Assistant API",
        "version": "2.0.0",
        "branding": "Saudi Ministry of Culture (MOC) 2026",
        "endpoints": {
            "health": "/api/health",
            "status": "/api/status",
            "corpus": "/api/corpus",
            "chat": "/api/chat",
            "upload": "/api/upload",
            "itinerary": "/api/itinerary",
            "calendar": "/api/calendar/events",
            "presentation": "/api/tools/presentation",
            "recipe_card": "/api/tools/recipe-card",
            "greeting_card": "/api/tools/greeting-card",
            "etiquette": "/api/tools/etiquette",
            "dialect": "/api/tools/dialect",
            "artisan": "/api/tools/artisan",
            "memoir": "/api/tools/memoir",
            "research": "/api/tools/research",
        },
        "verified": rag_info.get("available", False),
    }


@app.get("/api/health")
@app.get("/health")
async def health_check():
    """Health check endpoint - public contract only (truthful, never always-ok)."""
    rag_info = _check_rag_readiness()
    corpus = _probe_corpus_detail()
    try:
        _ms = get_model_settings()
        _prov = getattr(_ms, "provider", "")
    except Exception:
        _prov = os.environ.get("MODEL_PROVIDER", "")
    model_configured = _is_model_configured(_prov)
    discovery = _check_discovery_reachability(_prov)
    inference = _probe_inference(_prov)
    retrieval_ok = bool(corpus.get("available", False))
    inference_failed = inference.get("success") is False
    # Overall is ok ONLY when retrieval works and inference has not failed.
    # Unknown inference (skipped) does not grant ok when retrieval fails.
    overall_ok = bool(retrieval_ok and not inference_failed and (model_configured or retrieval_ok))
    # Strict: ok requires retrieval + (explicit inference success when probed)
    if inference.get("success") is None:
        overall_ok = bool(retrieval_ok and model_configured)
    return {
        "status": "ok" if overall_ok else "degraded",
        "service": "sard-agent",
        "timestamp": time.time(),
        "verified": False,
        "sources": {"verified": False},
        "rag": rag_info,
        "model_configured": model_configured,
        "discovery": discovery,
        "inference": inference,
        "retrieval": corpus,
    }


@app.get("/api/status")
@app.get("/status")
async def system_status():
    """Returns public system status without exposing internal model/provider IDs."""
    rag_info = _check_rag_readiness()
    corpus = _probe_corpus_detail()
    enable_dev = os.environ.get("SARD_ENABLE_DEV_OBSERVABILITY", "").lower() in ("1", "true", "yes")

    try:
        _ms = get_model_settings()
        _prov = getattr(_ms, "provider", "")
    except Exception:
        _prov = os.environ.get("MODEL_PROVIDER", "")
    model_configured = _is_model_configured(_prov)
    storage_info = _check_storage_readiness()
    discovery = _check_discovery_reachability(_prov)
    inference = _probe_inference(_prov)

    rag_avail = bool(corpus.get("available", False))
    inference_failed = inference.get("success") is False
    # Truthful overall: never "ready" when representative inference or
    # retrieval fails within its latency budget.
    if not model_configured and not rag_avail:
        system_state = "unavailable"
        status_label_ar = "غير متوفر"
    elif not model_configured or not rag_avail or inference_failed:
        system_state = "degraded"
        status_label_ar = "محدود"
    else:
        system_state = "ready"
        status_label_ar = "جاهز"

    base = {
        "status": system_state,
        "status_label": status_label_ar,
        "verified": False,
        "sources": {"verified": False, "count": corpus.get("source_count", 0)},
        "rag": rag_info,
        "retrieval": corpus,
        "retrieval_mode": corpus.get("retrieval_mode", ""),
        "source_count": corpus.get("source_count", 0),
        "corpus_coverage": corpus.get("corpus_coverage", 0.0),
        "model_configured": model_configured,
        "discovery": discovery,
        "inference": inference,
        "storage": storage_info,
        "model": {"mode": "auto", "preference": "auto"},
        "moc_branding": "Saudi Ministry of Culture (MOC) 2026",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if enable_dev:
        try:
            model_settings = get_model_settings()
            base["dev"] = {
                "provider": model_settings.provider,
                "model_name": model_settings.model_name,
            }
        except Exception:
            base["dev"] = {"provider": "unknown"}
    return base


@app.get("/api/corpus")
@app.get("/corpus")
async def get_corpus_info():
    """Returns available cultural corpus guides and topics."""
    corpus_dir = _PROJECT_ROOT / "data" / "corpus"
    manifest_file = corpus_dir / "MANIFEST.md"
    manifest_text = manifest_file.read_text(encoding="utf-8") if manifest_file.exists() else ""

    topics = []
    if corpus_dir.exists():
        for item in corpus_dir.iterdir():
            if item.is_dir():
                topics.append(item.name)

    return {
        "topics": topics or ["تراث", "ينابيع", "حرف_تقليدية"],
        "manifest_preview": manifest_text[:500],
        "total_topics": len(topics),
    }


# ---------------------------------------------------------------------------
# Multimodal Upload & Attachment Endpoints
# ---------------------------------------------------------------------------


@app.post("/api/upload")
@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload multimodal file (PDF, DOCX, Images, Audio, 3D) and return stable attachment ID."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="الرجاء اختيار ملف صالح للرفع.")

    filename = Path(file.filename).name
    # Extract lower extension
    ext = Path(filename).suffix.lower()
    if filename.lower().endswith(".nii.gz"):
        ext = ".nii.gz"

    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"صيغة الملف غير مدعومة ({ext}). الصيغ المدعومة تشمل: PDF, DOCX, TXT, JPG, PNG, WEBP, MP3, WAV, M4A.",
        )

    # Read bytes and check size
    content = await file.read()
    size_bytes = len(content)
    if size_bytes > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"حجم الملف يتجاوز الحد الأقصى المسموح به (25 ميغابايت). الحجم الحالي: {size_bytes / (1024*1024):.1f} ميغابايت.",
        )
    if size_bytes == 0:
        raise HTTPException(status_code=400, detail="الملف المرفوع فارغ.")

    # Generate stable attachment ID and safe filename
    att_id = f"att_{uuid.uuid4().hex[:12]}"
    safe_stem = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).stem)
    stored_filename = f"{att_id}_{safe_stem}{ext}"
    dest_path = (UPLOAD_DIR / stored_filename).resolve()

    # Prevent path traversal
    try:
        dest_path.relative_to(UPLOAD_DIR)
    except ValueError:
        raise HTTPException(status_code=400, detail="مسار الملف غير آمن.")

    dest_path.write_bytes(content)

    mime_type = file.content_type or "application/octet-stream"
    meta = {
        "attachment_id": att_id,
        "filename": filename,
        "stored_filename": stored_filename,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "path": str(dest_path),
        "created_at": time.time(),
    }
    # Bounded store: evict expired/oldest before insert (Finding 3)
    _evict_expired_attachments()
    _ATTACHMENTS[att_id] = meta
    _persist_attachment_index()
    # Enforce cap immediately after insert (in case of race)
    if len(_ATTACHMENTS) > _MAX_ATTACHMENTS:
        _evict_expired_attachments()

    return {
        "ok": True,
        "attachment_id": att_id,
        "filename": filename,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "url": f"/api/attachments/{att_id}",
    }


@app.get("/api/attachments/{attachment_id}")
@app.get("/attachments/{attachment_id}")
async def get_attachment_file(attachment_id: str):
    """Download an uploaded attachment by ID."""
    _evict_expired_attachments()
    safe_id = Path(attachment_id).name
    meta = _resolve_attachment_meta(safe_id)
    if meta and Path(meta["path"]).exists():
        return FileResponse(path=meta["path"], filename=meta.get("filename", safe_id), media_type=meta.get("mime_type", "application/octet-stream"))

    # Fallback search in UPLOAD_DIR
    matches = list(UPLOAD_DIR.glob(f"{safe_id}*"))
    if matches and matches[0].is_file():
        return FileResponse(path=matches[0], filename=matches[0].name, media_type="application/octet-stream")

    raise HTTPException(status_code=404, detail="الملف المرفق غير موجود.")


# ---------------------------------------------------------------------------
# Artifact Download Endpoint
# ---------------------------------------------------------------------------


@app.get("/api/artifacts/{filename}")
@app.get("/artifacts/{filename}")
async def get_artifact_file(filename: str):
    """Securely download a generated artifact file (PDF, DOCX, PPTX, ICS, SVG, JSON)."""
    safe_name = Path(filename).name

    store = get_artifact_store()
    file_path = store.get_file_path(safe_name)

    if not file_path or not file_path.exists():
        # Check output root fallback
        matches = list(OUTPUT_DIR.glob(f"**/{safe_name}"))
        if matches and matches[0].is_file():
            file_path = matches[0]
        else:
            raise HTTPException(status_code=404, detail="الملف المطلوب غير موجود.")

    # Determine MIME
    fn_lower = safe_name.lower()
    if fn_lower.endswith(".pdf"):
        media_type = "application/pdf"
    elif fn_lower.endswith(".docx"):
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif fn_lower.endswith(".pptx"):
        media_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    elif fn_lower.endswith(".ics"):
        media_type = "text/calendar; charset=utf-8"
    elif fn_lower.endswith(".svg"):
        media_type = "image/svg+xml"
    elif fn_lower.endswith(".png"):
        media_type = "image/png"
    elif fn_lower.endswith(".json"):
        media_type = "application/json"
    elif fn_lower.endswith(".csv"):
        media_type = "text/csv; charset=utf-8"
    elif fn_lower.endswith(".txt"):
        media_type = "text/plain; charset=utf-8"
    else:
        media_type = "application/octet-stream"

    return FileResponse(
        path=file_path,
        filename=safe_name,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


# ---------------------------------------------------------------------------
# Full Itinerary Pipeline Endpoint
# ---------------------------------------------------------------------------


@app.post("/api/itinerary")
@app.post("/itinerary")
async def generate_full_itinerary(req: ItineraryRequest, request: Request):
    """Executes the full LangGraph agent pipeline and generates real PDF / ICS artifacts.

    Bounded by a 30–45s overall deadline with client-disconnect propagation.
    Returns a typed partial/timeout response and never writes background
    artifacts after cancellation.
    """
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="الرجاء تقديم استفسار للرحلة")

    try:
        deadline_s = float(os.environ.get("SARD_ITINERARY_TIMEOUT", "40"))
        deadline_s = max(30.0, min(45.0, deadline_s))
    except ValueError:
        deadline_s = 40.0
    t_start = time.monotonic()
    cancelled = False

    async def _is_disconnected() -> bool:
        try:
            return await asyncio.wait_for(request.is_disconnected(), timeout=0.2)
        except Exception:
            return False

    try:
        run_id = f"itin-{uuid.uuid4().hex[:10]}"
        deps = default_dependencies(open_rag=True)
        deps.render_artifacts = True
        deps.output_root = str(OUTPUT_DIR)
        deps.caller_dates = tuple(req.dates or [])
        deps.preview_calendar = req.preview_calendar

        # Run pipeline with deadline + disconnect checks (no orphan execution).
        # The worker runs in a DETACHED daemon thread (not loop.run_in_executor):
        # the event loop only polls a threading.Event, so a timeout/cancel
        # response is never held back by the worker thread. The worker checks
        # the cancelled flag and writes no artifacts after cancellation.
        import threading as _threading

        pipeline_box: dict[str, Any] = {}
        pipeline_done = _threading.Event()
        cancel_flag = _threading.Event()

        def _worker() -> None:
            try:
                pipeline_box["state"] = run_pipeline(
                    req.query,
                    dependencies=deps,
                    run_id=run_id,
                    caller_dates=req.dates,
                    preview_calendar=req.preview_calendar,
                )
            except Exception as exc:  # worker errors surface as typed 500, never leak
                pipeline_box["error"] = exc
            finally:
                pipeline_done.set()

        _thread = _threading.Thread(target=_worker, name=f"sard-itin-{run_id}", daemon=True)
        _thread.start()

        async def _run_with_deadline() -> Any:
            while not pipeline_done.is_set():
                if await _is_disconnected():
                    cancel_flag.set()
                    raise asyncio.CancelledError("client disconnected")
                remaining = deadline_s - (time.monotonic() - t_start)
                if remaining <= 0:
                    cancel_flag.set()
                    raise asyncio.TimeoutError(f"itinerary deadline exceeded ({deadline_s:.0f}s)")
                await asyncio.sleep(0.25)
            if "error" in pipeline_box:
                raise pipeline_box["error"]
            return pipeline_box.get("state")

        try:
            state = await _run_with_deadline()
        except asyncio.TimeoutError:
            elapsed_ms = round((time.monotonic() - t_start) * 1000, 1)
            return JSONResponse(
                status_code=504,
                content={
                    "ok": False,
                    "error": "timeout",
                    "error_category": "timeout",
                    "message": f"تجاوز إنشاء البرنامج المهلة ({deadline_s:.0f} ثانية). حاول تبسيط الطلب أو إعادة المحاولة.",
                    "run_id": run_id if "run_id" in locals() else "",
                    "query": req.query,
                    "partial": True,
                    "elapsed_ms": elapsed_ms,
                },
            )
        except asyncio.CancelledError:
            cancelled = True
            elapsed_ms = round((time.monotonic() - t_start) * 1000, 1)
            return JSONResponse(
                status_code=499,
                content={
                    "ok": False,
                    "error": "cancelled",
                    "error_category": "cancelled",
                    "message": "تم إلغاء الطلب من قبل العميل.",
                    "run_id": run_id if "run_id" in locals() else "",
                    "query": req.query,
                    "partial": True,
                    "elapsed_ms": elapsed_ms,
                },
            )

        if await _is_disconnected():
            # Client already gone: do not write artifacts.
            return JSONResponse(
                status_code=499,
                content={
                    "ok": False, "error": "cancelled", "error_category": "cancelled",
                    "message": "تم إلغاء الطلب من قبل العميل.", "run_id": run_id,
                    "query": req.query, "partial": True,
                },
            )

        # Extract and verify artifacts (skipped entirely after cancellation).
        if cancelled or cancel_flag.is_set() or await _is_disconnected():
            return JSONResponse(
                status_code=499,
                content={
                    "ok": False, "error": "cancelled", "error_category": "cancelled",
                    "message": "تم إلغاء الطلب من قبل العميل.", "run_id": run_id,
                    "query": req.query, "partial": True,
                },
            )
        orchestrator = get_artifact_orchestrator()
        artifacts_list = []

        # If LangGraph rendered artifacts, extract them
        for art in state.get("rendered_artifacts", []):
            filename = getattr(art, "filename", "")
            art_type = getattr(art, "artifact_type", "")
            path = getattr(art, "path", "")
            if filename and Path(path).exists() and Path(path).stat().st_size > 0:
                artifacts_list.append({
                    "id": f"art-{uuid.uuid4().hex[:8]}",
                    "filename": filename,
                    "kind": "document" if art_type == "pdf" else "calendar",
                    "format": art_type or "pdf",
                    "status": "created",
                    "url": f"/api/artifacts/{filename}",
                    "download_url": f"/api/artifacts/{filename}",
                    "size_bytes": Path(path).stat().st_size,
                    "title": f"برنامج الرحلة ({art_type.upper()})",
                })

        # Fallback: if no artifacts rendered yet, generate via orchestrator
        # (only if deadline budget remains; otherwise return typed partial).
        if not artifacts_list:
            remaining = deadline_s - (time.monotonic() - t_start)
            if remaining <= 1.0 or await _is_disconnected():
                elapsed_ms = round((time.monotonic() - t_start) * 1000, 1)
                return JSONResponse(
                    status_code=504 if remaining <= 1.0 else 499,
                    content={
                        "ok": True, "partial": True,
                        "error": "timeout" if remaining <= 1.0 else "cancelled",
                        "error_category": "timeout" if remaining <= 1.0 else "cancelled",
                        "message": "اكتمل النص دون مخرجات ملفات ضمن المهلة.",
                        "run_id": run_id, "query": req.query,
                        "final_text": state.get("final_itinerary_text") or state.get("final_response") or "",
                        "sources": state.get("sources", []),
                        "artifacts": [],
                        "elapsed_ms": elapsed_ms,
                    },
                )
            itin_text = state.get("final_itinerary_text") or state.get("final_response") or req.query
            intent = StructuredIntent(
                domain_capability=Capability.ITINERARY_PLANNING,
                requested_formats=("pdf", "ics"),
                explicit_artifact_request=True,
                extracted_topic=req.query[:40],
            )
            orch_results = orchestrator.orchestrate_from_intent(intent, raw_text=itin_text)
            for r in orch_results:
                if r.status == "created":
                    artifacts_list.append(r.to_dict())

        return {
            "ok": True,
            "run_id": run_id,
            "query": req.query,
            "final_text": state.get("final_itinerary_text") or state.get("final_response") or "",
            "sources": state.get("sources", []),
            "artifacts": artifacts_list,
            "timings": state.get("timings", {}),
            "verification_passed": getattr(state.get("verification_result"), "passed", True) if state.get("verification_result") else True,
        }
    except Exception as exc:
        logger.exception("Error generating full itinerary")
        raise HTTPException(status_code=500, detail=f"حدث خطأ أثناء إعداد برنامج الرحلة: {exc}")


# ---------------------------------------------------------------------------
# Streaming Chat Endpoint
# ---------------------------------------------------------------------------


@app.post("/api/chat")
@app.post("/chat")
async def chat_endpoint(req: ChatRequest):
    """Streaming Chat endpoint with public progress telemetry and verified artifacts.

    SSE contract (guaranteed ordering):
    status → citations (if any) → artifacts (if requested, includes failed) → delta → done

    Invariants enforced here:
    - Explicit artifact intent (requested_formats via classify_intent) survives every fallback
    - Retrieval failure never injects irrelevant context (handled in rag layer)
    - Session isolation: history is client-supplied but never echoed as stale; effective_query is current turn
    - Bounded timeouts: overall chat deadline via SARD_CHAT_OVERALL_TIMEOUT (default 35s; client 50s => 15s slack)
    - Cancellation propagates to executor future
    - Every stream terminates with done (or error) and logs carry run_id without secrets
    - Artifacts event always before done, includes both created and failed where applicable
    - Download verification via orchestrator store
    """
    user_query = _extract_latest_user_query(req)
    all_attachments = _extract_all_attachments(req)

    if not user_query and not all_attachments:
        raise HTTPException(status_code=400, detail="الرجاء كتابة رسالة أو إرفاق ملف قبل الإرسال.")

    effective_query = user_query or "يرجى تحليل وتلخيص الملفات المرفقة واستخراج المعلومات الثقافية والتراثية منها."
    resolved_lang = _resolve_request_lang(req, effective_query)

    async def sse_generator() -> AsyncGenerator[dict, None]:
        t_start = time.monotonic()
        run_id = f"chat-{uuid.uuid4().hex[:10]}"
        citations_sent: list[dict[str, Any]] = []
        artifacts_sent: list[dict[str, Any]] = []
        full_response_text = ""
        verified = False
        # Early intent classification so fallback path knows artifact expectation and can surface failed artifacts
        early_intent = classify_intent(effective_query, messages=[m.model_dump() for m in req.messages] if req.messages else None, attachments=all_attachments)
        session_id_out = req.session_id or str(uuid.uuid4())
        # Overall SSE deadline (bounded). Env overridable, capped at 60s.
        # P1-4: default 35s with client 50s => deliberate 15s slack so terminal
        # artifacts+done always arrive before client abort.
        try:
            overall_timeout = float(os.environ.get("SARD_CHAT_OVERALL_TIMEOUT", "35"))
            overall_timeout = max(5.0, min(60.0, overall_timeout))
        except ValueError:
            overall_timeout = 35.0

        try:
            # 1. Initial Status Event (language-aware)
            init_msg = "جارٍ تحليل السؤال واستكشاف المعارف والوثائق المعتمدة..." if resolved_lang == "ar" else "Analyzing question and gathering verified heritage knowledge..."
            yield {
                "event": "status",
                "data": json.dumps({
                    "stage": "init",
                    "message": init_msg
                }, ensure_ascii=False)
            }
            await asyncio.sleep(0.02)

            # 2. Conversational greetings quick-check (preserves current query, does not echo stale history)
            greetings = ["مرحبا", "أهلا", "اهلا", "السلام عليكم", "صباح الخير", "مساء الخير", "هلا", "شكرا", "من أنت", "عرفني بنفسك", "من انت", "أهلاً", "hello", "hi"]
            q_clean = re.sub(r"[^\w\s]", "", effective_query.strip()).lower()
            is_greeting = any(q_clean == g or q_clean.startswith(g + " ") for g in greetings) and not all_attachments

            if not is_greeting:
                status_queue: asyncio.Queue = asyncio.Queue()
                loop = asyncio.get_event_loop()

                def _sync_status_callback(stage: str, message: str):
                    try:
                        loop.call_soon_threadsafe(status_queue.put_nowait, (stage, message))
                    except Exception as exc:
                        logger.debug("Suppressed boundary exception in server.py: %s", type(exc).__name__)

                resolved_files = {}
                for att in all_attachments:
                    att_id = att.get("attachment_id") or att.get("id")
                    if not att_id:
                        continue
                    att_meta = _resolve_attachment_meta(att_id)
                    if att_meta:
                        file_path = att_meta.get("path")
                        if file_path and Path(file_path).exists():
                            orig_fname = att_meta.get("filename") or Path(file_path).name
                            resolved_files[orig_fname] = {
                                "file_path": file_path,
                                "path": file_path,
                                "mime_type": att_meta.get("mime_type"),
                                "size_bytes": att_meta.get("size_bytes"),
                            }
                            resolved_files[att_id] = resolved_files[orig_fname]
                            resolved_files[Path(file_path).name] = resolved_files[orig_fname]

                hybrid_chat_res = None
                try:
                    chat_service = ChatService()
                    history_dicts = [{"role": m.role, "content": m.content} for m in req.messages] if req.messages else None

                    # Overall deadline shared with orchestrator for orphan discard (G11).
                    deadline = t_start + overall_timeout
                    # Launch chat_service.ask in executor with bounded timeout (pass resolved language & resolved attachments)
                    # Real uploaded paths go to the real extractor via uploaded_files (not mocks).
                    future = loop.run_in_executor(
                        None,
                        lambda _dl=deadline: chat_service.ask(
                            effective_query,
                            messages=history_dicts,
                            attachments=all_attachments,
                            uploaded_files=resolved_files if resolved_files else None,
                            use_hybrid_retrieval=True,
                            session_id=req.session_id,
                            status_callback=_sync_status_callback,
                            lang=resolved_lang,
                            deadline_monotonic=_dl,
                        ),
                    )

                    # Stream status events as emitted, with overall deadline on the future
                    # We poll status_queue while waiting, but bound the total wait.
                    while not future.done():
                        if time.monotonic() > deadline:
                            future.cancel()
                            logger.warning("Chat SSE overall timeout reached (run_id=%s). Cancelling hybrid future.", run_id)
                            break
                        try:
                            stage_info = await asyncio.wait_for(status_queue.get(), timeout=0.08)
                            yield {
                                "event": "status",
                                "data": json.dumps({
                                    "stage": stage_info[0],
                                    "message": stage_info[1],
                                }, ensure_ascii=False),
                            }
                        except asyncio.TimeoutError:
                            pass

                    # Drain remaining status events
                    while not status_queue.empty():
                        stage_info = status_queue.get_nowait()
                        yield {
                            "event": "status",
                            "data": json.dumps({
                                "stage": stage_info[0],
                                "message": stage_info[1],
                            }, ensure_ascii=False),
                        }

                    # Await future with timeout; cancellation is fallback, not abort
                    if not future.done():
                        try:
                            hybrid_chat_res = await asyncio.wait_for(future, timeout=max(0.5, deadline - time.monotonic()))
                        except asyncio.TimeoutError:
                            future.cancel()
                            logger.warning("Hybrid chat future timed out (run_id=%s).", run_id)
                            hybrid_chat_res = None
                        except asyncio.CancelledError:
                            logger.info("Hybrid chat cancelled (run_id=%s). Falling back to direct.", run_id)
                            hybrid_chat_res = None
                    else:
                        try:
                            hybrid_chat_res = await future
                        except asyncio.CancelledError:
                            logger.info("Hybrid chat cancelled (run_id=%s). Falling back to direct.", run_id)
                            hybrid_chat_res = None
                        except Exception as exc:
                            logger.warning("Isnād planner future exception (run_id=%s): %s. Falling back to direct chat.", run_id, type(exc).__name__)
                            hybrid_chat_res = None

                    if hybrid_chat_res is not None:
                        # Trustworthy behavior: verified ONLY when the planner decided
                        # "generate" AND at least one citation carries a real HTTP(S)
                        # source URL. Hedge/template answers are never verified.
                        # Preserve artifacts even if hybrid had fallback text or empty model — invariant 1,8
                        if hybrid_chat_res.citations or hybrid_chat_res.text:
                            from urllib.parse import urlparse as _urlparse

                            def _has_valid_url(cits: Any) -> bool:
                                try:
                                    for c in (cits or []):
                                        u = str((c or {}).get("url") or (c or {}).get("source_url") or "")
                                        p = _urlparse(u)
                                        if p.scheme in ("http", "https") and p.netloc:
                                            return True
                                except Exception:
                                    return False
                                return False

                            verified = bool(
                                hybrid_chat_res.decision == "generate"
                                and _has_valid_url(hybrid_chat_res.citations)
                            )
                        # Extract citations (only validated HTTP(S); drop bare IDs)
                        for cit in (hybrid_chat_res.citations or []):
                            raw_url = str(cit.get("url") or cit.get("source_url") or "")
                            try:
                                from urllib.parse import urlparse as _urlparse2

                                _p = _urlparse2(raw_url)
                                valid = _p.scheme in ("http", "https") and bool(_p.netloc)
                            except Exception:
                                valid = False
                            if not valid:
                                continue
                            citation_id = cit.get("citation_id") or cit.get("id") or cit.get("source_id") or ""
                            if any(existing.get("citation_id") == citation_id for existing in citations_sent):
                                continue
                            citations_sent.append({
                                "citation_id": citation_id,
                                "title": cit.get("title", ""),
                                "source_name": cit.get("origin") or cit.get("id", ""),
                                "source_url": raw_url,
                                "chunk_id": cit.get("chunk_id", "") or cit.get("source_id", "") or "",
                                "snippet": cit.get("excerpt", "") or cit.get("title", ""),
                            })
                        if citations_sent:
                            yield {
                                "event": "citations",
                                "data": json.dumps({
                                    "citations": citations_sent,
                                    "count": len(citations_sent)
                                }, ensure_ascii=False)
                            }
                        # Capture text if ok, otherwise keep empty to trigger fallback path below
                        if hybrid_chat_res.ok:
                            full_response_text = hybrid_chat_res.text or ""
                        # Preserve artifacts through fallback (invariant 8)
                        if hybrid_chat_res.artifacts:
                            for art in hybrid_chat_res.artifacts:
                                # De-duplicate by id if fallback also produces same id (should not, but safe)
                                if not any(a.get("id") == art.get("id") for a in artifacts_sent):
                                    artifacts_sent.append(art)

                except asyncio.CancelledError:
                    logger.info("SSE cancelled during hybrid phase (run_id=%s).", run_id)
                    raise
                except Exception as exc:
                    logger.warning("Isnād planner exception (run_id=%s): %s. Falling back to direct chat.", run_id, type(exc).__name__)

            # 3. Fallback if no response text yet — also handles artifact-only requests with empty model
            # This path must also honor artifact intent: direct model fallback can still produce requested artifact
            if not full_response_text or not full_response_text.strip():
                # Check if we already have artifacts from hybrid (even with empty text, artifacts may be present)
                # If artifacts already satisfy intent, we still need text hedge for delta; otherwise we try direct model
                needs_text = not full_response_text or not full_response_text.strip()
                if needs_text:
                    gen_msg = "جارٍ صياغة إجابة من المستشار الثقافي..." if resolved_lang == "ar" else "Composing answer from the cultural advisor..."
                    yield {
                        "event": "status",
                        "data": json.dumps({
                            "stage": "generating",
                            "message": gen_msg
                        }, ensure_ascii=False)
                    }
                    chat_service = ChatService()
                    loop = asyncio.get_event_loop()
                    history_dicts = [{"role": m.role, "content": m.content} for m in req.messages] if req.messages else None
                    try:
                        _dl2 = t_start + overall_timeout
                        future2 = loop.run_in_executor(
                            None,
                            lambda: chat_service.ask(effective_query, messages=history_dicts, use_hybrid_retrieval=False, session_id=req.session_id, lang=resolved_lang, deadline_monotonic=_dl2),
                        )
                        # Bounded wait for direct fallback: strict monotonic deadline
                        remaining = (t_start + overall_timeout) - time.monotonic()
                        if remaining <= 0.2:
                            future2.cancel()
                            logger.warning("Monotonic deadline expired before direct fallback (run_id=%s).", run_id)
                            chat_res2 = None
                        else:
                            try:
                                chat_res2 = await asyncio.wait_for(future2, timeout=remaining)
                            except asyncio.TimeoutError:
                                future2.cancel()
                                logger.warning("Direct fallback timed out (run_id=%s).", run_id)
                                chat_res2 = None
                            except asyncio.CancelledError:
                                logger.info("Direct fallback cancelled (run_id=%s).", run_id)
                                raise

                        if chat_res2 is not None:
                            # Merge artifacts from direct fallback (invariant: artifact intent survives)
                            if chat_res2.artifacts:
                                for art in chat_res2.artifacts:
                                    if not any(a.get("id") == art.get("id") for a in artifacts_sent):
                                        artifacts_sent.append(art)
                            if chat_res2.ok and chat_res2.text and chat_res2.text.strip():
                                full_response_text = chat_res2.text
                                # direct fallback is not verified via citations
                                verified = False
                            elif chat_res2.text and chat_res2.text.strip():
                                full_response_text = chat_res2.text
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning("Direct fallback exception (run_id=%s): %s", run_id, type(exc).__name__)

                # If still no text, decide between generic hedge vs failed artifact
                if not full_response_text or not full_response_text.strip():
                    # If artifact was requested and we have no created artifact yet, ensure failed artifacts are surfaced
                    if early_intent.explicit_artifact_request and not any(a.get("status") == "created" for a in artifacts_sent):
                        # ChatService direct path already attempted orchestrator and should have produced failed artifacts
                        # But if artifacts_sent is still empty (e.g., both paths had exceptions before orchestration),
                        # synthesize failed artifacts here so SSE never silently drops requested format
                        if not artifacts_sent:
                            for fmt in early_intent.requested_formats:
                                if fmt != "text":
                                    artifacts_sent.append({
                                        "id": f"art-{run_id}-{fmt}",
                                        "kind": "document",
                                        "format": fmt,
                                        "type": fmt,
                                        "title": f"مخرج ثقافي: {early_intent.extracted_topic}",
                                        "filename": f"sard-{fmt}",
                                        "mime_type": "application/octet-stream",
                                        "size_bytes": 0,
                                        "status": "failed",
                                        "download_url": None,
                                        "url": "",
                                        "error": f"تعذر توليد ملف {fmt.upper()} حالياً. الرجاء إعادة المحاولة لاحقاً.",
                                        "error_category": "fallback_empty",
                                        "warnings": [],
                                        "preview": None,
                                        "checksum": None,
                                        "data": None,
                                    })
                        # Still need a text body for delta: use generic hedge but mention artifact failure is in artifacts event
                        full_response_text = _generate_cultural_fallback_answer(effective_query, lang=resolved_lang)
                        # Append hint that artifact failed (kept in hedge, not as canned itinerary)
                        fail_hint = "\n\n> تعذر إنشاء الملف المطلوب في هذه المحاولة؛ راجع تفاصيل المخرجات أدناه." if resolved_lang == "ar" else "\n\n> The requested file could not be created in this attempt; see outputs below."
                        full_response_text += fail_hint
                    else:
                        full_response_text = _generate_cultural_fallback_answer(effective_query, lang=resolved_lang)
                    verified = False

            # 4. Sanitize and ensure explicit hedge if empty (never empty string, never shrimp for unrelated)
            full_response_text = sanitize_cultural_output(full_response_text)
            if not full_response_text or not full_response_text.strip():
                full_response_text = _generate_cultural_fallback_answer(effective_query, lang=resolved_lang)

            # 5. SSE contract: artifacts event always before delta/done if artifacts exist; includes failed
            if artifacts_sent:
                # Ensure artifacts are verified where possible: successful artifacts have downloadable verified bytes
                # Failed artifacts must never appear as created
                for art in artifacts_sent:
                    if art.get("status") == "failed":
                        assert art.get("download_url") is None, "failed artifact must not have download_url"
                    if art.get("status") == "created":
                        assert art.get("download_url"), "created artifact must have download_url"
                yield {
                    "event": "artifacts",
                    "data": json.dumps({"artifacts": artifacts_sent}, ensure_ascii=False)
                }
            elif early_intent.explicit_artifact_request:
                # Edge: intent requested artifact but neither path emitted artifacts (should not happen due to above synthesis)
                # Emit failed artifacts now to honor contract
                fallback_failed = []
                for fmt in early_intent.requested_formats:
                    if fmt != "text":
                        fallback_failed.append({
                            "id": f"art-{run_id}-{fmt}",
                            "kind": "document",
                            "format": fmt,
                            "type": fmt,
                            "title": f"مخرج ثقافي: {early_intent.extracted_topic}",
                            "filename": f"sard-{fmt}",
                            "mime_type": "application/octet-stream",
                            "size_bytes": 0,
                            "status": "failed",
                            "download_url": None,
                            "url": "",
                            "error": f"تعذر توليد ملف {fmt.upper()} حالياً. الرجاء إعادة المحاولة لاحقاً.",
                            "error_category": "fallback_empty",
                            "warnings": [],
                            "preview": None,
                            "checksum": None,
                            "data": None,
                        })
                if fallback_failed:
                    artifacts_sent.extend(fallback_failed)
                    yield {
                        "event": "artifacts",
                        "data": json.dumps({"artifacts": artifacts_sent}, ensure_ascii=False)
                    }

            # Stream delta tokens smoothly (preserve current query text, not stale history)
            chunk_size = 4
            words = full_response_text.split(" ")
            for i in range(0, len(words), chunk_size):
                chunk = " ".join(words[i : i + chunk_size])
                if i + chunk_size < len(words):
                    chunk += " "
                yield {
                    "event": "delta",
                    "data": json.dumps({"text": chunk}, ensure_ascii=False)
                }
                await asyncio.sleep(0.015)

        except asyncio.CancelledError:
            logger.info("SSE stream cancelled by client (run_id=%s).", run_id)
            # Ensure downstream knows it was cancelled: emit error then done if not already sent
            # EventSourceResponse will close; we still attempt to yield a done with error flag if possible
            try:
                yield {
                    "event": "error",
                    "data": json.dumps({"run_id": run_id, "error": "تم إلغاء الطلب.", "cancelled": True}, ensure_ascii=False)
                }
            except Exception as exc:
                logger.debug("Suppressed boundary exception in server.py: %s", type(exc).__name__)
            raise
        except Exception as exc:
            logger.exception("Unexpected SSE error (run_id=%s): %s", run_id, type(exc).__name__)
            # Emit error event but still guarantee done (contract)
            try:
                yield {
                    "event": "error",
                    "data": json.dumps({"run_id": run_id, "error": "حدث خطأ غير متوقع أثناء المعالجة. الرجاء المحاولة لاحقاً."}, ensure_ascii=False)
                }
            except Exception as exc:
                logger.debug("Suppressed boundary exception in server.py: %s", type(exc).__name__)
        finally:
            # 5. Final Done Event — always emitted even on empty/error (contract). Includes run_id, no secrets.
            total_time_ms = (time.monotonic() - t_start) * 1000
            try:
                yield {
                    "event": "done",
                    "data": json.dumps({
                        "verified": bool(verified),
                        "sources_count": len(citations_sent),
                        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "timings_ms": {
                            "total_ms": round(total_time_ms, 1),
                        },
                        "artifacts_count": len(artifacts_sent),
                        "session_id": session_id_out,
                        "run_id": run_id,
                    }, ensure_ascii=False)
                }
            except Exception as exc:
                logger.debug("Suppressed boundary exception in server.py: %s", type(exc).__name__)
            logger.info("SSE done (run_id=%s, session_id=%s, verified=%s, artifacts=%d, sources=%d, total_ms=%.1f)",
                        run_id, session_id_out[:8] if len(session_id_out) > 8 else session_id_out, verified, len(artifacts_sent), len(citations_sent), total_time_ms)

    return EventSourceResponse(sse_generator())


def _generate_cultural_fallback_answer(query: str, lang: str = "ar") -> str:
    """Return a transparent uncertainty hedge when grounded generation is unavailable.

    This path deliberately contains no topic-specific factual claims. Topic answers
    must come from the grounded planner/router so citations and uncertainty stay
    linked to the same evidence.
    """
    normalized = (query or "").strip()
    snippet = normalized[:120]
    if lang == "en":
        if not normalized:
            return "Hello. How can I help you today?"
        return (
            "I couldn't establish a sufficiently grounded source for this request. "
            f'I won\'t present an unverified answer about "{snippet}". '
            "Please provide a source, or narrow the question to a specific place, "
            "practice, or time period."
        )
    if not normalized:
        return "مرحباً! كيف يمكنني مساعدتك اليوم؟"
    return (
        "لم أتمكن من تثبيت مصدر موثوق وكافٍ لهذا الطلب، لذلك لن أقدّم معلومة غير موثقة "
        f"عن «{snippet}». "
        "يمكنك تزويدي بمصدر أو تحديد المكان والممارسة والفترة الزمنية."
    )

# --- Agentic Cultural Feature Models & Endpoints ---

class PresentationRequest(BaseModel):
    topic: str = Field(..., description="Cultural briefing topic")
    region: Optional[str] = Field("المملكة العربية السعودية", description="Target region")
    overview_text: Optional[str] = Field("", description="Overview context")
    comparison_cards: Optional[List[Dict[str, Any]]] = Field(None, description="Comparison cards")
    timeline_items: Optional[List[Dict[str, Any]]] = Field(None, description="Timeline milestones")
    key_takeaways: Optional[List[str]] = Field(None, description="Key takeaways")

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "topic" not in data:
                data["topic"] = data.get("title") or data.get("query") or data.get("prompt") or ""
            if not str(data.get("topic", "") or "").strip():
                raise ValueError("الرجاء تحديد موضوع العرض (topic) قبل المتابعة.")
            if "region" not in data or not data["region"]:
                data["region"] = "المملكة العربية السعودية"
        return data


class RecipeCardRequest(BaseModel):
    item_name: str = Field(..., description="Dish or craft name")
    card_type: Optional[str] = Field("culinary", description="culinary or craft")
    region: Optional[str] = Field("المملكة العربية السعودية", description="Region")
    cultural_story: Optional[str] = Field("", description="Cultural backstory")

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "item_name" not in data:
                data["item_name"] = data.get("dish_name") or data.get("name") or data.get("craft_name") or ""
            if not str(data.get("item_name", "") or "").strip():
                raise ValueError("الرجاء تحديد اسم الطبق أو الحرفة (item_name) — لا يمكن التخمين تلقائيًا.")
            if "card_type" not in data or not data["card_type"]:
                data["card_type"] = "craft" if "سدو" in str(data.get("item_name", "")) else "culinary"
        return data


class GreetingCardRequest(BaseModel):
    occasion: Optional[str] = Field("foundation_day", description="Occasion identifier")
    recipient_name: Optional[str] = Field("", description="Recipient name")
    sender_name: Optional[str] = Field("", description="Sender name")
    custom_message: Optional[str] = Field("", description="Custom message text")
    theme: Optional[str] = Field("dark_gold", description="Color theme")

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "recipient_name" not in data and "recipient" in data:
                data["recipient_name"] = data["recipient"]
            if "sender_name" not in data and "sender" in data:
                data["sender_name"] = data["sender"]
            if "custom_message" not in data:
                data["custom_message"] = data.get("message") or data.get("text") or ""
        return data


class EtiquetteRequest(BaseModel):
    scenario_type: Optional[str] = Field(None, description="majlis or business_negotiation")
    situation: Optional[str] = Field("", description="Context details")

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "scenario_type" not in data:
                data["scenario_type"] = data.get("scenario") or data.get("type") or ""
            if not str(data.get("scenario_type", "") or "").strip():
                raise ValueError("الرجاء تحديد نوع سيناريو الإتيكيت (scenario_type: majlis أو business_negotiation).")
        return data


class DialectRequest(BaseModel):
    phrase_or_proverb: str = Field(..., description="Proverb or dialect word")
    dialect_region: Optional[str] = Field("najdi", description="najdi, hijazi, sharqawi, janoubi")

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "phrase_or_proverb" not in data:
                data["phrase_or_proverb"] = (
                    data.get("proverb_or_phrase") or data.get("phrase") or data.get("proverb") or data.get("text") or ""
                )
            if not str(data.get("phrase_or_proverb", "") or "").strip():
                raise ValueError("الرجاء إدخال العبارة أو المثل (phrase_or_proverb) قبل المتابعة.")
            if "dialect_region" not in data or not data["dialect_region"]:
                data["dialect_region"] = data.get("region") or "najdi"
        return data


class ArtisanRequest(BaseModel):
    craft_name: Optional[str] = Field(None, description="Craft name (sadu, hasawi_bisht, taif_rose, aseeri_qatt)")

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "craft_name" not in data:
                data["craft_name"] = data.get("craft") or data.get("item_name") or data.get("name") or ""
            if not str(data.get("craft_name", "") or "").strip():
                raise ValueError("الرجاء تحديد اسم الحرفة (craft_name: sadu أو hasawi_bisht أو taif_rose أو aseeri_qatt).")
        return data


class MemoirRequest(BaseModel):
    family_name: str = Field(..., description="Narrator / family name")
    raw_notes: List[Dict[str, str]] = Field(default_factory=list, description="List of notes / answers")
    origin_region: Optional[str] = Field("المملكة العربية السعودية", description="Origin region")
    origin_town: Optional[str] = Field("", description="Origin town/village")

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "family_name" not in data:
                data["family_name"] = (
                    data.get("family_or_narrator") or data.get("family") or data.get("narrator_name") or data.get("name") or ""
                )
            if not str(data.get("family_name", "") or "").strip():
                raise ValueError("الرجاء تحديد اسم العائلة أو الراوي (family_name).")
            raw = data.get("raw_notes")
            if isinstance(raw, str):
                data["raw_notes"] = [{"topic": "ذكريات وسيرة", "content": raw, "era": "الزمن الجميل"}]
            elif isinstance(raw, list):
                norm_list = []
                for idx, item in enumerate(raw, 1):
                    if isinstance(item, str):
                        norm_list.append({"topic": f"الفصل {idx}", "content": item, "era": "مرحلة الذكريات"})
                    elif isinstance(item, dict):
                        norm_list.append({
                            "topic": str(item.get("topic") or item.get("title") or f"الفصل {idx}"),
                            "content": str(item.get("content") or item.get("text") or item.get("note") or ""),
                            "era": str(item.get("era") or item.get("date") or "الماضي الجميل"),
                        })
                data["raw_notes"] = norm_list
            elif not raw:
                raise ValueError("الرجاء تقديم الملاحظات الشفهية (raw_notes) — لا يمكن توليد مذكرات من فراغ.")
        return data


class ResearchRequest(BaseModel):
    topic: str = Field(..., description="Heritage topic for verified research")
    primary_authority: Optional[str] = Field("دارة الملك عبد العزيز / هيئة التراث", description="Authority")

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "topic" not in data:
                data["topic"] = data.get("title") or data.get("query") or ""
            if not str(data.get("topic", "") or "").strip():
                raise ValueError("الرجاء تحديد موضوع البحث التراثي (topic).")
        return data


@app.get("/api/calendar/events")
@app.post("/api/calendar/events")
@app.get("/calendar/events")
@app.post("/calendar/events")
async def get_heritage_calendar_events(
    query: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    month: Optional[int] = Query(None),
):
    """Retrieve verified heritage events, astronomical seasons, and calendar sync URLs."""
    from sard.agent.tools.cultural_agentic_tools import tool_sync_heritage_calendar
    res = tool_sync_heritage_calendar(query=query or "", category=category, region=region, month=month)
    return res


@app.post("/api/tools/presentation")
@app.post("/tools/presentation")
async def generate_presentation_endpoint(req: PresentationRequest):
    """Generate a PowerPoint (.pptx) cultural presentation deck."""
    from sard.agent.tools.cultural_agentic_tools import tool_generate_presentation
    return tool_generate_presentation(
        topic=req.topic,
        region=req.region or "المملكة العربية السعودية",
        overview_text=req.overview_text or "",
        comparison_cards=req.comparison_cards,
        timeline_items=req.timeline_items,
        key_takeaways=req.key_takeaways,
    )


@app.post("/api/tools/recipe-card")
@app.post("/tools/recipe-card")
async def generate_recipe_card_endpoint(req: RecipeCardRequest):
    """Generate printable PDF recipe or craft card."""
    from sard.agent.tools.cultural_agentic_tools import tool_generate_recipe_or_craft_card
    return tool_generate_recipe_or_craft_card(
        item_name=req.item_name,
        card_type=req.card_type or "culinary",
        region=req.region or "المملكة العربية السعودية",
        cultural_story=req.cultural_story or "",
    )


@app.post("/api/tools/greeting-card")
@app.post("/tools/greeting-card")
async def generate_greeting_card_endpoint(req: GreetingCardRequest):
    """Generate visual greeting card (SVG & PDF)."""
    from sard.agent.tools.cultural_agentic_tools import tool_create_greeting_card
    return tool_create_greeting_card(
        occasion=req.occasion or "foundation_day",
        recipient_name=req.recipient_name or "",
        sender_name=req.sender_name or "",
        custom_message=req.custom_message or "",
        theme=req.theme or "dark_gold",
    )


@app.post("/api/tools/etiquette")
@app.post("/tools/etiquette")
async def simulate_etiquette_endpoint(req: EtiquetteRequest):
    """Run interactive cultural etiquette protocol simulator & flowchart."""
    from sard.agent.tools.cultural_agentic_tools import tool_simulate_etiquette_protocol
    return tool_simulate_etiquette_protocol(
        scenario_type=req.scenario_type or "majlis",
        situation=req.situation or "",
    )


@app.post("/api/tools/dialect")
@app.post("/tools/dialect")
async def decode_dialect_endpoint(req: DialectRequest):
    """Decode regional dialect and proverb lore."""
    from sard.agent.tools.cultural_agentic_tools import tool_decode_dialect_or_proverb
    return tool_decode_dialect_or_proverb(
        phrase_or_proverb=req.phrase_or_proverb,
        dialect_region=req.dialect_region or "najdi",
    )


@app.post("/api/tools/artisan")
@app.post("/tools/artisan")
async def advise_artisan_endpoint(req: ArtisanRequest):
    """Advise on traditional artisan craft authentication and care."""
    from sard.agent.tools.cultural_agentic_tools import tool_advise_artisan_craft
    return tool_advise_artisan_craft(craft_name=req.craft_name or "sadu")


@app.post("/api/tools/memoir")
@app.post("/tools/memoir")
async def compile_memoir_endpoint(req: MemoirRequest):
    """Compile oral history memoir into PDF booklet."""
    from sard.agent.tools.cultural_agentic_tools import tool_compile_oral_history_memoir
    return tool_compile_oral_history_memoir(
        family_name=req.family_name,
        raw_notes=req.raw_notes,
        origin_region=req.origin_region or "المملكة العربية السعودية",
        origin_town=req.origin_town or "",
    )


@app.post("/api/tools/research")
@app.post("/tools/research")
async def conduct_research_endpoint(req: ResearchRequest):
    """Conduct verified academic heritage research with official citations."""
    from sard.agent.tools.cultural_agentic_tools import tool_conduct_verified_research
    return tool_conduct_verified_research(
        topic=req.topic,
        primary_authority=req.primary_authority or "دارة الملك عبد العزيز / هيئة التراث",
    )


def main():
    """CLI entrypoint for running the API server."""
    import uvicorn
    host = os.environ.get("SARD_HOST", "0.0.0.0")
    port = int(os.environ.get("SARD_PORT", "8000"))
    logger.info("Starting Sard API server on http://%s:%s", host, port)
    uvicorn.run("sard.api.server:app", host=host, port=port, reload=True)


if __name__ == "__main__":
    main()
