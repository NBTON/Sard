"""Zvec repository adapter: a small, focused wrapper — not a generic vector
store framework.

Zvec (https://zvec.org) is an in-process, embedded vector database. This
module owns:

- The collection schema (see module docstring in ``sard/rag/schemas.py``
  for the ``Chunk``/``EmbeddedChunk`` contracts this schema stores).
- A versioned collection path so an index can never silently be queried or
  written to with a different embedding model/dimension than it was built
  with.
- Batch insertion with idempotent upserts (chunk IDs are content-hash
  based; see ``sard/rag/chunking.py``).
- Dense vector search and full-text search, both returning
  ``RetrievedCandidate`` — never raw Zvec ``Doc`` objects — to the rest of
  the pipeline.
- A safe metadata-filter builder that never interpolates raw user text.

Only ONE process should ever open a collection for writing (ingestion).
Streamlit (and any other reader) must only query after ingestion has
finished; see the README "Zvec integration" section.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sard.rag.chunking import CHUNKING_VERSION
from sard.rag.fallbacks import FailureCategory, FallbackClassifiedError
from sard.rag.normalize import NORMALIZATION_VERSION, normalize_arabic
from sard.rag.schemas import EmbeddedChunk, RetrievalFilters, RetrievedCandidate

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "3"

CONTENT_FIELD = "content"
NORMALIZED_CONTENT_FIELD = "normalized_content"
DENSE_VECTOR_FIELD = "dense_embedding"

# Fields stored on every chunk document, beyond the Zvec `id` (== chunk_id).
_SCALAR_STRING_FIELDS = (
    NORMALIZED_CONTENT_FIELD,
    "title",
    "source_name",
    "source_url",
    "topic",
    "publication_date",
    "language",
    "document_id",
    "chunk_id",
    "content_hash",
    "citation_id",
    "embedding_model",
    "schema_version",
    "ingestion_version",
    "created_at",
    "metadata_json",
)

_OUTPUT_FIELDS = [CONTENT_FIELD, *_SCALAR_STRING_FIELDS, "embedding_dimension", "page_number", "section_heading"]

# Metadata filter fields allowed in RetrievalFilters -> Zvec filter expressions.
_FILTERABLE_FIELDS = {"topic", "source_name", "language", "publication_date"}

# Conservative allow-list for filter *values*: Arabic/Latin letters, digits,
# spaces, and a small set of punctuation used in topics/URLs/dates. This is
# the safety boundary against filter-syntax injection since these values
# may originate from upstream (e.g. a query-rewriter's inferred topic).
_SAFE_FILTER_VALUE_RE = re.compile(r"^[\w\u0600-\u06FF .:/_-]+$", re.UNICODE)


class ZvecSchemaMismatchError(FallbackClassifiedError):
    def __init__(self, message: str):
        super().__init__(FailureCategory.ZVEC_SCHEMA_MISMATCH, message)


class ZvecUnavailableError(FallbackClassifiedError):
    def __init__(self, message: str):
        super().__init__(FailureCategory.ZVEC_UNAVAILABLE, message)


class UnsafeFilterValueError(ValueError):
    """Raised when a filter value fails the safe-value allow-list check."""


def versioned_collection_path(
    base_path: str,
    embedding_model: str,
    embedding_dimension: int,
    schema_version: str = SCHEMA_VERSION,
    normalization_version: str = NORMALIZATION_VERSION,
    chunking_version: str = CHUNKING_VERSION,
) -> Path:
    """Compute a collection path that encodes every axis that must never be
    mixed silently: embedding model, dimension, schema, normalization, and
    chunking version.

    Example: ``data/zvec/sard/<embedding-model-hash>/schema-v3/``
    """
    fingerprint_basis = (
        f"{embedding_model}|{embedding_dimension}|norm-{normalization_version}|"
        f"chunk-{chunking_version}"
    )
    model_hash = hashlib.sha256(fingerprint_basis.encode("utf-8")).hexdigest()[:16]
    return Path(base_path) / model_hash / f"schema-v{schema_version}"


def build_safe_filter(filters: RetrievalFilters) -> Optional[str]:
    """Build a Zvec filter expression from structured, allow-listed fields.

    Never accepts raw free-text user input: only the four known
    ``RetrievalFilters`` fields are considered, and every value is checked
    against a conservative character allow-list before being embedded in
    the filter string (single quotes are rejected outright rather than
    escaped, since none of these fields legitimately need them).
    """
    clauses: list[str] = []
    for field_name in sorted(_FILTERABLE_FIELDS):
        value = getattr(filters, field_name, None)
        if value is None or value == "":
            continue
        if "'" in value or not _SAFE_FILTER_VALUE_RE.match(value):
            raise UnsafeFilterValueError(
                f"Refusing to build a Zvec filter with an unsafe value for "
                f"'{field_name}'."
            )
        clauses.append(f"{field_name} = '{value}'")
    if not clauses:
        return None
    return " AND ".join(clauses)


@dataclass
class CollectionStats:
    doc_count: int
    path: str
    embedding_model: str
    embedding_dimension: int


class ZvecRepository:
    """Focused repository around one versioned Zvec collection of chunks."""

    def __init__(self, collection, path: Path, embedding_model: str, embedding_dimension: int):
        self._collection = collection
        self.path = path
        self.embedding_model = embedding_model
        self.embedding_dimension = embedding_dimension

    # -- lifecycle ---------------------------------------------------

    @classmethod
    def open_or_create(
        cls,
        base_path: str,
        embedding_model: str,
        embedding_dimension: int,
        schema_version: str = SCHEMA_VERSION,
    ) -> "ZvecRepository":
        """Open the versioned collection for this embedding model, creating
        it (with schema validation baked into the path) if it doesn't exist.
        """
        import zvec

        if embedding_dimension <= 0:
            raise FallbackClassifiedError(
                FailureCategory.EMBEDDING_DIMENSION_MISMATCH,
                "Refusing to open/create a Zvec collection with a "
                f"non-positive embedding dimension ({embedding_dimension}).",
            )

        path = versioned_collection_path(base_path, embedding_model, embedding_dimension, schema_version)
        meta_path = path / "sard_collection_meta.json"

        if (path / "collection.json").exists() or meta_path.exists() or (path.exists() and any(path.iterdir())):
            collection = cls._open_existing(path, embedding_model, embedding_dimension)
        else:
            # zvec.create_and_open requires the target path to NOT already
            # exist. If a previous interrupted run left an EMPTY versioned
            # directory behind, remove it before creating the collection.
            if path.exists():
                path.rmdir()
            path.parent.mkdir(parents=True, exist_ok=True)
            schema = cls._build_schema(embedding_dimension)
            try:
                collection = zvec.create_and_open(path=str(path), schema=schema)
            except Exception as exc:
                raise ZvecUnavailableError(
                    f"Failed to create Zvec collection at {path}: {exc}"
                ) from exc
            meta_tmp = meta_path.with_suffix(meta_path.suffix + ".tmp")
            meta_tmp.write_text(
                json.dumps(
                    {
                        "embedding_model": embedding_model,
                        "embedding_dimension": embedding_dimension,
                        "schema_version": schema_version,
                        "normalization_version": NORMALIZATION_VERSION,
                        "chunking_version": CHUNKING_VERSION,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            meta_tmp.replace(meta_path)

        return cls(collection, path, embedding_model, embedding_dimension)

    @classmethod
    def _open_existing(cls, path: Path, embedding_model: str, embedding_dimension: int):
        import zvec

        meta_path = path / "sard_collection_meta.json"
        if not meta_path.exists():
            raise ZvecSchemaMismatchError(
                f"Collection at {path} has no Sard metadata; refusing to infer "
                "its embedding model or version from the live index."
            )
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ZvecSchemaMismatchError(
                f"Collection metadata at {path} is missing or malformed."
            ) from exc
        expected_meta = {
            "schema_version": path.name.removeprefix("schema-v"),
            "embedding_model": embedding_model,
            "embedding_dimension": embedding_dimension,
            "normalization_version": NORMALIZATION_VERSION,
            "chunking_version": CHUNKING_VERSION,
        }
        if any(meta.get(key) != value for key, value in expected_meta.items()):
            raise ZvecSchemaMismatchError(
                f"Collection at {path} has metadata incompatible with the "
                "requested model, dimension, or versioned preprocessing contract."
            )
        try:
            collection = zvec.open(path=str(path))
        except Exception as exc:
            raise ZvecUnavailableError(f"Failed to open Zvec collection at {path}: {exc}") from exc

        # Defense in depth: verify the live schema's fields and vector dimension.
        try:
            actual_fields = {f.name for f in collection.schema.fields}
            expected_fields = {
                CONTENT_FIELD,
                *_SCALAR_STRING_FIELDS,
                "embedding_dimension",
                "page_number",
                "section_heading",
            }
            if actual_fields != expected_fields:
                raise ZvecSchemaMismatchError(
                    f"Collection at {path} has an incompatible field schema. "
                    "Use a new versioned collection or rebuild it."
                )
            expected_types = {
                field_name: "INT32" if field_name == "embedding_dimension" else "STRING"
                for field_name in (*_SCALAR_STRING_FIELDS, CONTENT_FIELD)
            }
            expected_types.update(
                {"embedding_dimension": "INT32", "page_number": "INT32", "section_heading": "STRING"}
            )
            for field in collection.schema.fields:
                actual_type = getattr(field.data_type, "name", str(field.data_type).split(".")[-1])
                if actual_type != expected_types.get(field.name):
                    raise ZvecSchemaMismatchError(
                        f"Collection at {path} has incompatible type for field {field.name!r}."
                    )
                expected_nullable = field.name in {"page_number", "section_heading"}
                if bool(getattr(field, "nullable", False)) != expected_nullable:
                    raise ZvecSchemaMismatchError(
                        f"Collection at {path} has incompatible nullability for field {field.name!r}."
                    )
            vector_fields = {v.name: v for v in collection.schema.vectors}
            if set(vector_fields) != {DENSE_VECTOR_FIELD}:
                raise ZvecSchemaMismatchError(f"Collection at {path} has an incompatible vector schema.")
            live_dim = vector_fields[DENSE_VECTOR_FIELD].dimension
            metric_text = str(getattr(vector_fields[DENSE_VECTOR_FIELD].index_param, "metric_type", ""))
            if "COSINE" not in metric_text.upper():
                raise ZvecSchemaMismatchError(
                    f"Collection at {path} uses a vector metric other than cosine."
                )
        except ZvecSchemaMismatchError:
            raise
        except Exception as exc:
            raise ZvecSchemaMismatchError(
                f"Collection at {path} has an unreadable or incomplete Zvec schema."
            ) from exc
        if live_dim is not None and live_dim != embedding_dimension:
            raise ZvecSchemaMismatchError(
                f"Collection at {path} has dense vector dimension {live_dim}, "
                f"which does not match the requested dimension "
                f"{embedding_dimension}. Refusing to open."
            )
        return collection

    @staticmethod
    def _build_schema(embedding_dimension: int):
        import zvec

        fields = [
            zvec.FieldSchema(
                name=CONTENT_FIELD,
                data_type=zvec.DataType.STRING,
                index_param=zvec.FtsIndexParam(),
            ),
        ]
        for name in _SCALAR_STRING_FIELDS:
            index_param = (
                zvec.FtsIndexParam()
                if name == NORMALIZED_CONTENT_FIELD
                else (
                    zvec.InvertIndexParam(enable_range_optimization=True)
                    if name in _FILTERABLE_FIELDS or name in ("document_id", "content_hash", "chunk_id")
                    else None
                )
            )
            fields.append(zvec.FieldSchema(name=name, data_type=zvec.DataType.STRING, index_param=index_param))
        fields.append(zvec.FieldSchema(name="embedding_dimension", data_type=zvec.DataType.INT32))
        fields.append(zvec.FieldSchema(name="page_number", data_type=zvec.DataType.INT32, nullable=True))
        fields.append(zvec.FieldSchema(name="section_heading", data_type=zvec.DataType.STRING, nullable=True))

        vectors = [
            zvec.VectorSchema(
                name=DENSE_VECTOR_FIELD,
                data_type=zvec.DataType.VECTOR_FP32,
                dimension=embedding_dimension,
                index_param=zvec.HnswIndexParam(metric_type=zvec.MetricType.COSINE),
            )
        ]
        return zvec.CollectionSchema(name="sard_chunks", fields=fields, vectors=vectors)

    @classmethod
    def find_existing_for_model(
        cls, base_path: str, embedding_model: str
    ) -> Optional["ZvecRepository"]:
        """Open an already-ingested collection for ``embedding_model`` without
        any network call, by scanning versioned subdirectories for a
        ``sard_collection_meta.json`` recorded during creation.

        Returns ``None`` if no matching collection exists yet (e.g. before
        the first ingestion run).
        """
        base = Path(base_path)
        if not base.exists():
            return None
        for meta_path in base.glob("*/schema-v*/sard_collection_meta.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                meta.get("embedding_model") == embedding_model
                and str(meta.get("schema_version")) == SCHEMA_VERSION
                and meta.get("normalization_version") == NORMALIZATION_VERSION
                and meta.get("chunking_version") == CHUNKING_VERSION
            ):
                path = meta_path.parent
                collection = cls._open_existing(path, embedding_model, meta["embedding_dimension"])
                return cls(collection, path, embedding_model, meta["embedding_dimension"])
        return None

    @classmethod
    def inspect_collections_for_model(cls, base_path: str, embedding_model: str) -> list[dict]:
        """Return sanitized metadata for every collection built for a model.

        This is diagnostic only and never opens a collection.  It lets the CLI
        distinguish "not built" from "built with an incompatible schema or
        version" without touching vectors or making network calls.
        """
        base = Path(base_path)
        if not base.exists():
            return []
        found: list[dict] = []
        for meta_path in base.glob("*/schema-v*/sard_collection_meta.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if meta.get("embedding_model") == embedding_model:
                found.append({"path": str(meta_path.parent), **meta})
        return found

    def close(self) -> None:
        """Best-effort clean shutdown: flush pending writes and release the
        Python reference so Zvec's underlying resources can be freed."""
        if self._collection is None:
            return
        try:
            self._collection.flush()
        except Exception:
            logger.exception("Error flushing Zvec collection at %s", self.path)
        self._collection = None

    # -- writes --------------------------------------------------------

    def upsert_chunks(self, embedded_chunks: list[EmbeddedChunk], created_at: str) -> int:
        """Idempotently upsert chunks. Returns the number of docs written."""
        import zvec

        docs = []
        for ec in embedded_chunks:
            chunk = ec.chunk
            if ec.embedding_model != self.embedding_model:
                raise FallbackClassifiedError(
                    FailureCategory.EMBEDDING_DIMENSION_MISMATCH,
                    f"Refusing to insert a chunk embedded with "
                    f"{ec.embedding_model!r} into a collection built with "
                    f"{self.embedding_model!r}.",
                )
            if chunk.schema_version != SCHEMA_VERSION:
                raise ZvecSchemaMismatchError(
                    f"Refusing to insert chunk {chunk.chunk_id!r} with schema_version="
                    f"{chunk.schema_version!r} into schema-v{SCHEMA_VERSION}."
                )
            if ec.embedding_dimension != self.embedding_dimension:
                raise FallbackClassifiedError(
                    FailureCategory.EMBEDDING_DIMENSION_MISMATCH,
                    f"Declared embedding dimension {ec.embedding_dimension} does not "
                    f"match collection dimension {self.embedding_dimension}.",
                )
            if len(ec.dense_embedding) != self.embedding_dimension:
                raise FallbackClassifiedError(
                    FailureCategory.EMBEDDING_DIMENSION_MISMATCH,
                    f"Embedding dimension {len(ec.dense_embedding)} does not "
                    f"match collection dimension {self.embedding_dimension}.",
                )
            if not all(math.isfinite(float(value)) for value in ec.dense_embedding):
                raise FallbackClassifiedError(
                    FailureCategory.MALFORMED_OUTPUT,
                    f"Embedding for chunk {chunk.chunk_id!r} contains a non-finite value.",
                )
            docs.append(
                zvec.Doc(
                    id=chunk.chunk_id,
                    vectors={DENSE_VECTOR_FIELD: ec.dense_embedding},
                    fields={
                        CONTENT_FIELD: chunk.content,
                        NORMALIZED_CONTENT_FIELD: normalize_arabic(chunk.content),
                        "title": chunk.title,
                        "source_name": chunk.source_name,
                        "source_url": chunk.source_url,
                        "topic": chunk.topic,
                        "publication_date": chunk.publication_date or "",
                        "language": chunk.language,
                        "document_id": chunk.document_id,
                        "chunk_id": chunk.chunk_id,
                        "content_hash": chunk.content_hash,
                        "citation_id": chunk.citation_id,
                        "embedding_model": ec.embedding_model,
                        "embedding_dimension": ec.embedding_dimension,
                        "schema_version": chunk.schema_version,
                        "ingestion_version": chunk.ingestion_version,
                        "created_at": created_at,
                        "page_number": chunk.page_number,
                        "section_heading": chunk.section_heading or "",
                        "metadata_json": json.dumps(
                            chunk.extra_metadata, ensure_ascii=False, sort_keys=True
                        ),
                    },
                )
            )
        if not docs:
            return 0
        self._collection.upsert(docs)
        self._collection.flush()
        return len(docs)

    def delete_by_document_id(self, document_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", document_id):
            raise UnsafeFilterValueError("Refusing to delete with an unsafe document ID.")
        self._collection.delete_by_filter(filter=f"document_id = '{document_id}'")

    def delete_by_ids(self, chunk_ids: list[str]) -> None:
        """Delete an explicit set of stale chunk IDs after a replacement succeeds."""
        safe_ids = [cid for cid in chunk_ids if re.fullmatch(r"[A-Za-z0-9_-]+", cid)]
        if len(safe_ids) != len(chunk_ids):
            raise UnsafeFilterValueError("Refusing to delete a chunk with an unsafe ID.")
        for chunk_id in safe_ids:
            self._collection.delete(ids=[chunk_id])
        if safe_ids:
            self._collection.flush()

    # -- reads -----------------------------------------------------------

    def dense_search(
        self, query_vector: list[float], topk: int, filters: Optional[RetrievalFilters] = None
    ) -> list[RetrievedCandidate]:
        import zvec

        if topk < 1:
            return []
        if len(query_vector) != self.embedding_dimension:
            raise FallbackClassifiedError(
                FailureCategory.EMBEDDING_DIMENSION_MISMATCH,
                f"Query vector dimension {len(query_vector)} does not match "
                f"collection dimension {self.embedding_dimension}.",
            )

        filter_expr = build_safe_filter(filters) if filters else None
        results = self._collection.query(
            queries=zvec.Query(field_name=DENSE_VECTOR_FIELD, vector=query_vector),
            topk=topk,
            filter=filter_expr,
            output_fields=_OUTPUT_FIELDS,
        )
        candidates = [self._doc_to_candidate(d) for d in results]
        for rank, c in enumerate(candidates, start=1):
            c.dense_rank = rank
        return candidates

    def fts_search(
        self, query_text: str, topk: int, filters: Optional[RetrievalFilters] = None
    ) -> list[RetrievedCandidate]:
        import zvec

        if topk < 1 or not query_text.strip():
            return []

        filter_expr = build_safe_filter(filters) if filters else None
        results = self._collection.query(
            queries=zvec.Query(
                field_name=NORMALIZED_CONTENT_FIELD,
                fts=zvec.Fts(match_string=normalize_arabic(query_text)),
            ),
            topk=topk,
            filter=filter_expr,
            output_fields=_OUTPUT_FIELDS,
        )
        candidates = [self._doc_to_candidate(d) for d in results]
        for rank, c in enumerate(candidates, start=1):
            c.fts_score = c.dense_score
            c.dense_score = None
            c.fts_rank = rank
        return candidates

    def fetch_by_ids(self, chunk_ids: list[str]) -> dict[str, RetrievedCandidate]:
        if not chunk_ids:
            return {}
        docs = self._collection.fetch(ids=chunk_ids, output_fields=_OUTPUT_FIELDS, include_vector=False)
        return {cid: self._doc_to_candidate(doc) for cid, doc in docs.items()}

    @property
    def stats(self) -> CollectionStats:
        s = self._collection.stats
        return CollectionStats(
            doc_count=s.doc_count,
            path=str(self.path),
            embedding_model=self.embedding_model,
            embedding_dimension=self.embedding_dimension,
        )

    @staticmethod
    def _doc_to_candidate(doc) -> RetrievedCandidate:
        f = doc.fields or {}
        page_number = f.get("page_number")
        try:
            extra_metadata = json.loads(f.get("metadata_json") or "{}")
            if not isinstance(extra_metadata, dict):
                extra_metadata = {}
        except (TypeError, json.JSONDecodeError):
            extra_metadata = {}
        return RetrievedCandidate(
            chunk_id=doc.id,
            document_id=f.get("document_id", ""),
            citation_id=f.get("citation_id", ""),
            content=f.get(CONTENT_FIELD, ""),
            title=f.get("title", ""),
            source_name=f.get("source_name", ""),
            source_url=f.get("source_url", ""),
            topic=f.get("topic", ""),
            language=f.get("language", ""),
            publication_date=f.get("publication_date") or None,
            page_number=page_number if page_number not in (None, 0) else page_number,
            content_hash=f.get("content_hash", ""),
            dense_score=doc.score,
            extra_metadata=extra_metadata,
        )


def candidate_to_document(candidate: RetrievedCandidate):
    """Convert one :class:`RetrievedCandidate` into a LangChain ``Document``.

    Kept as a free function (not a method) so both the retriever adapter
    below and any future LangGraph node can reuse the exact same, tested
    mapping without depending on a particular retriever class.
    """
    from langchain_core.documents import Document

    return Document(
        page_content=candidate.content,
        metadata={
            "chunk_id": candidate.chunk_id,
            "document_id": candidate.document_id,
            "citation_id": candidate.citation_id,
            "title": candidate.title,
            "source_name": candidate.source_name,
            "source_url": candidate.source_url,
            "topic": candidate.topic,
            "language": candidate.language,
            "publication_date": candidate.publication_date,
            "page_number": candidate.page_number,
            "score": candidate.dense_score,
            "extra_metadata": candidate.extra_metadata,
        },
    )


def build_dense_retriever(repository: "ZvecRepository", embeddings, k: int = 10, filters: Optional[RetrievalFilters] = None):
    """Build a LangChain ``BaseRetriever`` over a Zvec collection.

    ``embeddings`` is any LangChain ``Embeddings`` instance (e.g. the one
    returned by ``sard.rag.embeddings`` internals) — this function never
    imports a provider SDK itself. Returns ``langchain_core.documents
    .Document`` objects only, per the "LangChain-compatible retriever
    adapter" requirement; it never leaks a raw Zvec ``Doc`` or
    ``RetrievedCandidate`` to callers.
    """
    from langchain_core.callbacks import CallbackManagerForRetrieverRun
    from langchain_core.retrievers import BaseRetriever

    class _ZvecDenseRetriever(BaseRetriever):
        def _get_relevant_documents(
            self, query: str, *, run_manager: Optional[CallbackManagerForRetrieverRun] = None
        ):
            vector = embeddings.embed_query(query)
            candidates = repository.dense_search(vector, topk=k, filters=filters)
            return [candidate_to_document(c) for c in candidates]

    return _ZvecDenseRetriever()
