"""Document loader tests: PDF (text + scanned), HTML, Markdown, plain text.

All fixtures are generated locally — no network access, no fabricated
"verified" content is implied; these are mechanical parsing tests only.
"""

from __future__ import annotations

import zlib

from sard.rag.loaders import (
    SCANNED_PAGE_MIN_CHARS,
    detect_file_type,
    load_document,
)
from sard.rag.schemas import SourceFileType


def _make_text_pdf(text: str, pages: int = 1) -> bytes:
    """Build a minimal, valid single/multi-page PDF whose pages contain the
    given text, computing xref offsets programmatically so pypdf can parse it.
    """
    objects: list[bytes] = []
    # Catalog
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    # Pages tree
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(pages))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    font_id = 3 + 2 * pages
    for i in range(pages):
        content_id = 4 + 2 * i
        page_id = 3 + 2 * i
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {content_id} 0 R /Resources << /Font << /F1 "
                f"{font_id} 0 R >> >> >>"
            ).encode()
        )
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
        compressed = zlib.compress(stream)
        objects.append(
            b"<< /Length " + str(len(compressed)).encode() + b" /Filter /FlateDecode >>\nstream\n" + compressed + b"\nendstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    )
    return bytes(out)


def test_detect_file_type_handles_all_supported_extensions(tmp_path):
    cases = {
        "a.pdf": SourceFileType.PDF,
        "b.html": SourceFileType.HTML,
        "c.htm": SourceFileType.HTML,
        "d.md": SourceFileType.MARKDOWN,
        "e.markdown": SourceFileType.MARKDOWN,
        "f.txt": SourceFileType.TEXT,
        "g.unknown": SourceFileType.TEXT,
    }
    for name, expected in cases.items():
        assert detect_file_type(tmp_path / name) == expected


def test_text_loader_preserves_content_and_sections(tmp_path):
    path = tmp_path / "doc.txt"
    path.write_text("العين الحارة في الأحساء مذكورة في المصادر التاريخية.\n\nفصل ثانٍ.", encoding="utf-8")
    loaded = load_document(path)
    assert loaded.file_type == SourceFileType.TEXT
    assert loaded.scanned_pages == []
    assert len(loaded.sections) == 1
    assert "الأحساء" in loaded.original_text


def test_markdown_loader_splits_sections_on_headings(tmp_path):
    path = tmp_path / "doc.md"
    path.write_text(
        "# عيون الأحساء\n\nنص عن العيون الحارة.\n\n## الاستخدام التقليدي\n\nنص آخر.",
        encoding="utf-8",
    )
    loaded = load_document(path)
    assert loaded.file_type == SourceFileType.MARKDOWN
    headings = [s.heading for s in loaded.sections]
    assert headings == ["عيون الأحساء", "الاستخدام التقليدي"]
    assert any("العيون الحارة" in s.text for s in loaded.sections)


def test_html_loader_strips_boilerplate_and_keeps_article_text(tmp_path):
    path = tmp_path / "doc.html"
    path.write_text(
        """
<html><head><title>صفحة</title></head><body>
<nav>روابط التنقل العليا</nav>
<h1>العيون المائية في الأحساء</h1>
<p>فقرة أولى عن الينابيع.</p>
<footer>حقوق النشر</footer>
<p>فقرة ثانية.</p>
</body></html>
""",
        encoding="utf-8",
    )
    loaded = load_document(path)
    assert loaded.file_type == SourceFileType.HTML
    full = loaded.original_text
    assert "فقرة أولى" in full
    assert "روابط التنقل" not in full  # nav decomposed
    assert "حقوق النشر" not in full  # footer decomposed
    # The h1 heading stays attached to the section that follows it.
    assert len(loaded.sections) == 1
    assert loaded.sections[0].heading == "العيون المائية في الأحساء"
    assert "فقرة ثانية" in loaded.sections[0].text


def test_pdf_loader_extracts_text_from_generated_pdf(tmp_path):
    path = tmp_path / "text.pdf"
    # Text must exceed the scanned-page minimum character threshold (40).
    path.write_bytes(_make_text_pdf("Hot springs text for extraction and verification purposes in this test document"))
    loaded = load_document(path)
    assert loaded.file_type == SourceFileType.PDF
    assert "Hot springs text" in loaded.original_text
    assert loaded.scanned_pages == []
    assert loaded.sections[0].page_number == 1


def test_pdf_loader_flags_blank_pages_as_scanned(tmp_path):
    path = tmp_path / "scanned.pdf"
    # Two pages of PDF containing text that extracts to almost nothing.
    path.write_bytes(_make_text_pdf(" ", pages=2))
    loaded = load_document(path)
    assert loaded.file_type == SourceFileType.PDF
    assert loaded.scanned_pages == [1, 2]
    # No fabricated content: the document yields no sections.
    assert len(loaded.sections) == 0


def test_pdf_loader_quarantines_short_nonempty_extraction(tmp_path):
    path = tmp_path / "short-scan.pdf"
    path.write_bytes(_make_text_pdf("tiny"))
    loaded = load_document(path)
    assert loaded.scanned_pages == [1]
    assert loaded.sections == []


def test_scanned_threshold_constant_is_sane():
    assert SCANNED_PAGE_MIN_CHARS > 0
