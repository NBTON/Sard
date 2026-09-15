"""J6: Blob persistence cross-instance (boundary F, mocked BlobClient/HTTP).

Put via store A, get via fresh store B sharing only the blob backend
(no shared filesystem). Asserts bytes + Content-Type + filename.
No live network, no secrets.
"""
from __future__ import annotations

import pytest

from sard.outputs.orchestrator import ConfigurableBlobArtifactStore
from sard.outputs.validation import ARTIFACT_MIME_TYPES

from .conftest import install_fake_blob


def _stores(tmp_path, monkeypatch, backend, endpoint="https://blob.test"):
    install_fake_blob(monkeypatch, backend, endpoint)
    fallback_a = tmp_path / "a"
    fallback_b = tmp_path / "b"
    fallback_a.mkdir()
    fallback_b.mkdir()
    # Import locally so the fallback FileSystemArtifactStore roots differ.
    from sard.outputs.orchestrator import FileSystemArtifactStore

    store_a = ConfigurableBlobArtifactStore(
        fallback_local=FileSystemArtifactStore(fallback_a),
        endpoint=endpoint,
        token="test-token-xyz",
        public_base_url=endpoint,
    )
    store_b = ConfigurableBlobArtifactStore(
        fallback_local=FileSystemArtifactStore(fallback_b),
        endpoint=endpoint,
        token="test-token-xyz",
        public_base_url=endpoint,
    )
    assert store_a.blob_configured and store_b.blob_configured
    return store_a, store_b


def test_06_blob_put_via_a_get_via_fresh_b(tmp_path, monkeypatch, fake_blob_backend):
    store_a, store_b = _stores(tmp_path, monkeypatch, fake_blob_backend)
    payload = "تقرير ثقافي عن العمارة النجدية — blob cross-instance.".encode("utf-8")
    art_id, safe_name, size, checksum = store_a.store_bytes("art-blob-001", "report.pdf", payload, "application/pdf")
    assert size == len(payload)
    assert safe_name.endswith(".pdf") and "art-blob-001" in safe_name
    # Blob has no local path: proves B cannot be reading A's filesystem.
    assert store_a.get_file_path("art-blob-001") is None
    assert store_b.get_file_path("art-blob-001") is None

    got_by_id = store_b.get_bytes("art-blob-001")
    assert got_by_id is not None
    data, filename, mime = got_by_id
    assert data == payload
    assert filename == safe_name
    assert mime == ARTIFACT_MIME_TYPES["pdf"] == "application/pdf"

    got_by_filename = store_b.get_bytes(safe_name)
    assert got_by_filename is not None
    assert got_by_filename[0] == payload
    assert got_by_filename[1] == safe_name
    assert store_b.exists("art-blob-001") is True
    # Backend holds exactly one data blob + one index doc, no filesystem share.
    assert len(fake_blob_backend.blobs) == 1
    assert len(fake_blob_backend.indexes) == 1


def test_06b_blob_canonicalizes_content_type(tmp_path, monkeypatch, fake_blob_backend):
    store_a, store_b = _stores(tmp_path, monkeypatch, fake_blob_backend)
    payload = b"%PDF-1.7 fake-bytes-for-mime-check"
    _, safe_name, _, _ = store_a.store_bytes("art-blob-002", "report.pdf", payload, "application/incorrect")
    _, _, mime = store_b.get_bytes("art-blob-002")
    assert mime == "application/pdf"
    assert safe_name.endswith(".pdf")


def test_06c_blob_duplicate_id_is_explicit_never_silent_overwrite(tmp_path, monkeypatch, fake_blob_backend):
    store_a, store_b = _stores(tmp_path, monkeypatch, fake_blob_backend)
    store_a.store_bytes("art-blob-003", "report.pdf", b"first-bytes", "application/pdf")
    with pytest.raises(RuntimeError):
        store_a.store_bytes("art-blob-003", "report.pdf", b"second-bytes", "application/pdf")
    data, _, _ = store_b.get_bytes("art-blob-003")
    assert data == b"first-bytes"
