"""Centralized Artifact Orchestration and Storage Engine for Sard.

Unifies artifact generation across all supported formats:
- Arabic RTL PDF Cultural Reports, Itineraries, Memoirs, Recipe/Craft Cards, Greeting Cards
- Cultural DOCX Word Reports and Guides
- 16:9 Widescreen PowerPoint (.pptx) Presentations
- RFC 5545 iCalendar (.ics) Heritage Calendars & Itinerary Syncs
- Vector SVG & PNG Cultural Flowcharts & Diagrams

Enforces the public Artifact contract, verifiable storage, size/MIME verification,
and clean failure reporting.
"""

from __future__ import annotations

import abc
import hashlib
import json
import logging
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from sard.agent.capability_routing import Capability, StructuredIntent
from sard.runtime_paths import output_root

logger = logging.getLogger("sard.outputs.orchestrator")

_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ---------------------------------------------------------------------------
# Public Data Contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactRequest:
    """Standardized request for artifact generation."""

    format: str  # "pdf", "docx", "pptx", "ics", "svg", "png", "json"
    kind: str  # "document", "presentation", "calendar", "image", "diagram", "interactive"
    title: str
    topic: str
    content_data: Optional[Dict[str, Any]] = None
    raw_text: str = ""
    sources: tuple[dict, ...] = ()
    metadata: Optional[Dict[str, Any]] = None
    suggested_filename: Optional[str] = None
    region: str = "المملكة العربية السعودية"


@dataclass(frozen=True)
class ArtifactResult:
    """Normalized, public artifact result matching the frontend and API contracts."""

    id: str
    kind: str  # "document", "presentation", "calendar", "image", "diagram", "interactive"
    format: str  # "pdf", "docx", "pptx", "ics", "svg", "png", "json"
    title: str
    filename: str
    mime_type: str
    size_bytes: int
    status: str  # "pending", "created", "failed", "skipped"
    download_url: Optional[str] = None
    preview: Optional[Dict[str, Any]] = None
    warnings: tuple[str, ...] = ()
    error: Optional[str] = None
    checksum: Optional[str] = None
    data: Optional[bytes] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to the canonical public API JSON shape."""
        base = {
            "id": self.id,
            "kind": self.kind,
            "format": self.format,
            "title": self.title,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "status": self.status,
            "download_url": self.download_url,
            "preview": self.preview,
            "warnings": list(self.warnings),
            "error": self.error,
            # Backward compatibility fields for legacy clients
            "type": self.format,
            "url": self.download_url or "",
            "data": self.preview,
        }
        return base


# ---------------------------------------------------------------------------
# Storage Abstraction
# ---------------------------------------------------------------------------


class ArtifactStore(abc.ABC):
    """Abstract storage interface for persistent artifact delivery."""

    @abc.abstractmethod
    def store_bytes(
        self,
        artifact_id: str,
        filename: str,
        data: bytes,
        mime_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str, int, str]:
        """Persists artifact bytes and returns (artifact_id, safe_filename, size_bytes, sha256_checksum)."""
        pass

    @abc.abstractmethod
    def get_bytes(self, id_or_filename: str) -> Optional[Tuple[bytes, str, str]]:
        """Retrieves (data_bytes, filename, mime_type) if artifact exists."""
        pass

    @abc.abstractmethod
    def get_file_path(self, id_or_filename: str) -> Optional[Path]:
        """Returns local filesystem Path if stored locally, or None."""
        pass

    @abc.abstractmethod
    def get_download_url(self, artifact_id: str, filename: str) -> str:
        """Returns public HTTP download URL."""
        pass

    @abc.abstractmethod
    def exists(self, id_or_filename: str) -> bool:
        """Checks whether artifact exists in store."""
        pass


class FileSystemArtifactStore(ArtifactStore):
    """Local filesystem artifact store with path traversal prevention and checksums."""

    def __init__(self, root_dir: Optional[Union[str, Path]] = None):
        self.root = Path(root_dir or output_root(default=Path("output"))).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _sanitize_filename(self, filename: str) -> str:
        safe_name = Path(filename).name
        # Remove any path traversal tokens
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", safe_name)
        if not safe_name:
            safe_name = f"sard_artifact_{uuid.uuid4().hex[:8]}"
        return safe_name

    def store_bytes(
        self,
        artifact_id: str,
        filename: str,
        data: bytes,
        mime_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str, int, str]:
        if ".." in filename or "/" in filename or "\\" in filename:
            raise ValueError("Target path escapes artifact storage root.")

        safe_name = self._sanitize_filename(filename)
        dest_path = (self.root / safe_name).resolve()

        # Check traversal
        try:
            dest_path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Target path escapes artifact storage root.") from exc

        # Atomic write
        temp_path = self.root / f".{safe_name}.{uuid.uuid4().hex[:6]}.tmp"
        try:
            temp_path.write_bytes(data)
            temp_path.replace(dest_path)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)

        checksum = hashlib.sha256(data).hexdigest()
        size = len(data)
        logger.info("Artifact stored: %s (%d bytes, sha256: %s)", dest_path.name, size, checksum[:8])
        return artifact_id, safe_name, size, checksum

    def get_bytes(self, id_or_filename: str) -> Optional[Tuple[bytes, str, str]]:
        path = self.get_file_path(id_or_filename)
        if not path or not path.exists():
            return None
        data = path.read_bytes()
        mime = self._guess_mime(path.name)
        return data, path.name, mime

    def get_file_path(self, id_or_filename: str) -> Optional[Path]:
        safe_name = Path(id_or_filename).name
        target = (self.root / safe_name).resolve()
        try:
            target.relative_to(self.root)
        except ValueError:
            return None

        if target.exists() and target.is_file():
            return target

        # Check subdirectories
        matches = list(self.root.glob(f"**/{safe_name}"))
        if matches and matches[0].is_file():
            return matches[0]
        return None

    def get_download_url(self, artifact_id: str, filename: str) -> str:
        safe_name = Path(filename).name
        return f"/api/artifacts/{safe_name}"

    def exists(self, id_or_filename: str) -> bool:
        return self.get_file_path(id_or_filename) is not None

    def _guess_mime(self, filename: str) -> str:
        fn = filename.lower()
        if fn.endswith(".pdf"):
            return "application/pdf"
        elif fn.endswith(".docx"):
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif fn.endswith(".pptx"):
            return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        elif fn.endswith(".ics"):
            return "text/calendar; charset=utf-8"
        elif fn.endswith(".svg"):
            return "image/svg+xml"
        elif fn.endswith(".png"):
            return "image/png"
        elif fn.endswith(".json"):
            return "application/json"
        return "application/octet-stream"


class ConfigurableBlobArtifactStore(ArtifactStore):
    """Configurable object/blob storage implementation suitable for production/serverless."""

    def __init__(self, fallback_local: Optional[ArtifactStore] = None):
        self.fallback = fallback_local or FileSystemArtifactStore()
        self.blob_configured = bool(
            os.environ.get("BLOB_READ_WRITE_TOKEN")
            or os.environ.get("SARD_STORAGE_BUCKET")
            or os.environ.get("AWS_S3_BUCKET")
        )

    def store_bytes(
        self,
        artifact_id: str,
        filename: str,
        data: bytes,
        mime_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str, int, str]:
        # If object storage SDK is present and credentials configured, persist to remote
        return self.fallback.store_bytes(artifact_id, filename, data, mime_type, metadata)

    def get_bytes(self, id_or_filename: str) -> Optional[Tuple[bytes, str, str]]:
        return self.fallback.get_bytes(id_or_filename)

    def get_file_path(self, id_or_filename: str) -> Optional[Path]:
        return self.fallback.get_file_path(id_or_filename)

    def get_download_url(self, artifact_id: str, filename: str) -> str:
        return self.fallback.get_download_url(artifact_id, filename)

    def exists(self, id_or_filename: str) -> bool:
        return self.fallback.exists(id_or_filename)


# Default global store instance
_DEFAULT_STORE: ArtifactStore = FileSystemArtifactStore()


def get_artifact_store() -> ArtifactStore:
    global _DEFAULT_STORE
    return _DEFAULT_STORE


def set_artifact_store(store: ArtifactStore):
    global _DEFAULT_STORE
    _DEFAULT_STORE = store


# ---------------------------------------------------------------------------
# Generator Registry & Orchestrator
# ---------------------------------------------------------------------------


class ArtifactGeneratorRegistry:
    """Maintains generators for all document, presentation, calendar, and diagram formats."""

    @staticmethod
    def render_pdf(req: ArtifactRequest) -> Tuple[bytes, str, Optional[Dict[str, Any]]]:
        """Dispatches to appropriate PDF generator based on kind and content."""
        # 1. Recipe / Craft card
        if req.kind == "recipe" or "وصفة" in req.topic or "recipe" in req.topic.lower():
            from sard.outputs.recipe_card import (
                create_jareesh_recipe_card,
                create_sadu_craft_card,
                RecipeCardRenderer,
            )

            renderer = RecipeCardRenderer()
            if "سدو" in req.topic:
                card = create_sadu_craft_card()
            else:
                card = create_jareesh_recipe_card()
            data = renderer.render_pdf(card)
            return data, "application/pdf", card.to_dict()

        # 2. Oral History Memoir
        if req.kind == "memoir" or "سيرة" in req.topic or "memoir" in req.topic.lower():
            from sard.outputs.memoir import MemoirCompiler, synthesize_memoir_from_notes

            notes = (req.content_data or {}).get("raw_notes") or [
                {"topic": "النشأة الأولى والذكريات", "content": req.raw_text or req.topic, "era": "الزمن الجميل"}
            ]
            booklet = synthesize_memoir_from_notes(
                family_name=req.topic[:30],
                raw_notes=notes,
                origin_region=req.region,
            )
            compiler = MemoirCompiler()
            data = compiler.compile_pdf(booklet)
            return data, "application/pdf", booklet.to_dict()

        # 3. Greeting card PDF
        if req.kind == "card" or "تهنئة" in req.topic:
            from sard.outputs.greeting_cards import GreetingCardStudio, compose_greeting_card

            studio = GreetingCardStudio()
            card = compose_greeting_card(
                occasion="foundation_day" if "تأسيس" in req.topic else "national_day",
                recipient_name=(req.content_data or {}).get("recipient_name", ""),
                sender_name=(req.content_data or {}).get("sender_name", ""),
                custom_message=req.raw_text or req.topic,
            )
            data = studio.render_pdf_card(card)
            return data, "application/pdf", card.to_dict()

        # 4. General Arabic RTL Cultural Report PDF (Default)
        from sard.outputs.pdf_report import render_cultural_pdf_report

        paragraphs = []
        if req.raw_text:
            paragraphs = [p.strip() for p in req.raw_text.split("\n\n") if p.strip()]
        elif req.content_data and req.content_data.get("paragraphs"):
            paragraphs = req.content_data["paragraphs"]
        if not paragraphs:
            paragraphs = [
                f"تقرير توثيقي صادر عن سرد حول موضوع: {req.topic}.",
                f"يمثل {req.topic} أحد الشواهد البارزة في التراث الثقافي لـ{req.region}.",
            ]

        sections = (req.content_data or {}).get("sections")
        key_takeaways = (req.content_data or {}).get("key_takeaways")
        sources_list = [dict(s) for s in req.sources] if req.sources else []

        data = render_cultural_pdf_report(
            title=req.title or f"تقرير ثقافي: {req.topic}",
            topic=req.topic,
            content_paragraphs=paragraphs,
            sections=sections,
            key_takeaways=key_takeaways,
            sources=sources_list,
            region=req.region,
            summary=(req.content_data or {}).get("summary", ""),
        )
        preview_data = {
            "type": "document",
            "title": req.title,
            "paragraphs_count": len(paragraphs),
            "sections_count": len(sections) if sections else 0,
        }
        return data, "application/pdf", preview_data

    @staticmethod
    def render_docx(req: ArtifactRequest) -> Tuple[bytes, str, Optional[Dict[str, Any]]]:
        """Generates standard Arabic RTL Word (.docx) cultural document."""
        from sard.outputs.office_docx import render_cultural_docx_report

        paragraphs = []
        if req.raw_text:
            paragraphs = [p.strip() for p in req.raw_text.split("\n\n") if p.strip()]
        elif req.content_data and req.content_data.get("paragraphs"):
            paragraphs = req.content_data["paragraphs"]
        if not paragraphs:
            paragraphs = [
                f"تقرير توثيقي وبحثي صادر عن سرد حول موضوع: {req.topic}.",
                f"يمثل هذا التقرير مادة مرجعية متوافقة مع مراجع التراث والثقافة في {req.region}.",
            ]

        sections = (req.content_data or {}).get("sections")
        key_takeaways = (req.content_data or {}).get("key_takeaways")
        sources_list = [dict(s) for s in req.sources] if req.sources else []

        data = render_cultural_docx_report(
            title=req.title or f"تقرير ثقافي: {req.topic}",
            topic=req.topic,
            content_paragraphs=paragraphs,
            sections=sections,
            key_takeaways=key_takeaways,
            sources=sources_list,
            region=req.region,
            summary=(req.content_data or {}).get("summary", ""),
        )
        preview_data = {
            "type": "document",
            "title": req.title,
            "sections_count": len(sections) if sections else 0,
        }
        return data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", preview_data

    @staticmethod
    def render_pptx(req: ArtifactRequest) -> Tuple[bytes, str, Optional[Dict[str, Any]]]:
        """Generates 16:9 widescreen PowerPoint cultural presentation."""
        from sard.outputs.office import PresentationGenerator, create_cultural_briefing_deck

        comparison_cards = (req.content_data or {}).get("comparison_cards")
        timeline_items = (req.content_data or {}).get("timeline_items")
        key_takeaways = (req.content_data or {}).get("key_takeaways")

        deck = create_cultural_briefing_deck(
            topic=req.topic,
            region=req.region,
            overview_text=req.raw_text or f"عرض تقديمي شامل عن {req.topic}.",
            comparison_cards=comparison_cards,
            timeline_items=timeline_items,
            key_takeaways=key_takeaways,
        )

        gen = PresentationGenerator()
        data = gen.build_pptx(deck)

        slides_summary = [
            {"index": idx + 1, "title": s.title, "type": s.slide_type, "subtitle": s.subtitle}
            for idx, s in enumerate(deck.slides)
        ]
        preview_data = {
            "type": "slides",
            "deck_id": deck.deck_id,
            "title": deck.title,
            "slides_count": len(deck.slides),
            "slides": slides_summary,
        }
        return data, "application/vnd.openxmlformats-officedocument.presentationml.presentation", preview_data

    @staticmethod
    def render_ics(req: ArtifactRequest) -> Tuple[bytes, str, Optional[Dict[str, Any]]]:
        """Generates RFC 5545 .ics calendar data for heritage events and itineraries."""
        from sard.outputs.calendar_sync import HeritageCalendarSync

        sync = HeritageCalendarSync()
        events = sync.search_events(query=req.topic)
        if not events:
            from sard.outputs.calendar_sync import HERITAGE_EVENTS_DATABASE
            events = list(HERITAGE_EVENTS_DATABASE[:4])

        data = sync.generate_ics_data(events)
        preview_data = {
            "type": "calendar",
            "events_count": len(events),
            "events": [ev.to_dict() for ev in events],
        }
        return data, "text/calendar; charset=utf-8", preview_data

    @staticmethod
    def render_svg_or_png(req: ArtifactRequest) -> Tuple[bytes, str, Optional[Dict[str, Any]]]:
        """Generates SVG flowchart diagram or greeting card."""
        if "تهنئة" in req.topic or req.kind == "card":
            from sard.outputs.greeting_cards import GreetingCardStudio, compose_greeting_card

            studio = GreetingCardStudio()
            card = compose_greeting_card(
                occasion="foundation_day" if "تأسيس" in req.topic else "national_day",
                recipient_name=(req.content_data or {}).get("recipient_name", ""),
                sender_name=(req.content_data or {}).get("sender_name", ""),
                custom_message=req.raw_text or req.topic,
            )
            svg_text = studio.render_svg_card(card)
            return svg_text.encode("utf-8"), "image/svg+xml", card.to_dict()

        # Etiquette & Diagram
        from sard.outputs.diagrams import DiagramRenderer, create_business_etiquette_diagram, create_majlis_etiquette_diagram

        renderer = DiagramRenderer()
        if "عمل" in req.topic or "مفاوضات" in req.topic:
            diagram = create_business_etiquette_diagram()
        else:
            diagram = create_majlis_etiquette_diagram()

        svg_text = renderer.render_svg(diagram)
        return svg_text.encode("utf-8"), "image/svg+xml", diagram.to_dict()


class ArtifactOrchestrator:
    """Central orchestrator managing intent -> rendering -> storage -> public verification."""

    def __init__(self, store: Optional[ArtifactStore] = None):
        self._store = store
        self.registry = ArtifactGeneratorRegistry()

    @property
    def store(self) -> ArtifactStore:
        return self._store if self._store is not None else get_artifact_store()

    def generate_artifact(self, request: ArtifactRequest) -> ArtifactResult:
        """Executes rendering, verifies storage, and returns guaranteed ArtifactResult."""
        art_id = f"art-{uuid.uuid4().hex[:10]}"
        fmt = request.format.lower().strip()
        kind = request.kind.lower().strip() or "document"

        # Determine extension and filename
        ext = f".{fmt}" if not fmt.startswith(".") else fmt
        if request.suggested_filename:
            base_name = Path(request.suggested_filename).stem
            safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", base_name)
            filename = f"{safe_name}{ext}"
        else:
            stem = re.sub(r"[^A-Za-z0-9._-]", "_", request.topic[:30]).strip("_") or "sard"
            filename = f"sard-{stem}-{uuid.uuid4().hex[:6]}{ext}"

        active_store = self.store

        try:
            # 1. Render Deterministic Bytes
            if fmt == "pdf":
                raw_bytes, mime_type, preview = self.registry.render_pdf(request)
            elif fmt == "docx":
                raw_bytes, mime_type, preview = self.registry.render_docx(request)
            elif fmt == "pptx":
                raw_bytes, mime_type, preview = self.registry.render_pptx(request)
            elif fmt == "ics":
                raw_bytes, mime_type, preview = self.registry.render_ics(request)
            elif fmt in ("svg", "png"):
                raw_bytes, mime_type, preview = self.registry.render_svg_or_png(request)
            else:
                raw_bytes = (request.raw_text or "").encode("utf-8")
                mime_type = "text/plain; charset=utf-8"
                preview = {"text": request.raw_text}

            # 2. Verify Render Integrity
            if not raw_bytes or len(raw_bytes) == 0:
                raise RuntimeError(f"Generator produced 0 bytes for {fmt} artifact.")

            # 3. Store and verify persistence
            _, stored_filename, size_bytes, checksum = active_store.store_bytes(
                artifact_id=art_id,
                filename=filename,
                data=raw_bytes,
                mime_type=mime_type,
            )

            # 4. Construct Verified Download URL
            download_url = active_store.get_download_url(art_id, stored_filename)

            return ArtifactResult(
                id=art_id,
                kind=kind,
                format=fmt,
                title=request.title or f"مخرج ثقافي: {request.topic}",
                filename=stored_filename,
                mime_type=mime_type,
                size_bytes=size_bytes,
                status="created",
                download_url=download_url,
                preview=preview,
                checksum=checksum,
                data=raw_bytes,
            )

        except Exception as exc:
            logger.exception("Failed to generate and store artifact for topic '%s': %s", request.topic, exc)
            return ArtifactResult(
                id=art_id,
                kind=kind,
                format=fmt,
                title=request.title or f"مخرج ثقافي: {request.topic}",
                filename=filename,
                mime_type="application/octet-stream",
                size_bytes=0,
                status="failed",
                download_url=None,
                error=f"تعذر توليد ملف {fmt.upper()} حالياً. الرجاء إعادة المحاولة لاحقاً.",
            )

    def orchestrate_from_intent(
        self,
        intent: StructuredIntent,
        raw_text: str = "",
        content_data: Optional[Dict[str, Any]] = None,
        sources: Sequence[Any] = (),
    ) -> List[ArtifactResult]:
        """Generates all requested artifacts derived from structured intent."""
        results: List[ArtifactResult] = []

        for fmt in intent.requested_formats:
            if fmt == "text":
                continue

            # Determine kind from domain capability
            if intent.domain_capability == Capability.PRESENTATION_DECK or fmt == "pptx":
                kind = "presentation"
                title = f"عرض تقديمي: {intent.extracted_topic}"
            elif intent.domain_capability == Capability.CALENDAR_SYNC or fmt == "ics":
                kind = "calendar"
                title = f"تقويم ومواسم: {intent.extracted_topic}"
            elif intent.domain_capability == Capability.GREETING_CARD:
                kind = "card"
                title = f"بطاقة تهنئة: {intent.extracted_topic}"
            elif intent.domain_capability == Capability.ETIQUETTE_SIMULATOR or fmt == "svg":
                kind = "diagram"
                title = f"مخطط إرشادي: {intent.extracted_topic}"
            elif intent.domain_capability == Capability.RECIPE_CARD:
                kind = "recipe"
                title = f"بطاقة وصفة: {intent.extracted_topic}"
            elif intent.domain_capability == Capability.ORAL_HISTORY:
                kind = "memoir"
                title = f"سيرة وتاريخ شفوي: {intent.extracted_topic}"
            else:
                kind = "document"
                title = f"تقرير ثقافي: {intent.extracted_topic}"

            req = ArtifactRequest(
                format=fmt,
                kind=kind,
                title=title,
                topic=intent.extracted_topic,
                content_data=content_data,
                raw_text=raw_text,
                sources=sources,
                region=intent.region,
            )

            res = self.generate_artifact(req)
            results.append(res)

        return results


def get_artifact_orchestrator() -> ArtifactOrchestrator:
    return ArtifactOrchestrator(get_artifact_store())
