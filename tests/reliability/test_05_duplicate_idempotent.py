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
