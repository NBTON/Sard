"""Ingestion pipeline tests: end-to-end ingestion with a real Zvec collection
and a fake (offline) embedding service, covering idempotency, resumability,
changed-source re-ingestion (stale chunk cleanup), metadata sidecars,
scanned-PDF quarantine, dedup counting, and report accuracy.
"""

from __future__ import annotations

import json

import pytest

from sard.rag.chunking import compute_content_hash
from sard.rag.ingest import (
    IngestionManifest,
    MissingMetadataError,
    build_chunks_for_document,
    ingest_directory,
    load_metadata_sidecar,
)
from sard.rag.schemas import DocumentMetadata
from sard.rag.zvec_store import ZvecRepository

EMBED_DIM = 4


class _FakeEmbeddingService:
    """Mimics EmbeddingService.embed_documents's call signature (offline)."""

    def __init__(self, dim: int = EMBED_DIM, fail: bool = False):
        self.dim = dim
        self.fail = fail
        self.calls = 0

    def embed_documents(self, model_id, texts, expected_dim=None):
        self.calls += 1
        if self.fail:
            from sard.rag.fallbacks import AllCandidatesFailedError, FailureCategory, FallbackClassifiedError

            raise AllCandidatesFailedError(
                "embedding_documents",
                [
                    FallbackClassifiedError(
                        FailureCategory.MODEL_UNAVAILABLE, "fake model unavailable"
                    ),
                ],
            )
        return _EmbeddingOutcome([[0.1 + i * 0.01] * self.dim for i in range(len(texts))])

    def discover_dimension(self, model_id):
        return self.dim


class _EmbeddingOutcome:
    def __init__(self, vectors):
        self.vectors = vectors


@pytest.fixture
def repo(tmp_path):
    repository = ZvecRepository.open_or_create(str(tmp_path / "zvec"), "test-embed", EMBED_DIM)
    yield repository
    repository.close()


def _write_source(corpus_dir, name, content, meta=None, topic="springs"):
    path = corpus_dir / name
    path.write_text(content, encoding="utf-8")
    meta = meta or {
        "source_name": "مصدر تجريبي",
        "source_url": f"https://example.com/{name}",
        "title": f"عنوان {name}",
        "topic": topic,
        "publication_date": "2020-01-01",
        "language": "ar",
    }
    (corpus_dir / f"{name}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )
    return path


def test_ingest_directory_ingests_all_formats_and_reports(tmp_path, repo):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _write_source(corpus, "a.md", "# عيون الأحساء\n\nنص عن الينابيع الحارة في الأحساء.", topic="springs")
    _write_source(corpus, "b.txt", "نص بسيط عن تجفيف الروبيان في الساحل الشرقي.", topic="shrimp")

    report = ingest_directory(corpus, repo, _FakeEmbeddingService(), "test-embed")

    assert report.documents_seen == 2
    assert report.documents_ingested == 2
    assert report.documents_failed == 0
    assert report.documents_skipped_unchanged == 0
    assert report.chunks_inserted >= 2
    assert report.embedding_model_used == "test-embed"
    assert repo.stats.doc_count == report.chunks_inserted

    manifest_path = repo.path / "ingestion_manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest) == 2


def test_ingestion_is_idempotent_on_rerun(tmp_path, repo):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _write_source(corpus, "a.md", "# عيون الأحساء\n\nنص عن الينابيع الحارة.", topic="springs")

    first = ingest_directory(corpus, repo, _FakeEmbeddingService(), "test-embed")
    second = ingest_directory(corpus, repo, _FakeEmbeddingService(), "test-embed")

    assert second.documents_skipped_unchanged == 1
    assert second.chunks_inserted == 0
    assert second.documents_ingested == 0
    assert repo.stats.doc_count == first.chunks_inserted  # no duplicates


def test_changed_source_is_reingested_and_stale_chunks_removed(tmp_path, repo):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    path = _write_source(corpus, "a.md", "نص النسخة الأولى عن الينابيع الحارة في الأحساء.", topic="springs")

    first = ingest_directory(corpus, repo, _FakeEmbeddingService(), "test-embed")
    assert first.documents_ingested == 1
    old_chunk_ids = {c.chunk_id for c in repo.dense_search([0.1] * EMBED_DIM, topk=50)}
    assert old_chunk_ids

    # Same file, different content -> new content hash -> re-chunked.
    _write_source(corpus, "a.md", "نص النسخة الثانية المختلف كليًا عن العيون المائية.", topic="springs")
    second = ingest_directory(corpus, repo, _FakeEmbeddingService(), "test-embed")
    assert second.documents_ingested == 1
    assert second.documents_skipped_unchanged == 0

    new_chunk_ids = {c.chunk_id for c in repo.dense_search([0.1] * EMBED_DIM, topk=50)}
    # The old chunk set must be gone; the new set must exist and differ.
    assert new_chunk_ids
    assert new_chunk_ids != old_chunk_ids
    assert not (old_chunk_ids & new_chunk_ids)
    fetched = repo.fetch_by_ids(list(old_chunk_ids))
    assert all(cid not in fetched for cid in old_chunk_ids)


def test_ingestion_resumes_after_failed_document(tmp_path, repo):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _write_source(corpus, "ok.md", "نص سليم عن الينابيع الحارة.", topic="springs")
    bad = corpus / "bad.md"
    bad.write_text("نص بدون سايدكار.", encoding="utf-8")  # no .meta.json

    first = ingest_directory(corpus, repo, _FakeEmbeddingService(), "test-embed")
    assert first.documents_seen == 2
    assert first.documents_ingested == 1
    assert first.documents_failed == 1
    assert any("Missing metadata sidecar" in e for e in first.errors)

    # Add the missing sidecar; re-running must only ingest the new one.
    _write_source(corpus, "bad.md", "نص سليم عن تجفيف الروبيان.", topic="shrimp")
    second = ingest_directory(corpus, repo, _FakeEmbeddingService(), "test-embed")
    assert second.documents_seen == 2
    assert second.documents_ingested == 1
    assert second.documents_skipped_unchanged == 1


def test_embedding_failure_is_reported_not_crashing(tmp_path, repo):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _write_source(corpus, "a.md", "نص عن الينابيع الحارة في الأحساء.", topic="springs")

    report = ingest_directory(corpus, repo, _FakeEmbeddingService(fail=True), "test-embed")
    assert report.documents_failed == 1
    assert report.documents_ingested == 0
    assert any("Embedding failed" in e for e in report.errors)
    assert repo.stats.doc_count == 0


def test_scanned_pdf_pages_are_quarantined_and_reported(tmp_path, repo):
    from tests.rag.test_loaders import _make_text_pdf

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    pdf = corpus / "scan.pdf"
    pdf.write_bytes(_make_text_pdf(" ", pages=2))
    # Write only the sidecar; do NOT overwrite the PDF bytes.
    (corpus / "scan.pdf.meta.json").write_text(
        json.dumps(
            {
                "source_name": "مصدر تجريبي",
                "source_url": "https://example.com/scan.pdf",
                "title": "مستند ممسوح",
                "topic": "springs",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = ingest_directory(corpus, repo, _FakeEmbeddingService(), "test-embed")
    # Both pages flagged as likely-scanned and quarantined (never fabricated).
    assert len(report.scanned_pages_quarantined) == 2
    assert {p.page_number for p in report.scanned_pages_quarantined} == {1, 2}
    assert all("quarantined" in p.reason.lower() for p in report.scanned_pages_quarantined)
    # The scanned doc has no usable text -> skipped with a warning, not fabricated.
    assert any("No usable text" in w for w in report.warnings)
    assert report.documents_failed == 0
    assert report.documents_ingested == 0


def test_exact_duplicate_chunks_are_deduplicated_within_document():
    metadata = DocumentMetadata(
        source_name="مصدر",
        source_url="https://example.com/x",
        title="عنوان",
        topic="springs",
        document_id="DOC-x",
    )
    from sard.rag.schemas import ParsedSection

    # A text long enough that each identical section splits into MULTIPLE
    # chunks; because both sections are byte-identical, the second section's
    # chunks collide with the first's content hashes and must be deduplicated.
    long_text = ("الينابيع الحارة في الأحساء مذكورة في المصادر التاريخية. " * 200).strip()
    sections = [
        ParsedSection(heading=None, text=long_text),
        ParsedSection(heading=None, text=long_text),
    ]
    chunks, dedup = build_chunks_for_document(metadata, "DOC-x", sections)
    assert dedup >= 1
    assert len(chunks) > 1  # the long text really was split into multiple chunks
    hashes = [c.content_hash for c in chunks]
    assert len(hashes) == len(set(hashes))


def test_load_metadata_sidecar_validates_required_fields(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _write_source(corpus, "a.md", "نص")
    meta = load_metadata_sidecar(corpus / "a.md")
    assert meta.document_id.startswith("DOC-")
    assert meta.topic == "springs"

    partial = corpus / "b.md"
    partial.write_text("نص", encoding="utf-8")
    (corpus / "b.md.meta.json").write_text(json.dumps({"source_name": "فقط"}), encoding="utf-8")
    with pytest.raises(MissingMetadataError):
        load_metadata_sidecar(partial)


def test_ingestion_manifest_tracks_document_state(tmp_path):
    manifest = IngestionManifest(tmp_path / "m" / "ingestion_manifest.json")
    chunk_ids = ["chunk-1", "chunk-2"]
    manifest.record("DOC-1", "hash-1", chunk_ids, "corpus/a.md")
    assert manifest.get("DOC-1")["chunk_ids"] == chunk_ids
    manifest.save()

    reloaded = IngestionManifest(tmp_path / "m" / "ingestion_manifest.json")
    assert reloaded.get("DOC-1")["document_hash"] == "hash-1"
    assert reloaded.source_paths() == ["corpus/a.md"]


def test_document_hash_detects_source_change():
    content = "نص عن الينابيع الحارة في الأحساء"
    assert compute_content_hash(content) != compute_content_hash(content + " إضافي")
