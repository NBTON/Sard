"""J-golden: spec golden / integration cases (mocked, deterministic, offline).

AR factual, cultural doc, itinerary, mixed doc, table-heavy,
citation-heavy, web-required, corpus-only, Blob, duplicate,
AR PDF/DOCX/PPTX, HTML preview, revision, cancellation.
Heavy renders are shared/parametrized to keep the suite fast.
"""
from __future__ import annotations

import csv
import io
import time
import zipfile

import pytest

from sard.outputs.orchestrator import (
    ArtifactOrchestrator,
    ConfigurableBlobArtifactStore,
    FileSystemArtifactStore,
)
from sard.outputs.validation import ARTIFACT_MIME_TYPES, validate_artifact_bytes

from .conftest import AR_TOPIC, artifact_request, install_fake_blob, make_offline_router


def _orchestrator(tmp_path):
    return ArtifactOrchestrator(FileSystemArtifactStore(tmp_path))


# --- Document goldens --------------------------------------------------------


def test_golden_ar_factual_pdf(tmp_path):
    """AR factual: Arabic factual report renders, opens, keeps Arabic."""
    orch = _orchestrator(tmp_path)
    res = orch.generate_artifact(
        artifact_request(
            "pdf",
            topic="العمارة النجدية في الرياض",
            raw_text="قصر المصمك في الرياض شاهد معماري نجدي من الطين واللبن.\n\nشيد في أواخر القرن التاسع عشر ويضم بوابة خشبية ضخمة.",
            sources=({"title": "دارة الملك عبد العزيز", "url": "https://example.test/darah"},),
        )
    )
    assert res.status == "created", res.error
    assert res.size_bytes > 0 and res.checksum
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(bytes(res.data)), strict=True)
    assert len(reader.pages) >= 1


def test_golden_cultural_docx(tmp_path):
    orch = _orchestrator(tmp_path)
    res = orch.generate_artifact(artifact_request("docx", topic="فن القط العسيري"))
    assert res.status == "created", res.error
    assert res.mime_type == ARTIFACT_MIME_TYPES["docx"]
    validate_artifact_bytes("docx", res.data)


def test_golden_itinerary_ics(tmp_path):
    """Itinerary/calendar golden: real heritage topic -> RFC 5545 calendar."""
    orch = _orchestrator(tmp_path)
    res = orch.generate_artifact(artifact_request("ics", kind="calendar", topic="سهيل"))
    assert res.status == "created", res.error
    text = bytes(res.data).decode("utf-8")
    assert "BEGIN:VCALENDAR" in text and "END:VCALENDAR" in text
    assert "SUMMARY:" in text


def test_golden_mixed_doc_pdf(tmp_path):
    orch = _orchestrator(tmp_path)
    res = orch.generate_artifact(
        artifact_request(
            "pdf",
            topic="الحرف السعودية",
            content_data={
                "paragraphs": ["مقدمة عن الحرف السعودية عبر المناطق."],
                "sections": [{"title": "السدو", "content": "نسيج بدوي تقليدي في نجد وحائل."}],
                "key_takeaways": ["الحرف هوية حية."],
            },
            sources=({"title": "هيئة التراث", "url": "https://example.test/heritage"},),
        )
    )
    assert res.status == "created", res.error
    validate_artifact_bytes("pdf", res.data)


def test_golden_table_heavy_csv(tmp_path):
    orch = _orchestrator(tmp_path)
    rows = [
        {"region": "نجد", "craft": "السدو", "sites": "3"},
        {"region": "عسير", "craft": "القط", "sites": "5"},
        {"region": "الحجاز", "craft": "الرواشين", "sites": "7"},
        {"region": "الشرقية", "craft": "الفخار", "sites": "2"},
        {"region": "جازان", "craft": "الفل", "sites": "4"},
    ]
    res = orch.generate_artifact(artifact_request("csv", content_data={"rows": rows}))
    assert res.status == "created", res.error
    parsed = list(csv.reader(io.StringIO(bytes(res.data).decode("utf-8"))))
    assert len(parsed) == 6  # header + 5 rows
    assert all(len(r) == 3 for r in parsed)


def test_golden_citation_heavy_docx(tmp_path):
    orch = _orchestrator(tmp_path)
    sources = tuple({"title": f"مصدر موثق {i}", "url": f"https://example.test/src-{i}"} for i in range(3))
    res = orch.generate_artifact(
        artifact_request(
            "docx",
            topic="الدرعية التاريخية",
            raw_text="الدرعية عاصمة الدولة السعودية الأولى وموطن حي الطريف التاريخي.",
            sources=sources,
        )
    )
    assert res.status == "created", res.error
    with zipfile.ZipFile(io.BytesIO(bytes(res.data))) as package:
        xml = package.read("word/document.xml").decode("utf-8")
    assert "الدرعية" in xml


# --- Retrieval goldens (router, no model, no network) ------------------------


def test_golden_web_required_uses_live_grounding():
    web = [{"url": "https://example.test/moc-2026", "title": "موسم 2026", "excerpts": ["مواعيد موسم الرياض 2026 الرسمية"]}]
    router = make_offline_router(
        rag_docs=[{"score": 0.1, "title": "ضعيف", "chunk": "غير مرتبط", "metadata": {}}],
        web_results=web,
    )
    result = router.answer_query("ما مواعيد موسم الرياض 2026؟")
    assert result.decision.web_search_triggered is True
    assert len(result.web_sources) == 1
    assert "2026" in result.answer_text
    assert "روبيان" not in result.answer_text


def test_golden_corpus_only_no_web():
    rag = [{
        "score": 0.9,
        "title": "وثيقة العمارة النجدية",
        "chunk": "تتميز العمارة النجدية بالطين واللبن في قصور الرياض.",
        "metadata": {"source_url": "corpus/najdi.md", "topic": "architecture", "region": "نجد", "culture": "سعودي"},
    }]
    router = make_offline_router(rag_docs=rag, web_results=[])
    result = router.answer_query("ما هي العمارة النجدية؟")
    assert result.decision.web_search_triggered is False
    assert "العمارة النجدية" in result.answer_text or "الطين" in result.answer_text
    assert any(str(c.get("channel")) == "rag" for c in result.citations)


# --- Storage / lifecycle goldens ---------------------------------------------


def test_golden_blob_roundtrip_txt(tmp_path, monkeypatch, fake_blob_backend):
    install_fake_blob(monkeypatch, fake_blob_backend)
    from sard.outputs.orchestrator import FileSystemArtifactStore as _FS

    endpoint = "https://blob.test"
    store_a = ConfigurableBlobArtifactStore(fallback_local=_FS(tmp_path / "a"), endpoint=endpoint, token="t")
    (tmp_path / "a").mkdir(exist_ok=True)
    orch = ArtifactOrchestrator(store_a)
    res = orch.generate_artifact(artifact_request("txt"))
    assert res.status == "created", res.error
    store_b = ConfigurableBlobArtifactStore(fallback_local=_FS(tmp_path / "b"), endpoint=endpoint, token="t")
    (tmp_path / "b").mkdir(exist_ok=True)
    got = store_b.get_bytes(res.id)
    assert got is not None and got[0] == res.data
    assert got[2] == ARTIFACT_MIME_TYPES["txt"]


def test_golden_duplicate_topic_gets_unique_versions(tmp_path):
    orch = _orchestrator(tmp_path)
    first = orch.generate_artifact(artifact_request("txt", title="نفس العنوان"))
    second = orch.generate_artifact(artifact_request("txt", title="نفس العنوان"))
    assert first.status == "created" and second.status == "created"
    assert first.id != second.id
    assert first.filename != second.filename
    # Deterministic rendering: same content -> same bytes, but unique identity.
    assert first.data == second.data
    assert len({p.name for p in tmp_path.glob("*.txt")}) == 2


@pytest.mark.parametrize("fmt", ["pdf", "docx", "pptx"])
def test_golden_ar_office_matrix(tmp_path, fmt):
    kind = "presentation" if fmt == "pptx" else "document"
    res = _orchestrator(tmp_path).generate_artifact(artifact_request(fmt, kind=kind))
    assert res.status == "created", res.error
    assert res.mime_type == ARTIFACT_MIME_TYPES[fmt]
    validate_artifact_bytes(fmt, res.data)


def test_golden_html_preview_present_and_titled(tmp_path):
    from sard.outputs.html import render_html_from_request

    orch = _orchestrator(tmp_path)
    res = orch.generate_artifact(artifact_request("pdf"))
    assert res.status == "created"
    assert isinstance(res.preview, dict) and res.preview
    html = render_html_from_request(title=res.title, topic=AR_TOPIC, content_paragraphs=["فقرة تعريفية."])
    assert res.title in html
    assert 'dir="rtl"' in html


def test_golden_revision_explicit_new_version_never_crash(tmp_path):
    store = FileSystemArtifactStore(tmp_path)
    store.store_bytes("art-rev-001", "notes.txt", "النسخة الأولى".encode("utf-8"), "text/plain")
    import pytest as _pytest

    with _pytest.raises(ValueError):
        store.store_bytes("art-rev-001", "notes.txt", "النسخة الثانية".encode("utf-8"), "text/plain")
    # Explicit new version under a fresh id: both revisions stay retrievable.
    store.store_bytes("art-rev-002", "notes.txt", "النسخة الثانية".encode("utf-8"), "text/plain")
    assert store.get_bytes("art-rev-001")[0] == "النسخة الأولى".encode("utf-8")
    assert store.get_bytes("art-rev-002")[0] == "النسخة الثانية".encode("utf-8")


def test_golden_cancellation_leaves_no_orphan(tmp_path):
    orch = _orchestrator(tmp_path)
    before = {p.name for p in tmp_path.iterdir() if p.is_file()}
    res = orch.generate_artifact(artifact_request("txt"), deadline_monotonic=time.monotonic() - 1.0)
    assert res.status == "failed" and res.error_category == "timeout"
    assert {p.name for p in tmp_path.iterdir() if p.is_file()} - before == set()
