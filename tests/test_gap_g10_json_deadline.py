"""G10: pure JSON/CSV/TXT must fast-path without slow planner (<=10s)."""
import time
from sard.agent.chat_service import ChatService
from sard.outputs.orchestrator import FileSystemArtifactStore, set_artifact_store

def test_gap_g10_json_fastpath(tmp_path):
    store = FileSystemArtifactStore(root_dir=tmp_path)
    set_artifact_store(store)
    svc = ChatService(chat_model=None, orchestrator=None)
    # Inject a dummy model to avoid live keys? Use fastpath which skips planner entirely.
    # Fastpath triggers regardless of model; ensure it returns quickly.
    t0 = time.monotonic()
    res = svc.ask("Create JSON output about Najdi architecture", use_hybrid_retrieval=True, lang="en")
    dt = time.monotonic() - t0
    assert dt < 10.0, f"JSON fastpath took {dt:.1f}s"
    assert res.ok is True
    assert any(a.get("format")=="json" for a in (res.artifacts or []))
    art = [a for a in res.artifacts if a.get("format")=="json"][0]
    assert art.get("status")=="created"
    assert art.get("size_bytes",0) > 0
    # Validate parseable JSON bytes via store
    data, fn, mime = store.get_bytes(art["filename"])
    import json
    parsed = json.loads(data.decode("utf-8"))
    assert isinstance(parsed, (dict, list))
