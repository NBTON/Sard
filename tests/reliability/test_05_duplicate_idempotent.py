"""J7: duplicate / retry artifact idempotent (boundaries F + app-service).

Same run_id+artifact_id -> existing object or explicit new version, never crash.
"""
from __future__ import annotations

import pytest

from sard.application.contracts import (
    UIExecutionMode,
    UIModeKind,
    UIModeStatus,
    UIRunRequest,
    UIRunResult,
)
from sard.application.service import ApplicationServiceError, SardApplicationService
from sard.outputs.orchestrator import FileSystemArtifactStore


def test_07_store_duplicate_refuses_explicitly_and_preserves_original(tmp_path):
    store = FileSystemArtifactStore(tmp_path)
    store.store_bytes("art-dup-001", "report.pdf", b"%PDF-1.7 original-bytes", "application/pdf")
    with pytest.raises(ValueError, match="overwrite"):
        store.store_bytes("art-dup-001", "report.pdf", b"%PDF-1.7 second-bytes", "application/pdf")
    data, filename, mime = store.get_bytes("art-dup-001")
    assert data == b"%PDF-1.7 original-bytes"
    assert "art-dup-001" in filename
    assert mime == "application/pdf"
    # Explicit new version with a fresh id succeeds (retry as new version).
    store.store_bytes("art-dup-002", "report.pdf", b"%PDF-1.7 second-bytes", "application/pdf")
    assert store.get_bytes("art-dup-002")[0] == b"%PDF-1.7 second-bytes"
    assert store.get_bytes("art-dup-001")[0] == b"%PDF-1.7 original-bytes"


def _demo_result(run_id: str) -> UIRunResult:
    return UIRunResult(
        run_id=run_id,
        final_answer="إجابة تجريبية موثقة",
        graph_outcome="completed",
        mode=UIModeStatus(
            kind=UIModeKind.CACHED_DEMO,
            retrieval_mode="hybrid_fused",
            model_fallback_used=False,
            execution_mode=UIExecutionMode.CACHED_DEMO,
        ),
        sources=(),
        itinerary=None,
        artifacts=(),
        progress_events=(),
    )


def test_07b_service_retry_same_run_id_returns_existing_object():
    calls: list[str] = []

    def _provider(request: UIRunRequest) -> UIRunResult:
        calls.append(request.run_id)
        return _demo_result(request.run_id)

    svc = SardApplicationService(dependencies=None, cached_demo_provider=_provider)
    req = UIRunRequest(query="سؤال ثقافي", run_id="run-dup-001", execution_mode=UIExecutionMode.CACHED_DEMO)
    first = svc.run(req)
    second = svc.run(req)
    assert first.run_id == "run-dup-001"
    assert second.run_id == "run-dup-001"
    assert second.final_answer == first.final_answer
    # Idempotent: provider ran once, retry served the retained snapshot.
    assert calls == ["run-dup-001"]


def test_07c_service_concurrent_duplicate_is_explicit_error_not_crash():
    svc = SardApplicationService(dependencies=None, cached_demo_provider=None)
    svc._started_run_ids.add("run-active-001")
    svc._active_run_ids.add("run-active-001")
    req = UIRunRequest(query="سؤال", run_id="run-active-001", execution_mode=UIExecutionMode.CACHED_DEMO)
    with pytest.raises(ApplicationServiceError) as exc:
        list(svc.stream_run(req))
    assert exc.value.category == "duplicate_run"


def test_07d_orchestrator_maps_store_duplicate_to_explicit_category(tmp_path, monkeypatch):
    """Prod duplicate_artifact branch: store overwrite refusal -> failed, never crash."""
    from sard.outputs.orchestrator import ArtifactOrchestrator, FileSystemArtifactStore

    from .conftest import artifact_request

    orch = ArtifactOrchestrator(FileSystemArtifactStore(tmp_path))

    def _boom(**kwargs):
        raise ValueError("Refusing to overwrite existing artifact.")

    monkeypatch.setattr(orch.store, "store_bytes", _boom)
    res = orch.generate_artifact(artifact_request("txt", raw_text="retry after duplicate"))
    assert res.status == "failed"
    assert res.error_category == "duplicate_artifact"
    assert res.download_url is None

def test_07e_dg2_identical_retry_reuses_stored_artifact(tmp_path):
    """DG-2: same run+topic+bytes twice -> created twice, one identity."""
    from sard.outputs.orchestrator import ArtifactOrchestrator, FileSystemArtifactStore

    from .conftest import artifact_request

    orch = ArtifactOrchestrator(FileSystemArtifactStore(tmp_path))
    req = artifact_request("txt", raw_text="retry reuse text",
                           metadata={"run_id": "run-reuse-1", "version": 1})
    first = orch.generate_artifact(req)
    second = orch.generate_artifact(req)
    assert first.status == "created"
    assert second.status == "created"
    assert first.id == second.id
    assert first.checksum == second.checksum
    assert first.filename == second.filename
    assert first.download_url == second.download_url
    assert second.download_url is not None


def test_07f_dg2_changed_content_same_version_stays_conflict(tmp_path):
    """DG-2: different bytes under the same id+version still conflict."""
    from sard.outputs.orchestrator import ArtifactOrchestrator, FileSystemArtifactStore

    from .conftest import artifact_request

    orch = ArtifactOrchestrator(FileSystemArtifactStore(tmp_path))
    first = orch.generate_artifact(artifact_request(
        "txt", raw_text="original bytes",
        metadata={"run_id": "run-conflict-1", "version": 1}))
    assert first.status == "created"
    conflict = orch.generate_artifact(artifact_request(
        "txt", raw_text="CHANGED bytes",
        metadata={"run_id": "run-conflict-1", "version": 1}))
    assert conflict.status == "failed"
    assert conflict.error_category == "duplicate_artifact"
    assert conflict.download_url is None


def test_07g_dg2_concurrent_identical_retries_share_one_artifact(tmp_path):
    """DG-2: concurrent identical retries -> all created, one id, intact bytes."""
    import threading

    from sard.outputs.orchestrator import ArtifactOrchestrator, FileSystemArtifactStore

    from .conftest import artifact_request

    store = FileSystemArtifactStore(tmp_path)
    orch = ArtifactOrchestrator(store)
    results = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait(timeout=10)
        results.append(orch.generate_artifact(artifact_request(
            "txt", raw_text="concurrent text",
            metadata={"run_id": "run-conc-1", "version": 1})))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert len(results) == 8
    assert all(r.status == "created" for r in results)
    assert len({r.id for r in results}) == 1
    assert len({r.checksum for r in results}) == 1
    stored, _, _ = store.get_bytes(results[0].filename)
    assert stored == b"concurrent text"


def test_07h_dg2_idempotency_key_retry_reuses(tmp_path):
    """DG-2: chat-plumbed idempotency key + same bytes reuses the record."""
    from sard.outputs.orchestrator import ArtifactOrchestrator, FileSystemArtifactStore

    from .conftest import artifact_request

    orch = ArtifactOrchestrator(FileSystemArtifactStore(tmp_path))
    meta = {"run_id": "chat-abc", "version": 1, "idempotency_key": "chat-abc:txt"}
    first = orch.generate_artifact(artifact_request("txt", raw_text="keyed text", metadata=dict(meta)))
    second = orch.generate_artifact(artifact_request("txt", raw_text="keyed text", metadata=dict(meta)))
    assert first.status == "created"
    assert second.status == "created"
    assert first.id == second.id
    assert first.checksum == second.checksum

class _StubBlobStore:
    """Isolated non-filesystem store double (duplicate-on-rewrite semantics)."""

    def __init__(self):
        self.records = {}
        self.blobs = {}

    def store_bytes(self, artifact_id, filename, data, mime_type, metadata=None):
        import hashlib

        meta = dict(metadata or {})
        if artifact_id in self.records:
            raise ValueError("Refusing to overwrite existing artifact.")
        self.records[artifact_id] = {
            "filename": "stored-%s" % filename,
            "sha256": hashlib.sha256(bytes(data)).hexdigest(),
            "version": int(meta.get("version", 1)),
        }
        self.blobs["stored-%s" % filename] = (bytes(data), filename, mime_type)
        rec = self.records[artifact_id]
        return artifact_id, rec["filename"], len(data), rec["sha256"]

    def get_metadata(self, artifact_id):
        return self.records.get(artifact_id)

    def get_bytes(self, filename):
        return self.blobs.get(filename)

    def get_download_url(self, artifact_id, filename):
        return "/api/artifacts/%s" % filename


def test_07i_dg2_stub_backend_reuses_identical_retry(tmp_path):
    """DG-2: idempotent reuse works through the generic store interface."""
    from sard.outputs.orchestrator import ArtifactOrchestrator

    from .conftest import artifact_request

    orch = ArtifactOrchestrator(_StubBlobStore())
    req = artifact_request("txt", raw_text="backend-agnostic text",
                           metadata={"run_id": "run-stub-1", "version": 1})
    first = orch.generate_artifact(req)
    second = orch.generate_artifact(req)
    assert first.status == "created"
    assert second.status == "created"
    assert first.id == second.id
    assert first.checksum == second.checksum
    assert first.filename == second.filename
    assert first.download_url == second.download_url
