"""Bug #3: HTML is a stored artifact format (not preview-only)."""

from __future__ import annotations

import pytest

from sard.outputs.orchestrator import ArtifactOrchestrator, ArtifactRequest, FileSystemArtifactStore
from sard.outputs.validation import ARTIFACT_MIME_TYPES, ArtifactValidationError, validate_artifact_bytes


def _request() -> ArtifactRequest:
    return ArtifactRequest(
        format="html",
        kind="document",
        title="تقرير: العمارة النجدية",
        topic="العمارة النجدية",
        raw_text="تتميز العمارة النجدية باستخدام الطين واللبن.\n\nتضم قصور المصمك شواهد معمارية موثقة.",
    )


def test_html_registered_as_stored_format():
    assert ARTIFACT_MIME_TYPES["html"] == "text/html; charset=utf-8"


def test_generate_artifact_html_created_with_valid_bytes_and_preview(tmp_path):
    orch = ArtifactOrchestrator(FileSystemArtifactStore(tmp_path))
    result = orch.generate_artifact(_request())

    assert result.status == "created", result.error
    assert result.format == "html"
    assert result.mime_type == "text/html; charset=utf-8"
    assert result.filename.endswith(".html")
    assert result.size_bytes > 0
    assert result.data
    assert result.download_url is not None
    assert result.preview is not None and result.preview.get("items") is not None

    text = result.data.decode("utf-8")
    lowered = text.lower()
    assert "<html" in lowered and "</html>" in lowered
    assert validate_artifact_bytes("html", result.data).format == "html"

    stored = FileSystemArtifactStore(tmp_path).get_bytes(result.filename)
    assert stored is not None and stored[0] == result.data


def test_html_validator_rejects_non_html():
    with pytest.raises(ArtifactValidationError):
        validate_artifact_bytes("html", b"not html at all")
    with pytest.raises(ArtifactValidationError):
        validate_artifact_bytes("html", b"")
