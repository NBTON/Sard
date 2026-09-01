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
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from sard.runtime_paths import DEFAULT_VERCEL_BLOB_ENDPOINT, output_root
from sard.outputs.validation import (
    ARTIFACT_MIME_TYPES,
    ArtifactValidationError,
    validate_artifact_bytes,
)

if TYPE_CHECKING:
    from sard.agent.capability_routing import StructuredIntent

logger = logging.getLogger("sard.outputs.orchestrator")

_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_FORMAT_EXTENSIONS = {fmt: f".{fmt}" for fmt in ARTIFACT_MIME_TYPES}


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
    error_category: Optional[str] = None

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
            "error_category": self.error_category,
            "checksum": self.checksum,
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
    """Storage interface for durable artifact delivery.

    Implementations must make ``artifact_id`` and ``filename`` independently
    addressable.  ``get_file_path`` may return ``None`` for object storage;
    callers should use ``get_bytes`` when they need portable retrieval.
    """

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
    """Atomic local store with a durable ID-to-file metadata index.

    Files are kept flat because the existing download endpoint accepts a
    filename.  A UUID-derived suffix prevents same-title collisions while the
    sidecar metadata makes ID lookup work in a fresh process/store instance.
    """

    def __init__(self, root_dir: Optional[Union[str, Path]] = None):
        self.root = Path(root_dir or output_root(default=Path("output"))).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._metadata_root = self.root / ".artifact-metadata"
        self._metadata_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @staticmethod
    def _validate_id(artifact_id: str) -> str:
        value = str(artifact_id or "")
        if not _SAFE_ID_RE.fullmatch(value):
            raise ValueError("Artifact ID must be a safe identifier.")
        return value

    @staticmethod
    def _validate_filename(filename: str) -> str:
        value = str(filename or "")
        if not value or any(token in value for token in ("/", "\\", "\x00")):
            raise ValueError("Target path escapes artifact storage root.")
        if value in {".", ".."} or ".." in Path(value).parts:
            raise ValueError("Target path escapes artifact storage root.")
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", value)
        if not safe_name or safe_name.startswith(".") or not _SAFE_FILENAME_RE.fullmatch(safe_name):
            raise ValueError("Artifact filename must use safe ASCII characters.")
        return safe_name

    @staticmethod
    def _mime_for(filename: str, supplied: str) -> str:
        suffix = Path(filename).suffix.lower().lstrip(".")
        return ARTIFACT_MIME_TYPES.get(suffix, supplied or "application/octet-stream")

    @classmethod
    def _stored_filename(cls, artifact_id: str, filename: str) -> str:
        requested = cls._validate_filename(filename)
        stem = Path(requested).stem[:96] or "sard-artifact"
        suffix = Path(requested).suffix.lower()
        return cls._validate_filename(f"{stem}--{artifact_id}{suffix}")

    def _metadata_path(self, artifact_id: str) -> Path:
        return self._metadata_root / f"{self._validate_id(artifact_id)}.json"

    def _destination(self, filename: str) -> Path:
        destination = (self.root / filename).resolve()
        try:
            destination.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Target path escapes artifact storage root.") from exc
        return destination

    def _publish(self, temporary: Path, destination: Path) -> None:
        try:
            os.link(temporary, destination)
        except FileExistsError as exc:
            raise ValueError("Refusing to overwrite existing artifact.") from exc
        finally:
            temporary.unlink(missing_ok=True)

    def _write_metadata(self, artifact_id: str, record: dict[str, object]) -> None:
        metadata_path = self._metadata_path(artifact_id)
        temporary = metadata_path.with_name(f".{metadata_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            self._publish(temporary, metadata_path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _record_for(self, id_or_filename: str) -> Optional[dict[str, object]]:
        value = str(id_or_filename or "")
        if any(token in value for token in ("/", "\\", "\x00")):
            return None
        if _SAFE_ID_RE.fullmatch(value):
            path = self._metadata_path(value)
            if path.is_file():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    return None
        return None

    def store_bytes(
        self,
        artifact_id: str,
        filename: str,
        data: bytes,
        mime_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str, int, str]:
        safe_id = self._validate_id(artifact_id)
        requested = self._validate_filename(filename)
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ValueError("Artifact bytes must be non-empty.")
        raw = bytes(data)
        mime = self._mime_for(requested, mime_type)
        safe_name = self._stored_filename(safe_id, requested)
        dest_path = self._destination(safe_name)
        checksum = hashlib.sha256(raw).hexdigest()
        record = {
            "artifact_id": safe_id,
            "filename": safe_name,
            "mime_type": mime,
            "size_bytes": len(raw),
            "checksum": checksum,
            "metadata": metadata or {},
        }
        with self._lock:
            if self._metadata_path(safe_id).exists() or dest_path.exists():
                raise ValueError("Refusing to overwrite existing artifact.")
            temporary = self.root / f".{safe_name}.{uuid.uuid4().hex}.tmp"
            published = False
            try:
                with temporary.open("xb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                self._publish(temporary, dest_path)
                published = True
                self._write_metadata(safe_id, record)
            except Exception:
                temporary.unlink(missing_ok=True)
                if published:
                    dest_path.unlink(missing_ok=True)
                raise
        logger.info("Artifact stored: %s (%d bytes, sha256: %s)", safe_name, len(raw), checksum[:8])
        return safe_id, safe_name, len(raw), checksum

    def get_bytes(self, id_or_filename: str) -> Optional[Tuple[bytes, str, str]]:
        path = self.get_file_path(id_or_filename)
        if not path or not path.exists():
            return None
        data = path.read_bytes()
        record = self._record_for(id_or_filename)
        if record is None:
            # Resolve metadata from the exact filename, without recursively
            # searching other requests' directories/files.
            for candidate in self._metadata_root.glob("*.json"):
                try:
                    item = json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if item.get("filename") == path.name:
                    record = item
                    break
        mime = str((record or {}).get("mime_type") or self._guess_mime(path.name))
        return data, path.name, mime

    def get_file_path(self, id_or_filename: str) -> Optional[Path]:
        value = str(id_or_filename or "")
        record = self._record_for(value)
        safe_name = str(record.get("filename")) if record else value
        if not safe_name or any(token in safe_name for token in ("/", "\\", "\x00")):
            return None
        try:
            safe_name = self._validate_filename(safe_name)
            target = self._destination(safe_name)
        except ValueError:
            return None
        if target.exists() and target.is_file():
            return target
        return None

    def get_download_url(self, artifact_id: str, filename: str) -> str:
        safe_name = self._validate_filename(filename)
        self._validate_id(artifact_id)
        return "/api/artifacts/" + urllib.parse.quote(safe_name, safe="")

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
    """Use a configured HTTP blob service, with local storage when unconfigured.

    The adapter speaks the common signed/tokenized blob REST contract used by
    Vercel Blob and compatible object stores: PUT/GET at ``endpoint/key`` with
    a bearer token.  It is intentionally opt-in, so tests and local development
    never contact a provider or require credentials.
    """

    def __init__(
        self,
        fallback_local: Optional[ArtifactStore] = None,
        *,
        endpoint: Optional[str] = None,
        token: Optional[str] = None,
        public_base_url: Optional[str] = None,
        timeout: float = 15.0,
    ):
        self.fallback = fallback_local or FileSystemArtifactStore()
        self.endpoint = (
            endpoint
            or os.environ.get("SARD_BLOB_ENDPOINT")
            or (DEFAULT_VERCEL_BLOB_ENDPOINT if os.environ.get("BLOB_READ_WRITE_TOKEN") else "")
        ).rstrip("/")
        self.token = token or os.environ.get("SARD_BLOB_TOKEN") or os.environ.get("BLOB_READ_WRITE_TOKEN")
        self.public_base_url = (public_base_url or os.environ.get("SARD_BLOB_PUBLIC_BASE_URL") or self.endpoint).rstrip("/")
        self.timeout = timeout
        self.blob_configured = bool(self.endpoint and self.token)

    def _key(self, artifact_id: str, filename: str) -> str:
        if not _SAFE_ID_RE.fullmatch(str(artifact_id or "")):
            raise ValueError("Artifact ID must be a safe identifier.")
        safe_name = FileSystemArtifactStore._validate_filename(filename)
        return f"artifacts/{artifact_id}/{safe_name}"

    def _url(self, key: str, base: Optional[str] = None) -> str:
        return f"{(base or self.endpoint).rstrip('/')}/{urllib.parse.quote(key, safe='/')}"

    def store_bytes(
        self,
        artifact_id: str,
        filename: str,
        data: bytes,
        mime_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str, int, str]:
        if not self.blob_configured:
            return self.fallback.store_bytes(artifact_id, filename, data, mime_type, metadata)
        if not data:
            raise ValueError("Artifact bytes must be non-empty.")
        safe_name = FileSystemArtifactStore._stored_filename(artifact_id, filename)
        key = self._key(artifact_id, safe_name)
        canonical_mime = ARTIFACT_MIME_TYPES.get(Path(safe_name).suffix.lower().lstrip("."), mime_type)
        request = urllib.request.Request(
            self._url(key),
            data=bytes(data),
            method="PUT",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": canonical_mime,
                "x-artifact-id": str(artifact_id),
                "If-None-Match": "*",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
                if payload:
                    try:
                        json.loads(payload.decode("utf-8"))
                    except ValueError:
                        pass
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise RuntimeError("Configured artifact object storage is unavailable.") from exc
        checksum = hashlib.sha256(bytes(data)).hexdigest()
        # Providers may return a public URL, but the portable contract remains
        # the safe filename plus checksum and ID.
        # Keep a tiny remote index so ID lookup is possible after a new store
        # instance is created.  It contains no user content.
        index = json.dumps({"filename": safe_name, "mime_type": canonical_mime}, separators=(",", ":")).encode()
        index_request = urllib.request.Request(
            self._url(f"artifacts/{artifact_id}.json"),
            data=index,
            method="PUT",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json", "If-None-Match": "*"},
        )
        try:
            with urllib.request.urlopen(index_request, timeout=self.timeout):
                pass
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError("Configured artifact object storage is unavailable.") from exc
        return str(artifact_id), safe_name, len(data), checksum

    def get_bytes(self, id_or_filename: str) -> Optional[Tuple[bytes, str, str]]:
        if not self.blob_configured:
            return self.fallback.get_bytes(id_or_filename)
        value = str(id_or_filename or "")
        if any(token in value for token in ("/", "\\", "\x00")):
            return None
        if _SAFE_ID_RE.fullmatch(value):
            index_key = f"artifacts/{value}.json"
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(self._url(index_key), headers={"Authorization": f"Bearer {self.token}"}),
                    timeout=self.timeout,
                ) as response:
                    record = json.loads(response.read().decode("utf-8"))
                filename = FileSystemArtifactStore._validate_filename(str(record["filename"]))
                mime = str(record.get("mime_type") or "application/octet-stream")
            except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError, KeyError):
                return None
        else:
            try:
                filename = FileSystemArtifactStore._validate_filename(value)
            except ValueError:
                return None
            match = re.search(r"--(art-[A-Za-z0-9_-]+)\.[A-Za-z0-9]+$", filename)
            if not match:
                return None
            mime = ARTIFACT_MIME_TYPES.get(Path(filename).suffix.lower().lstrip("."), "application/octet-stream")
            value = match.group(1)
        try:
            with urllib.request.urlopen(
                urllib.request.Request(self._url(self._key(value, filename), self.endpoint), headers={"Authorization": f"Bearer {self.token}"}),
                timeout=self.timeout,
            ) as response:
                return response.read(), filename, mime
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            return None

    def get_file_path(self, id_or_filename: str) -> Optional[Path]:
        return None if self.blob_configured else self.fallback.get_file_path(id_or_filename)

    def get_download_url(self, artifact_id: str, filename: str) -> str:
        if not self.blob_configured:
            return self.fallback.get_download_url(artifact_id, filename)
        return self._url(self._key(artifact_id, filename), self.public_base_url)

    def exists(self, id_or_filename: str) -> bool:
        if not self.blob_configured:
            return self.fallback.exists(id_or_filename)
        return self.get_bytes(id_or_filename) is not None


# Default global store instance
_DEFAULT_STORE: ArtifactStore = ConfigurableBlobArtifactStore()


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
        """Generates a valid SVG or a small valid PNG preview."""
        if req.format.lower().strip() == "png":
            return ArtifactGeneratorRegistry._render_png(req)
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

    @staticmethod
    def _render_png(req: ArtifactRequest) -> Tuple[bytes, str, Optional[Dict[str, Any]]]:
        """Build a dependency-free RGB PNG suitable for artifact previews."""
        import struct
        import zlib

        width, height = 1200, 800
        # Sard paper background with a clay header and gold accent line.  Text
        # remains available in the accompanying preview metadata; keeping this
        # renderer dependency-free makes PNG generation reliable in serverless.
        paper = (243, 238, 228)
        clay = (190, 74, 36)
        gold = (196, 164, 106)
        rows = []
        for y in range(height):
            color = clay if y < 120 else gold if 120 <= y < 132 else paper
            rows.append(b"\x00" + bytes(color) * width)

        def chunk(kind: bytes, payload: bytes) -> bytes:
            return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

        data = b"\x89PNG\r\n\x1a\n"
        data += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        data += chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
        data += chunk(b"IEND", b"")
        return data, ARTIFACT_MIME_TYPES["png"], {"type": "image", "width": width, "height": height, "title": req.title}

    @staticmethod
    def render_json(req: ArtifactRequest) -> Tuple[bytes, str, Optional[Dict[str, Any]]]:
        payload = dict(req.content_data or {})
        payload.setdefault("title", req.title)
        payload.setdefault("topic", req.topic)
        if req.raw_text:
            payload.setdefault("text", req.raw_text)
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        return data, ARTIFACT_MIME_TYPES["json"], payload

    @staticmethod
    def render_csv(req: ArtifactRequest) -> Tuple[bytes, str, Optional[Dict[str, Any]]]:
        import csv
        import io

        rows = (req.content_data or {}).get("rows")
        if rows is None:
            rows = [{"title": req.title, "topic": req.topic, "text": req.raw_text}]
        stream = io.StringIO(newline="")
        if rows and isinstance(rows[0], dict):
            fields = list(rows[0].keys())
            writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        else:
            writer = csv.writer(stream)
            writer.writerows(rows)
        data = stream.getvalue().encode("utf-8")
        return data, ARTIFACT_MIME_TYPES["csv"], {"type": "table", "rows": len(rows)}

    @staticmethod
    def render_txt(req: ArtifactRequest) -> Tuple[bytes, str, Optional[Dict[str, Any]]]:
        data = (req.raw_text or req.topic or req.title).encode("utf-8")
        return data, ARTIFACT_MIME_TYPES["txt"], {"type": "text", "characters": len(data)}


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
        art_id = f"art-{uuid.uuid4().hex}"
        fmt = request.format.lower().strip()
        kind = request.kind.lower().strip() or "document"
        ext = _FORMAT_EXTENSIONS.get(fmt, f".{re.sub(r'[^A-Za-z0-9]', '', fmt)[:10] or 'bin'}")
        filename = f"sard-artifact{ext}"
        stage = "render"
        try:
            if fmt not in ARTIFACT_MIME_TYPES:
                raise ArtifactValidationError("unsupported_format")
            if request.suggested_filename:
                requested = str(request.suggested_filename)
                if any(token in requested for token in ("/", "\\", "\x00")) or ".." in Path(requested).parts:
                    raise ArtifactValidationError("unsafe_filename", "Artifact filename is unsafe.")
                base_name = Path(requested).stem
                safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", base_name).strip("._") or "sard-artifact"
                filename = f"{safe_name}{ext}"
            else:
                stem = re.sub(r"[^A-Za-z0-9._-]", "_", request.topic[:30]).strip("._") or "sard"
                filename = f"sard-{stem}{ext}"

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
            elif fmt == "json":
                raw_bytes, mime_type, preview = self.registry.render_json(request)
            elif fmt == "csv":
                raw_bytes, mime_type, preview = self.registry.render_csv(request)
            elif fmt == "txt":
                raw_bytes, mime_type, preview = self.registry.render_txt(request)
            else:
                raise ArtifactValidationError("unsupported_format")

            # 2. Verify Render Integrity
            validate_artifact_bytes(fmt, raw_bytes)
            mime_type = ARTIFACT_MIME_TYPES[fmt]

            # 3. Store and verify persistence
            stage = "store"
            active_store = self.store
            _, stored_filename, size_bytes, checksum = active_store.store_bytes(
                artifact_id=art_id,
                filename=filename,
                data=raw_bytes,
                mime_type=mime_type,
                metadata=request.metadata,
            )
            if size_bytes != len(raw_bytes) or checksum != hashlib.sha256(raw_bytes).hexdigest():
                raise RuntimeError("Stored artifact metadata does not match generated bytes.")
            stored = active_store.get_bytes(stored_filename)
            if stored is None or stored[0] != bytes(raw_bytes) or stored[2] != mime_type:
                raise RuntimeError("Stored artifact could not be verified.")
            validate_artifact_bytes(fmt, stored[0])

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

        except ArtifactValidationError as exc:
            logger.exception("Artifact validation failed for format %s", fmt)
            category = exc.category
            return ArtifactResult(
                id=art_id, kind=kind, format=fmt, title=request.title or f"مخرج ثقافي: {request.topic}",
                filename=filename, mime_type=ARTIFACT_MIME_TYPES.get(fmt, "application/octet-stream"),
                size_bytes=0, status="failed", download_url=None,
                error="تعذر التحقق من الملف الناتج. الرجاء إعادة المحاولة لاحقاً.", error_category=category,
            )
        except Exception:
            logger.exception("Artifact generation or storage failed for format %s", fmt)
            category = "storage_error" if stage == "store" else "renderer_exception"
            return ArtifactResult(
                id=art_id,
                kind=kind,
                format=fmt,
                title=request.title or f"مخرج ثقافي: {request.topic}",
                filename=filename,
                mime_type=ARTIFACT_MIME_TYPES.get(fmt, "application/octet-stream"),
                size_bytes=0,
                status="failed",
                download_url=None,
                error=f"تعذر توليد ملف {fmt.upper()} حالياً. الرجاء إعادة المحاولة لاحقاً.",
                error_category=category,
            )

    def orchestrate_from_intent(
        self,
        intent: StructuredIntent,
        raw_text: str = "",
        content_data: Optional[Dict[str, Any]] = None,
        sources: Sequence[Any] = (),
    ) -> List[ArtifactResult]:
        """Generates all requested artifacts derived from structured intent."""
        # Import lazily: sard.agent's package initializer imports the chat
        # service, which in turn exposes this orchestrator.
        from sard.agent.capability_routing import Capability

        results: List[ArtifactResult] = []

        for fmt in intent.requested_formats:
            if fmt == "text":
                continue

            # Determine kind from domain capability (bilingual titles)
            import re as _re
            is_ar = bool(_re.search(r"[\u0600-\u06FF]", intent.extracted_topic))
            if intent.domain_capability == Capability.PRESENTATION_DECK or fmt == "pptx":
                kind = "presentation"
                title = f"عرض تقديمي: {intent.extracted_topic}" if is_ar else f"Presentation: {intent.extracted_topic}"
            elif intent.domain_capability == Capability.CALENDAR_SYNC or fmt == "ics":
                kind = "calendar"
                title = f"تقويم ومواسم: {intent.extracted_topic}" if is_ar else f"Heritage Calendar: {intent.extracted_topic}"
            elif intent.domain_capability == Capability.GREETING_CARD:
                kind = "card"
                title = f"بطاقة تهنئة: {intent.extracted_topic}" if is_ar else f"Greeting Card: {intent.extracted_topic}"
            elif intent.domain_capability == Capability.ETIQUETTE_SIMULATOR or fmt == "svg":
                kind = "diagram"
                title = f"مخطط إرشادي: {intent.extracted_topic}" if is_ar else f"Guidance Diagram: {intent.extracted_topic}"
            elif intent.domain_capability == Capability.RECIPE_CARD:
                kind = "recipe"
                title = f"بطاقة وصفة: {intent.extracted_topic}" if is_ar else f"Recipe Card: {intent.extracted_topic}"
            elif intent.domain_capability == Capability.ORAL_HISTORY:
                kind = "memoir"
                title = f"سيرة وتاريخ شفوي: {intent.extracted_topic}" if is_ar else f"Memoir: {intent.extracted_topic}"
            else:
                kind = "document"
                title = f"تقرير ثقافي: {intent.extracted_topic}" if is_ar else f"Cultural Report: {intent.extracted_topic}"

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
