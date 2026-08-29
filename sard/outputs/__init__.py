"""Provider-independent output contracts, generators, storage, and orchestration."""

from sard.outputs.artifacts import ArtifactManager, ArtifactWriteResult
from sard.outputs.calendar import CalendarRenderResult, render_calendar
from sard.outputs.office_docx import CulturalDocxDocument, DocxGenerator, render_cultural_docx_report
from sard.outputs.orchestrator import (
    ArtifactGeneratorRegistry,
    ArtifactOrchestrator,
    ArtifactRequest,
    ArtifactResult,
    ArtifactStore,
    ConfigurableBlobArtifactStore,
    FileSystemArtifactStore,
    get_artifact_orchestrator,
    get_artifact_store,
    set_artifact_store,
)
from sard.outputs.pdf import RenderedArtifact, render_pdf
from sard.outputs.pdf_report import render_cultural_pdf_report
from sard.outputs.raw import RawTextResult, render_raw_text
from sard.outputs.schemas import (
    CitationSource,
    Coordinates,
    FieldSupport,
    Itinerary,
    ItineraryDay,
    ItineraryStop,
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
    "render_cultural_pdf_report",
    "render_cultural_docx_report",
    "CulturalDocxDocument",
    "DocxGenerator",
    "ArtifactRequest",
    "ArtifactResult",
    "ArtifactStore",
    "FileSystemArtifactStore",
    "ConfigurableBlobArtifactStore",
    "ArtifactGeneratorRegistry",
    "ArtifactOrchestrator",
    "get_artifact_orchestrator",
    "get_artifact_store",
    "set_artifact_store",
]
