"""Provider-independent output contracts and renderers."""

from sard.outputs.pdf import RenderedArtifact, render_pdf
from sard.outputs.schemas import (
    CitationSource,
    Itinerary,
    ItineraryDay,
    ItineraryStop,
    TextBlock,
)

__all__ = [
    "CitationSource",
    "Itinerary",
    "ItineraryDay",
    "ItineraryStop",
    "RenderedArtifact",
    "TextBlock",
    "render_pdf",
]
