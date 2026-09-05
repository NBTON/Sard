"""G11: expired deadline must discard store (no orphan files)."""
import time
from pathlib import Path
from sard.outputs.orchestrator import ArtifactOrchestrator, ArtifactRequest, FileSystemArtifactStore

def test_gap_g11_no_orphan_after_deadline(tmp_path):
    store = FileSystemArtifactStore(root_dir=tmp_path)
    orch = ArtifactOrchestrator(store)
    before = set(p.name for p in tmp_path.iterdir() if p.is_file())
    past = time.monotonic() - 1.0
    req = ArtifactRequest(format="pdf", kind="document", title="t", topic="Najd", raw_text="hello world test content")
    res = orch.generate_artifact(req, deadline_monotonic=past)
    assert res.status == "failed"
    assert res.error_category == "timeout"
    assert res.download_url is None
    after = set(p.name for p in tmp_path.iterdir() if p.is_file())
    # Only metadata sidecars allowed? Actually no new artifact files.
    new_files = after - before
    # Filter out .artifact-metadata dir (separate); tmp_path root should have no new files
    assert len(new_files) == 0, f"orphan files written after deadline: {new_files}"
