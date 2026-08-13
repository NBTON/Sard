"""Provider-independent output contracts and renderers."""

from sard.outputs.artifacts import ArtifactManager, ArtifactWriteResult
from sard.outputs.calendar import CalendarRenderResult, render_calendar
from sard.outputs.pdf import RenderedArtifact, render_pdf
from sard.outputs.raw import RawTextResult, render_raw_text
from sard.outputs.schemas import (
    CitationSource,
    Coordinates,
    Itinerary,
    ItineraryDay,
    ItineraryStop,
    FieldSupport,
    TextBlock,
    VerificationStatus,
)

__all__ = [
    "CitationSource",
    "Coordinates",
    "FieldSupport",
    "Itinerary",
    "ItineraryDay",
    "ItineraryStop",
    "RenderedArtifact",
    "ArtifactManager",
    "ArtifactWriteResult",
    "CalendarRenderResult",
    "RawTextResult",
    "VerificationStatus",
    "render_calendar",
    "render_raw_text",
    "TextBlock",
    "render_pdf",
]
