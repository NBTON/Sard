"""Coordinator revision contract: POST /{id}/revisions + GET /{id}/versions.

Covers: same-ID version+1 allocation, immutable version persistence,
prior-version retrieval (bytes + preview + download), failure atomicity
(prior intact), idempotent retries, and version-specific preview/download
semantics. Narrowly scoped to the artifact backend (orchestrator + API).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sard.api.server import app
from sard.outputs.orchestrator import (
    ArtifactOrchestrator,
    ArtifactRequest,
    FileSystemArtifactStore,
    set_artifact_store,
)
from sard.outputs.validation import validate_artifact_bytes


@pytest.fixture
def client(tmp_path):
    store = FileSystemArtifactStore(root_dir=tmp_path)
    set_artifact_store(store)
    return TestClient(app), store


def _request(**kwargs) -> ArtifactRequest:
    params = {
        "format": "txt",
        "kind": "document",
        "title": "تقرير التنقيح",
        "topic": "العمارة النجدية",
        "raw_text": "الطين مادة البناء الأولى.\n\nالمصمك شاهد معماري موثق.",
    }
    params.update(kwargs)
    return ArtifactRequest(**params)


def _create(store: FileSystemArtifactStore):
    result = ArtifactOrchestrator(store).generate_artifact(_request())
    assert result.status == "created", result.error
    return result


def test_revision_allocates_v2_same_id_and_preserves_v1(client):
    test_client, store = client
    created = _create(store)
    v1_bytes = bytes(created.data or b"")

    resp = test_client.post(
        f"/api/artifacts/{created.id}/revisions",
        json={"instruction": "المصمك -> قصر المصمك"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "created"
    assert body["artifact_id"] == created.id
    assert body["id"] == created.id
    assert body["version"] == 2

    versions = test_client.get(f"/api/artifacts/{created.id}/versions").json()
    assert versions["artifact_id"] == created.id
    assert versions["count"] == 2
    assert [v["version"] for v in versions["versions"]] == [1, 2]
    for view in versions["versions"]:
        assert view["status"] == "created"
        assert view["preview"] is not None and isinstance(view["preview"].get("items"), list)
        assert view["download_url"] == f"/api/artifacts/version/{created.id}/{view['version']}"

    dl1 = test_client.get(f"/api/artifacts/version/{created.id}/1")
    dl2 = test_client.get(f"/api/artifacts/version/{created.id}/2")
    assert dl1.status_code == 200 and bytes(dl1.content) == v1_bytes
    assert dl2.status_code == 200
    assert "قصر المصمك" in dl2.content.decode("utf-8")
    assert "الطين مادة البناء الأولى" in dl2.content.decode("utf-8")


def test_failed_revision_leaves_prior_version_intact(client):
    test_client, store = client
    created = _create(store)
    v1_bytes = bytes(created.data or b"")

    bad_format = test_client.post(
        f"/api/artifacts/{created.id}/revisions",
        json={"instruction": "append: ملاحظة", "format": "exe"},
    )
    assert bad_format.status_code == 422

    missing_target = test_client.post(
        f"/api/artifacts/{created.id}/revisions",
        json={"instruction": "نص غير موجود أبدا -> بديل"},
    )
    assert missing_target.status_code == 422

    versions = test_client.get(f"/api/artifacts/{created.id}/versions").json()
    assert versions["count"] == 1
    dl1 = test_client.get(f"/api/artifacts/version/{created.id}/1")
    assert dl1.status_code == 200 and bytes(dl1.content) == v1_bytes


def test_revision_idempotency_key_reuses_version(client):
    test_client, store = client
    created = _create(store)

    first = test_client.post(
        f"/api/artifacts/{created.id}/revisions",
        json={"instruction": "append: سطر تنقيحي", "idempotency_key": "rev-key-1"},
    )
    second = test_client.post(
        f"/api/artifacts/{created.id}/revisions",
        json={"instruction": "append: سطر تنقيحي", "idempotency_key": "rev-key-1"},
    )
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["version"] == second.json()["version"] == 2
    versions = test_client.get(f"/api/artifacts/{created.id}/versions").json()
    assert versions["count"] == 2


def test_revision_with_format_conversion_keeps_v1_bytes(client):
    test_client, store = client
    created = _create(store)
    v1_bytes = bytes(created.data or b"")

    resp = test_client.post(
        f"/api/artifacts/{created.id}/revisions",
        json={"instruction": "append: فقرة إضافية", "format": "html"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["format"] == "html"
    assert body["version"] == 2
    validate_artifact_bytes("html", bytes(test_client.get(f"/api/artifacts/version/{created.id}/2").content))

    dl1 = test_client.get(f"/api/artifacts/version/{created.id}/1")
    assert bytes(dl1.content) == v1_bytes
    assert "text/plain" in dl1.headers["content-type"]


def test_unknown_artifact_revision_is_422_and_versions_404(client):
    test_client, _ = client
    resp = test_client.post(
        "/api/artifacts/art-unknown999/revisions",
        json={"instruction": "append: x"},
    )
    assert resp.status_code == 422
    assert test_client.get("/api/artifacts/art-unknown999/versions").status_code == 404


def test_blob_stores_delegate_version_document_surface(tmp_path):
    """Production wiring uses blob stores; they must not drop revision support.

    Regression: VercelBlob/ConfigurableBlob stores lacked put/get_document and
    list/get_version_bytes, so chat-created artifacts (default store) failed
    revision with unknown_artifact while injected FileSystem stores passed.
    """
    from sard.outputs.orchestrator import (
        ArtifactOrchestrator,
        ConfigurableBlobArtifactStore,
        VercelBlobArtifactStore,
    )

    for store_cls in (VercelBlobArtifactStore, ConfigurableBlobArtifactStore):
        store = store_cls(fallback_local=FileSystemArtifactStore(root_dir=tmp_path / store_cls.__name__))
        assert not getattr(store, "blob_configured", True)
        orch = ArtifactOrchestrator(store)
        created = orch.generate_artifact(_request())
        assert created.status == "created", created.error
        assert store.get_document(created.id) is not None
        assert len(store.list_versions(created.id)) >= 1
        revised = orch.revise_artifact(created.id, updated_text="Revised body.")
        assert revised.status == "created", revised.error
        assert len(store.list_versions(created.id)) >= 2
        v1 = store.get_version_bytes(created.id, 1)
        assert v1 is not None and bytes(created.data or b"") == v1[0]
