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
        run_id, version, artifact_type = _resolve_run_version(metadata)
        record = {
            "artifact_id": safe_id,
            "run_id": run_id,
            "version": version,
            "type": artifact_type,
            "key": _blob_key_new(run_id, safe_id, version, safe_name),
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
            for candidate in self._metadata_root.glob("*.json"):
                try:
                    item = json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
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
                    except Exception:
                        pass
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
            except Exception:
                pass
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
        except Exception:
            pass
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

            # 1. Render Deterministic Bytes (never start doomed renders:
            # check reserve-preserving deadline AND cancel before render).
            if _cancelled():
                raise _Cancelled("cancelled before render")
            if _expired():
                raise TimeoutError("Artifact deadline exceeded before render; refusing doomed render.")
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
                metadata=request.metadata,
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
            try:
                from sard.outputs.document import ArtifactDocument as _ArtifactDocument

                _doc = _ArtifactDocument.from_request(
                    request,
                    artifact_id=art_id,
                    preview=preview if isinstance(preview, dict) else None,
                    checksum=checksum,
                )
                canonical = _doc.to_preview()
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
                    if "type" not in preview:
                        if any(k in preview for k in ("card_type", "occasion", "item_name", "ingredients_or_materials", "steps")):
                            canonical["card_data"] = preview
                        if any(k in preview for k in ("diagram_type", "nodes", "timeline_milestones", "comparison_aspects")):
                            canonical["diagram_data"] = preview
                document = _doc
            except Exception:
                logger.debug("ArtifactDocument preview build failed; using renderer preview.", exc_info=True)
                canonical = preview if isinstance(preview, dict) else {"type": kind, "title": request.title}
                document = None

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
    ) -> List[ArtifactResult]:
        """Generates all requested artifacts derived from structured intent.

        Workstream E: checks the reserve-preserving deadline before each
        format and degrades to a typed ``failed`` (``error_category``
        timeout/cancelled) instead of starting doomed renders.
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


def get_artifact_orchestrator() -> ArtifactOrchestrator:
    return ArtifactOrchestrator(get_artifact_store())
