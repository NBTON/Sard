"""Workstream A: canonical ArtifactDocument acceptance tests."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sard.outputs.document import (
    ArtifactBlock,
    ArtifactDocument,
    ArtifactMetadata,
    ArtifactSection,
)
from sard.outputs.orchestrator import (
    ArtifactOrchestrator,
    ArtifactRequest,
    FileSystemArtifactStore,
)
from sard.outputs.schemas import (
    CitationSource,
    FieldSupport,
    Itinerary,
    ItineraryDay,
    ItineraryStop,
    TextBlock,
)

CID_ONE = "CIT-DOC-001"
CID_TWO = "CIT-DOC-002"


def _sources() -> tuple[CitationSource, ...]:
    return (
        CitationSource(CID_ONE, "دليل الواحة", "https://example.org/oasis?lang=ar"),
        CitationSource(CID_TWO, "دليل السوق", "https://example.com/market?lang=ar"),
    )


def _itinerary() -> Itinerary:
    stop = ItineraryStop(
        time="09:00",
        title="المحطة الأولى",
        location="الواحة",
        paragraphs=(TextBlock(f"وصف موثق [{CID_ONE}]", (CID_ONE,)),),
        bullets=(TextBlock("ملاحظة من المستخدم.",),),
        stop_id="stop-one",
        description=(TextBlock(f"وصف موثق [{CID_ONE}]", (CID_ONE,)),),
        practical_notes=(TextBlock("ملاحظة من المستخدم.",),),
        citation_ids=(CID_ONE,),
        field_support=(
            FieldSupport("title", (CID_ONE,)),
            FieldSupport("location", (CID_ONE,)),
            FieldSupport("time", provenance="user_provided"),
            FieldSupport("description", (CID_ONE,)),
            FieldSupport("practical_notes", provenance="user_provided"),
        ),
    )
    day = ItineraryDay(
        title="اليوم الأول",
        stops=(stop,),
        notes=(TextBlock(f"ملاحظة اليوم [{CID_ONE}]", (CID_ONE,)),),
        field_support=(FieldSupport("title", provenance="user_provided"),),
    )
    return Itinerary(
        title="رحلة التحقق",
        summary=f"ملخص موثق [{CID_ONE}]",
        days=(day,),
        sources=_sources(),
        generated_at=datetime(2026, 8, 13, 9, 30, tzinfo=ZoneInfo("Asia/Riyadh")),
        run_id="doc-test-run",
        field_support=(
            FieldSupport("title", provenance="user_provided"),
            FieldSupport("summary", (CID_ONE,)),
        ),
    )


def test_from_itinerary_round_trip_validate_citations():
    itinerary = _itinerary()
    doc = ArtifactDocument.from_itinerary(itinerary, artifact_id="art-doc-1", run_id="doc-test-run")
    mapping = doc.validate_citations()
    assert set(mapping) == {CID_ONE, CID_TWO}
    assert CID_ONE in doc.all_source_ids()
    # Round-trip through the orchestrator request shim preserves title/topic.
    request = doc.to_artifact_request()
    assert isinstance(request, ArtifactRequest)
    assert request.title == itinerary.title
    rebuilt = ArtifactDocument.from_request(request, artifact_id="art-doc-1")
    assert rebuilt.metadata.title == doc.metadata.title
    assert rebuilt.to_preview()["title"] == doc.to_preview()["title"]


def test_to_preview_superset_keys():
    doc = ArtifactDocument.from_itinerary(_itinerary(), artifact_id="art-doc-2")
    preview = doc.to_preview()
    for key in ("type", "title", "counts", "items"):
        assert key in preview, f"missing canonical key {key}"
    # Superset covering every legacy preview_data shape.
    for key in (
        "paragraphs_count", "sections_count",
        "slides_count", "slides",
        "events_count", "events",
        "rows", "characters", "text",
    ):
        assert key in preview, f"missing compat key {key}"
    assert isinstance(preview["counts"], dict)
    assert preview["counts"]["sections"] == len(doc.sections)
    assert preview["counts"]["sources"] == 2
    assert isinstance(preview["items"], list) and preview["items"]


def test_preview_types_cover_formats():
    itinerary = _itinerary()
    assert ArtifactDocument.from_itinerary(itinerary, format="pptx", kind="presentation").to_preview()["type"] == "slides"
    assert ArtifactDocument.from_itinerary(itinerary, format="ics", kind="calendar").to_preview()["type"] == "calendar"
    assert ArtifactDocument.from_itinerary(itinerary, format="png", kind="image").to_preview()["type"] == "image"
    assert ArtifactDocument.from_itinerary(itinerary, format="csv", kind="table").to_preview()["type"] == "table"
    assert ArtifactDocument.from_itinerary(itinerary, format="txt", kind="text").to_preview()["type"] == "text"


def test_evidence_rule_filters_unverified_uncited_blocks():
    sections = (
        ArtifactSection(
            section_id="s1",
            title="قسم",
            blocks=(
                ArtifactBlock(block_id="cited", block_type="paragraph", text="موثق", source_ids=(CID_ONE,)),
                ArtifactBlock(block_id="plain-verified", block_type="paragraph", text="غير موثق", verification_status="verified"),
                ArtifactBlock(block_id="user", block_type="paragraph", text="من المستخدم", verification_status="user_provided"),
                ArtifactBlock(block_id="uncertain", block_type="paragraph", text="غير مؤكد", verification_status="uncertain"),
            ),
        ),
    )
    doc = ArtifactDocument(
        metadata=ArtifactMetadata(artifact_id="a", format="pdf", kind="document", title="t", topic="t"),
        sections=sections,
        sources=_sources(),
    )
    assert doc.sections[0].blocks[0].is_renderable() is True
    assert doc.sections[0].blocks[1].is_renderable() is False
    assert doc.sections[0].blocks[2].is_renderable() is True
    assert doc.sections[0].blocks[3].is_renderable() is True
    ids = {item["id"] for item in doc.to_preview()["items"]}
    assert "cited" in ids and "user" in ids and "uncertain" in ids
    assert "plain-verified" not in ids


def test_legacy_alias_presence():
    doc = ArtifactDocument.from_itinerary(_itinerary(), artifact_id="art-doc-3")
    preview = doc.to_preview()
    for alias in ("card_data", "diagram_data", "slides"):
        assert alias in preview, f"missing deprecated alias {alias}"
    assert preview["_deprecated_aliases"] == ["card_data", "diagram_data", "slides"]


def test_orchestrator_preview_uses_document_with_legacy_aliases(tmp_path):
    store = FileSystemArtifactStore(tmp_path)
    orchestrator = ArtifactOrchestrator(store)
    request = ArtifactRequest(
        format="txt",
        kind="document",
        title="تقرير",
        topic="العمارة النجدية",
        raw_text="نص تجريبي.",
    )
    result = orchestrator.generate_artifact(request)
    assert result.status == "created"
    assert result.document is not None
    assert result.preview is not None
    assert result.preview == result.document.to_preview()
    for key in ("type", "title", "counts", "items", "card_data", "diagram_data", "slides"):
        assert key in result.preview


def test_url_parity_fs_store(tmp_path):
    store = FileSystemArtifactStore(tmp_path)
    filename = "sard-topic--art-abc123.pdf"
    assert store.get_download_url("art-abc123", filename) == f"/api/artifacts/{filename}"
    orchestrator = ArtifactOrchestrator(store)
    result = orchestrator.generate_artifact(
        ArtifactRequest(format="txt", kind="document", title="t", topic="نجد", raw_text="نص.")
    )
    assert result.status == "created"
    assert result.download_url == store.get_download_url(result.id, result.filename)


def test_duplicate_valueerror_maps_to_duplicate_artifact(tmp_path, monkeypatch):
    store = FileSystemArtifactStore(tmp_path)
    orchestrator = ArtifactOrchestrator(store)

    def _boom(**kwargs):
        raise ValueError("Refusing to overwrite existing artifact.")

    monkeypatch.setattr(store, "store_bytes", _boom)
    result = orchestrator.generate_artifact(
        ArtifactRequest(format="txt", kind="document", title="t", topic="نجد", raw_text="نص.")
    )
    assert result.status == "failed"
    assert result.error_category == "duplicate_artifact"


def test_service_view_populates_preview():
    from sard.application.service import SardApplicationService
    from sard.outputs.orchestrator import ArtifactResult

    service = SardApplicationService.__new__(SardApplicationService)
    result = ArtifactResult(
        id="art-x",
        kind="document",
        format="pdf",
        title="تقرير",
        filename="report.pdf",
        mime_type="application/pdf",
        size_bytes=10,
        status="created",
        download_url="/api/artifacts/report.pdf",
        preview={"type": "document", "title": "تقرير", "counts": {}, "items": []},
    )
    view = service._artifact_view(result)
    assert view is not None
    assert view.preview == result.preview


def test_markdown_table_parsed_to_table_block():
    from sard.outputs.document import parse_markdown_table

    rows = parse_markdown_table("| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |")
    assert rows == [["A", "B"], ["1", "2"], ["3", "4"]]
    # Lenient: header + aligned rows without a separator line.
    rows = parse_markdown_table("| A | B |\n| 1 | 2 |")
    assert rows == [["A", "B"], ["1", "2"]]
    assert parse_markdown_table("Just a paragraph, no pipes.") is None
    assert parse_markdown_table("| A |\nplain line without pipe") is None


def test_from_request_table_renders_once_in_every_renderer(tmp_path):
    import io
    import zipfile

    from sard.outputs.document import ArtifactDocument
    from sard.outputs.html import render_html_document
    from sard.outputs.pdf import build_pdf_from_document

    raw = "Body para one.\n\n| H1 | H2 |\n| A1 | B1 |\n| A2 | B2 |"
    req = ArtifactRequest(format="pdf", kind="document", title="T", topic="TT", raw_text=raw)
    doc = ArtifactDocument.from_request(req)
    tables = [b for s in doc.sections for b in s.blocks if b.block_type == "table"]
    assert len(tables) == 1
    assert tables[0].data["rows"] == [["H1", "H2"], ["A1", "B1"], ["A2", "B2"]]

    html = render_html_document(doc)
    assert html.count("<table") == 1

    pdf_text = "".join(
        page.extract_text() or ""
        for page in __import__("pypdf").PdfReader(io.BytesIO(build_pdf_from_document(doc))).pages
    )
    # Body emitted exactly once (no flat-paragraph + section duplication).
    assert pdf_text.count("Body para one.") == 1
    assert "A1" in pdf_text and "B2" in pdf_text


def test_single_section_pdf_has_no_title_echo_or_body_dup():
    import io

    from sard.outputs.document import ArtifactDocument
    from sard.outputs.pdf import build_pdf_from_document

    req = ArtifactRequest(
        format="pdf", kind="document", title="DocTitle", topic="Top",
        raw_text="First para.\n\nSecond para.",
    )
    doc = ArtifactDocument.from_request(req)
    pdf_text = "".join(
        page.extract_text() or ""
        for page in __import__("pypdf").PdfReader(io.BytesIO(build_pdf_from_document(doc))).pages
    )
    assert pdf_text.count("First para.") == 1
    assert pdf_text.count("Second para.") == 1
    # Cover title appears once as title, not again as a section header.
    assert "◆ DocTitle" not in pdf_text
