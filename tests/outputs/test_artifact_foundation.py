"""Focused acceptance tests for the artifact validation/storage boundary."""

from __future__ import annotations

import concurrent.futures
import hashlib
from pathlib import Path

import fitz
import pytest

from sard.outputs.orchestrator import (
    ArtifactGeneratorRegistry,
    ArtifactOrchestrator,
    ArtifactRequest,
    ConfigurableBlobArtifactStore,
    FileSystemArtifactStore,
)
from sard.outputs.artifacts import ArtifactError, ArtifactManager
from sard.outputs.validation import ArtifactValidationError, validate_artifact_bytes
from sard.runtime_paths import durable_storage_configured, output_root, output_root_is_ephemeral


def _request(fmt: str, **kwargs) -> ArtifactRequest:
    return ArtifactRequest(
        format=fmt,
        kind=kwargs.pop("kind", "document"),
        title=kwargs.pop("title", "Arabic English artifact"),
        topic=kwargs.pop("topic", "العمارة النجدية / Najdi architecture"),
        raw_text=kwargs.pop("raw_text", "نص عربي متصل English mixed text 123."),
        **kwargs,
    )


@pytest.mark.parametrize("fmt", ["pdf", "docx", "pptx", "ics", "svg", "png", "json", "csv", "txt"])
def test_every_public_format_is_validated_and_retrievable(fmt, tmp_path):
    store = FileSystemArtifactStore(tmp_path)
    # G7: ICS needs a real matching topic (no canned fallback).
    _topic = "سهيل" if fmt == "ics" else "العمارة النجدية / Najdi architecture"
    result = ArtifactOrchestrator(store).generate_artifact(_request(fmt, kind="calendar" if fmt == "ics" else "document", topic=_topic))

    assert result.status == "created", result.error
    assert result.size_bytes > 0
    assert result.checksum == hashlib.sha256(result.data).hexdigest()
    assert result.data == store.get_bytes(result.id)[0]
    assert result.data == store.get_bytes(result.filename)[0]
    assert validate_artifact_bytes(fmt, result.data).size_bytes == result.size_bytes


def test_general_pdf_does_not_require_an_itinerary_and_is_parseable(tmp_path):
    result = ArtifactOrchestrator(FileSystemArtifactStore(tmp_path)).generate_artifact(
        _request("pdf", kind="document", raw_text="English heading\n\nالعربية المختلطة مع نص طويل."),
    )
    assert result.status == "created"
    with fitz.open(stream=result.data, filetype="pdf") as document:
        assert document.page_count >= 1
        text = "".join(page.get_text() for page in document)
    assert "English" in text
    import arabic_reshaper

    assert arabic_reshaper.reshape("العربية") in text


def test_same_title_and_concurrent_requests_get_unique_files(tmp_path):
    def generate(index: int):
        store = FileSystemArtifactStore(tmp_path)
        return ArtifactOrchestrator(store).generate_artifact(
            _request("txt", title="same requested title", raw_text=f"request {index}"),
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(generate, range(8)))
    assert all(item.status == "created" for item in results)
    assert len({item.id for item in results}) == 8
    assert len({item.filename for item in results}) == 8
    assert len(list(tmp_path.glob("*.txt"))) == 8


def test_renderer_exception_empty_and_invalid_bytes_are_failures(monkeypatch, tmp_path):
    orchestrator = ArtifactOrchestrator(FileSystemArtifactStore(tmp_path))
    monkeypatch.setattr(orchestrator.registry, "render_pdf", lambda request: (b"", "application/pdf", None))
    empty = orchestrator.generate_artifact(_request("pdf"))
    assert empty.status == "failed"
    assert empty.error_category == "empty_output"

    monkeypatch.setattr(orchestrator.registry, "render_pdf", lambda request: (b"not a pdf", "application/pdf", None))
    invalid = orchestrator.generate_artifact(_request("pdf"))
    assert invalid.status == "failed"
    assert invalid.error_category == "invalid_signature"
    assert invalid.download_url is None

    monkeypatch.setattr(orchestrator.registry, "render_pdf", lambda request: (_ for _ in ()).throw(RuntimeError("secret")))
    raised = orchestrator.generate_artifact(_request("pdf"))
    assert raised.status == "failed"
    assert raised.error_category == "renderer_exception"
    assert "secret" not in (raised.error or "")


def test_suggested_filename_traversal_is_rejected_without_writing(tmp_path):
    result = ArtifactOrchestrator(FileSystemArtifactStore(tmp_path)).generate_artifact(
        _request("txt", suggested_filename="../../escape.txt"),
    )
    assert result.status == "failed"
    assert result.error_category == "unsafe_filename"
    assert not (tmp_path.parent / "escape.txt").exists()


def test_new_store_instance_resolves_id_and_preserves_mime(tmp_path):
    first = FileSystemArtifactStore(tmp_path)
    result = ArtifactOrchestrator(first).generate_artifact(_request("json"))
    second = FileSystemArtifactStore(tmp_path)
    retrieved = second.get_bytes(result.id)
    assert retrieved is not None
    assert retrieved[0] == result.data
    assert retrieved[2] == "application/json"


def test_storage_rejects_direct_traversal_and_never_overwrites(tmp_path):
    store = FileSystemArtifactStore(tmp_path)
    with pytest.raises(ValueError):
        store.store_bytes("art-one", "../escape.txt", b"x", "text/plain")
    store.store_bytes("art-one", "same.txt", b"x", "text/plain")
    with pytest.raises(ValueError, match="overwrite"):
        store.store_bytes("art-one", "same.txt", b"y", "text/plain")


def test_artifact_manager_never_publishes_empty_or_invalid_known_formats(tmp_path):
    manager = ArtifactManager(tmp_path, "validation-run")
    with pytest.raises(ArtifactError, match="empty"):
        manager.write_bytes(b"", filename="empty.pdf", artifact_type="pdf", display_label="x", mime_type="application/pdf")
    with pytest.raises(ArtifactError, match="invalid"):
        manager.write_bytes(b"not pdf", filename="invalid.pdf", artifact_type="pdf", display_label="x", mime_type="application/pdf")
    assert not list((tmp_path / "validation-run").glob("*.pdf"))


def test_vercel_tmp_is_explicitly_ephemeral(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("SARD_BLOB_ENDPOINT", raising=False)
    monkeypatch.delenv("SARD_BLOB_TOKEN", raising=False)
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    monkeypatch.setattr("sard.runtime_paths.tempfile.gettempdir", lambda: str(tmp_path))
    assert output_root_is_ephemeral() is True
    assert durable_storage_configured() is False
    assert output_root() == tmp_path / "sard-output"
    assert isinstance(ConfigurableBlobArtifactStore(fallback_local=FileSystemArtifactStore(tmp_path)), ConfigurableBlobArtifactStore)


@pytest.mark.parametrize(
    ("fmt", "data", "category"),
    [
        ("pdf", b"", "empty_output"),
        ("pdf", b"%PDF-1.7\nnot complete", "unparseable"),
        ("docx", b"PK\x03\x04", "invalid_docx"),
        ("pptx", b"PK\x03\x04", "invalid_pptx"),
        ("svg", b"<svg><script>alert(1)</script></svg>", "unsafe_xml"),
        ("png", b"not png", "invalid_signature"),
        ("json", b"{bad", "unparseable"),
        ("csv", b"a,b\n1", "invalid_schema"),
        ("txt", b"\xff", "invalid_encoding"),
    ],
)
def test_validators_return_stable_failure_categories(fmt, data, category):
    with pytest.raises(ArtifactValidationError) as error:
        validate_artifact_bytes(fmt, data)
    assert error.value.category == category
