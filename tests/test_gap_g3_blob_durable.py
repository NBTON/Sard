"""G3: official Vercel Blob SDK wrapper exists; storage truth surfaced."""
from sard.outputs.orchestrator import VercelBlobArtifactStore, ConfigurableBlobArtifactStore, FileSystemArtifactStore
from sard.runtime_paths import durable_storage_configured
import os

def test_gap_g3_vercel_blob_store_exists_and_falls_back(tmp_path, monkeypatch):
    # Unconfigured -> falls back to local FS, stores verifiably
    for k in ["BLOB_READ_WRITE_TOKEN","SARD_BLOB_TOKEN","SARD_BLOB_ENDPOINT"]:
        monkeypatch.delenv(k, raising=False)
    store = VercelBlobArtifactStore(fallback_local=FileSystemArtifactStore(root_dir=tmp_path))
    assert store.blob_configured is False
    aid, fname, size, checksum = store.store_bytes("art-test123", "hello.pdf", b"%PDF-1.4 test", "application/pdf")
    assert size > 0 and checksum
    assert store.exists(fname)
    data, fn, mime = store.get_bytes(fname)
    assert data.startswith(b"%PDF")

def test_gap_g3_durable_config_reflects_env(monkeypatch):
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    monkeypatch.delenv("SARD_BLOB_TOKEN", raising=False)
    monkeypatch.delenv("SARD_BLOB_ENDPOINT", raising=False)
    assert durable_storage_configured() is False
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "tok-test")
    # endpoint defaults to vercel blob endpoint when token set
    assert durable_storage_configured() is True
