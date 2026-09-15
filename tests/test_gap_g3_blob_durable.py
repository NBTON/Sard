"""G3: official Vercel Blob SDK wrapper exists; storage truth surfaced."""
from dataclasses import dataclass, field

from fastapi.testclient import TestClient

from sard.outputs.orchestrator import VercelBlobArtifactStore, FileSystemArtifactStore, get_artifact_store, set_artifact_store
from sard.runtime_paths import durable_storage_configured

def test_gap_g3_vercel_blob_store_exists_and_falls_back(tmp_path, monkeypatch):
    # Unconfigured -> falls back to local FS, stores verifiably
    for k in ["BLOB_READ_WRITE_TOKEN","VERCEL_BLOB_READ_WRITE_TOKEN","SARD_BLOB_TOKEN","SARD_BLOB_ENDPOINT"]:
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
    monkeypatch.delenv("VERCEL_BLOB_READ_WRITE_TOKEN", raising=False)
    monkeypatch.delenv("SARD_BLOB_TOKEN", raising=False)
    monkeypatch.delenv("SARD_BLOB_ENDPOINT", raising=False)
    assert durable_storage_configured() is False
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "tok-test")
    # endpoint defaults to vercel blob endpoint when token set
    assert durable_storage_configured() is True


def test_gap_g3_vercel_alias_token_configures_store(monkeypatch, tmp_path):
    """VERCEL_BLOB_READ_WRITE_TOKEN alias alone must configure durable storage."""
    for k in ["BLOB_READ_WRITE_TOKEN", "SARD_BLOB_TOKEN", "SARD_BLOB_ENDPOINT"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("VERCEL_BLOB_READ_WRITE_TOKEN", "tok-alias")
    assert durable_storage_configured() is True
    store = VercelBlobArtifactStore(fallback_local=FileSystemArtifactStore(root_dir=tmp_path))
    assert store.blob_configured is True


# --- Fake SDK (no network) ---------------------------------------------------

@dataclass
class _FakePutResult:
    url: str
    download_url: str
    pathname: str
    content_type: str = ""
    content_disposition: str = ""


@dataclass
class _FakeGetResult:
    content: bytes
    content_type: str | None = None
    pathname: str = ""
    url: str = ""
    download_url: str = ""


@dataclass
class _FakeHeadResult:
    size: int
    pathname: str
    content_type: str = ""
    url: str = ""
    download_url: str = ""


@dataclass
class _FakeListItem:
    pathname: str
    url: str = ""
    download_url: str = ""
    size: int = 0


@dataclass
class _FakeListResult:
    blobs: list = field(default_factory=list)


class _FakeBlobClient:
    """Minimal in-memory stand-in for vercel.blob.BlobClient (no network)."""

    def __init__(self):
        self.objects: dict[str, dict] = {}
        self.put_kwargs: list[dict] = []

    def put(self, path, body, *, access="public", content_type=None, **kwargs):
        self.put_kwargs.append({"path": path, "access": access, "content_type": content_type, **kwargs})
        raw = bytes(body)
        url = f"https://blob.test/{path}"
        self.objects[path] = {"data": raw, "content_type": content_type or "application/octet-stream", "url": url}
        return _FakePutResult(url=url, download_url=url, pathname=path, content_type=content_type or "")

    def get(self, url_or_path, *, access="private", **kwargs):
        _ = (access, kwargs)
        key = str(url_or_path).replace("https://blob.test/", "", 1)
        if key not in self.objects:
            raise ValueError(f"not found: {key}")
        obj = self.objects[key]
        return _FakeGetResult(content=obj["data"], content_type=obj["content_type"], pathname=key, url=obj["url"], download_url=obj["url"])

    def head(self, url_or_path, **kwargs):
        _ = kwargs
        key = str(url_or_path).replace("https://blob.test/", "", 1)
        if key not in self.objects:
            raise ValueError(f"not found: {key}")
        obj = self.objects[key]
        return _FakeHeadResult(size=len(obj["data"]), pathname=key, content_type=obj["content_type"], url=obj["url"], download_url=obj["url"])

    def delete(self, url_or_path, **kwargs):
        _ = kwargs
        keys = [url_or_path] if isinstance(url_or_path, str) else list(url_or_path)
        for key in keys:
            self.objects.pop(str(key).replace("https://blob.test/", "", 1), None)

    def list_objects(self, *, prefix=None, limit=None, **kwargs):
        _ = (limit, kwargs)
        items = [_FakeListItem(pathname=k, url=v["url"], download_url=v["url"], size=len(v["data"])) for k, v in self.objects.items() if not prefix or k.startswith(prefix)]
        return _FakeListResult(blobs=items)


def test_gap_g3_sdk_put_uses_private_kwargs_and_dataclass(tmp_path, monkeypatch):
    """SDK put must use kwargs (not dict) with private access; dataclass handled."""
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "tok-fake")
    monkeypatch.delenv("SARD_BLOB_ENDPOINT", raising=False)
    fake = _FakeBlobClient()
    store = VercelBlobArtifactStore(fallback_local=FileSystemArtifactStore(root_dir=tmp_path), client=fake)
    assert store.blob_configured is True
    aid, fname, size, checksum = store.store_bytes("art-priv001", "report.pdf", b"%PDF-1.4 private", "application/pdf")
    assert aid == "art-priv001" and size > 0 and checksum
    assert fake.put_kwargs and fake.put_kwargs[0]["access"] == "private"
    assert fake.put_kwargs[0]["content_type"] == "application/pdf"
    assert fake.put_kwargs[0]["path"].startswith("runs/default/art-priv001/v1/")
    # SDK urls retained server-side, never in the client download URL.
    assert store._blob_urls, "SDK url/download_url must be retained server-side"
    assert store.get_download_url(aid, fname).startswith("/api/artifacts/")
    # Local mirror kept for same-process verification.
    assert store.get_file_path(fname) is not None
    meta = store.get_metadata(aid)
    assert meta and meta.get("artifact_id") == "art-priv001" and meta.get("key", "").startswith("runs/")


def test_gap_g3_cross_process_fs_fresh_instance(tmp_path):
    """Fresh FS instance on the same root resolves both id and filename (sidecar)."""
    root = tmp_path / "runs"
    store_a = FileSystemArtifactStore(root_dir=root)
    aid, fname, _, _ = store_a.store_bytes("art-crossfs1", "doc.pdf", b"%PDF-1.4 cross", "application/pdf")
    store_b = FileSystemArtifactStore(root_dir=root)  # fresh instance, no shared memory
    for lookup in (aid, fname):
        got = store_b.get_bytes(lookup)
        assert got is not None, f"fresh instance must resolve {lookup!r}"
        data, resolved_name, mime = got
        assert data.startswith(b"%PDF") and resolved_name == fname and mime == "application/pdf"
    assert store_b.exists(aid) and store_b.get_file_path(fname) is not None


def test_gap_g3_cross_process_blob_fresh_instance_and_http(tmp_path, monkeypatch):
    """Second instance with an empty local disk reads via blob; HTTP streams bytes."""
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "tok-fake")
    monkeypatch.delenv("SARD_BLOB_ENDPOINT", raising=False)
    fake = _FakeBlobClient()
    mirror_a = tmp_path / "instA"
    mirror_b = tmp_path / "instB"  # separate /tmp simulating a second Vercel instance
    store_a = VercelBlobArtifactStore(fallback_local=FileSystemArtifactStore(root_dir=mirror_a), client=fake)
    aid, fname, _, _ = store_a.store_bytes("art-crossblob1", "memo.pdf", b"%PDF-1.4 blob-cross", "application/pdf")

    store_b = VercelBlobArtifactStore(fallback_local=FileSystemArtifactStore(root_dir=mirror_b), client=fake)
    # No local mirror on instance B: forces the blob-configured streaming path.
    assert store_b.get_file_path(fname) is None
    for lookup in (aid, fname):
        got = store_b.get_bytes(lookup)
        assert got is not None, f"fresh blob instance must resolve {lookup!r} without original disk"
        data, resolved_name, mime = got
        assert data == b"%PDF-1.4 blob-cross"
        assert resolved_name == fname
        assert mime == "application/pdf"

    previous = get_artifact_store()
    set_artifact_store(store_b)
    try:
        client = TestClient(__import__("sard.api.server", fromlist=["app"]).app)
        resp = client.get(f"/api/artifacts/{fname}")
        assert resp.status_code == 200
        assert "application/pdf" in resp.headers.get("content-type", "")
        assert fname in resp.headers.get("content-disposition", "")
        assert resp.content == b"%PDF-1.4 blob-cross"
    finally:
        set_artifact_store(previous)
