"""Ingestion pipeline: verified documents -> normalized, embedded Zvec chunks.

Orchestrates (without duplicating) the other RAG modules:

``loaders`` (parse) -> ``normalize`` (clean) -> ``chunking`` (split + hash)
-> ``embeddings`` (NVIDIA NIM) -> ``zvec_store`` (persist).

Design choices worth calling out:

- **Metadata sidecars.** Each source file ``name.ext`` must have a
  ``name.meta.json`` next to it with the required provenance fields
  (source_name, source_url, title, topic, publication_date, language).
  This keeps ``loaders.py`` format-agnostic (a PDF can't carry YAML
  frontmatter) and keeps provenance explicit and auditable instead of
  guessed from file content.
- **Resumable, idempotent ingestion.** A JSON manifest
  (``<collection_path>/ingestion_manifest.json``) records each document's
  content hash and chunk IDs. Re-running ingestion on unchanged files is a
  fast no-op; changed files are re-chunked and upserted (chunk IDs are
  content-hash based, so unchanged chunks keep the same ID and citation).
- **Scanned PDF pages** are never fabricated: if no configured
  vision-language model can be reached, the page is quarantined for manual
  review and reported — never silently dropped or invented.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from sard.rag.chunking import (
    CHUNKING_VERSION,
    chunk_sections,
    compute_chunk_id,
    compute_citation_id,
    compute_content_hash,
    compute_document_hash,
    compute_document_id,
)
from sard.rag.embeddings import EmbeddingService
from sard.rag.fallbacks import AllCandidatesFailedError
from sard.rag.loaders import load_document
from sard.rag.normalize import clean_document_text
from sard.rag.schemas import Chunk, DocumentMetadata, EmbeddedChunk, SourceFileType
from sard.rag.zvec_store import ZvecRepository, SCHEMA_VERSION

logger = logging.getLogger(__name__)

INGESTION_VERSION = "1"
EMBED_BATCH_SIZE = 16


class MissingMetadataError(Exception):
    """Raised when a source file has no matching ``.meta.json`` sidecar."""


@dataclass
class QuarantinedPage:
    source_path: str
    page_number: int
    reason: str
    vlm_models_tried: list[str] = field(default_factory=list)


@dataclass
class IngestionReport:
    documents_seen: int = 0
    documents_ingested: int = 0
    documents_skipped_unchanged: int = 0
    documents_failed: int = 0
    chunks_inserted: int = 0
    chunks_deduplicated: int = 0
    scanned_pages_quarantined: list[QuarantinedPage] = field(default_factory=list)
    embedding_model_used: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "documents_seen": self.documents_seen,
            "documents_ingested": self.documents_ingested,
            "documents_skipped_unchanged": self.documents_skipped_unchanged,
            "documents_failed": self.documents_failed,
            "chunks_inserted": self.chunks_inserted,
            "chunks_deduplicated": self.chunks_deduplicated,
            "scanned_pages_quarantined": [
                {
                    "source_path": p.source_path,
                    "page_number": p.page_number,
                    "reason": p.reason,
                    "vlm_models_tried": p.vlm_models_tried,
                }
                for p in self.scanned_pages_quarantined
            ],
            "embedding_model_used": self.embedding_model_used,
            "warnings": self.warnings,
            "errors": self.errors,
        }


def load_metadata_sidecar(source_path: Path) -> DocumentMetadata:
    meta_path = source_path.with_suffix(source_path.suffix + ".meta.json")
    if not meta_path.exists():
        raise MissingMetadataError(
            f"Missing metadata sidecar for {source_path}: expected {meta_path.name} "
            "with source_name/source_url/title/topic (see docs/rag-corpus-manifest.md)."
        )
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MissingMetadataError(
            f"Metadata sidecar {meta_path} is not valid JSON ({type(exc).__name__})."
        ) from exc
    if not isinstance(data, dict):
        raise MissingMetadataError(f"Metadata sidecar {meta_path} must contain a JSON object.")
    required = ["source_name", "source_url", "title", "topic"]
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise MissingMetadataError(
            f"Metadata sidecar {meta_path} is missing required field(s): {missing}"
        )
    parsed_url = urlparse(str(data["source_url"]))
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise MissingMetadataError(
            f"Metadata sidecar {meta_path} has an invalid source_url; use a verifiable http(s) URL."
        )
    return DocumentMetadata(
        source_name=data["source_name"],
        source_url=data["source_url"],
        title=data["title"],
        topic=data["topic"],
        document_id=compute_document_id(data["source_url"], data["title"]),
        language=data.get("language", "ar"),
        publication_date=data.get("publication_date"),
        extra={k: v for k, v in data.items() if k not in required + ["language", "publication_date"]},
    )


class IngestionManifest:
    """Tracks per-document ingestion state for resumability/idempotency."""

    def __init__(self, path: Path):
        self.path = path
        self.load_error: Optional[str] = None
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                self._data = loaded if isinstance(loaded, dict) else {}
                if not isinstance(loaded, dict):
                    self.load_error = "Ingestion manifest was not a JSON object; starting a safe resume pass."
            except (OSError, json.JSONDecodeError) as exc:
                self._data = {}
                self.load_error = f"Ingestion manifest could not be read safely: {type(exc).__name__}."
        else:
            self._data = {}

    def get(self, document_id: str) -> Optional[dict]:
        return self._data.get(document_id)

    def record(
        self,
        document_id: str,
        document_hash: str,
        chunk_ids: list[str],
        source_path: str,
        metadata_hash: str = "",
    ) -> None:
        self._data[document_id] = {
            "document_hash": document_hash,
            "metadata_hash": metadata_hash,
            "chunking_version": CHUNKING_VERSION,
            "schema_version": SCHEMA_VERSION,
            "chunk_ids": chunk_ids,
            "source_path": source_path,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp_path.replace(self.path)

    def all_document_ids(self) -> list[str]:
        return list(self._data.keys())

    def source_paths(self) -> list[str]:
        return [v["source_path"] for v in self._data.values()]


def _discover_source_files(corpus_dir: Path) -> list[Path]:
    exts = {".pdf", ".html", ".htm", ".md", ".markdown", ".txt"}
    return sorted(
        p
        for p in corpus_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() in exts
        # The manifest documents corpus provenance and coverage; it is not
        # itself evidence and therefore must not require a source sidecar.
        and p.name.upper() != "MANIFEST.MD"
    )


def _metadata_hash(metadata: DocumentMetadata) -> str:
    payload = {
        "source_name": metadata.source_name,
        "source_url": metadata.source_url,
        "title": metadata.title,
        "topic": metadata.topic,
        "language": metadata.language,
        "publication_date": metadata.publication_date,
        "extra": metadata.extra,
    }
    return compute_content_hash(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def build_chunks_for_document(
    metadata: DocumentMetadata,
    document_id: str,
    sections,
) -> tuple[list[Chunk], int]:
    """Build deterministic chunks for one document.

    Returns ``(chunks, deduplicated_count)`` — the second value counts exact
    duplicate chunk texts (same content hash) seen more than once within this
    document, which are dropped so each unique content has exactly one chunk.
    """
    pieces = chunk_sections(sections)
    chunks: list[Chunk] = []
    seen_hashes: set[str] = set()
    deduplicated = 0
    for piece in pieces:
        content = clean_document_text(piece.text)
        if not content:
            continue
        content_hash = compute_content_hash(content)
        if content_hash in seen_hashes:
            deduplicated += 1
            continue  # exact-duplicate chunk within this document
        seen_hashes.add(content_hash)
        chunk_id = compute_chunk_id(document_id, content_hash)
        citation_id = compute_citation_id(document_id, content_hash)
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                document_id=document_id,
                citation_id=citation_id,
                content=content,
                content_hash=content_hash,
                title=metadata.title,
                source_name=metadata.source_name,
                source_url=metadata.source_url,
                topic=metadata.topic,
                language=metadata.language,
                publication_date=metadata.publication_date,
                schema_version=SCHEMA_VERSION,
                ingestion_version=INGESTION_VERSION,
                page_number=piece.page_number,
                section_heading=piece.section_heading,
                extra_metadata=metadata.extra,
            )
        )
    return chunks, deduplicated


def ingest_directory(
    corpus_dir: Path,
    repository: ZvecRepository,
    embedding_service: EmbeddingService,
    embedding_model_id: str,
    batch_size: int = EMBED_BATCH_SIZE,
) -> IngestionReport:
    """Ingest every supported file under ``corpus_dir`` into ``repository``.

    Safe to re-run: unchanged documents are skipped (resumable ingestion),
    changed documents are re-chunked and upserted.
    """
    if not corpus_dir.exists():
        raise FileNotFoundError(2, "Corpus directory not found", str(corpus_dir))
    if not corpus_dir.is_dir():
        raise NotADirectoryError(20, "Corpus path is not a directory", str(corpus_dir))

    report = IngestionReport(embedding_model_used=embedding_model_id)
    manifest = IngestionManifest(repository.path / "ingestion_manifest.json")
    if manifest.load_error:
        report.warnings.append(manifest.load_error)

    source_files = _discover_source_files(corpus_dir)
    report.documents_seen = len(source_files)

    for source_path in source_files:
        try:
            metadata = load_metadata_sidecar(source_path)
        except MissingMetadataError as exc:
            report.documents_failed += 1
            report.errors.append(str(exc))
            continue

        try:
            loaded = load_document(source_path)
        except Exception as exc:  # noqa: BLE001
            report.documents_failed += 1
            report.errors.append(f"Failed to parse {source_path}: {exc}")
            continue

        document_hash = compute_document_hash(loaded.original_text)
        metadata_hash = _metadata_hash(metadata)
        existing = manifest.get(metadata.document_id)
        if (
            existing is not None
            and existing.get("document_hash") == document_hash
            and existing.get("metadata_hash") == metadata_hash
            and existing.get("chunking_version") == CHUNKING_VERSION
            and existing.get("source_path") == str(source_path)
        ):
            report.documents_skipped_unchanged += 1
            continue

        old_chunk_ids = list(existing.get("chunk_ids", [])) if existing is not None else []
        if existing is not None:
            # The source changed since the last run: remember to drop the
            # OLD chunk set below AFTER the new chunks embed successfully,
            # so a failed re-ingestion never loses the previous version.
            changed_document = True
        else:
            changed_document = False

        for page_no in loaded.scanned_pages:
            report.scanned_pages_quarantined.append(
                QuarantinedPage(
                    source_path=str(source_path),
                    page_number=page_no,
                    reason=(
                        "Page has insufficient extractable text (likely scanned). "
                        "No vision-language model call was attempted in this "
                        "environment; page is quarantined for manual review."
                    ),
                    vlm_models_tried=[],
                )
            )

        chunks, deduplicated = build_chunks_for_document(metadata, metadata.document_id, loaded.sections)
        report.chunks_deduplicated += deduplicated
        if not chunks:
            report.warnings.append(f"No usable text extracted from {source_path}; skipping.")
            continue

        texts = [c.content for c in chunks]
        embedded_chunks: list[EmbeddedChunk] = []
        try:
            for i in range(0, len(texts), batch_size):
                batch_texts = texts[i : i + batch_size]
                batch_chunks = chunks[i : i + batch_size]
                outcome = embedding_service.embed_documents(
                    embedding_model_id, batch_texts, expected_dim=repository.embedding_dimension
                )
                for chunk, vector in zip(batch_chunks, outcome.vectors):
                    embedded_chunks.append(
                        EmbeddedChunk(
                            chunk=chunk,
                            dense_embedding=vector,
                            embedding_model=embedding_model_id,
                            embedding_dimension=len(vector),
                        )
                    )
        except AllCandidatesFailedError as exc:
            report.documents_failed += 1
            report.errors.append(
                f"Embedding failed for {source_path} after exhausting all "
                f"configured candidates for use case '{exc.use_case}'."
            )
            continue
        except Exception:
            report.documents_failed += 1
            report.errors.append(
                f"Embedding failed for {source_path} with an unexpected provider error; "
                "the previous indexed version was preserved."
            )
            continue

        created_at = datetime.now(timezone.utc).isoformat()
        try:
            inserted = repository.upsert_chunks(embedded_chunks, created_at=created_at)
            if changed_document:
                new_chunk_ids = {c.chunk_id for c in chunks}
                repository.delete_by_ids([cid for cid in old_chunk_ids if cid not in new_chunk_ids])
        except Exception as exc:  # noqa: BLE001 - persistence boundary
            report.documents_failed += 1
            report.errors.append(
                f"Persistence failed for {source_path} ({type(exc).__name__}); "
                "the previous manifest entry was preserved."
            )
            continue
        report.chunks_inserted += inserted
        report.documents_ingested += 1

        manifest.record(
            metadata.document_id,
            document_hash,
            [c.chunk_id for c in chunks],
            str(source_path),
            metadata_hash=metadata_hash,
        )
        try:
            manifest.save()
        except Exception as exc:  # noqa: BLE001 - persistence boundary
            report.documents_failed += 1
            report.documents_ingested -= 1
            report.chunks_inserted -= inserted
            report.errors.append(
                f"Manifest persistence failed after indexing {source_path} "
                f"({type(exc).__name__}); the next run will safely reconcile it."
            )

    return report
