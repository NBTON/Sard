"""Chunking + stable ID/hash tests (no network required)."""

from sard.rag.chunking import (
    approx_token_count,
    chunk_sections,
    compute_chunk_id,
    compute_citation_id,
    compute_content_hash,
    compute_document_hash,
    compute_document_id,
)
from sard.rag.schemas import ParsedSection


def test_document_id_is_stable_for_same_url():
    a = compute_document_id("https://example.com/a", "Title A")
    b = compute_document_id("https://example.com/a", "Different Title")
    assert a == b  # URL is the basis, not title


def test_document_id_differs_for_different_url():
    a = compute_document_id("https://example.com/a", "Title")
    b = compute_document_id("https://example.com/b", "Title")
    assert a != b


def test_content_hash_and_chunk_id_are_stable_across_runs():
    content = "الينابيع الحارة في الأحساء موضوع تراثي مهم."
    h1 = compute_content_hash(content)
    h2 = compute_content_hash(content)
    assert h1 == h2

    doc_id = "DOC-abc123"
    chunk_id_1 = compute_chunk_id(doc_id, h1)
    chunk_id_2 = compute_chunk_id(doc_id, h2)
    assert chunk_id_1 == chunk_id_2


def test_citation_id_stable_when_content_and_document_unchanged():
    content = "نص تجريبي حول تجفيف الروبيان."
    doc_id = "DOC-xyz789"
    h = compute_content_hash(content)
    cit1 = compute_citation_id(doc_id, h)
    cit2 = compute_citation_id(doc_id, h)
    assert cit1 == cit2
    assert cit1.startswith("CIT-")


def test_citation_id_differs_for_different_content():
    doc_id = "DOC-xyz789"
    cit1 = compute_citation_id(doc_id, compute_content_hash("نص أول"))
    cit2 = compute_citation_id(doc_id, compute_content_hash("نص مختلف تمامًا"))
    assert cit1 != cit2


def test_document_hash_changes_when_text_changes():
    h1 = compute_document_hash("النص الأصلي")
    h2 = compute_document_hash("النص المعدل")
    assert h1 != h2


def test_approx_token_count_basic():
    assert approx_token_count("") == 0
    assert approx_token_count("كلمة واحدة") == 2
    assert approx_token_count("one two three") == 3


def test_chunk_sections_respects_target_and_max_tokens():
    long_paragraph = " ".join(["كلمة"] * 1000)
    sections = [ParsedSection(heading="عنوان", text=long_paragraph, page_number=1)]
    chunks = chunk_sections(sections, target_tokens=200, max_tokens=250, overlap_ratio=0.15)
    assert len(chunks) > 1
    for c in chunks:
        assert approx_token_count(c.text) <= 250 + 40  # allow small overlap slack


def test_chunk_sections_keeps_heading_with_first_chunk():
    sections = [
        ParsedSection(heading="عنوان القسم", text="فقرة أولى قصيرة.\n\nفقرة ثانية قصيرة.", page_number=1)
    ]
    chunks = chunk_sections(sections, target_tokens=500, max_tokens=800)
    assert len(chunks) == 1
    assert chunks[0].section_heading == "عنوان القسم"


def test_chunk_sections_produces_overlap_between_adjacent_chunks():
    # Build enough distinct paragraphs to force a split.
    paragraphs = [f"فقرة رقم {i} " + " ".join(["كلمة"] * 60) for i in range(20)]
    text = "\n\n".join(paragraphs)
    sections = [ParsedSection(heading=None, text=text, page_number=None)]
    chunks = chunk_sections(sections, target_tokens=300, max_tokens=350, overlap_ratio=0.15)
    assert len(chunks) >= 2
    first_words = set(chunks[0].text.split()[-20:])
    second_words = set(chunks[1].text.split()[:40])
    assert first_words & second_words  # some overlap carried into next chunk
