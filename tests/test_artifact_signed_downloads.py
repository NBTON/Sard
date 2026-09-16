"""Signed expiring artifact downloads + storage-truth surfaces.

Signed mode (SARD_DOWNLOAD_SECRET set): bare filename/version URLs are
rejected (401), expired links are 410 Gone, tampered signatures are 401,
and API-issued signed URLs download with correct MIME/bytes. Open mode
(local dev): plain URLs keep working and /api/status reports it.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from sard.api.server import app
from sard.outputs.orchestrator import (
    ArtifactOrchestrator,
    ArtifactRequest,
    FileSystemArtifactStore,
    set_artifact_store,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = FileSystemArtifactStore(root_dir=tmp_path)
    set_artifact_store(store)
    monkeypatch.setenv("SARD_DOWNLOAD_SECRET", "test-secret-for-signing-only")
    yield TestClient(app), store
    monkeypatch.delenv("SARD_DOWNLOAD_SECRET", raising=False)


def _create(store: FileSystemArtifactStore):
    result = ArtifactOrchestrator(store).generate_artifact(
        ArtifactRequest(
            format="txt", kind="document", title="تقرير", topic="الدرعية",
            raw_text="نص توثيقي أول.\n\nنص توثيقي ثان.",
        )
    )
    assert result.status == "created", result.error
    return result


def test_signed_download_round_trip(client):
    test_client, store = client
    created = _create(store)
    url = created.download_url
    assert "exp=" in url and "sig=" in url, f"download URL must be signed in signed mode: {url}"
    resp = test_client.get(url)
    assert resp.status_code == 200, resp.text
    assert resp.content == bytes(created.data or b"")
    assert "text/plain" in resp.headers["content-type"]


def test_bare_filename_rejected_in_signed_mode(client):
    test_client, store = client
    created = _create(store)
    plain = created.download_url.split("?")[0]
    resp = test_client.get(plain)
    assert resp.status_code == 401, resp.text


def test_tampered_signature_rejected(client):
    test_client, store = client
    created = _create(store)
    url = created.download_url
    tampered = url[:-1] + ("0" if not url.endswith("0") else "1")
    assert test_client.get(tampered).status_code == 401


def test_expired_link_is_gone(client):
    import hmac as _hmac
    import hashlib as _hashlib

    test_client, store = client
    created = _create(store)
    plain = created.download_url.split("?")[0]
    filename = plain.rsplit("/", 1)[-1]
    past = int(time.time()) - 10
    sig = _hmac.new(
        b"test-secret-for-signing-only",
        f"file:{filename}|{past}".encode(),
        _hashlib.sha256,
    ).hexdigest()[:48]
    resp = test_client.get(f"{plain}?exp={past}&sig={sig}")
    assert resp.status_code == 410, resp.text


def test_version_download_signed(client):
    test_client, store = client
    created = _create(store)
    rev = test_client.post(
        f"/api/artifacts/{created.id}/revisions",
        json={"instruction": "append: فقرة إضافية"},
    )
    assert rev.status_code == 200, rev.text
    versions = test_client.get(f"/api/artifacts/{created.id}/versions").json()
    assert versions["count"] == 2
    for view in versions["versions"]:
        assert "exp=" in view["download_url"] and "sig=" in view["download_url"]
    v1_plain = versions["versions"][0]["download_url"].split("?")[0]
    assert test_client.get(v1_plain).status_code == 401
    v1_signed = versions["versions"][0]["download_url"]
    assert test_client.get(v1_signed).status_code == 200


def test_status_reports_surfaces_and_download_mode(client):
    test_client, _ = client
    body = test_client.get("/api/status").json()
    storage = body.get("storage", {})
    assert storage.get("downloads") == "signed"
    surfaces = storage.get("surfaces", {})
    assert surfaces.get("artifacts")
    assert surfaces.get("attachments") == "ephemeral_filesystem"
    assert surfaces.get("run_records") == "ephemeral_filesystem"


def test_open_mode_without_secret(tmp_path, monkeypatch):
    from sard.api import server as server_mod

    monkeypatch.delenv("SARD_DOWNLOAD_SECRET", raising=False)
    store = FileSystemArtifactStore(root_dir=tmp_path)
    set_artifact_store(store)
    test_client = TestClient(app)
    created = _create(store)
    assert "exp=" not in (created.download_url or "")
    plain = (created.download_url or "").split("?")[0]
    assert test_client.get(plain).status_code == 200
    body = test_client.get("/api/status").json()
    assert body.get("storage", {}).get("downloads") == "open"
    assert server_mod is not None


def test_run_record_keeps_query_preview_only(client):
    from sard.api.server import _run_record_get, _run_record_put

    long_query = "سؤال طويل جدا " * 50
    _run_record_put("run-preview-1", "succeeded", [], True, {"query": long_query})
    record = _run_record_get("run-preview-1")
    assert record is not None
    assert len(record.get("query", "")) <= 120
    assert record.get("query_truncated") is True


def test_metadata_exposes_no_provider_direct_urls(client):
    import json as _json

    _, store = client
    created = _create(store)
    meta = store.get_metadata(created.id)
    assert meta is not None
    blob = _json.dumps(meta, ensure_ascii=False)
    assert "blob.vercel-storage.com" not in blob
    assert "download_url" not in meta or str(meta.get("download_url", "")).startswith("/api/")
