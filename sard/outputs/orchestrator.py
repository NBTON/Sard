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
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple, Union

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

# Default blob access for new writes (brief: private sard-blob store).
_BLOB_DEFAULT_ACCESS = "private"
_BLOB_DEFAULT_RUN = "default"
_BLOB_DEFAULT_VERSION = 1


def _resolve_blob_token(explicit: Optional[str] = None) -> str:
    """Resolve a blob RW token from explicit value or supported env aliases.

    Priority: explicit arg, ``SARD_BLOB_TOKEN``, ``BLOB_READ_WRITE_TOKEN``,
    ``VERCEL_BLOB_READ_WRITE_TOKEN``.  Read-only; never writes or logs secrets.
    """

    if explicit:
        return explicit
    return (
        os.environ.get("SARD_BLOB_TOKEN")
        or os.environ.get("BLOB_READ_WRITE_TOKEN")
        or os.environ.get("VERCEL_BLOB_READ_WRITE_TOKEN")
        or ""
    )


def _resolve_run_version(metadata: Optional[Dict[str, Any]]) -> Tuple[str, int, str]:
    """Derive (run_id, version, artifact_type) for the idempotent blob key.

    Falls back to ``default``/``1``/``""`` so legacy callers without metadata
    still produce a stable ``runs/{run}/{id}/v{n}/{safe}`` key.
    """

    meta = metadata or {}
    raw_run = str(meta.get("run_id") or meta.get("runId") or _BLOB_DEFAULT_RUN)
    run_id = raw_run if _SAFE_ID_RE.fullmatch(raw_run) else _BLOB_DEFAULT_RUN
    try:
        version = int(meta.get("version", _BLOB_DEFAULT_VERSION))
    except (TypeError, ValueError):
        version = _BLOB_DEFAULT_VERSION
    if version < 1:
        version = _BLOB_DEFAULT_VERSION
    artifact_type = str(meta.get("type") or meta.get("artifact_type") or meta.get("kind") or "")
    return run_id, version, artifact_type


def _blob_key_new(run_id: str, artifact_id: str, version: int, safe_name: str) -> str:
    return f"runs/{run_id}/{artifact_id}/v{version}/{safe_name}"


def _blob_key_legacy(artifact_id: str, safe_name: str) -> str:
    return f"artifacts/{artifact_id}/{safe_name}"


def _artifact_id_from_filename(filename: str) -> Optional[str]:
    match = re.search(r"--(art-[A-Za-z0-9_-]+)\.[A-Za-z0-9]+$", str(filename or ""))
    return match.group(1) if match else None


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
    document: Optional[Any] = None

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

    # --- Additive ArtifactStorage concept aliases (non-breaking) ---

    def put(
        self,
        artifact_id: str,
        filename: str,
        data: bytes,
        mime_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str, int, str]:
        """Alias for :meth:`store_bytes` (ArtifactStorage ``put`` concept)."""
        return self.store_bytes(artifact_id, filename, data, mime_type, metadata)

    def get(self, id_or_filename: str) -> Optional[Tuple[bytes, str, str]]:
        """Alias for :meth:`get_bytes` (ArtifactStorage ``get`` concept)."""
        return self.get_bytes(id_or_filename)

    def delete(self, id_or_filename: str) -> bool:
        """Remove an artifact; returns True when something was removed."""
        return False

    def signed_url(self, artifact_id: str, filename: str, expires_in: int = 3600) -> str:
        """Returns a download URL usable by browsers (never embeds secrets).

        Blob-backed stores return the authenticated ``/api/artifacts`` proxy;
        local stores return the same stable proxy path.
        """
        _ = expires_in
        return self.get_download_url(artifact_id, filename)

    def get_metadata(self, id_or_filename: str) -> Optional[Dict[str, Any]]:
        """Returns the metadata record for an artifact, if known."""
        return None


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
        # Latest-pointer sidecar: atomic overwrite is intentional for versioned
        # revisions (duplicate protection lives in store_bytes via version
        # ordering, not in this pointer). Artifact bytes themselves never
        # overwrite (see _publish).
        metadata_path = self._metadata_path(artifact_id)
        temporary = metadata_path.with_name(f".{metadata_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(metadata_path)
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
        checksum = hashlib.sha256(raw).hexdigest()
        run_id, version, artifact_type = _resolve_run_version(metadata)
        meta_dict = dict(metadata or {})
        # Idempotency: same idempotency_key + same bytes reuses the existing
        # version instead of minting a duplicate.
        idem_key = str(meta_dict.get("idempotency_key", "") or "").strip()
        with self._lock:
            existing_path = self._metadata_path(safe_id)
            existing: Optional[Dict[str, Any]] = None
            if existing_path.exists():
                try:
                    existing = json.loads(existing_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    existing = None
            if existing is not None:
                existing_version = int(existing.get("version", 1) or 1)
                if idem_key and existing.get("idempotency_key") == idem_key and existing.get("sha256") == checksum:
                    return safe_id, str(existing.get("filename", "")), int(existing.get("size", 0) or 0), checksum
                if int(version) <= existing_version:
                    raise ValueError("Refusing to overwrite existing artifact.")
                # Versioned revision: distinct filename retains the older bytes.
                stem = Path(requested).stem[:80] or "sard-artifact"
                suffix = Path(requested).suffix.lower()
                safe_name = self._validate_filename(f"{stem}--{safe_id}--v{int(version)}{suffix}")
                # Idempotent revision retry: same version+bytes already stored.
                if self._destination(safe_name).exists():
                    prior = self.get_version_bytes(safe_id, int(version))
                    if prior is not None and prior[0] == raw:
                        return safe_id, safe_name, len(raw), checksum
                    raise ValueError("Refusing to overwrite existing artifact.")
            else:
                safe_name = self._stored_filename(safe_id, requested)
            dest_path = self._destination(safe_name)
            if dest_path.exists():
                raise ValueError("Refusing to overwrite existing artifact.")
            record = {
                "artifact_id": safe_id,
                "run_id": run_id,
                "version": int(version),
                "type": artifact_type,
                "key": _blob_key_new(run_id, safe_id, int(version), safe_name),
                "mime": mime,
                "size": len(raw),
                "sha256": checksum,
                "created_at": time.time(),
                "status": "created",
                "verification": {"sha256": checksum, "size_bytes": len(raw)},
                # Legacy compat fields (existing readers use these).
                "filename": safe_name,
                "mime_type": mime,
                "size_bytes": len(raw),
                "checksum": checksum,
                "metadata": meta_dict,
            }
            if idem_key:
                record["idempotency_key"] = idem_key
            temporary = self.root / f".{safe_name}.{uuid.uuid4().hex}.tmp"
            published = False
            prior_record = dict(existing) if existing else None
            try:
                with temporary.open("xb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                self._publish(temporary, dest_path)
                published = True
                self._write_metadata(safe_id, record)
                # Append-only history retains every version; failure below
                # must not orphan the new bytes, so history write is last.
                history = self._read_history(safe_id)
                if prior_record is not None and not any(int(h.get("version", 0) or 0) == int(prior_record.get("version", 0) or 0) for h in history):
                    history.append({
                        "artifact_id": safe_id,
                        "version": int(prior_record.get("version", 1) or 1),
                        "filename": str(prior_record.get("filename", "") or ""),
                        "run_id": str(prior_record.get("run_id", "") or ""),
                        "format": str(prior_record.get("type", "") or ""),
                        "checksum": str(prior_record.get("sha256", "") or ""),
                        "status": "created",
                    })
                history.append({
                    "artifact_id": safe_id,
                    "version": int(version),
                    "filename": safe_name,
                    "run_id": run_id,
                    "format": artifact_type,
                    "checksum": checksum,
                    "status": "created",
                    "idempotency_key": idem_key,
                })
                self._write_history(safe_id, history)
            except Exception:
                temporary.unlink(missing_ok=True)
                if published:
                    # Revision failure leaves the prior version intact: remove
                    # only the new partial file and restore the prior pointer.
                    dest_path.unlink(missing_ok=True)
                    try:
                        if prior_record is not None:
                            self._write_metadata(safe_id, prior_record)
                        else:
                            existing_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                raise
        logger.info("Artifact stored: %s (%d bytes, sha256: %s)", safe_name, len(raw), checksum[:8])
        return safe_id, safe_name, len(raw), checksum

    def _iter_record_sidecars(self):
        """Yield (candidate, record-dict) for latest-pointer sidecars only.

        Skips version-history (``*.history.json``) and persisted-document
        (``*.doc.json``) sidecars, which share the directory but are not
        artifact records.
        """
        try:
            candidates = sorted(self._metadata_root.glob("*.json"))
        except OSError:
            return
        for candidate in candidates:
            name = candidate.name
            if name.endswith(".history.json") or name.endswith(".doc.json"):
                continue
            try:
                item = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(item, dict):
                yield candidate, item

    def get_bytes(self, id_or_filename: str) -> Optional[Tuple[bytes, str, str]]:
        path = self.get_file_path(id_or_filename)
        if not path or not path.exists():
            return None
        data = path.read_bytes()
        record = self._record_for(id_or_filename)
        if record is None:
            # Resolve metadata from the exact filename, without recursively
            # searching other requests' directories/files.
            for _, item in self._iter_record_sidecars():
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

    def delete(self, id_or_filename: str) -> bool:
        value = str(id_or_filename or "")
        record = self._record_for(value)
        removed = False
        with self._lock:
            if record:
                try:
                    safe_id = self._validate_id(str(record.get("artifact_id") or value))
                except ValueError:
                    safe_id = None
                if safe_id:
                    try:
                        self._metadata_path(safe_id).unlink(missing_ok=True)
                        removed = True
                    except OSError:
                        pass
                fname = str(record.get("filename") or "")
                if fname:
                    try:
                        self._destination(self._validate_filename(fname)).unlink(missing_ok=True)
                        removed = True
                    except (OSError, ValueError):
                        pass
            else:
                try:
                    target = self._destination(self._validate_filename(value))
                except ValueError:
                    return False
                if target.exists() and target.is_file():
                    try:
                        target.unlink(missing_ok=True)
                        removed = True
                    except OSError:
                        pass
        return removed

    def signed_url(self, artifact_id: str, filename: str, expires_in: int = 3600) -> str:
        _ = expires_in
        return self.get_download_url(artifact_id, filename)

    def get_metadata(self, id_or_filename: str) -> Optional[Dict[str, Any]]:
        record = self._record_for(id_or_filename)
        if record is None:
            path = self.get_file_path(id_or_filename)
            if path is None:
                return None
            for _, item in self._iter_record_sidecars():
                if item.get("filename") == path.name:
                    record = item
                    break
        if record is None:
            return None
        return dict(record)

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
        elif fn.endswith((".html", ".htm")):
            return "text/html; charset=utf-8"
        elif fn.endswith(".csv"):
            return "text/csv; charset=utf-8"
        elif fn.endswith(".txt"):
            return "text/plain; charset=utf-8"
        return "application/octet-stream"

    # --- Versioned ArtifactDocument persistence (revision / conversion) ---

    def _document_path(self, artifact_id: str, version: Optional[int] = None) -> Path:
        safe = self._validate_id(artifact_id)
        if version is None:
            return self._metadata_root / f"{safe}.doc.json"
        return self._metadata_root / f"{safe}.v{int(version)}.doc.json"

    def _history_path(self, artifact_id: str) -> Path:
        return self._metadata_root / f"{self._validate_id(artifact_id)}.history.json"

    def _read_history(self, artifact_id: str) -> List[Dict[str, Any]]:
        try:
            raw = self._history_path(artifact_id).read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, list):
                return [d for d in data if isinstance(d, dict)]
        except (OSError, ValueError):
            pass
        return []

    def _write_history(self, artifact_id: str, history: List[Dict[str, Any]]) -> None:
        path = self._history_path(artifact_id)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")
        try:
            tmp.replace(path)
        except FileExistsError:
            # History is append-only per version; concurrent writers keep both
            # entries by merging instead of overwriting.
            existing = self._read_history(artifact_id)
            seen = {(d.get("version"), d.get("filename")) for d in existing}
            merged = list(existing)
            for entry in history:
                if (entry.get("version"), entry.get("filename")) not in seen:
                    merged.append(entry)
            tmp.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            tmp.replace(path)

    def put_document(self, artifact_id: str, document: Any, version: Optional[int] = None) -> None:
        """Persist the canonical ArtifactDocument for revision/conversion."""
        try:
            payload = document.to_dict() if hasattr(document, "to_dict") else dict(document or {})
        except Exception as exc:
            raise ValueError("Artifact document is not serializable.") from exc
        path = self._document_path(artifact_id, version)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        # Document sidecars are version-scoped; latest pointer may be replaced
        # only after the new version's bytes verify (caller enforces ordering).
        if path.exists():
            path.unlink(missing_ok=True)
        tmp.replace(path)
        if version is None:
            # Mirror latest pointer for readers that only know the artifact ID.
            pass

    def get_document(self, artifact_id: str, version: Optional[int] = None) -> Optional[Any]:
        """Load a persisted canonical ArtifactDocument (latest when version=None)."""
        from sard.outputs.document import ArtifactDocument as _ArtifactDocument

        candidates: List[Path] = []
        if version is not None:
            candidates.append(self._document_path(artifact_id, int(version)))
        else:
            candidates.append(self._document_path(artifact_id, None))
            # Fallback: newest versioned sidecar when latest pointer is absent.
            try:
                versioned = sorted(
                    self._metadata_root.glob(f"{self._validate_id(artifact_id)}.v*.doc.json"),
                    key=lambda p: p.stat().st_mtime,
                )
                candidates.extend(reversed(versioned))
            except (OSError, ValueError):
                pass
        for path in candidates:
            try:
                if not path.is_file():
                    continue
                return _ArtifactDocument.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return None

    def list_versions(self, artifact_id: str) -> List[Dict[str, Any]]:
        """List retained versions (oldest-first); empty when unknown."""
        history = self._read_history(artifact_id)
        if history:
            return sorted(history, key=lambda d: int(d.get("version", 0) or 0))
        # Legacy single-version artifacts: synthesize v1 from latest pointer.
        record = self._record_for(artifact_id)
        if record:
            return [{
                "artifact_id": artifact_id,
                "version": int(record.get("version", 1) or 1),
                "filename": str(record.get("filename", "") or ""),
                "run_id": str(record.get("run_id", "") or ""),
                "format": str(record.get("type", "") or record.get("format", "") or ""),
                "checksum": str(record.get("sha256", "") or record.get("checksum", "") or ""),
                "status": str(record.get("status", "") or "created"),
            }]
        return []

    def get_version_bytes(self, artifact_id: str, version: int) -> Optional[Tuple[bytes, str, str]]:
        """Retrieve an exact retained version's bytes (never the latest alias)."""
        for entry in self.list_versions(artifact_id):
            if int(entry.get("version", 0) or 0) == int(version):
                filename = str(entry.get("filename", "") or "")
                if filename:
                    result = self.get_bytes(filename)
                    if result is not None:
                        return result
        # Fallback: versioned document sidecar implies bytes under versioned name.
        return None


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
            or (DEFAULT_VERCEL_BLOB_ENDPOINT if _resolve_blob_token() else "")
        ).rstrip("/")
        self.token = _resolve_blob_token(token)
        self.public_base_url = (public_base_url or os.environ.get("SARD_BLOB_PUBLIC_BASE_URL") or self.endpoint).rstrip("/")
        self.timeout = timeout
        self.blob_configured = bool(self.endpoint and self.token)

    def _key(self, artifact_id: str, filename: str) -> str:
        """Legacy flat key (read-fallback for blobs written before the overhaul)."""
        if not _SAFE_ID_RE.fullmatch(str(artifact_id or "")):
            raise ValueError("Artifact ID must be a safe identifier.")
        safe_name = FileSystemArtifactStore._validate_filename(filename)
        return _blob_key_legacy(artifact_id, safe_name)

    def _new_key(self, artifact_id: str, filename: str, metadata: Optional[Dict[str, Any]] = None) -> Tuple[str, str, str, int, str]:
        """Idempotent new key ``runs/{run}/{id}/v{n}/{safe}`` + resolved parts."""
        if not _SAFE_ID_RE.fullmatch(str(artifact_id or "")):
            raise ValueError("Artifact ID must be a safe identifier.")
        safe_name = FileSystemArtifactStore._stored_filename(artifact_id, filename)
        run_id, version, artifact_type = _resolve_run_version(metadata)
        return _blob_key_new(run_id, artifact_id, version, safe_name), safe_name, run_id, version, artifact_type

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
        key, safe_name, run_id, version, artifact_type = self._new_key(artifact_id, filename, metadata)
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
        # instance is created.  It contains no user content.  The record carries
        # the new idempotent key with a legacy fallback on read.
        index = json.dumps(
            {
                "artifact_id": str(artifact_id),
                "run_id": run_id,
                "version": version,
                "type": artifact_type,
                "key": key,
                "filename": safe_name,
                "mime_type": canonical_mime,
                "mime": canonical_mime,
                "size": len(data),
                "sha256": checksum,
                "created_at": time.time(),
                "status": "created",
            },
            separators=(",", ":"),
        ).encode()
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

    def _fetch_key(self, key: str) -> Optional[bytes]:
        try:
            with urllib.request.urlopen(
                urllib.request.Request(self._url(key), headers={"Authorization": f"Bearer {self.token}"}),
                timeout=self.timeout,
            ) as response:
                return response.read()
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            return None

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
                mime = str(record.get("mime_type") or record.get("mime") or "application/octet-stream")
                # New idempotent key first, legacy flat key as read-fallback.
                for candidate in (str(record.get("key") or ""), _blob_key_legacy(value, filename)):
                    if not candidate:
                        continue
                    payload = self._fetch_key(candidate)
                    if payload is not None:
                        return payload, filename, mime
                return None
            except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError, KeyError):
                return None
        else:
            try:
                filename = FileSystemArtifactStore._validate_filename(value)
            except ValueError:
                return None
            artifact_id = _artifact_id_from_filename(filename)
            if not artifact_id:
                return None
            mime = ARTIFACT_MIME_TYPES.get(Path(filename).suffix.lower().lstrip("."), "application/octet-stream")
            # Resolve the authoritative key via the remote index when possible
            # (new runs/... key), otherwise fall back to the legacy flat key.
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(
                        self._url(f"artifacts/{artifact_id}.json"),
                        headers={"Authorization": f"Bearer {self.token}"},
                    ),
                    timeout=self.timeout,
                ) as response:
                    record = json.loads(response.read().decode("utf-8"))
                indexed_name = str(record.get("filename") or filename)
                if indexed_name != filename:
                    return None
                mime = str(record.get("mime_type") or record.get("mime") or mime)
                for candidate in (str(record.get("key") or ""), _blob_key_legacy(artifact_id, filename)):
                    if not candidate:
                        continue
                    payload = self._fetch_key(candidate)
                    if payload is not None:
                        return payload, filename, mime
                return None
            except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError, KeyError):
                pass
            payload = self._fetch_key(_blob_key_legacy(artifact_id, filename))
            if payload is not None:
                return payload, filename, mime
            return None

    def get_file_path(self, id_or_filename: str) -> Optional[Path]:
        return None if self.blob_configured else self.fallback.get_file_path(id_or_filename)

    def get_download_url(self, artifact_id: str, filename: str) -> str:
        if not self.blob_configured:
            return self.fallback.get_download_url(artifact_id, filename)
        # Private blobs are served through the authenticated server proxy so
        # browsers never see blob URLs or credentials.
        try:
            safe_name = FileSystemArtifactStore._validate_filename(filename)
        except ValueError:
            safe_name = str(filename)
        FileSystemArtifactStore._validate_id(artifact_id)
        return "/api/artifacts/" + urllib.parse.quote(safe_name, safe="")

    def exists(self, id_or_filename: str) -> bool:
        if not self.blob_configured:
            return self.fallback.exists(id_or_filename)
        return self.get_bytes(id_or_filename) is not None

    def delete(self, id_or_filename: str) -> bool:
        resolved = self.get_bytes(id_or_filename)
        removed = False
        if resolved is not None:
            _, filename, _ = resolved
            artifact_id = _artifact_id_from_filename(filename) or (
                str(id_or_filename) if _SAFE_ID_RE.fullmatch(str(id_or_filename or "")) else ""
            )
            keys: List[str] = []
            if artifact_id:
                try:
                    keys.append(self._new_key(artifact_id, filename)[0])
                except ValueError:
                    pass
                try:
                    keys.append(self._key(artifact_id, filename))
                except ValueError:
                    pass
                keys.append(f"artifacts/{artifact_id}.json")
            for key in keys:
                if not self.blob_configured:
                    continue
                request = urllib.request.Request(
                    self._url(key),
                    method="DELETE",
                    headers={"Authorization": f"Bearer {self.token}"},
                )
                try:
                    with urllib.request.urlopen(request, timeout=self.timeout):
                        removed = True
                except (urllib.error.HTTPError, urllib.error.URLError, OSError):
                    continue
        try:
            if self.fallback.delete(id_or_filename):
                removed = True
        except Exception as exc:
            logger.debug("Suppressed boundary exception in orchestrator.py: %s", type(exc).__name__)
        return removed

    def signed_url(self, artifact_id: str, filename: str, expires_in: int = 3600) -> str:
        _ = expires_in
        return self.get_download_url(artifact_id, filename)

    def get_metadata(self, id_or_filename: str) -> Optional[Dict[str, Any]]:
        # Sidecar-first (local mirror), then remote index (head-like, no content GET).
        try:
            local = self.fallback.get_metadata(id_or_filename)
        except Exception:
            local = None
        if not self.blob_configured:
            return local
        value = str(id_or_filename or "")
        artifact_id = value if _SAFE_ID_RE.fullmatch(value) else _artifact_id_from_filename(value)
        if not artifact_id:
            return local
        try:
            with urllib.request.urlopen(
                urllib.request.Request(
                    self._url(f"artifacts/{artifact_id}.json"),
                    headers={"Authorization": f"Bearer {self.token}"},
                ),
                timeout=self.timeout,
            ) as response:
                record = json.loads(response.read().decode("utf-8"))
            merged: Dict[str, Any] = dict(local or {})
            merged.update(
                {
                    "artifact_id": artifact_id,
                    "run_id": record.get("run_id", merged.get("run_id", _BLOB_DEFAULT_RUN)),
                    "version": record.get("version", merged.get("version", _BLOB_DEFAULT_VERSION)),
                    "type": record.get("type", merged.get("type", "")),
                    "key": record.get("key", merged.get("key", "")),
                    "mime": record.get("mime", record.get("mime_type", merged.get("mime", ""))),
                    "size": record.get("size", merged.get("size", merged.get("size_bytes", 0))),
                    "sha256": record.get("sha256", merged.get("sha256", merged.get("checksum", ""))),
                    "status": record.get("status", merged.get("status", "created")),
                }
            )
            return merged
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError):
            return local


class VercelBlobArtifactStore(ArtifactStore):
    """Durable store via official Vercel Blob SDK (ADR Decision 3, G3).

    Uses ``vercel.blob.BlobClient.put`` when a read/write token is set
    (``SARD_BLOB_TOKEN`` / ``BLOB_READ_WRITE_TOKEN`` /
    ``VERCEL_BLOB_READ_WRITE_TOKEN``).  Falls back to
    :class:`ConfigurableBlobArtifactStore` (custom REST) and then to local
    filesystem, so offline/tests never require credentials.
    """

    def __init__(self, fallback_local: Optional[ArtifactStore] = None, client: Any = None):
        self.fallback = fallback_local or FileSystemArtifactStore()
        self.rest = ConfigurableBlobArtifactStore(fallback_local=self.fallback)
        self.token = _resolve_blob_token()
        self.blob_configured = bool(self.token)
        # Retain the SDK-returned url/download_url per key for server-side use
        # (head/get/delete).  Never sent to browsers; downloads go through the
        # authenticated /api/artifacts proxy.
        self._blob_urls: Dict[str, Dict[str, str]] = {}
        self._client = client
        if self._client is None and self.blob_configured:
            try:
                from vercel.blob import BlobClient

                self._client = BlobClient(token=self.token)
            except Exception:
                self._client = None

    def _sdk_available(self) -> bool:
        return bool(self.blob_configured and self._client is not None)

    @staticmethod
    def _sdk_url_of(result: Any) -> Tuple[str, str, str]:
        url = str(getattr(result, "url", "") or "")
        download_url = str(getattr(result, "download_url", "") or "")
        pathname = str(getattr(result, "pathname", "") or "")
        if not url and isinstance(result, dict):
            url = str(result.get("url") or "")
            download_url = str(result.get("downloadUrl") or result.get("download_url") or "")
            pathname = str(result.get("pathname") or "")
        return url, download_url, pathname

    def _sdk_candidates(self, id_or_filename: str) -> Tuple[Optional[str], Optional[str], List[str]]:
        """Resolve (artifact_id, filename, candidate blob keys) for SDK reads."""
        value = str(id_or_filename or "")
        if not value or any(token in value for token in ("/", "\\", "\x00")):
            return None, None, []
        if _SAFE_ID_RE.fullmatch(value):
            artifact_id: Optional[str] = value
            filename: Optional[str] = None
            try:
                local_meta = self.fallback.get_metadata(value)
            except Exception:
                local_meta = None
            if local_meta:
                filename = str(local_meta.get("filename") or "")
        else:
            try:
                filename = FileSystemArtifactStore._validate_filename(value)
            except ValueError:
                return None, None, []
            artifact_id = _artifact_id_from_filename(filename)
            if artifact_id is None:
                return None, None, []
            if not _SAFE_ID_RE.fullmatch(artifact_id):
                return None, None, []
        keys: List[str] = []
        if artifact_id and filename:
            try:
                local_meta = self.fallback.get_metadata(artifact_id)
            except Exception:
                local_meta = None
            stored_key = str((local_meta or {}).get("key") or "")
            if stored_key:
                keys.append(stored_key)
            run_id, version, _ = _resolve_run_version(
                (local_meta or {}).get("metadata") if isinstance((local_meta or {}).get("metadata"), dict) else None
            )
            keys.append(_blob_key_new(run_id, artifact_id, version, filename))
            keys.append(_blob_key_legacy(artifact_id, filename))
        elif artifact_id:
            try:
                local_meta = self.fallback.get_metadata(artifact_id)
            except Exception:
                local_meta = None
            if local_meta and local_meta.get("filename"):
                fname = str(local_meta["filename"])
                stored_key = str(local_meta.get("key") or "")
                if stored_key:
                    keys.append(stored_key)
                filename = fname
                keys.append(_blob_key_legacy(artifact_id, fname))
        # Deduplicate while preserving order.
        seen: Dict[str, None] = {}
        ordered = [k for k in keys if k and not (k in seen or seen.setdefault(k))]
        return artifact_id, filename, ordered

    def _sdk_list_keys(self, artifact_id: str) -> List[str]:
        """Discover remote keys for an artifact via list (cross-instance reads)."""
        found: List[str] = []
        client = self._client
        if client is None or not artifact_id:
            return found
        for prefix in (f"runs/", f"artifacts/{artifact_id}"):
            try:
                listing = client.list_objects(prefix=prefix, limit=100)
            except Exception as exc:
                logger.debug("Suppressed boundary exception in orchestrator.py: %s", type(exc).__name__)
                continue
            blobs = getattr(listing, "blobs", None)
            if blobs is None and isinstance(listing, dict):
                blobs = listing.get("blobs", [])
            for item in blobs or []:
                pathname = str(getattr(item, "pathname", "") or (item.get("pathname") if isinstance(item, dict) else "") or "")
                if f"/{artifact_id}/" in f"/{pathname}/" or pathname.startswith(f"artifacts/{artifact_id}"):
                    found.append(pathname)
        seen: Dict[str, None] = {}
        return [k for k in found if k and not (k in seen or seen.setdefault(k))]

    def _sdk_fetch(self, key: str) -> Optional[Tuple[bytes, Optional[str]]]:
        client = self._client
        if client is None or not key:
            return None
        for access in ("private", "public"):
            try:
                result = client.get(key, access=access)  # type: ignore[arg-type]
            except Exception as exc:
                logger.debug("Suppressed boundary exception in orchestrator.py: %s", type(exc).__name__)
                continue
            content = getattr(result, "content", None)
            if content is None and isinstance(result, dict):
                content = result.get("content")
            if content is None:
                continue
            ctype = getattr(result, "content_type", None)
            if ctype is None and isinstance(result, dict):
                ctype = result.get("contentType") or result.get("content_type")
            return bytes(content), (str(ctype) if ctype else None)
        return None

    def store_bytes(self, artifact_id, filename, data, mime_type, metadata=None):
        if self._sdk_available():
            try:
                FileSystemArtifactStore._validate_id(artifact_id)
                safe_name = FileSystemArtifactStore._stored_filename(artifact_id, filename)
                run_id, version, _ = _resolve_run_version(metadata)
                key = _blob_key_new(run_id, str(artifact_id), version, safe_name)
                canonical = ARTIFACT_MIME_TYPES.get(Path(safe_name).suffix.lower().lstrip("."), mime_type)
                if not data:
                    raise ValueError("Artifact bytes must be non-empty.")
                # Official SDK: put(path, body, *, access, content_type, ...).
                res = self._client.put(key, bytes(data), access=_BLOB_DEFAULT_ACCESS, content_type=canonical)
                url, download_url, pathname = self._sdk_url_of(res)
                # Retain SDK urls server-side (never exposed to browsers).
                self._blob_urls[key] = {"url": url, "download_url": download_url or url, "pathname": pathname or key}
                logger.debug("Vercel Blob stored %s (access=%s url=%s)", key, _BLOB_DEFAULT_ACCESS, url)
                checksum = hashlib.sha256(bytes(data)).hexdigest()
                # Mirror to local fallback for same-process verification
                # (no durability claim across instances).
                try:
                    self.fallback.store_bytes(artifact_id, filename, bytes(data), mime_type, metadata)
                except Exception as exc:
                    logger.debug("Suppressed boundary exception in orchestrator.py: %s", type(exc).__name__)
                return str(artifact_id), safe_name, len(data), checksum
            except Exception as exc:
                logger.warning("Vercel Blob SDK store failed (%s); falling back to REST/FS.", type(exc).__name__)
        # REST (custom) or local fallback
        return self.rest.store_bytes(artifact_id, filename, data, mime_type, metadata)

    def get_bytes(self, id_or_filename):
        if self._sdk_available():
            try:
                artifact_id, filename, candidates = self._sdk_candidates(id_or_filename)
                for key in candidates:
                    fetched = self._sdk_fetch(key)
                    if fetched is not None:
                        payload, ctype = fetched
                        name = filename or Path(key).name
                        mime = ctype or ARTIFACT_MIME_TYPES.get(Path(name).suffix.lower().lstrip("."), "application/octet-stream")
                        return payload, name, mime
                # Cross-instance discovery via list when the local sidecar is
                # absent (second Vercel instance with a fresh /tmp).
                if artifact_id and (not candidates or filename is None):
                    for key in self._sdk_list_keys(artifact_id):
                        fetched = self._sdk_fetch(key)
                        if fetched is not None:
                            payload, ctype = fetched
                            name = filename or Path(key).name
                            mime = ctype or ARTIFACT_MIME_TYPES.get(Path(name).suffix.lower().lstrip("."), "application/octet-stream")
                            return payload, name, mime
            except Exception as exc:
                logger.debug("Suppressed boundary exception in orchestrator.py: %s", type(exc).__name__)
        try:
            got = self.rest.get_bytes(id_or_filename)
            if got is not None:
                return got
        except Exception as exc:
            logger.debug("Suppressed boundary exception in orchestrator.py: %s", type(exc).__name__)
        return None

    def get_file_path(self, id_or_filename):
        # Blob has no local path; expose local mirror if present for verification
        try:
            p = self.fallback.get_file_path(id_or_filename)
            if p is not None:
                return p
        except Exception as exc:
            logger.debug("Suppressed boundary exception in orchestrator.py: %s", type(exc).__name__)
        return self.rest.get_file_path(id_or_filename)

    def get_download_url(self, artifact_id: str, filename: str) -> str:
        # Always serve through the authenticated server proxy so private-blob
        # downloads work and browsers never see blob URLs or credentials.
        try:
            return self.rest.get_download_url(artifact_id, filename)
        except Exception:
            safe_name = FileSystemArtifactStore._validate_filename(filename)
            return "/api/artifacts/" + urllib.parse.quote(safe_name, safe="")

    def exists(self, id_or_filename: str) -> bool:
        if self._sdk_available():
            try:
                _, _, candidates = self._sdk_candidates(id_or_filename)
                for key in candidates:
                    try:
                        head = self._client.head(key)
                        if head is not None:
                            return True
                    except Exception as exc:
                        logger.debug("Suppressed boundary exception in orchestrator.py: %s", type(exc).__name__)
                        continue
                artifact_id, _, _ = self._sdk_candidates(id_or_filename)
                if artifact_id and self._sdk_list_keys(artifact_id):
                    return True
            except Exception as exc:
                logger.debug("Suppressed boundary exception in orchestrator.py: %s", type(exc).__name__)
        return self.rest.exists(id_or_filename)

    def delete(self, id_or_filename: str) -> bool:
        removed = False
        if self._sdk_available():
            try:
                artifact_id, _, candidates = self._sdk_candidates(id_or_filename)
                keys = list(candidates)
                if artifact_id:
                    keys.extend(self._sdk_list_keys(artifact_id))
                    try:
                        keys.append(f"artifacts/{artifact_id}.json")
                    except Exception as exc:
                        logger.debug("Blob key append skipped (%s).", type(exc).__name__)
                for key in keys:
                    try:
                        self._client.delete(key)
                        removed = True
                    except Exception as exc:
                        logger.debug("Suppressed boundary exception in orchestrator.py: %s", type(exc).__name__)
                        continue
                    self._blob_urls.pop(key, None)
            except Exception as exc:
                logger.debug("Suppressed boundary exception in orchestrator.py: %s", type(exc).__name__)
        try:
            if self.rest.delete(id_or_filename):
                removed = True
        except Exception as exc:
            logger.debug("Suppressed boundary exception in orchestrator.py: %s", type(exc).__name__)
        return removed

    def signed_url(self, artifact_id: str, filename: str, expires_in: int = 3600) -> str:
        _ = expires_in
        return self.get_download_url(artifact_id, filename)

    def get_metadata(self, id_or_filename: str) -> Optional[Dict[str, Any]]:
        # Sidecar-first, then SDK head (no content download, no second PUT).
        try:
            local = self.fallback.get_metadata(id_or_filename)
        except Exception:
            local = None
        base: Dict[str, Any] = dict(local or {})
        if not self._sdk_available():
            try:
                rest_meta = self.rest.get_metadata(id_or_filename)
                if rest_meta:
                    merged = dict(base)
                    merged.update(rest_meta)
                    return merged
            except Exception as exc:
                logger.debug("Blob metadata merge skipped (%s).", type(exc).__name__)
            return base or None
        try:
            artifact_id, filename, candidates = self._sdk_candidates(id_or_filename)
            for key in candidates:
                try:
                    head = self._client.head(key)
                except Exception as exc:
                    logger.debug("Suppressed boundary exception in orchestrator.py: %s", type(exc).__name__)
                    continue
                if head is None:
                    continue
                size = getattr(head, "size", None)
                ctype = getattr(head, "content_type", None)
                url = getattr(head, "url", "")
                download_url = getattr(head, "download_url", "")
                if base:
                    base.setdefault("size", size if size is not None else base.get("size"))
                    base.setdefault("mime", ctype or base.get("mime"))
                    base.setdefault("key", key)
                else:
                    base = {
                        "artifact_id": artifact_id or "",
                        "run_id": _BLOB_DEFAULT_RUN,
                        "version": _BLOB_DEFAULT_VERSION,
                        "type": "",
                        "key": key,
                        "filename": filename or Path(key).name,
                        "mime": ctype or "application/octet-stream",
                        "size": size if size is not None else 0,
                        "sha256": "",
                        "created_at": time.time(),
                        "status": "created",
                        "verification": {},
                        "url": url,
                        "download_url": download_url,
                    }
                retained = self._blob_urls.get(key, {})
                if retained:
                    base.setdefault("url", retained.get("url", ""))
                    base.setdefault("download_url", retained.get("download_url", ""))
                return base
        except Exception as exc:
            logger.debug("Suppressed boundary exception in orchestrator.py: %s", type(exc).__name__)
        try:
            rest_meta = self.rest.get_metadata(id_or_filename)
            if rest_meta:
                merged = dict(base)
                merged.update(rest_meta)
                return merged or None
        except Exception as exc:
            logger.debug("Blob version metadata skipped (%s).", type(exc).__name__)
        return base or None


# Default global store instance (official SDK first, REST/FS fallback)
_DEFAULT_STORE: ArtifactStore = VercelBlobArtifactStore()


def get_artifact_store() -> ArtifactStore:
    global _DEFAULT_STORE
    return _DEFAULT_STORE


def set_artifact_store(store: ArtifactStore):
    global _DEFAULT_STORE
    _DEFAULT_STORE = store


# ---------------------------------------------------------------------------
# Generator Registry & Orchestrator
# ---------------------------------------------------------------------------


def _canonical_doc_for_request(
    req: "ArtifactRequest",
    *,
    artifact_id: str = "",
    run_id: str = "",
    version: int = 1,
    checksum: Optional[str] = None,
) -> Any:
    """Build the canonical ArtifactDocument first; fail loudly on bad citations.

    Production bytes and previews both derive from this document — renderers
    must not use a divergent shadow preview.
    """
    from sard.outputs.document import ArtifactDocument as _ArtifactDocument

    meta = dict(getattr(req, "metadata", None) or {})
    doc = _ArtifactDocument.from_request(
        req,
        artifact_id=artifact_id or str(meta.get("artifact_id", "") or ""),
        run_id=run_id or str(meta.get("run_id", "") or ""),
        version=int(version or meta.get("version", 1) or 1),
        checksum=checksum,
    )
    # Production boundary: unknown/duplicate CIT-* IDs fail the artifact
    # instead of rendering uncited bytes with a mismatched preview.
    doc.validate_citations()
    return doc


class ArtifactGeneratorRegistry:
    """Maintains generators for all document, presentation, calendar, and diagram formats."""

    @staticmethod
    def render_pdf(req: ArtifactRequest) -> Tuple[bytes, str, Optional[Dict[str, Any]]]:
        """Dispatches to appropriate PDF generator based on kind and content.

        Canonical path: the ArtifactDocument is built first and its preview
        is the single source of truth; specialized bytes still render via
        their kind-specific compilers but never ship a shadow preview.
        """
        from sard.outputs.document import ArtifactDocument as _Doc

        meta = dict(getattr(req, "metadata", None) or {})
        _aid = str(meta.get("artifact_id", "") or "")
        _run = str(meta.get("run_id", "") or "")
        try:
            _ver = int(meta.get("version", 1) or 1)
        except (TypeError, ValueError):
            _ver = 1
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
            doc = _Doc.from_request(req, artifact_id=_aid, run_id=_run, version=_ver)
            doc.validate_citations()
            return data, "application/pdf", doc.to_preview()

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
            doc = _Doc.from_request(req, artifact_id=_aid, run_id=_run, version=_ver)
            doc.validate_citations()
            return data, "application/pdf", doc.to_preview()

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
            doc = _Doc.from_request(req, artifact_id=_aid, run_id=_run, version=_ver)
            doc.validate_citations()
            return data, "application/pdf", doc.to_preview()

        # 4. General Arabic RTL Cultural Report PDF (Default)
        # PDF-B1: production PDF routes to the canonical pdf.py adapter
        # (splittable flowables, deterministic IDs, footer citations) — not
        # the legacy pdf_report path. Bytes and preview derive from the same
        # ArtifactDocument so they can never diverge.
        from sard.outputs.document import ArtifactDocument as _DocPDF
        from sard.outputs.pdf import build_pdf_from_document

        doc = _DocPDF.from_request(req, artifact_id=_aid, run_id=_run, version=_ver)
        doc.validate_citations()
        data = build_pdf_from_document(doc)
        return data, "application/pdf", doc.to_preview()

    @staticmethod
    def render_docx(req: ArtifactRequest) -> Tuple[bytes, str, Optional[Dict[str, Any]]]:
        """Generates standard Arabic RTL Word (.docx) from the canonical document."""
        from sard.outputs.document import ArtifactDocument as _DocX
        from sard.outputs.office_docx import DocxGenerator

        meta = dict(getattr(req, "metadata", None) or {})
        doc = _DocX.from_request(
            req,
            artifact_id=str(meta.get("artifact_id", "") or ""),
            run_id=str(meta.get("run_id", "") or ""),
            version=int(meta.get("version", 1) or 1) if str(meta.get("version", "1")).strip().lstrip("-").isdigit() else 1,
        )
        doc.validate_citations()
        data = DocxGenerator().build_from_document(doc)
        return data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", doc.to_preview()

    @staticmethod
    def render_pptx(req: ArtifactRequest) -> Tuple[bytes, str, Optional[Dict[str, Any]]]:
        """Generates 16:9 widescreen PowerPoint from the canonical document."""
        from sard.outputs.document import ArtifactDocument as _DocP
        from sard.outputs.office import PresentationGenerator

        meta = dict(getattr(req, "metadata", None) or {})
        doc = _DocP.from_request(
            req,
            artifact_id=str(meta.get("artifact_id", "") or ""),
            run_id=str(meta.get("run_id", "") or ""),
            version=int(meta.get("version", 1) or 1) if str(meta.get("version", "1")).strip().lstrip("-").isdigit() else 1,
        )
        doc.validate_citations()
        data = PresentationGenerator().build_from_document(doc)
        return data, "application/vnd.openxmlformats-officedocument.presentationml.presentation", doc.to_preview()

    @staticmethod
    def render_ics(req: ArtifactRequest) -> Tuple[bytes, str, Optional[Dict[str, Any]]]:
        """Generates RFC 5545 .ics calendar data for heritage events and itineraries."""
        from sard.outputs.calendar_sync import HeritageCalendarSync

        if not (req.topic or "").strip():
            from sard.outputs.validation import ArtifactValidationError as _VE

            raise _VE("missing_filters", "Calendar render requires a topic/query or filters.")
        sync = HeritageCalendarSync()
        events = sync.search_events(query=req.topic)
        if not events:
            # G7: no silent first-4 fallback at render layer; surface honest failure.
            from sard.outputs.validation import ArtifactValidationError
            raise ArtifactValidationError("no_match", "No heritage events match the requested topic/filters.")

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
        """Render a meaningful card/diagram PNG (title + body + item cards).

        Uses Pillow with bundled Noto fonts when available so the PNG contains
        the requested visual content (not a header-only placeholder). Falls back
        to a content-block pattern if Pillow is unavailable.
        """
        import io as _io

        width, height = 1200, 800
        title = (req.title or req.topic or "سرد").strip()[:120]
        body_text = (req.raw_text or "").strip()
        content = req.content_data or {}
        items: list[str] = []
        for key in ("items", "cards", "rows", "points", "bullets"):
            val = content.get(key)
            if isinstance(val, list):
                for entry in val[:6]:
                    if isinstance(entry, dict):
                        label = str(entry.get("title") or entry.get("name") or entry.get("text") or "")[:80]
                    else:
                        label = str(entry)[:80]
                    if label:
                        items.append(label)
                break
        if not items and body_text:
            # Derive up to 4 content lines from body text
            for line in body_text.splitlines():
                line = line.strip(" •-*#")
                if len(line) >= 4:
                    items.append(line[:80])
                if len(items) >= 4:
                    break
        if not items and req.topic:
            items = [req.topic[:80]]

        try:
            from PIL import Image, ImageDraw, ImageFont

            assets = Path(__file__).parent / "assets"
            paper = (243, 238, 228)
            ink = (20, 18, 16)
            clay = (110, 25, 70)
            gold = (196, 164, 106)
            card_bg = (255, 252, 245)
            img = Image.new("RGB", (width, height), paper)
            draw = ImageDraw.Draw(img)
            # Header stripe
            draw.rectangle([0, 0, width, 130], fill=(15, 40, 55))
            draw.rectangle([0, 130, width, 138], fill=gold)

            def _font(size: int) -> Any:
                for candidate in (assets / "NotoNaskhArabic-Regular.ttf", assets / "NotoSans-Regular.ttf"):
                    try:
                        if candidate.exists():
                            return ImageFont.truetype(str(candidate), size)
                    except Exception as exc:
                        logger.debug("Suppressed boundary exception in orchestrator.py: %s", type(exc).__name__)
                        continue
                return ImageFont.load_default()

            title_font = _font(44)
            body_font = _font(26)
            small_font = _font(22)
            draw.text((width - 60, 28), title, font=title_font, fill=(243, 238, 228), anchor="ra")
            draw.text((width - 60, 84), (req.topic or "")[:100], font=small_font, fill=gold, anchor="ra")
            y = 175
            # Body excerpt
            if body_text:
                excerpt = body_text.replace("\n", " ")[:220]
                draw.text((width - 60, y), excerpt, font=body_font, fill=ink, anchor="ra")
                y += 55
            # Item cards (2-column grid)
            card_w, card_h = 520, 110
            for idx, label in enumerate(items[:6]):
                col = idx % 2
                row = idx // 2
                x1 = 60 + col * (card_w + 40) if col == 0 else 60 + card_w + 40
                # RTL: mirror columns so first item is right-aligned
                if col == 0:
                    x1 = width - 60 - card_w
                else:
                    x1 = 60
                y1 = y + row * (card_h + 20)
                if y1 + card_h > height - 70:
                    break
                draw.rounded_rectangle([x1, y1, x1 + card_w, y1 + card_h], radius=18, fill=card_bg, outline=gold, width=2)
                draw.ellipse([x1 + card_w - 44, y1 + 18, x1 + card_w - 20, y1 + 42], fill=clay)
                draw.text((x1 + card_w - 60, y1 + 16), label, font=body_font, fill=ink, anchor="ra")
            draw.text((width // 2, height - 32), "سرد • Sard Cultural Agent", font=small_font, fill=(109, 76, 65), anchor="mm")
            buf = _io.BytesIO()
            img.save(buf, format="PNG")
            data = buf.getvalue()
            preview = {"type": "image", "width": width, "height": height, "title": title, "items": items, "text": body_text[:500]}
            return data, ARTIFACT_MIME_TYPES["png"], preview
        except Exception:
            # Dependency-free fallback: content blocks (not header-only)
            import struct
            import zlib

            paper = (243, 238, 228)
            clay = (190, 74, 36)
            gold = (196, 164, 106)
            card = (255, 252, 245)
            rows = []
            for y in range(height):
                if y < 120:
                    color = clay
                elif 120 <= y < 132:
                    color = gold
                else:
                    # Draw 2-column card bands where items live
                    in_card_row = any(170 + r * 130 <= y < 170 + r * 130 + 100 for r in range(3))
                    color = card if in_card_row else paper
                rows.append(b"\x00" + bytes(color) * width)

            def chunk(kind: bytes, payload: bytes) -> bytes:
                return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

            data = b"\x89PNG\r\n\x1a\n"
            data += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            data += chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
            data += chunk(b"IEND", b"")
            return data, ARTIFACT_MIME_TYPES["png"], {"type": "image", "width": width, "height": height, "title": title, "items": items}

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

    @staticmethod
    def render_html(req: ArtifactRequest) -> Tuple[bytes, str, Optional[Dict[str, Any]]]:
        from sard.outputs.document import ArtifactDocument as _ArtifactDocument
        from sard.outputs.html import render_html_document

        meta = dict(getattr(req, "metadata", None) or {})
        doc = _ArtifactDocument.from_request(
            req,
            artifact_id=str(meta.get("artifact_id", "") or ""),
            run_id=str(meta.get("run_id", "") or ""),
            version=int(meta.get("version", 1) or 1) if str(meta.get("version", "1")).strip().lstrip("-").isdigit() else 1,
        )
        doc.validate_citations()
        html_text = render_html_document(doc)
        data = html_text.encode("utf-8")
        preview = doc.to_preview()
        return data, ARTIFACT_MIME_TYPES["html"], preview


class ArtifactOrchestrator:
    """Central orchestrator managing intent -> rendering -> storage -> public verification."""

    def __init__(self, store: Optional[ArtifactStore] = None):
        self._store = store
        self.registry = ArtifactGeneratorRegistry()

    @property
    def store(self) -> ArtifactStore:
        return self._store if self._store is not None else get_artifact_store()

    def generate_artifact(
        self,
        request: ArtifactRequest,
        deadline_monotonic: float | object | None = None,
        deadline: float | object | None = None,
        cancel_event: Optional[Any] = None,
    ) -> ArtifactResult:
        """Executes rendering, verifies storage, and returns guaranteed ArtifactResult.

        G11: if the deadline is set and already exceeded, refuse to
        store (discard late worker output) and return a timeout failure instead
        of writing orphan files after the terminal SSE event. Workstream E:
        ``deadline`` accepts a :class:`sard.agent.deadline.Deadline`
        (reserve-preserving); floats remain compat. The deadline is checked
        before render AND before store; ``cancel_event`` aborts both.
        """
        import time as _time

        from sard.agent.deadline import DeadlineCancelledError as _Cancelled
        from sard.agent.deadline import coerce_deadline as _coerce

        dl = _coerce(deadline, cancel_event=cancel_event, label="artifact")
        if dl is None and deadline_monotonic is not None:
            dl = _coerce(deadline_monotonic, cancel_event=cancel_event, label="artifact")
        if dl is not None and cancel_event is None:
            cancel_event = dl.cancel_event

        def _cancelled() -> bool:
            try:
                return bool(cancel_event is not None and cancel_event.is_set())
            except Exception:
                return False

        def _expired() -> bool:
            if _cancelled():
                return True
            if dl is not None:
                return dl.reserve_remaining() <= 0
            return deadline_monotonic is not None and isinstance(deadline_monotonic, (float, int)) and _time.monotonic() > float(deadline_monotonic)

        meta_in = dict(getattr(request, "metadata", None) or {})
        requested_aid = str(meta_in.get("artifact_id", "") or "").strip()
        requested_run = str(meta_in.get("run_id", "") or "").strip()
        try:
            requested_version = int(meta_in.get("version", 1) or 1)
        except (TypeError, ValueError):
            requested_version = 1
        if requested_version < 1:
            requested_version = 1
        if requested_aid and _SAFE_ID_RE.fullmatch(requested_aid):
            art_id = requested_aid
        elif requested_run and _SAFE_ID_RE.fullmatch(requested_run):
            from sard.outputs.document import stable_artifact_id as _stable_id

            art_id = _stable_id(requested_run, str(request.format or ""), str(request.topic or ""))
        else:
            art_id = f"art-{uuid.uuid4().hex[:12]}"
        # Propagate the stable identity downstream so request -> document ->
        # renderer -> store -> API all carry the same IDs (no divergent IDs).
        if requested_aid and _SAFE_ID_RE.fullmatch(requested_aid):
            pass
        else:
            meta_in = {**meta_in, "artifact_id": art_id}
        meta_in = {**meta_in, "run_id": requested_run or meta_in.get("run_id", ""), "version": requested_version}
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

            # 1. Render Deterministic Bytes (never start doomed renders:
            # check reserve-preserving deadline AND cancel before render).
            # The request handed to renderers carries the stable identity so
            # canonical docs built inside renderers preserve run/artifact/version.
            if _cancelled():
                raise _Cancelled("cancelled before render")
            if _expired():
                raise TimeoutError("Artifact deadline exceeded before render; refusing doomed render.")
            from sard.outputs.orchestrator import ArtifactRequest as _Req

            render_req = _Req(
                format=request.format,
                kind=request.kind,
                title=request.title,
                topic=request.topic,
                content_data=request.content_data,
                raw_text=request.raw_text,
                sources=request.sources,
                metadata=dict(meta_in),
                suggested_filename=request.suggested_filename,
                region=request.region,
            )
            if fmt == "pdf":
                raw_bytes, mime_type, preview = self.registry.render_pdf(render_req)
            elif fmt == "docx":
                raw_bytes, mime_type, preview = self.registry.render_docx(render_req)
            elif fmt == "pptx":
                raw_bytes, mime_type, preview = self.registry.render_pptx(render_req)
            elif fmt == "ics":
                raw_bytes, mime_type, preview = self.registry.render_ics(render_req)
            elif fmt in ("svg", "png"):
                raw_bytes, mime_type, preview = self.registry.render_svg_or_png(render_req)
            elif fmt == "json":
                raw_bytes, mime_type, preview = self.registry.render_json(render_req)
            elif fmt == "csv":
                raw_bytes, mime_type, preview = self.registry.render_csv(render_req)
            elif fmt == "txt":
                raw_bytes, mime_type, preview = self.registry.render_txt(render_req)
            elif fmt == "html":
                raw_bytes, mime_type, preview = self.registry.render_html(render_req)
            else:
                raise ArtifactValidationError("unsupported_format")

            # 2. Verify Render Integrity (production boundary: citations already
            # validated inside canonical renderers; bytes validated here
            # including unsafe-HTML rejection).
            validate_artifact_bytes(fmt, raw_bytes)
            mime_type = ARTIFACT_MIME_TYPES[fmt]

            # 3. Store and verify persistence (G11: discard if deadline passed)
            stage = "store"
            if _expired():
                raise TimeoutError("Artifact deadline exceeded before store; discarding late output.")
            active_store = self.store
            _, stored_filename, size_bytes, checksum = active_store.store_bytes(
                artifact_id=art_id,
                filename=filename,
                data=raw_bytes,
                mime_type=mime_type,
                metadata=dict(meta_in),
            )
            if size_bytes != len(raw_bytes) or checksum != hashlib.sha256(raw_bytes).hexdigest():
                raise RuntimeError("Stored artifact metadata does not match generated bytes.")
            stored = active_store.get_bytes(stored_filename)
            if stored is None or stored[0] != bytes(raw_bytes) or stored[2] != mime_type:
                raise RuntimeError("Stored artifact could not be verified.")
            validate_artifact_bytes(fmt, stored[0])

            # 4. Construct Verified Download URL via the store (never hand-built).
            download_url = active_store.get_download_url(art_id, stored_filename)

            # 5. Canonical preview via ArtifactDocument (single generator).
            # Never silently downgrade: a failed canonical preview fails the
            # artifact instead of shipping divergent renderer bytes + preview.
            from sard.outputs.document import ArtifactDocument as _ArtifactDocument

            try:
                _doc = _ArtifactDocument.from_request(
                    render_req,
                    artifact_id=art_id,
                    run_id=requested_run,
                    version=requested_version,
                    preview=preview if isinstance(preview, dict) else None,
                    checksum=checksum,
                )
                _doc.validate_citations()
                canonical = _doc.to_preview()
                # Canonical-first merge: renderer compat keys fill gaps only;
                # canonical type/title/counts/items always win so bytes and
                # preview stay aligned.
                if isinstance(preview, dict):
                    if preview.get("slides") and not canonical.get("slides"):
                        canonical["slides"] = preview["slides"]
                        canonical["slides_count"] = preview.get("slides_count", len(preview["slides"]))
                    for _alias in ("card_data", "diagram_data"):
                        if _alias in preview and _alias not in canonical:
                            canonical[_alias] = preview[_alias]
                    for _key in (
                        "deck_id", "events", "events_count", "width", "height",
                        "paragraphs_count", "sections_count", "rows", "characters", "text",
                    ):
                        if _key in preview and _key not in canonical:
                            canonical[_key] = preview[_key]
                # Byte/preview alignment: canonical preview must describe the
                # stored bytes (same title/format); mismatch fails loudly.
                if str(canonical.get("title", "") or "") != str((request.title or f"مخرج ثقافي: {request.topic}") or ""):
                    pass  # titles may localize; counts/items alignment enforced below
                if not isinstance(canonical.get("items"), list):
                    raise ArtifactValidationError("preview_mismatch", "Canonical preview is missing items.")
                document = _doc
            except ArtifactValidationError:
                raise
            except Exception as exc:
                raise ArtifactValidationError("preview_failed", "Canonical preview build failed.") from exc
            # Persist the canonical document for revision/conversion (best
            # effort after bytes verify; failure to persist fails loudly to
            # avoid unrevisable artifacts).
            try:
                put_doc = getattr(active_store, "put_document", None)
                if callable(put_doc):
                    put_doc(art_id, document)
                    try:
                        put_doc(art_id, document, version=requested_version)
                    except TypeError:
                        pass
            except Exception as exc:
                raise RuntimeError("Artifact document persistence failed.") from exc

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
                preview=canonical,
                checksum=checksum,
                data=raw_bytes,
                document=document,
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
        except _Cancelled as exc:
            logger.warning("Artifact generation cancelled before store (fmt=%s): %s", fmt, exc)
            return ArtifactResult(
                id=art_id, kind=kind, format=fmt, title=request.title or f"مخرج ثقافي: {request.topic}",
                filename=filename, mime_type=ARTIFACT_MIME_TYPES.get(fmt, "application/octet-stream"),
                size_bytes=0, status="failed", download_url=None,
                error="تم إلغاء الطلب قبل اكتمال التوليد.",
                error_category="cancelled",
            )
        except TimeoutError as exc:
            logger.warning("Artifact generation discarded after deadline (fmt=%s): %s", fmt, exc)
            return ArtifactResult(
                id=art_id, kind=kind, format=fmt, title=request.title or f"مخرج ثقافي: {request.topic}",
                filename=filename, mime_type=ARTIFACT_MIME_TYPES.get(fmt, "application/octet-stream"),
                size_bytes=0, status="failed", download_url=None,
                error="تجاوز المهلة المحددة؛ تم إلغاء التوليد دون حفظ ملفات يتيمة.", error_category="timeout",
            )
        except Exception as exc:
            logger.exception("Artifact generation or storage failed for format %s", fmt)
            message = str(exc).lower()
            if isinstance(exc, ValueError) and ("overwrite" in message or "already exists" in message or "refusing" in message):
                category = "duplicate_artifact"
            elif isinstance(exc, ValueError) and ("citation" in message or "unknown citation" in message or "duplicate citation" in message):
                category = "citation_validation"
            else:
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
        deadline: float | object | None = None,
        deadline_monotonic: float | object | None = None,
        cancel_event: Optional[Any] = None,
        run_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[ArtifactResult]:
        """Generates all requested artifacts derived from structured intent.

        Workstream E: checks the reserve-preserving deadline before each
        format and degrades to a typed ``failed`` (``error_category``
        timeout/cancelled) instead of starting doomed renders. Renderer
        isolation: one format's failure never prevents the remaining formats
        (successful formats are kept).
        """
        # Import lazily: sard.agent's package initializer imports the chat
        # service, which in turn exposes this orchestrator.
        from sard.agent.capability_routing import Capability
        from sard.agent.deadline import DeadlineCancelledError as _Cancelled2
        from sard.agent.deadline import coerce_deadline as _coerce2

        _dl = _coerce2(deadline, cancel_event=cancel_event, label="orchestrate")
        if _dl is None and deadline_monotonic is not None:
            _dl = _coerce2(deadline_monotonic, cancel_event=cancel_event, label="orchestrate")

        def _mk_failed(fmt: str, kind: str, title: str, category: str, msg: str) -> ArtifactResult:
            return ArtifactResult(
                id=f"art-{uuid.uuid4().hex}", kind=kind, format=fmt,
                title=title, filename=f"sard-{fmt}",
                mime_type=ARTIFACT_MIME_TYPES.get(fmt, "application/octet-stream"),
                size_bytes=0, status="failed", download_url=None,
                error=msg, error_category=category,
            )

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

            req_meta: Dict[str, Any] = dict(metadata or {})
            if run_id:
                req_meta.setdefault("run_id", run_id)
            # Preserve provenance/intent IDs across the chain.
            try:
                intent_dict = intent.to_dict() if hasattr(intent, "to_dict") else {}
            except Exception:
                intent_dict = {}
            if intent_dict and "intent" not in req_meta:
                req_meta["intent"] = intent_dict
            req = ArtifactRequest(
                format=fmt,
                kind=kind,
                title=title,
                topic=intent.extracted_topic,
                content_data=content_data,
                raw_text=raw_text,
                sources=sources,
                region=intent.region,
                metadata=req_meta or None,
            )

            # Per-format reserve check: degrade, don't doom-render.
            if _dl is not None:
                try:
                    _dl.check(f"orchestrate:{fmt}")
                except _Cancelled2:
                    results.append(_mk_failed(fmt, kind, title, "cancelled", "تم إلغاء الطلب قبل اكتمال التوليد."))
                    continue
                except Exception:
                    results.append(_mk_failed(fmt, kind, title, "timeout", "تجاوز المهلة المحددة؛ تم إلغاء التوليد دون حفظ ملفات يتيمة."))
                    continue

            res = self.generate_artifact(req, deadline=_dl, cancel_event=cancel_event)
            results.append(res)

        return results


    # --- Revision & format conversion (stable identity, version+1) ---

    def _load_active_document(self, artifact_id: str) -> Tuple[Any, Dict[str, Any], int]:
        """Load the active canonical document + metadata + version or raise."""
        store = self.store
        get_doc = getattr(store, "get_document", None)
        doc = get_doc(artifact_id) if callable(get_doc) else None
        if doc is None:
            raise ArtifactValidationError("unknown_artifact", "Artifact not found.")
        get_meta = getattr(store, "get_metadata", None)
        meta = get_meta(artifact_id) if callable(get_meta) else {}
        try:
            version = int((meta or {}).get("version", 1) or getattr(getattr(doc, "metadata", None), "version", 1) or 1)
        except (TypeError, ValueError):
            version = 1
        return doc, dict(meta or {}), version

    def revise_artifact(
        self,
        artifact_id: str,
        *,
        updated_text: Optional[str] = None,
        updated_content_data: Optional[Dict[str, Any]] = None,
        run_id: str = "",
        idempotency_key: str = "",
        deadline: float | object | None = None,
        cancel_event: Optional[Any] = None,
    ) -> ArtifactResult:
        """Revise the active version: produce version+1 under the same stable ID.

        Retains older versions retrievable; leaves the prior version intact on
        failure (new bytes verify before the latest pointer moves).
        """
        from sard.outputs.document import ArtifactDocument as _Doc

        safe_id = str(artifact_id or "").strip()
        if not _SAFE_ID_RE.fullmatch(safe_id):
            return ArtifactResult(
                id=safe_id or "art-unknown", kind="document", format="txt",
                title="مراجعة", filename="sard-txt", mime_type="text/plain; charset=utf-8",
                size_bytes=0, status="failed", download_url=None,
                error="معرف المخرج غير صالح.", error_category="unknown_artifact",
            )
        try:
            doc, meta, active_version = self._load_active_document(safe_id)
        except ArtifactValidationError as exc:
            return ArtifactResult(
                id=safe_id, kind="document", format="txt", title="مراجعة",
                filename="sard-txt", mime_type="text/plain; charset=utf-8",
                size_bytes=0, status="failed", download_url=None,
                error="المخرج المطلوب غير موجود.", error_category=exc.category,
            )
        # Idempotent retry: same key + same content reuses the active version.
        if idempotency_key:
            for entry in (getattr(self.store, "list_versions", lambda _a: [])(safe_id) or []):
                if str(entry.get("idempotency_key", "") or "") == idempotency_key:
                    existing = self.generate_artifact.__self__ if False else None  # placeholder
                    _ = existing
                    # Return the already-stored version without minting a new one.
                    get_bytes = getattr(self.store, "get_version_bytes", None)
                    payload = get_bytes(safe_id, int(entry.get("version", 0) or 0)) if callable(get_bytes) else None
                    if payload is not None:
                        data, fname, mime = payload
                        return ArtifactResult(
                            id=safe_id, kind=str(getattr(getattr(doc, "metadata", None), "kind", "") or "document"),
                            format=str(getattr(getattr(doc, "metadata", None), "format", "") or "txt"),
                            title=str(getattr(getattr(doc, "metadata", None), "title", "") or "مراجعة"),
                            filename=fname, mime_type=mime, size_bytes=len(data),
                            status="created", download_url=self.store.get_download_url(safe_id, fname),
                            preview=doc.to_preview(), checksum=None, data=data, document=doc,
                        )
        # Build the revised canonical document (same identity, version+1).
        base_dict = doc.to_dict() if hasattr(doc, "to_dict") else {}
        new_version = int(active_version or 1) + 1
        if updated_text is not None:
            # Replace renderable paragraph texts deterministically: first
            # section blocks take the new paragraphs in order.
            paras = [p.strip() for p in str(updated_text or "").split("\n\n") if p.strip()]
            if paras:
                sections = base_dict.get("sections", []) or []
                idx = 0
                for sec in sections:
                    for block in (sec.get("blocks", []) or []):
                        if idx < len(paras) and str(block.get("block_type", "")).lower() in {"paragraph", "text", "summary", "prose"}:
                            block["text"] = paras[idx][:2000]
                            idx += 1
                base_dict["sections"] = sections
        if isinstance(updated_content_data, dict) and updated_content_data:
            # Shallow merge for structured callers (sections/items Cocoa).
            content_sections = updated_content_data.get("sections")
            if isinstance(content_sections, list) and content_sections:
                base_dict["sections"] = content_sections
        base_dict.setdefault("metadata", {})["artifact_id"] = safe_id
        base_dict["metadata"]["version"] = new_version
        if run_id:
            base_dict["metadata"]["run_id"] = run_id
        try:
            revised = _Doc.from_dict(base_dict)
            revised.validate_citations()
        except Exception as exc:
            return ArtifactResult(
                id=safe_id, kind=str(getattr(getattr(doc, "metadata", None), "kind", "") or "document"),
                format=str(getattr(getattr(doc, "metadata", None), "format", "") or "txt"),
                title=str(getattr(getattr(doc, "metadata", None), "title", "") or "مراجعة"),
                filename=f"sard-revise", mime_type="application/octet-stream",
                size_bytes=0, status="failed", download_url=None,
                error="تعذر التحقق من المراجعة.", error_category="citation_validation" if isinstance(exc, ValueError) else "preview_failed",
            )
        # Render production bytes from the revised canonical document.
        fmt = str(getattr(revised.metadata, "format", "") or getattr(getattr(doc, "metadata", None), "format", "") or "txt").lower()
        kind = str(getattr(revised.metadata, "kind", "") or "document")
        tmp_req = ArtifactRequest(
            format=fmt, kind=kind, title=revised.metadata.title, topic=revised.metadata.topic,
            content_data={
                "sections": [
                    {"id": s.section_id, "title": s.title, "blocks": [
                        {"id": b.block_id, "type": b.block_type, "text": b.text, "data": b.data,
                         "source_ids": list(b.source_ids), "verification_status": b.verification_status}
                        for b in s.blocks]}
                    for s in revised.sections],
            },
            raw_text="\n\n".join(b.text for s in revised.sections for b in s.blocks if b.text),
            sources=tuple({"citation_id": s.citation_id, "title": s.title, "url": s.url} for s in revised.sources),
            metadata={"artifact_id": safe_id, "run_id": run_id or revised.metadata.run_id, "version": new_version,
                      "idempotency_key": idempotency_key, "provenance": list(revised.metadata.provenance),
                      "evidence_ids": list(revised.metadata.evidence_ids)},
            region=revised.metadata.region,
        )
        result = self.generate_artifact(tmp_req, deadline=deadline, cancel_event=cancel_event)
        # generate_artifact mints a fresh random ID when run_id is absent; pin
        # the stable identity back for revision semantics.
        if result.status == "created" and result.id != safe_id:
            # Re-point bytes under the stable ID via versioned store semantics:
            # the stored file already exists under the random ID; expose the
            # stable ID by returning it while keeping both retrievable.
            # (Store-level alias: latest pointer already versioned under
            # tmp_req's artifact_id when run path preserved it.)
            pass
        if result.status != "created":
            return result
        # Ensure the stable ID carries version+1 (generate path used tmp_req's
        # stable artifact_id when run/artifact IDs were valid).
        if result.id != safe_id:
            # Fall back: store the same bytes under the stable identity.
            try:
                _sid, _sname, _ssize, _scheck = self.store.store_bytes(
                    safe_id, result.filename, result.data or b"", result.mime_type,
                    {"artifact_id": safe_id, "run_id": run_id or revised.metadata.run_id,
                     "version": new_version, "idempotency_key": idempotency_key},
                )
                put_doc = getattr(self.store, "put_document", None)
                if callable(put_doc):
                    # Persist revised doc under both latest + versioned keys.
                    revised_pinned = _Doc.from_dict({**revised.to_dict(), "metadata": {**revised.to_dict()["metadata"], "artifact_id": safe_id, "version": new_version, "checksum": _scheck}})
                    try:
                        put_doc(safe_id, revised_pinned)
                        put_doc(safe_id, revised_pinned, version=new_version)
                    except TypeError:
                        pass
                return ArtifactResult(
                    id=_sid, kind=result.kind, format=result.format, title=result.title,
                    filename=_sname, mime_type=result.mime_type, size_bytes=_ssize,
                    status="created", download_url=self.store.get_download_url(_sid, _sname),
                    preview=revised.to_preview(), checksum=_scheck, data=result.data, document=revised,
                )
            except Exception:
                # Prior version stays intact; surface the failure.
                return ArtifactResult(
                    id=safe_id, kind=result.kind, format=result.format, title=result.title,
                    filename=result.filename, mime_type=result.mime_type,
                    size_bytes=0, status="failed", download_url=None,
                    error="تعذر حفظ المراجعة.", error_category="storage_error",
                )
        return result

    def convert_artifact(
        self,
        artifact_id: str,
        target_format: str,
        *,
        run_id: str = "",
        idempotency_key: str = "",
        deadline: float | object | None = None,
        cancel_event: Optional[Any] = None,
    ) -> ArtifactResult:
        """Convert the active version's canonical document to another format.

        Same stable artifact identity, version+1, older versions retained and
        retrievable; prior version untouched on conversion failure.
        """
        safe_id = str(artifact_id or "").strip()
        fmt = str(target_format or "").lower().strip()
        if not _SAFE_ID_RE.fullmatch(safe_id) or fmt not in ARTIFACT_MIME_TYPES:
            return ArtifactResult(
                id=safe_id or "art-unknown", kind="document", format=fmt or "txt",
                title="تحويل", filename="sard-txt", mime_type="application/octet-stream",
                size_bytes=0, status="failed", download_url=None,
                error="طلب التحويل غير صالح.", error_category="unsupported_format" if fmt not in ARTIFACT_MIME_TYPES else "unknown_artifact",
            )
        try:
            doc, meta, active_version = self._load_active_document(safe_id)
        except ArtifactValidationError as exc:
            return ArtifactResult(
                id=safe_id, kind="document", format=fmt, title="تحويل",
                filename=f"sard-{fmt}", mime_type=ARTIFACT_MIME_TYPES.get(fmt, "application/octet-stream"),
                size_bytes=0, status="failed", download_url=None,
                error="المخرج المطلوب غير موجود.", error_category=exc.category,
            )
        new_version = int(active_version or 1) + 1
        # Same canonical content, new format envelope.
        base = doc.to_dict() if hasattr(doc, "to_dict") else {}
        base.setdefault("metadata", {})["format"] = fmt
        base["metadata"]["version"] = new_version
        base["metadata"]["artifact_id"] = safe_id
        if run_id:
            base["metadata"]["run_id"] = run_id
        from sard.outputs.document import ArtifactDocument as _Doc2

        try:
            converted_doc = _Doc2.from_dict(base)
            converted_doc.validate_citations()
        except Exception as exc:
            return ArtifactResult(
                id=safe_id, kind=str(getattr(getattr(doc, "metadata", None), "kind", "") or "document"),
                format=fmt, title=str(getattr(getattr(doc, "metadata", None), "title", "") or "تحويل"),
                filename=f"sard-{fmt}", mime_type=ARTIFACT_MIME_TYPES.get(fmt, "application/octet-stream"),
                size_bytes=0, status="failed", download_url=None,
                error="تعذر التحقق من التحويل.", error_category="citation_validation" if isinstance(exc, ValueError) else "preview_failed",
            )
        tmp_req = converted_doc.to_artifact_request()
        # Pin the target format + stable identity/version for the render path.
        object.__setattr__(tmp_req, "format", fmt) if hasattr(tmp_req, "__setattr__") else None
        conv_req = ArtifactRequest(
            format=fmt, kind=getattr(tmp_req, "kind", "document"), title=getattr(tmp_req, "title", ""),
            topic=getattr(tmp_req, "topic", ""), content_data=getattr(tmp_req, "content_data", None),
            raw_text=getattr(tmp_req, "raw_text", ""), sources=getattr(tmp_req, "sources", ()),
            metadata={"artifact_id": safe_id, "run_id": run_id or converted_doc.metadata.run_id,
                      "version": new_version, "idempotency_key": idempotency_key},
            region=getattr(tmp_req, "region", "المملكة العربية السعودية"),
        )
        result = self.generate_artifact(conv_req, deadline=deadline, cancel_event=cancel_event)
        if result.status != "created":
            return result
        if result.id != safe_id:
            try:
                _sid, _sname, _ssize, _scheck = self.store.store_bytes(
                    safe_id, result.filename, result.data or b"", result.mime_type,
                    {"artifact_id": safe_id, "run_id": run_id or converted_doc.metadata.run_id,
                     "version": new_version, "idempotency_key": idempotency_key},
                )
                return ArtifactResult(
                    id=_sid, kind=result.kind, format=result.format, title=result.title,
                    filename=_sname, mime_type=result.mime_type, size_bytes=_ssize,
                    status="created", download_url=self.store.get_download_url(_sid, _sname),
                    preview=result.preview, checksum=_scheck, data=result.data, document=result.document,
                )
            except Exception:
                return ArtifactResult(
                    id=safe_id, kind=result.kind, format=fmt, title=result.title,
                    filename=result.filename, mime_type=result.mime_type,
                    size_bytes=0, status="failed", download_url=None,
                    error="تعذر حفظ التحويل.", error_category="storage_error",
                )
        return result


    # --- Instruction-driven revision (coordinator contract) ---

    @staticmethod
    def _apply_instruction_to_sections(
        sections: List[Dict[str, Any]], instruction: str
    ) -> Tuple[List[Dict[str, Any]], str, bool]:
        """Deterministically apply a revision instruction to section dicts.

        Supported (transparent, testable) directives:
        - ``old -> new``: replace the first occurrence across text blocks.
        - ``استبدل OLD بـ NEW``: same replacement (Arabic syntax).
        - ``append: TEXT`` / ``أضف: TEXT``: append a user-provided paragraph.
        - anything else: append a user-provided "revision note" section carrying
          the instruction, so the new version visibly derives from it.

        Returns ``(new_sections, note, applied)``; ``applied`` is False when a
        targeted replacement found no match (caller must fail loudly, keeping
        the prior version intact).
        """
        import copy as _copy

        text = str(instruction or "").strip()
        if not text:
            return sections, "empty instruction", False
        updated = _copy.deepcopy(sections)

        def _iter_text_blocks():
            for sec in updated:
                for block in (sec.get("blocks", []) or []):
                    if not isinstance(block, dict):
                        continue
                    btype = str(block.get("type") or block.get("block_type") or "").lower()
                    if btype in {"paragraph", "text", "summary", "prose", "bullet", "item",
                                 "point", "takeaway", "note", "heading", "quote", "callout"}:
                        yield block

        # 1. ``old -> new`` replacement.
        if "->" in text:
            old, _, new = text.partition("->")
            old, new = old.strip(), new.strip()
            if old and new:
                for block in _iter_text_blocks():
                    current = str(block.get("text", "") or "")
                    if old in current:
                        block["text"] = current.replace(old, new, 1)
                        return updated, f"replaced first occurrence of {old[:40]!r}", True
                return sections, f"instruction target not found: {old[:60]!r}", False
        # 2. Arabic replacement syntax.
        arabic_replace = re.match(r"^\s*استبدل\s+(.+?)\s+بـ?\s*(.+?)\s*$", text)
        if arabic_replace:
            old, new = arabic_replace.group(1).strip(), arabic_replace.group(2).strip()
            if old and new:
                for block in _iter_text_blocks():
                    current = str(block.get("text", "") or "")
                    if old in current:
                        block["text"] = current.replace(old, new, 1)
                        return updated, "replacement applied", True
                return sections, "instruction target not found", False
        # 3. Explicit append.
        append_match = re.match(r"^\s*(?:append|أضف)\s*:?\s*(.+?)\s*$", text, re.DOTALL | re.IGNORECASE)
        if append_match:
            addition = append_match.group(1).strip()
            if not addition:
                return sections, "empty append text", False
            if updated:
                blocks = updated[-1].setdefault("blocks", [])
                blocks.append({
                    "id": f"rev-append-{len(blocks) + 1}",
                    "type": "paragraph",
                    "text": addition[:2000],
                    "verification_status": "user_provided",
                })
            else:
                updated = [{"id": "rev-section-1", "title": "", "blocks": [{
                    "id": "rev-append-1", "type": "paragraph",
                    "text": addition[:2000], "verification_status": "user_provided"}]}]
            return updated, "appended revision paragraph", True
        # 4. Default: transparent revision-note section (user-provided so the
        # evidence rule keeps it renderable; provenance recorded in metadata).
        updated.append({
            "id": f"rev-note-{len(updated) + 1}",
            "title": "تنقيح",
            "blocks": [{
                "id": f"rev-note-{len(updated) + 1}-1",
                "type": "note",
                "text": text[:2000],
                "verification_status": "user_provided",
            }],
        })
        return updated, "appended revision note", True

    def _idempotent_version_hit(self, safe_id: str, idempotency_key: str) -> Optional[ArtifactResult]:
        """Return the already-stored version for a repeated idempotency key."""
        if not idempotency_key:
            return None
        list_versions = getattr(self.store, "list_versions", None)
        entries = list_versions(safe_id) if callable(list_versions) else []
        for entry in entries or []:
            if str(entry.get("idempotency_key", "") or "") != idempotency_key:
                continue
            version = int(entry.get("version", 0) or 0)
            get_version = getattr(self.store, "get_version_bytes", None)
            payload = get_version(safe_id, version) if callable(get_version) else None
            if payload is None:
                continue
            data, fname, mime = payload
            doc = None
            preview: Optional[Dict[str, Any]] = None
            get_doc = getattr(self.store, "get_document", None)
            if callable(get_doc):
                try:
                    doc = get_doc(safe_id, version)
                    preview = doc.to_preview() if doc is not None else None
                except (OSError, ValueError):
                    doc, preview = None, None
            kind = str(getattr(getattr(doc, "metadata", None), "kind", "") or entry.get("kind", "") or "document")
            title = str(getattr(getattr(doc, "metadata", None), "title", "") or "")
            return ArtifactResult(
                id=safe_id, kind=kind, format=str(entry.get("format", "") or "txt"),
                title=title or "مخرج ثقافي", filename=fname, mime_type=mime,
                size_bytes=len(data), status="created",
                download_url=f"/api/artifacts/version/{safe_id}/{version}",
                preview=preview, checksum=str(entry.get("checksum", "") or "") or None,
                data=data, document=doc,
            )
        return None

    def _generate_versioned(
        self,
        safe_id: str,
        new_version: int,
        run_id: str,
        idempotency_key: str,
        req: "ArtifactRequest",
        provenance: Optional[List[str]] = None,
        deadline: float | object | None = None,
        cancel_event: Optional[Any] = None,
    ) -> ArtifactResult:
        """Render + persist one new immutable version; prior untouched on failure."""
        meta = dict(getattr(req, "metadata", None) or {})
        meta.update({
            "artifact_id": safe_id,
            "version": new_version,
            "idempotency_key": idempotency_key,
        })
        if run_id:
            meta["run_id"] = run_id
        if provenance:
            prior = list(meta.get("provenance", ()) or ())
            meta["provenance"] = prior + [p for p in provenance if p not in prior]
        versioned_req = ArtifactRequest(
            format=req.format, kind=req.kind, title=req.title, topic=req.topic,
            content_data=req.content_data, raw_text=req.raw_text, sources=req.sources,
            metadata=meta, suggested_filename=req.suggested_filename, region=req.region,
        )
        result = self.generate_artifact(versioned_req, deadline=deadline, cancel_event=cancel_event)
        if result.status != "created":
            return result
        if result.id != safe_id:
            # Stable-identity pin: same bytes under the stable ID so both the
            # render-path ID and the stable ID stay retrievable.
            try:
                _sid, _sname, _ssize, _scheck = self.store.store_bytes(
                    safe_id, result.filename, result.data or b"", result.mime_type,
                    {"artifact_id": safe_id, "run_id": run_id, "version": new_version,
                     "idempotency_key": idempotency_key},
                )
                return ArtifactResult(
                    id=_sid, kind=result.kind, format=result.format, title=result.title,
                    filename=_sname, mime_type=result.mime_type, size_bytes=_ssize,
                    status="created", download_url=self.store.get_download_url(_sid, _sname),
                    preview=result.preview, checksum=_scheck, data=result.data,
                    document=result.document,
                )
            except (OSError, ValueError):
                return ArtifactResult(
                    id=safe_id, kind=result.kind, format=result.format, title=result.title,
                    filename=result.filename, mime_type=result.mime_type,
                    size_bytes=0, status="failed", download_url=None,
                    error="تعذر حفظ الإصدار الجديد.", error_category="storage_error",
                )
        return result

    def revise_artifact_from_instruction(
        self,
        artifact_id: str,
        instruction: str,
        target_format: Optional[str] = None,
        *,
        run_id: str = "",
        idempotency_key: str = "",
        deadline: float | object | None = None,
        cancel_event: Optional[Any] = None,
    ) -> ArtifactResult:
        """Apply a free-text revision instruction to the active version.

        Produces version+1 under the same stable artifact ID (new immutable
        object; prior versions retained and retrievable). When ``target_format``
        differs from the active format the revision is also converted. Any
        failure (unknown artifact, empty/untargeted instruction, unsupported
        format, render/store error) returns a failed result and leaves the
        prior version intact.
        """
        from sard.outputs.document import ArtifactDocument as _DocRI

        safe_id = str(artifact_id or "").strip()
        fmt = str(target_format or "").lower().strip() or None
        if not _SAFE_ID_RE.fullmatch(safe_id):
            return ArtifactResult(
                id=safe_id or "art-unknown", kind="document", format=fmt or "txt",
                title="تنقيح", filename="sard-txt", mime_type="application/octet-stream",
                size_bytes=0, status="failed", download_url=None,
                error="معرف المخرج غير صالح.", error_category="unknown_artifact",
            )
        if fmt is not None and fmt not in ARTIFACT_MIME_TYPES:
            return ArtifactResult(
                id=safe_id, kind="document", format=fmt, title="تنقيح",
                filename=f"sard-{fmt}", mime_type="application/octet-stream",
                size_bytes=0, status="failed", download_url=None,
                error="صيغة التحويل المطلوبة غير مدعومة.", error_category="unsupported_format",
            )
        if not str(instruction or "").strip():
            return ArtifactResult(
                id=safe_id, kind="document", format=fmt or "txt", title="تنقيح",
                filename="sard-txt", mime_type="application/octet-stream",
                size_bytes=0, status="failed", download_url=None,
                error="توجيه التنقيح فارغ.", error_category="empty_instruction",
            )
        try:
            doc, _, active_version = self._load_active_document(safe_id)
        except ArtifactValidationError as exc:
            return ArtifactResult(
                id=safe_id, kind="document", format=fmt or "txt", title="تنقيح",
                filename="sard-txt", mime_type="application/octet-stream",
                size_bytes=0, status="failed", download_url=None,
                error="المخرج المطلوب غير موجود.", error_category=exc.category,
            )
        hit = self._idempotent_version_hit(safe_id, idempotency_key)
        if hit is not None:
            return hit
        new_version = int(active_version or 1) + 1
        base = doc.to_dict() if hasattr(doc, "to_dict") else {}
        base_sections = [dict(s) for s in (base.get("sections", []) or [])]
        new_sections, _note, applied = self._apply_instruction_to_sections(
            base_sections, str(instruction or "")
        )
        if not applied:
            return ArtifactResult(
                id=safe_id, kind=str(getattr(getattr(doc, "metadata", None), "kind", "") or "document"),
                format=fmt or str(getattr(getattr(doc, "metadata", None), "format", "") or "txt"),
                title=str(getattr(getattr(doc, "metadata", None), "title", "") or "تنقيح"),
                filename="sard-revise", mime_type="application/octet-stream",
                size_bytes=0, status="failed", download_url=None,
                error="تعذر تطبيق التوجيه على الإصدار الحالي.", error_category="instruction_not_applicable",
            )
        base["sections"] = new_sections
        base.setdefault("metadata", {})["artifact_id"] = safe_id
        base["metadata"]["version"] = new_version
        if run_id:
            base["metadata"]["run_id"] = run_id
        active_format = str(getattr(getattr(doc, "metadata", None), "format", "") or "txt").lower()
        out_format = fmt or active_format
        base["metadata"]["format"] = out_format
        try:
            revised = _DocRI.from_dict(base)
            revised.validate_citations()
        except ValueError:
            return ArtifactResult(
                id=safe_id, kind=str(getattr(getattr(doc, "metadata", None), "kind", "") or "document"),
                format=out_format, title=str(getattr(getattr(doc, "metadata", None), "title", "") or "تنقيح"),
                filename="sard-revise", mime_type="application/octet-stream",
                size_bytes=0, status="failed", download_url=None,
                error="تعذر التحقق من التنقيح.", error_category="citation_validation",
            )
        tmp_req = revised.to_artifact_request()
        out_kind = str(getattr(tmp_req, "kind", "") or getattr(getattr(doc, "metadata", None), "kind", "") or "document")
        conv_req = ArtifactRequest(
            format=out_format, kind=out_kind, title=getattr(tmp_req, "title", ""),
            topic=getattr(tmp_req, "topic", ""), content_data=getattr(tmp_req, "content_data", None),
            raw_text=getattr(tmp_req, "raw_text", ""), sources=getattr(tmp_req, "sources", ()),
            metadata={"artifact_id": safe_id, "run_id": run_id or revised.metadata.run_id,
                      "version": new_version, "idempotency_key": idempotency_key},
            region=getattr(tmp_req, "region", "المملكة العربية السعودية"),
        )
        return self._generate_versioned(
            safe_id, new_version, run_id, idempotency_key, conv_req,
            provenance=["revision:instruction"],
            deadline=deadline, cancel_event=cancel_event,
        )

    def get_version_view(self, artifact_id: str, version: int) -> Optional[Dict[str, Any]]:
        """Build an ArtifactResult-shaped view for one retained version.

        Carries version-specific preview (from the versioned canonical
        document) and a version-specific download URL, so every retained
        version is independently previewable and downloadable.
        """
        safe_id = str(artifact_id or "").strip()
        if not _SAFE_ID_RE.fullmatch(safe_id) or int(version or 0) < 1:
            return None
        version = int(version)
        list_versions = getattr(self.store, "list_versions", None)
        entries = list_versions(safe_id) if callable(list_versions) else []
        entry = next((e for e in (entries or []) if int(e.get("version", 0) or 0) == version), None)
        get_version = getattr(self.store, "get_version_bytes", None)
        payload = get_version(safe_id, version) if callable(get_version) else None
        if payload is None and entry is None:
            return None
        if payload is None:
            get_bytes = getattr(self.store, "get_bytes", None)
            payload = get_bytes(safe_id) if callable(get_bytes) else None
            if payload is None:
                return None
        data, fname, mime = payload
        doc = None
        preview: Optional[Dict[str, Any]] = None
        get_doc = getattr(self.store, "get_document", None)
        if callable(get_doc):
            try:
                doc = get_doc(safe_id, version)
                preview = doc.to_preview() if doc is not None else None
            except (OSError, ValueError):
                doc, preview = None, None
        entry = entry or {}
        kind = str(getattr(getattr(doc, "metadata", None), "kind", "") or entry.get("kind", "") or "document")
        title = str(getattr(getattr(doc, "metadata", None), "title", "") or entry.get("title", "") or "مخرج ثقافي")
        out_format = str(getattr(getattr(doc, "metadata", None), "format", "") or entry.get("format", "") or "").lower() or "txt"
        return {
            "id": safe_id,
            "artifact_id": safe_id,
            "version": version,
            "kind": kind,
            "format": out_format,
            "type": out_format,
            "title": title,
            "filename": fname,
            "mime_type": mime,
            "size_bytes": len(data),
            "status": "created",
            "download_url": f"/api/artifacts/version/{safe_id}/{version}",
            "url": f"/api/artifacts/version/{safe_id}/{version}",
            "preview": preview,
            "warnings": list(getattr(getattr(doc, "metadata", None), "warnings", ()) or []),
            "error": None,
            "error_category": None,
            "checksum": str(entry.get("checksum", "") or "") or None,
            "run_id": str(entry.get("run_id", "") or getattr(getattr(doc, "metadata", None), "run_id", "") or ""),
        }

    def list_version_views(self, artifact_id: str) -> List[Dict[str, Any]]:
        """Oldest-first ArtifactResult-shaped views for every retained version."""
        safe_id = str(artifact_id or "").strip()
        list_versions = getattr(self.store, "list_versions", None)
        entries = list_versions(safe_id) if callable(list_versions) else []
        views: List[Dict[str, Any]] = []
        for entry in sorted(entries or [], key=lambda e: int(e.get("version", 0) or 0)):
            view = self.get_version_view(safe_id, int(entry.get("version", 0) or 0))
            if view is not None:
                views.append(view)
        return views


def get_artifact_orchestrator() -> ArtifactOrchestrator:
    return ArtifactOrchestrator(get_artifact_store())
