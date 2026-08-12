"""Typed, provider-neutral itinerary contracts for output renderers.

These types deliberately contain no renderer, RAG, UI, or model dependencies.
They are suitable as the output boundary of a later itinerary workflow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional


CITATION_ID_RE = re.compile(r"^CIT-[A-Za-z0-9_-]{3,60}$")
INLINE_CITATION_RE = re.compile(r"\[(CIT-[A-Za-z0-9_-]{3,60})\]")


@dataclass(frozen=True)
class CitationSource:
    """Metadata copied from a supplied source; optional values are never inferred."""

    citation_id: str
    title: str
    url: str
    page: Optional[int] = None
    section: Optional[str] = None
    publication_date: Optional[date] = None

    def __post_init__(self) -> None:
        if not CITATION_ID_RE.fullmatch(self.citation_id):
            raise ValueError(f"Invalid stable citation ID: {self.citation_id!r}")
        if not self.title.strip() or not self.url.strip():
            raise ValueError("Citation title and URL are required.")
        if self.page is not None and self.page < 1:
            raise ValueError("Citation page must be positive when supplied.")


@dataclass(frozen=True)
class TextBlock:
    """One paragraph or bullet and its explicit source references."""

    text: str
    citation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("Text blocks cannot be blank.")


@dataclass(frozen=True)
class ItineraryStop:
    time: str
    title: str
    location: str
    paragraphs: tuple[TextBlock, ...] = ()
    bullets: tuple[TextBlock, ...] = ()
    notes: tuple[TextBlock, ...] = ()


@dataclass(frozen=True)
class ItineraryDay:
    title: str
    date: Optional[date] = None
    stops: tuple[ItineraryStop, ...] = ()
    notes: tuple[TextBlock, ...] = ()


@dataclass(frozen=True)
class Itinerary:
    title: str
    summary: str
    days: tuple[ItineraryDay, ...]
    sources: tuple[CitationSource, ...]
    generated_at: datetime
    notes: tuple[TextBlock, ...] = ()

    def __post_init__(self) -> None:
        if not self.title.strip() or not self.summary.strip():
            raise ValueError("Itinerary title and summary are required.")
        if not self.days:
            raise ValueError("An itinerary must contain at least one day.")
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at must include a timezone.")

    def all_text_blocks(self) -> tuple[TextBlock, ...]:
        blocks = list(self.notes)
        for day in self.days:
            blocks.extend(day.notes)
            for stop in day.stops:
                blocks.extend(stop.paragraphs)
                blocks.extend(stop.bullets)
                blocks.extend(stop.notes)
        return tuple(blocks)

    def validate_citations(self) -> dict[str, CitationSource]:
        """Reject duplicate/unknown references and return the canonical mapping."""

        mapping: dict[str, CitationSource] = {}
        for source in self.sources:
            if source.citation_id in mapping:
                raise ValueError(f"Duplicate citation ID: {source.citation_id}")
            mapping[source.citation_id] = source

        for block in self.all_text_blocks():
            declared = set(block.citation_ids)
            inline = set(INLINE_CITATION_RE.findall(block.text))
            unknown = (declared | inline) - mapping.keys()
            if unknown:
                raise ValueError(f"Unknown citation ID(s): {', '.join(sorted(unknown))}")
        return mapping
