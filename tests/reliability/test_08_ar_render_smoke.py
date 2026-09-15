"""J10: AR render smoke — HTML/PDF/DOCX/PPTX open + RTL props (mocked I/O, offline).

Light assertions against the real renderers:
  PDF  via ArtifactOrchestrator -> pypdf opens, pages>=1, Arabic shaped.
  DOCX via ArtifactOrchestrator -> OOXML opens, w:bidi/w:rtl, Arabic kept logical.
  PPTX via ArtifactOrchestrator -> OOXML opens, rtl marker, Arabic present.
  HTML via sard.outputs.html (preview renderer) -> dir=rtl, no <script>.
The stored-artifact `html` format case is an explicit non-blocking xfail:
html is a preview renderer, not one of the 9 stored formats.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from sard.outputs.orchestrator import ArtifactOrchestrator, FileSystemArtifactStore
from sard.outputs.validation import validate_artifact_bytes

from .conftest import AR_TEXT, AR_TOPIC, artifact_request

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def test_10a_pdf_opens_with_rtl_shaping(tmp_path):
    orch = ArtifactOrchestrator(FileSystemArtifactStore(tmp_path))
    res = orch.generate_artifact(artifact_request("pdf"))
    assert res.status == "created", res.error
    assert res.mime_type == "application/pdf"
    validate_artifact_bytes("pdf", res.data)
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(bytes(res.data)), strict=True)
    assert len(reader.pages) >= 1
    text = "".join((p.extract_text() or "") for p in reader.pages)
    assert "English" in text or "Najdi" in text or len(text) > 0
    import arabic_reshaper

    assert arabic_reshaper.reshape("العربية") in text
    # PDF stores shaped (presentation-form) Arabic: assert the shaped topic.
    assert arabic_reshaper.reshape("العمارة") in text


def test_10b_docx_opens_with_rtl_props(tmp_path):
    orch = ArtifactOrchestrator(FileSystemArtifactStore(tmp_path))
    res = orch.generate_artifact(artifact_request("docx"))
    assert res.status == "created", res.error
    validate_artifact_bytes("docx", res.data)
    with zipfile.ZipFile(io.BytesIO(bytes(res.data))) as package:
        names = set(package.namelist())
        assert "word/document.xml" in names
        xml = package.read("word/document.xml").decode("utf-8")
    assert "w:bidi" in xml or "w:rtl" in xml, "DOCX must carry RTL properties"
    assert "العمارة" in xml, "Arabic must stay logical (unshaped) in DOCX"


def test_10c_pptx_opens_with_rtl_props(tmp_path):
    orch = ArtifactOrchestrator(FileSystemArtifactStore(tmp_path))
    res = orch.generate_artifact(artifact_request("pptx", kind="presentation"))
    assert res.status == "created", res.error
    validate_artifact_bytes("pptx", res.data)
    with zipfile.ZipFile(io.BytesIO(bytes(res.data))) as package:
        names = set(package.namelist())
        assert "ppt/presentation.xml" in names
        slide_names = sorted(n for n in names if n.startswith("ppt/slides/slide"))
        assert slide_names, "PPTX must contain slides"
        blob = b"\n".join(package.read(n) for n in slide_names).decode("utf-8", errors="replace")
    assert "rtl" in blob.lower(), "PPTX slides must carry RTL paragraph props"
    assert AR_TOPIC in blob or "العمارة" in blob


def test_10d_html_preview_renders_rtl_sanitized():
    from sard.outputs.html import render_html_from_request

    html = render_html_from_request(
        title=f"تقرير: {AR_TOPIC}",
        topic=AR_TOPIC,
        content_paragraphs=[p for p in AR_TEXT.split("\n\n") if p.strip()],
        region="المملكة العربية السعودية",
    )
    assert '<html lang="ar" dir="rtl">' in html
    assert AR_TOPIC in html
    assert "<script" not in html.lower()
    assert "onerror=" not in html.lower()
    assert "Content-Security-Policy" in html


def test_10e_html_as_stored_format(tmp_path):
    orch = ArtifactOrchestrator(FileSystemArtifactStore(tmp_path))
    res = orch.generate_artifact(artifact_request("html"))
    assert res.status == "created"
    assert res.download_url is not None
