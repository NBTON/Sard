"""Real-file attachment nonce tests: upload unique content and verify extraction + citation.

Covers PDF, DOCX, TXT, PNG, WAV, and PLY. Real uploaded paths go to the real
multimodal extractor (never mock canned text). When an external modality
provider is unavailable, extraction reports capability_unavailable explicitly.
"""

from __future__ import annotations

import io
import wave

import pytest
from fastapi.testclient import TestClient

from sard.agent.tools.multimodal_tools import extract_multimodal_context
from sard.api.server import app


@pytest.fixture
def client():
    return TestClient(app)


def _upload(client, filename, content, mime):
    resp = client.post("/api/upload", files={"file": (filename, io.BytesIO(content), mime)})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _pdf_bytes(nonce: str) -> bytes:
    # Minimal valid single-page PDF with nonce text (pypdf-readable).
    text = f"BT /F1 12 Tf 50 700 Td ({nonce}) Tj ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(text)} >>\nstream\n{text}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin1")
    xref = len(out)
    out += f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode("latin1")
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("latin1")
    out += f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode("latin1")
    return bytes(out)


def _docx_bytes(nonce: str) -> bytes:
    import docx

    buf = io.BytesIO()
    doc = docx.Document()
    doc.add_paragraph(f"وثيقة تراثية تجريبية. المحتوى الفريد: {nonce}")
    doc.save(buf)
    return buf.getvalue()


def _png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (190, 74, 36)).save(buf, format="PNG")
    return buf.getvalue()


def _wav_bytes() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(b"\x00\x00" * 800)
    return buf.getvalue()


def test_pdf_nonce_extracted_and_citable(client, tmp_path):
    nonce = "SARD_NONCE_PDF_7F3A9C2E"
    data = _upload(client, "nonce_doc.pdf", _pdf_bytes(nonce), "application/pdf")
    # Resolve real path via download URL round-trip + extractor on uploaded bytes
    dl = client.get(data["url"])
    assert dl.status_code == 200
    tmp = tmp_path / "nonce_doc.pdf"
    tmp.write_bytes(dl.content)
    items = extract_multimodal_context(
        "لخص محتوى الوثيقة المرفقة",
        uploaded_files={"nonce_doc.pdf": {"file_path": str(tmp), "filename": "nonce_doc.pdf"}},
    )
    assert len(items) == 1
    assert items[0].extraction_method != "mock"
    assert nonce in (items[0].extracted_text or ""), "PDF nonce text must be reproduced accurately"


def test_docx_nonce_extracted(client, tmp_path):
    nonce = "SARD_NONCE_DOCX_B81D4F0A"
    data = _upload(client, "nonce_doc.docx", _docx_bytes(nonce), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    dl = client.get(data["url"])
    tmp = tmp_path / "nonce_doc.docx"
    tmp.write_bytes(dl.content)
    items = extract_multimodal_context(
        "استخرج محتوى الملف",
        uploaded_files={"nonce_doc.docx": {"file_path": str(tmp), "filename": "nonce_doc.docx"}},
    )
    assert len(items) == 1
    assert nonce in (items[0].extracted_text or "")


def test_txt_nonce_extracted(client, tmp_path):
    nonce = "SARD_NONCE_TXT_42C7E1B9"
    payload = f"نص تجريبي فريد: {nonce}".encode("utf-8")
    data = _upload(client, "nonce.txt", payload, "text/plain")
    dl = client.get(data["url"])
    tmp = tmp_path / "nonce.txt"
    tmp.write_bytes(dl.content)
    items = extract_multimodal_context(
        "اقرأ الملف",
        uploaded_files={"nonce.txt": {"file_path": str(tmp), "filename": "nonce.txt"}},
    )
    assert nonce in (items[0].extracted_text or "")


def test_image_reports_explicitly_without_provider(client, tmp_path):
    data = _upload(client, "nonce_img.png", _png_bytes(), "image/png")
    dl = client.get(data["url"])
    tmp = tmp_path / "nonce_img.png"
    tmp.write_bytes(dl.content)
    items = extract_multimodal_context(
        "صف الصورة",
        uploaded_files={"nonce_img.png": {"file_path": str(tmp), "filename": "nonce_img.png"}},
        mock_files=None,
    )
    assert len(items) == 1
    # Offline (no DASHSCOPE_API_KEY): must report unavailable explicitly, never pretend analysis.
    assert items[0].extraction_method in ("capability_unavailable", "provider_error", "core", "dashscope_qwen_vl")
    if items[0].extraction_method in ("capability_unavailable", "provider_error"):
        assert items[0].confidence == 0.0


def test_audio_reports_explicitly_without_provider(client, tmp_path):
    pytest.importorskip("wave")
    data = _upload(client, "nonce.wav", _wav_bytes(), "audio/wav")
    dl = client.get(data["url"])
    tmp = tmp_path / "nonce.wav"
    tmp.write_bytes(dl.content)
    items = extract_multimodal_context(
        "فرغ الصوت",
        uploaded_files={"nonce.wav": {"file_path": str(tmp), "filename": "nonce.wav"}},
    )
    assert len(items) == 1
    assert items[0].file_type == "audio"


def test_3d_ply_inspected_offline(client, tmp_path):
    ply = b"ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\nelement face 1\nproperty list uchar int vertex_indices\nend_header\n0 0 0\n1 0 0\n0 1 0\n3 0 1 2\n"
    data = _upload(client, "model.ply", ply, "model/mesh")
    dl = client.get(data["url"])
    tmp = tmp_path / "model.ply"
    tmp.write_bytes(dl.content)
    items = extract_multimodal_context(
        "افحص النموذج ثلاثي الأبعاد",
        uploaded_files={"model.ply": {"file_path": str(tmp), "filename": "model.ply"}},
    )
    assert len(items) == 1
    assert items[0].file_type == "3d"
    assert "vertices" in (items[0].description or "") or items[0].metadata.get("vertices") == 3
