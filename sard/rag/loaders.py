"""Document loaders: PDF, HTML, Markdown, and plain text -> ParsedDocument.

Text extraction is always attempted first. PDFs whose pages yield too
little extractable text are flagged as likely-scanned and returned with
empty/partial content plus a list of page numbers that need either a
vision-language model pass (see ``sard/rag/ingest.py``) or manual-review
quarantine — this module never fabricates page content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sard.rag.normalize import clean_document_text, remove_repeated_lines
from sard.rag.schemas import ParsedSection, SourceFileType

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

# Heuristic: a PDF page with fewer than this many extractable characters is
# treated as "likely scanned" rather than assumed to be a genuinely short
# page, to avoid silently dropping content without flagging it.
SCANNED_PAGE_MIN_CHARS = 40


@dataclass
class LoadedDocument:
    original_text: str
    sections: list[ParsedSection]
    file_type: SourceFileType
    scanned_pages: list[int]  # page numbers that look scanned/unextractable


def detect_file_type(path: Path) -> SourceFileType:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return SourceFileType.PDF
    if suffix in (".html", ".htm"):
        return SourceFileType.HTML
    if suffix in (".md", ".markdown"):
        return SourceFileType.MARKDOWN
    return SourceFileType.TEXT


def load_document(path: Path) -> LoadedDocument:
    file_type = detect_file_type(path)
    if file_type == SourceFileType.PDF:
        return _load_pdf(path)
    if file_type == SourceFileType.HTML:
        return _load_html(path)
    if file_type == SourceFileType.MARKDOWN:
        return _load_markdown(path)
    return _load_text(path)


def _load_text(path: Path) -> LoadedDocument:
    raw = path.read_text(encoding="utf-8", errors="replace")
    cleaned = clean_document_text(raw)
    sections = [ParsedSection(heading=None, text=cleaned, page_number=None)]
    return LoadedDocument(
        original_text=cleaned,
        sections=sections,
        file_type=SourceFileType.TEXT,
        scanned_pages=[],
    )


def _load_markdown(path: Path) -> LoadedDocument:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = raw.split("\n")

    sections: list[ParsedSection] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    def flush():
        if current_lines:
            text = clean_document_text("\n".join(current_lines))
            if text:
                sections.append(ParsedSection(heading=current_heading, text=text, page_number=None))

    for line in lines:
        match = _HEADING_RE.match(line.strip())
        if match:
            flush()
            current_lines = []
            current_heading = match.group(2).strip()
        else:
            current_lines.append(line)
    flush()

    if not sections:
        sections = [ParsedSection(heading=None, text=clean_document_text(raw), page_number=None)]

    full_text = clean_document_text(raw)
    return LoadedDocument(
        original_text=full_text,
        sections=sections,
        file_type=SourceFileType.MARKDOWN,
        scanned_pages=[],
    )


def _load_html(path: Path) -> LoadedDocument:
    from bs4 import BeautifulSoup

    raw_html = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(raw_html, "lxml")

    # Drop elements that are almost always navigation/boilerplate, not
    # article content.
    for tag_name in ("nav", "header", "footer", "script", "style", "aside", "form"):
        for tag in soup.find_all(tag_name):
            tag.decompose()

    sections: list[ParsedSection] = []
    heading_tags = {"h1", "h2", "h3", "h4", "h5", "h6"}
    current_heading: str | None = None
    current_texts: list[str] = []

    def flush():
        if current_texts:
            text = clean_document_text("\n".join(current_texts))
            if text:
                sections.append(ParsedSection(heading=current_heading, text=text, page_number=None))

    body = soup.body or soup
    for el in body.find_all(True):
        if el.name in heading_tags:
            flush()
            current_texts = []
            current_heading = el.get_text(" ", strip=True)
        elif el.name in ("p", "li", "td", "blockquote") and not el.find(heading_tags):
            text = el.get_text(" ", strip=True)
            if text:
                current_texts.append(text)
    flush()

    if not sections:
        full_text = clean_document_text(soup.get_text("\n", strip=True))
        sections = [ParsedSection(heading=None, text=full_text, page_number=None)]

    full_text = clean_document_text(soup.get_text("\n", strip=True))
    return LoadedDocument(
        original_text=full_text,
        sections=sections,
        file_type=SourceFileType.HTML,
        scanned_pages=[],
    )


def _load_pdf(path: Path) -> LoadedDocument:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    raw_pages: list[str] = []
    for page in reader.pages:
        try:
            raw_pages.append(page.extract_text() or "")
        except Exception:
            raw_pages.append("")

    scanned_pages = [
        i + 1 for i, text in enumerate(raw_pages) if len(text.strip()) < SCANNED_PAGE_MIN_CHARS
    ]

    cleaned_pages = remove_repeated_lines(raw_pages)

    sections: list[ParsedSection] = []
    for i, page_text in enumerate(cleaned_pages):
        if i + 1 in set(scanned_pages):
            # A short extraction is not reliable evidence, even when it is
            # non-empty. Keep the page number in ``scanned_pages`` so
            # ingestion can quarantine it for review, but never index it.
            continue
        text = clean_document_text(page_text)
        if text:
            sections.append(ParsedSection(heading=None, text=text, page_number=i + 1))

    full_text = "\n\n".join(s.text for s in sections)
    return LoadedDocument(
        original_text=full_text,
        sections=sections,
        file_type=SourceFileType.PDF,
        scanned_pages=scanned_pages,
    )
