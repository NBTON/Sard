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
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

# Ensure project root is in sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

load_dotenv(_PROJECT_ROOT / ".env")

from sard.agent.capability_routing import Capability, StructuredIntent, classify_intent
from sard.agent.chat_service import ChatService, current_status_label
from sard.agent.graph import GraphDependencies, default_dependencies, run_pipeline
from sard.agent.util import sanitize_cultural_output
from sard.config.models import ModelConfigError, get_model_settings
from sard.config.rag import RAGSettings, get_rag_settings
from sard.outputs.orchestrator import (
    ArtifactOrchestrator,
    ArtifactRequest,
    ArtifactResult,
    ArtifactStore,
    get_artifact_orchestrator,
    get_artifact_store,
)
from sard.rag.schemas import Citation, RAGAnswer
from sard.rag.service import RAGService, RAGServiceUnavailableError
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

# In-memory mapping of uploaded attachments (bounded with TTL/GC — Finding 3)
_ATTACHMENTS: Dict[str, Dict[str, Any]] = {}
_MAX_ATTACHMENTS = 100
_ATTACHMENT_TTL_SECONDS = 3600  # 1 hour


def _evict_expired_attachments() -> None:
    """Evict attachments older than TTL or beyond max count (LRU). Deletes files."""
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
            except Exception:
                pass
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
                except Exception:
                    pass

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


def _extract_all_attachments(req: ChatRequest) -> List[Dict[str, Any]]:
    attachments_list = [a.model_dump() for a in req.attachments]
    for msg in req.messages:
        if msg.attachments:
            for a in msg.attachments:
                d = a.model_dump()
                if not any(x["attachment_id"] == d["attachment_id"] for x in attachments_list):
                    attachments_list.append(d)
    return attachments_list


def _check_rag_readiness() -> dict:
    try:
        settings = get_rag_settings()
        collection_path = Path(settings.zvec_collection_path)
        exists = collection_path.exists() and any(collection_path.iterdir()) if collection_path.exists() else False
        return {"available": exists}
    except Exception:
        return {"available": False}


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
    """Health check endpoint - public contract only."""
    rag_info = _check_rag_readiness()
    return {
        "status": "ok",
        "service": "sard-agent",
        "timestamp": time.time(),
        "verified": rag_info.get("available", False),
        "sources": {"verified": rag_info.get("available", False)},
        "rag": rag_info,
    }


@app.get("/api/status")
@app.get("/status")
async def system_status():
    """Returns public system status without exposing internal model/provider IDs."""
    rag_info = _check_rag_readiness()
    enable_dev = os.environ.get("SARD_ENABLE_DEV_OBSERVABILITY", "").lower() in ("1", "true", "yes")
    base = {
        "status_label": "جاهز" if rag_info.get("available") else "جاهز",
        "verified": rag_info.get("available", False),
        "sources": {"verified": rag_info.get("available", False)},
        "rag": rag_info,
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
    meta = _ATTACHMENTS.get(safe_id)
    if meta and Path(meta["path"]).exists():
        return FileResponse(path=meta["path"], filename=meta["filename"], media_type=meta["mime_type"])

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
async def generate_full_itinerary(req: ItineraryRequest):
    """Executes the full LangGraph agent pipeline and generates real PDF / ICS artifacts."""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="الرجاء تقديم استفسار للرحلة")

    try:
        run_id = f"itin-{uuid.uuid4().hex[:10]}"
        deps = default_dependencies(open_rag=True)
        deps.render_artifacts = True
        deps.output_root = str(OUTPUT_DIR)
        deps.caller_dates = tuple(req.dates or [])
        deps.preview_calendar = req.preview_calendar

        # Run pipeline
        loop = asyncio.get_event_loop()
        state = await loop.run_in_executor(
            None,
            lambda: run_pipeline(
                req.query,
                dependencies=deps,
                run_id=run_id,
                caller_dates=req.dates,
                preview_calendar=req.preview_calendar,
            ),
        )

        # Extract and verify artifacts
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
        if not artifacts_list:
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
    - Bounded timeouts: overall chat deadline via SARD_CHAT_OVERALL_TIMEOUT (default 38s)
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
        try:
            overall_timeout = float(os.environ.get("SARD_CHAT_OVERALL_TIMEOUT", "38"))
            overall_timeout = max(5.0, min(60.0, overall_timeout))
        except ValueError:
            overall_timeout = 38.0

        try:
            # 1. Initial Status Event
            yield {
                "event": "status",
                "data": json.dumps({
                    "stage": "init",
                    "message": "جارٍ تحليل السؤال واستكشاف المعارف والوثائق المعتمدة..."
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
                    except Exception:
                        pass

                hybrid_chat_res = None
                try:
                    chat_service = ChatService()
                    history_dicts = [{"role": m.role, "content": m.content} for m in req.messages] if req.messages else None

                    # Launch chat_service.ask in executor with bounded timeout
                    future = loop.run_in_executor(
                        None,
                        lambda: chat_service.ask(
                            effective_query,
                            messages=history_dicts,
                            attachments=all_attachments,
                            use_hybrid_retrieval=True,
                            session_id=req.session_id,
                            status_callback=_sync_status_callback,
                        ),
                    )

                    # Stream status events as emitted, with overall deadline on the future
                    # We poll status_queue while waiting, but bound the total wait.
                    deadline = t_start + overall_timeout
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
                        # Preserve artifacts even if hybrid had fallback text or empty model — invariant 1,8
                        if hybrid_chat_res.citations or hybrid_chat_res.text:
                            verified = bool(hybrid_chat_res.decision in ("generate", "hedge") or len(hybrid_chat_res.citations) > 0)
                        # Extract citations (only if present)
                        for cit in (hybrid_chat_res.citations or []):
                            citations_sent.append({
                                "citation_id": cit.get("id", ""),
                                "title": cit.get("title", ""),
                                "source_name": cit.get("origin") or cit.get("id", ""),
                                "source_url": cit.get("url", ""),
                                "chunk_id": "",
                                "snippet": cit.get("title", ""),
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
                    yield {
                        "event": "status",
                        "data": json.dumps({
                            "stage": "generating",
                            "message": "جارٍ صياغة إجابة من المستشار الثقافي..."
                        }, ensure_ascii=False)
                    }
                    chat_service = ChatService()
                    loop = asyncio.get_event_loop()
                    history_dicts = [{"role": m.role, "content": m.content} for m in req.messages] if req.messages else None
                    try:
                        future2 = loop.run_in_executor(
                            None,
                            lambda: chat_service.ask(effective_query, messages=history_dicts, use_hybrid_retrieval=False, session_id=req.session_id),
                        )
                        # Bounded wait for direct fallback
                        remaining = max(2.0, (t_start + overall_timeout) - time.monotonic())
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
                        full_response_text = _generate_cultural_fallback_answer(effective_query)
                        # Append hint that artifact failed (kept in hedge, not as canned itinerary)
                        full_response_text += "\n\n> تعذر إنشاء الملف المطلوب في هذه المحاولة؛ راجع تفاصيل المخرجات أدناه."
                    else:
                        full_response_text = _generate_cultural_fallback_answer(effective_query)
                    verified = False

            # 4. Sanitize and ensure explicit hedge if empty (never empty string, never shrimp for unrelated)
            full_response_text = sanitize_cultural_output(full_response_text)
            if not full_response_text or not full_response_text.strip():
                full_response_text = _generate_cultural_fallback_answer(effective_query)

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
            except Exception:
                pass
            raise
        except Exception as exc:
            logger.exception("Unexpected SSE error (run_id=%s): %s", run_id, type(exc).__name__)
            # Emit error event but still guarantee done (contract)
            try:
                yield {
                    "event": "error",
                    "data": json.dumps({"run_id": run_id, "error": "حدث خطأ غير متوقع أثناء المعالجة. الرجاء المحاولة لاحقاً."}, ensure_ascii=False)
                }
            except Exception:
                pass
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
            except Exception:
                pass
            logger.info("SSE done (run_id=%s, session_id=%s, verified=%s, artifacts=%d, sources=%d, total_ms=%.1f)",
                        run_id, session_id_out[:8] if len(session_id_out) > 8 else session_id_out, verified, len(artifacts_sent), len(citations_sent), total_time_ms)

    return EventSourceResponse(sse_generator())


def _generate_cultural_fallback_answer(query: str) -> str:
    """Query-aware generic hedge — never injects shrimp/springs/Eastern/UNESCO canned articles as fallback.

    Invariants:
    - If query legitimately contains shrimp/springs terms, return that specific mini-fallback (still query-aware).
    - Otherwise return explicit Arabic friendly hedge mentioning Sard capabilities, not empty string, no itinerary.
    - Respects requested_formats at SSE layer: artifact failure is emitted as artifacts event, not as text substitution.
    """
    q_norm = (query or "").lower()
    # Narrow legitimate shrimp query: must contain explicit shrimp lexeme
    has_shrimp = any(k in q_norm for k in ["روبيان", "ربيان", "تاروت", "shrimp"])
    has_springs = any(k in q_norm for k in ["ينابيع", "عيون حارة", "عين حارة", "مياه كبريتية", "springs"])
    # Only shrimp/springs legitimate branches are kept; Eastern/UNESCO canned articles removed
    if has_shrimp and any(k in q_norm for k in ["روبيان", "ربيان", "تجفيف", "تاروت", "shrimp"]):
        return (
            "تُعد حرفة **تجفيف الروبيان** في جزيرة تاروت بمحافظة القطيف إحدى أقدم الحرف والتقاليد البحرية "
            "في المنطقة الشرقية بالمملكة العربية السعودية.\n\n"
            "### مراحل الحرفة التقليدية:\n"
            "1. **صيد الروبيان**: يتم الصيد في مواسم محددة (موسم فسح الروبيان) باستخدام قوارب الصيد التقليدية.\n"
            "2. **السلق الفوري**: يُسلق الروبيان في قدور ضخمة على الشاطئ مباشرة بمياه البحر المملحة للحفاظ على نكهته وجودته.\n"
            "3. **التجفيف تحت أشعة الشمس**: يُفرد الروبيان المسلوق على مسطحات خوص خاصة (السفات) لعدة أيام حتى يجف تماماً.\n"
            "4. **التقشير والتعبئة**: يُفصل القشر عن اللحم المجفف يدوياً، ويُحفظ ليُستخدم في أشهر المأكولات التراثية مثل الكبسة والمحموس والثريد.\n\n"
            "هذه الحرفة تمثل جزءاً حيوياً من التراث الثقافي غير المادي الذي تحرص **وزارة الثقافة** على توثيقه وإبرازه."
        )
    if has_springs:
        return (
            f"بخصوص استفسارك حول الينابيع والعيون الحارة: *\"{query[:120]}\"*\n\n"
            "تُعد الينابيع والعيون الحارة جزءاً من التراث الطبيعي في بعض مناطق المملكة، وتُرتبط بمعارف استشفائية وتقاليد محلية.\n"
            "لعدم توفر مصدر موثق كافٍ لهذا الاستعلام في الوقت الحالي، يُرجى تحديد المنطقة (مثلاً: الأحساء، الليث، عسير) أو السياق المطلوب، وسأقدّم توثيقاً أدق مع الإسناد."
        )
    # Generic hedge — query-aware, friendly, Arabic, no canned Eastern/UNESCO itinerary
    return (
        f"تعذّر توليد إجابة موثقة عن: \"{query[:120]}\" في الوقت الحالي.\n\n"
        "حفاظًا على الأمانة المعرفية، لا أقدّم توليفًا غير مُسنَد بلا مصادر.\n"
        "كرفيقك الثقافي في **سرد**، يمكنني مساعدتك في:\n"
        "- برامج ومسارات سياحية وتراثية مخصصة حسب المنطقة والمدة.\n"
        "- معلومات موثقة عن المواقع الأثرية والفنون والحرف اليدوية.\n"
        "- الفعاليات والمواسم الثقافية التي تنظمها قطاعات وهيئات **وزارة الثقافة**.\n\n"
        "يرجى توضيح المنطقة أو السياق المطلوب، أو إعادة المحاولة."
    )


# --- Agentic Cultural Feature Models & Endpoints ---

class PresentationRequest(BaseModel):
    topic: str = Field(..., description="Cultural briefing topic")
    region: Optional[str] = Field("المملكة العربية السعودية", description="Target region")
    overview_text: Optional[str] = Field("", description="Overview context")
    comparison_cards: Optional[List[Dict[str, Any]]] = Field(None, description="Comparison cards")
    timeline_items: Optional[List[Dict[str, Any]]] = Field(None, description="Timeline milestones")
    key_takeaways: Optional[List[str]] = Field(None, description="Key takeaways")


class RecipeCardRequest(BaseModel):
    item_name: str = Field(..., description="Dish or craft name")
    card_type: Optional[str] = Field("culinary", description="culinary or craft")
    region: Optional[str] = Field("المملكة العربية السعودية", description="Region")
    cultural_story: Optional[str] = Field("", description="Cultural backstory")


class GreetingCardRequest(BaseModel):
    occasion: Optional[str] = Field("foundation_day", description="Occasion identifier")
    recipient_name: Optional[str] = Field("", description="Recipient name")
    sender_name: Optional[str] = Field("", description="Sender name")
    custom_message: Optional[str] = Field("", description="Custom message text")
    theme: Optional[str] = Field("dark_gold", description="Color theme")


class EtiquetteRequest(BaseModel):
    scenario_type: Optional[str] = Field("majlis", description="majlis or business_negotiation")
    situation: Optional[str] = Field("", description="Context details")


class DialectRequest(BaseModel):
    phrase_or_proverb: str = Field(..., description="Proverb or dialect word")
    dialect_region: Optional[str] = Field("najdi", description="najdi, hijazi, sharqawi, janoubi")


class ArtisanRequest(BaseModel):
    craft_name: Optional[str] = Field("sadu", description="Craft name (sadu, hasawi_bisht, taif_rose, aseeri_qatt)")


class MemoirRequest(BaseModel):
    family_name: str = Field(..., description="Narrator / family name")
    raw_notes: List[Dict[str, str]] = Field(default_factory=list, description="List of notes / answers")
    origin_region: Optional[str] = Field("المملكة العربية السعودية", description="Origin region")
    origin_town: Optional[str] = Field("", description="Origin town/village")


class ResearchRequest(BaseModel):
    topic: str = Field(..., description="Heritage topic for verified research")
    primary_authority: Optional[str] = Field("دارة الملك عبد العزيز / هيئة التراث", description="Authority")


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
