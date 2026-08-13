"""Typed, provider-neutral contracts for Sard output artifacts.

The original Step 4 fields remain source-compatible.  Step 6 adds explicit
provenance, dates, times, verification metadata, and the fields needed by the
calendar and raw-text renderers.  Nothing in this module imports a provider,
retriever, or graph implementation.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time as time_type
from enum import Enum
from typing import Optional


CITATION_ID_RE = re.compile(r"^CIT-[A-Za-z0-9_-]{3,60}$")
INLINE_CITATION_RE = re.compile(r"\[(CIT-[A-Za-z0-9_-]{3,60})\]")


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    EVIDENCE_LIMITED = "evidence_limited"


@dataclass(frozen=True)
class CitationSource:
    """Metadata copied from a supplied source; absent values stay absent."""

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
class Coordinates:
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.latitude) or not -90 <= self.latitude <= 90:
            raise ValueError("Latitude must be a finite value between -90 and 90.")
        if not math.isfinite(self.longitude) or not -180 <= self.longitude <= 180:
            raise ValueError("Longitude must be a finite value between -180 and 180.")


@dataclass(frozen=True)
class FieldSupport:
    """Provenance for one factual itinerary field."""

    field_name: str
    citation_ids: tuple[str, ...] = ()
    claim_ids: tuple[str, ...] = ()
    provenance: str = "verified"  # verified | user_provided | uncertain

    def __post_init__(self) -> None:
        if not self.field_name.strip():
            raise ValueError("Field support requires a field name.")
        if self.provenance not in {"verified", "user_provided", "uncertain"}:
            raise ValueError(f"Unknown field provenance: {self.provenance!r}")


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
    """One stop; the first fields preserve the Step 4 constructor contract."""

    time: str
    title: str
    location: str
    paragraphs: tuple[TextBlock, ...] = ()
    bullets: tuple[TextBlock, ...] = ()
    notes: tuple[TextBlock, ...] = ()
    stop_id: str = ""
    start_time: Optional[time_type] = None
    end_time: Optional[time_type] = None
    location_name: Optional[str] = None
    address: Optional[str] = None
    coordinates: Optional[Coordinates] = None
    description: tuple[TextBlock, ...] = ()
    practical_notes: tuple[TextBlock, ...] = ()
    accessibility_notes: tuple[TextBlock, ...] = ()
    citation_ids: tuple[str, ...] = ()
    field_support: tuple[FieldSupport, ...] = ()

    @property
    def effective_location_name(self) -> str:
        return (self.location_name if self.location_name is not None else self.location).strip()

    @property
    def effective_description(self) -> tuple[TextBlock, ...]:
        return self.description or self.paragraphs

    @property
    def effective_practical_notes(self) -> tuple[TextBlock, ...]:
        return self.practical_notes or self.bullets

    @property
    def effective_accessibility_notes(self) -> tuple[TextBlock, ...]:
        return self.accessibility_notes

    def support_for(self, field_name: str) -> Optional[FieldSupport]:
        for support in self.field_support:
            if support.field_name == field_name:
                return support
        return None


@dataclass(frozen=True)
class ItineraryDay:
    title: str
    date: Optional[date] = None
    stops: tuple[ItineraryStop, ...] = ()
    notes: tuple[TextBlock, ...] = ()
    relative_day_number: Optional[int] = None
    field_support: tuple[FieldSupport, ...] = ()


@dataclass(frozen=True)
class Itinerary:
    """Final typed itinerary consumed by all Step 6 renderers."""

    # Step 4 positional contract.
    title: str
    summary: str
    days: tuple[ItineraryDay, ...]
    sources: tuple[CitationSource, ...]
    generated_at: datetime
    notes: tuple[TextBlock, ...] = ()
    # Step 6 metadata.
    run_id: str = ""
    timezone: str = "Asia/Riyadh"
    explicit_dates: tuple[date, ...] = ()
    verification_status: VerificationStatus = VerificationStatus.VERIFIED
    retrieval_mode: str = ""
    model_fallback_used: bool = False
    warnings: tuple[str, ...] = ()
    degraded_notice: Optional[str] = None
    citation_ids: tuple[str, ...] = ()
    field_support: tuple[FieldSupport, ...] = ()

    def __post_init__(self) -> None:
        if not self.title.strip() or not self.summary.strip():
            raise ValueError("Itinerary title and summary are required.")
        if not self.days:
            raise ValueError("An itinerary must contain at least one day.")
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("generated_at must include a timezone.")
        if isinstance(self.verification_status, str):
            object.__setattr__(self, "verification_status", VerificationStatus(self.verification_status))

    def all_text_blocks(self) -> tuple[TextBlock, ...]:
        blocks = list(self.notes)
        for day in self.days:
            blocks.extend(day.notes)
            for stop in day.stops:
                blocks.extend(stop.paragraphs)
                blocks.extend(stop.bullets)
                blocks.extend(stop.notes)
                blocks.extend(stop.description)
                blocks.extend(stop.practical_notes)
                blocks.extend(stop.accessibility_notes)
        return tuple(blocks)

    def all_citation_ids(self) -> tuple[str, ...]:
        ids: list[str] = []
        seen: set[str] = set()

        def add(values) -> None:
            for value in values or ():
                if value not in seen:
                    seen.add(value)
                    ids.append(value)

        add(self.citation_ids)
        for support in self.field_support:
            add(support.citation_ids)
        for block in self.all_text_blocks():
            add(block.citation_ids)
            add(INLINE_CITATION_RE.findall(block.text))
        for day in self.days:
            for support in day.field_support:
                add(support.citation_ids)
            for stop in day.stops:
                add(stop.citation_ids)
                for support in stop.field_support:
                    add(support.citation_ids)
        return tuple(ids)

    def validate_citations(self) -> dict[str, CitationSource]:
        """Reject duplicate/unknown references and return the source map."""

        mapping: dict[str, CitationSource] = {}
        for source in self.sources:
            if source.citation_id in mapping:
                raise ValueError(f"Duplicate citation ID: {source.citation_id}")
            mapping[source.citation_id] = source

        unknown = set(self.all_citation_ids()) - mapping.keys()
        if unknown:
            raise ValueError(f"Unknown citation ID(s): {', '.join(sorted(unknown))}")
        return mapping
