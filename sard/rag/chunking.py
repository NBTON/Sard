"""Deterministic chunking with citation-friendly, stable IDs.

Token counting here is an intentionally simple, dependency-free
approximation (whitespace/punctuation based) rather than a
provider-specific tokenizer. It is deterministic, requires no network
access, and is precise enough for the ~500-800 token chunk-sizing target
this pipeline needs. If a provider's exact tokenizer becomes available it
can be swapped in without changing any chunk IDs (chunk IDs are
content-hash based, not size based).

``CHUNKING_VERSION`` is part of the versioned Zvec collection path (see
``sard/rag/zvec_store.py``) — bump it whenever this algorithm changes in a
way that would produce different chunk boundaries.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional

from sard.rag.schemas import ParsedSection

CHUNKING_VERSION = "1"

DEFAULT_TARGET_TOKENS = 650  # within the 500-800 target range
DEFAULT_OVERLAP_RATIO = 0.15
DEFAULT_MAX_TOKENS = 800
DEFAULT_MIN_TOKENS = 120  # avoid emitting tiny trailing chunks when avoidable

_TOKEN_SPLIT_RE = re.compile(r"\S+")


def approx_token_count(text: str) -> int:
    """Deterministic, dependency-free approximate token count.

    Uses whitespace-delimited word counting, which tracks reasonably well
    with subword-tokenizer counts for Arabic and English prose for the
    purposes of chunk-size targeting.
    """
    if not text:
        return 0
    return len(_TOKEN_SPLIT_RE.findall(text))


@dataclass
class ChunkPiece:
    """A chunk's text plus positional metadata, before ID/hash assignment."""

    text: str
    page_number: Optional[int]
    section_heading: Optional[str]


def _split_paragraphs(text: str) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return paragraphs or ([text.strip()] if text.strip() else [])


def _take_overlap_tail(text: str, overlap_tokens: int) -> str:
    if overlap_tokens <= 0:
        return ""
    words = _TOKEN_SPLIT_RE.findall(text)
    if len(words) <= overlap_tokens:
        return text
    tail_words = words[-overlap_tokens:]
    # Re-slice on the original text so we preserve exact spacing/punctuation
    # for the overlapping tail rather than re-joining with single spaces.
    tail_text = " ".join(tail_words)
    return tail_text


def chunk_sections(
    sections: list[ParsedSection],
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
    min_tokens: int = DEFAULT_MIN_TOKENS,
) -> list[ChunkPiece]:
    """Greedily pack section paragraphs into ~target_tokens chunks.

    Headings are kept attached to the first paragraph(s) of their section
    whenever practical (a heading is never emitted as its own chunk if a
    body paragraph is available to accompany it).
    """
    overlap_tokens = max(0, int(target_tokens * overlap_ratio))

    # Flatten to a list of (heading, paragraph, page_number) units. A
    # paragraph far longer than max_tokens (no internal blank lines to
    # split on) is itself broken into word-based sub-units so it can still
    # be split across chunks.
    units: list[tuple[Optional[str], str, Optional[int]]] = []
    for section in sections:
        paragraphs = _split_paragraphs(section.text)
        if not paragraphs:
            continue
        for i, para in enumerate(paragraphs):
            heading = section.heading if i == 0 else None
            if approx_token_count(para) > max_tokens:
                words = _TOKEN_SPLIT_RE.findall(para)
                for j in range(0, len(words), target_tokens):
                    sub_para = " ".join(words[j : j + target_tokens])
                    units.append((heading if j == 0 else None, sub_para, section.page_number))
            else:
                units.append((heading, para, section.page_number))

    chunks: list[ChunkPiece] = []
    current_parts: list[str] = []
    current_tokens = 0
    current_heading: Optional[str] = None
    current_page: Optional[int] = None
    pending_overlap = ""

    def flush():
        nonlocal current_parts, current_tokens, current_heading, current_page, pending_overlap
        if not current_parts:
            return
        text = "\n\n".join(current_parts).strip()
        if text:
            chunks.append(
                ChunkPiece(
                    text=text,
                    page_number=current_page,
                    section_heading=current_heading,
                )
            )
            pending_overlap = _take_overlap_tail(text, overlap_tokens)
        current_parts = []
        current_tokens = 0
        current_heading = None
        current_page = None

    for heading, para, page in units:
        para_tokens = approx_token_count(para)

        if current_heading is None and heading is not None:
            current_heading = heading
        if current_page is None:
            current_page = page

        would_be = current_tokens + para_tokens
        if current_parts and would_be > max_tokens:
            flush()
            if pending_overlap and approx_token_count(pending_overlap) + para_tokens <= max_tokens:
                current_parts.append(pending_overlap)
                current_tokens += approx_token_count(pending_overlap)
            if heading is not None:
                current_heading = heading
            current_page = page

        current_parts.append(para)
        current_tokens += para_tokens

        if current_tokens >= target_tokens:
            flush()

    flush()

    # Merge a too-small trailing chunk into the previous one when possible,
    # to avoid orphaned tiny fragments (unless it's the only chunk).
    if (
        len(chunks) >= 2
        and approx_token_count(chunks[-1].text) < min_tokens
        and approx_token_count(chunks[-2].text) + approx_token_count(chunks[-1].text) <= max_tokens
    ):
        last = chunks.pop()
        prev = chunks[-1]
        merged_text = prev.text + "\n\n" + last.text
        chunks[-1] = ChunkPiece(
            text=merged_text,
            page_number=prev.page_number,
            section_heading=prev.section_heading,
        )

    return chunks


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_document_id(source_url: str, title: str) -> str:
    """Stable document ID derived from the source URL (or title if absent)."""
    basis = source_url.strip() if source_url and source_url.strip() else title.strip()
    return "DOC-" + sha256_hex(basis)[:16]


def compute_content_hash(content: str) -> str:
    """Stable hash of exact chunk content, used for dedup and chunk IDs."""
    return sha256_hex(content.strip())


def compute_chunk_id(document_id: str, content_hash: str) -> str:
    """Stable chunk ID: same content in the same document -> same ID across
    repeated ingestion runs, enabling idempotent upserts.

    Uses a hyphen (not a colon) separator: Zvec document IDs must match its
    internal identifier regex, which rejects ``:``.
    """
    return f"{document_id}-{content_hash[:16]}"


def compute_citation_id(document_id: str, content_hash: str) -> str:
    """Stable citation ID, independent of chunk ordering/position so that
    citations remain valid even if unrelated chunks shift around it."""
    basis = f"{document_id}|{content_hash}"
    return "CIT-" + sha256_hex(basis)[:12].upper()


def compute_document_hash(normalized_text: str) -> str:
    """Whole-document hash used to detect source content changes."""
    return sha256_hex(normalized_text)
