"""G4: attachments resolvable in a second process via durable index + glob."""
from fastapi.testclient import TestClient
from sard.api import server as srv
from sard.api.server import app

def test_gap_g4_attachment_second_process(tmp_path, monkeypatch):
    # Isolate upload dir
    monkeypatch.setattr(srv, "UPLOAD_DIR", tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    srv._ATTACHMENTS.clear()
    c = TestClient(app)
    # Upload canary PDF bytes
    nonce = "SARD_CANARY_NONCE_G4_987654321"
    pdf_bytes = b"%PDF-1.4\n" + nonce.encode() + b"\n%%EOF"
    r = c.post("/api/upload", files={"file": ("canary.pdf", pdf_bytes, "application/pdf")})
    assert r.status_code == 200
    att_id = r.json()["attachment_id"]
    # Simulate second process: wipe in-memory map
    srv._ATTACHMENTS.clear()
    # Resolver must still find via index file / glob
    meta = srv._resolve_attachment_meta(att_id)
    assert meta is not None
    from pathlib import Path
    assert Path(meta["path"]).exists()
    assert Path(meta["path"]).read_bytes() == pdf_bytes
    # Download endpoint still works
    r2 = c.get(f"/api/attachments/{att_id}")
    assert r2.status_code == 200
    assert r2.content == pdf_bytes
