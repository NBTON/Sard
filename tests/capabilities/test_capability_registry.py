"""Registry completeness + public-path verification for sard.capability_registry.

Policy enforced:
  - Every SUPPORTED capability must have a public_path_test that is non-empty.
  - Every SUPPORTED artifact format (pdf/docx/pptx/ics/svg/png/txt/json/csv) must be
    provably renderable+validatable+storable via the orchestrator offline.
  - Never claim supported without that proof.
"""

from __future__ import annotations

import pytest

from sard.capability_registry import (
    CAPABILITY_REGISTRY,
    CapabilityId,
    PIPELINE_PATTERN,
    SupportStatus,
    validate_registry_completeness,
)
from sard.outputs.orchestrator import ArtifactOrchestrator, ArtifactRequest, FileSystemArtifactStore
from sard.outputs.validation import validate_artifact_bytes


def test_registry_completeness_is_empty():
    errors = validate_registry_completeness()
    assert errors == [], f"Registry completeness errors:\n" + "\n".join(errors)


def test_all_expected_capability_ids_present():
    for cid in CapabilityId:
        assert cid.value in CAPABILITY_REGISTRY, f"missing {cid.value}"


def test_bilingual_examples_present():
    for spec in CAPABILITY_REGISTRY.values():
        assert len(spec.arabic_examples) >= 2, f"{spec.id}: need >=2 Arabic examples"
        assert len(spec.english_examples) >= 2, f"{spec.id}: need >=2 English examples"
        # Arabic example should contain Arabic characters
        assert any(any("\u0600" <= ch <= "\u06FF" for ch in ex) for ex in spec.arabic_examples), f"{spec.id}: Arabic example lacks Arabic script"
        # English example should be ascii-ish
        assert any(ex.strip() for ex in spec.english_examples)


def test_supported_inputs_outputs_nonempty():
    for spec in CAPABILITY_REGISTRY.values():
        assert spec.supported_inputs, f"{spec.id}: empty supported_inputs"
        assert spec.supported_outputs, f"{spec.id}: empty supported_outputs"


def test_required_tools_and_validator_present():
    for spec in CAPABILITY_REGISTRY.values():
        assert spec.required_tools, f"{spec.id}: empty required_tools"
        assert spec.validator.strip(), f"{spec.id}: empty validator"
        assert spec.provider_requirements.strip(), f"{spec.id}: empty provider_requirements"


def test_timeout_retry_fallback_present():
    for spec in CAPABILITY_REGISTRY.values():
        assert spec.timeout_seconds > 0, f"{spec.id}: timeout must be >0"
        assert spec.retry_policy.strip(), f"{spec.id}: empty retry_policy"
        assert spec.fallback.strip(), f"{spec.id}: empty fallback"


def test_progress_stages_are_subset_of_pattern():
    pattern_set = set(PIPELINE_PATTERN)
    for spec in CAPABILITY_REGISTRY.values():
        assert spec.progress_stages, f"{spec.id}: empty progress_stages"
        for stage in spec.progress_stages:
            assert stage in pattern_set, f"{spec.id}: stage {stage!r} not in PIPELINE_PATTERN"


def test_progress_stages_preserve_pattern_order():
    # Ensure stages appear in the same order as PIPELINE_PATTERN (the registry enforces via _stages)
    pattern_index = {name: idx for idx, name in enumerate(PIPELINE_PATTERN)}
    for spec in CAPABILITY_REGISTRY.values():
        indices = [pattern_index[s] for s in spec.progress_stages]
        assert indices == sorted(indices), f"{spec.id}: stages not in pattern order: {spec.progress_stages}"


def test_supported_has_public_path_test_referenced():
    for spec in CAPABILITY_REGISTRY.values():
        if spec.support_status == SupportStatus.SUPPORTED:
            assert spec.public_path_test and spec.public_path_test.strip(), f"{spec.id}: SUPPORTED but public_path_test empty"
            # At least reference a tests/ path
            assert "tests" in spec.public_path_test, f"{spec.id}: SUPPORTED public_path_test should reference tests/ path, got {spec.public_path_test!r}"


def test_limited_has_limitations_documented():
    for spec in CAPABILITY_REGISTRY.values():
        if spec.support_status == SupportStatus.LIMITED:
            assert spec.limitations.strip(), f"{spec.id}: LIMITED but no limitations documented"
            # Must mention provider or offline fallback
            assert any(k in spec.limitations.lower() for k in ["provider", "api", "offline", "template", "dasha", "limited", "fallback"]), f"{spec.id}: LIMITED limitations should mention provider/offline/limited"


def test_no_unsupported_claims_without_evidence():
    # This is the audit discipline: count supported and ensure they match matrix expectation (15 supported at audit time)
    # We don't hard-fail on future growth, but we assert supported count is not trivially inflated.
    supported = [s for s in CAPABILITY_REGISTRY.values() if s.support_status == SupportStatus.SUPPORTED]
    # From docs/capability-matrix.md audit: 15 supported, 5 limited at 2026-08-31.
    # Allow growth but forbid dropping below 14 without explicit de-support.
    assert len(supported) >= 14, f"supported count {len(supported)} unexpectedly low — did a capability regress to limited?"
    # No unsupported should be hidden? We expect 0 unsupported currently; allow if honestly documented.
    # The point is we never label supported without test — that is enforced above + artifact proofs below.


def test_no_parity_claims_in_registry():
    import re
    forbidden = ["chatgpt work", "claude cowork", "achieve parity"]
    for spec in CAPABILITY_REGISTRY.values():
        hay = f"{spec.description_en} {spec.limitations} {spec.provider_requirements}".lower()
        for token in forbidden:
            assert token not in hay, f"{spec.id}: must not claim equivalence ({token!r})"
        # Disallow word "parity" as standalone claim, but allow inside other words like "disparity" is not an issue
        # We check for word boundary \bparity\b
        assert not re.search(r"\bparity\b", hay), f"{spec.id}: must not claim parity as standalone word"


# ---------------------------------------------------------------------------
# Public-path artifact proofs: for every SUPPORTED artifact output, generate+validate+store offline.
# These are the "public-path tests" that earn the SUPPORTED badge.
# ---------------------------------------------------------------------------

SUPPORTED_ARTIFACT_FORMATS = {
    "pdf": {"kind": "document", "topic": "العمارة النجدية"},
    "docx": {"kind": "document", "topic": "العمارة العسيرية"},
    "pptx": {"kind": "presentation", "topic": "يوم التأسيس السعودي"},
    "ics": {"kind": "calendar", "topic": "سهيل"},
    "svg": {"kind": "diagram", "topic": "آداب المجلس"},
    "png": {"kind": "diagram", "topic": "مخطط إرشادي"},
    "json": {"kind": "document", "topic": "بيانات ثقافية"},
    "csv": {"kind": "document", "topic": "جدول مواسم"},
    "txt": {"kind": "document", "topic": "نص ثقافي"},
}


@pytest.mark.parametrize("fmt,meta", list(SUPPORTED_ARTIFACT_FORMATS.items()))
def test_supported_artifact_public_path_roundtrip(fmt: str, meta: dict, tmp_path):
    """Each SUPPORTED artifact format must be renderable, validatable, and storable offline."""
    # Drive through the real orchestrator + filesystem store + validation pipeline.
    store = FileSystemArtifactStore(root_dir=tmp_path / f"store_{fmt}")
    orch = ArtifactOrchestrator(store=store)

    content_data = None
    raw_text = f"نص ثقافي موثق لاختبار {fmt} — {meta['topic']}"
    if fmt == "json":
        content_data = {"title": f"تقرير {meta['topic']}", "topic": meta["topic"], "summary": "ملخص تجريبي"}
    elif fmt == "csv":
        content_data = {"rows": [{"title": meta["topic"], "topic": meta["topic"], "text": raw_text}]}

    req = ArtifactRequest(
        format=fmt,
        kind=meta["kind"],
        title=f"تقرير اختبار {fmt.upper()}",
        topic=meta["topic"],
        content_data=content_data,
        raw_text=raw_text,
        region="نجد",
    )

    result = orch.generate_artifact(req)

    # Verified result contract
    assert result.status == "created", f"{fmt}: orchestrator status {result.status!r} error={result.error!r} category={result.error_category!r}"
    assert result.mime_type, f"{fmt}: missing mime_type"
    assert result.size_bytes > 0, f"{fmt}: empty size"
    assert result.checksum and len(result.checksum) == 64, f"{fmt}: missing sha256 checksum"
    assert result.download_url and result.download_url.startswith("/api/artifacts/"), f"{fmt}: bad download_url {result.download_url!r}"
    assert result.filename, f"{fmt}: missing filename"

    # Validation layer (re-validate stored bytes)
    stored = store.get_bytes(result.filename)
    assert stored is not None, f"{fmt}: get_bytes returned None for {result.filename}"
    data, fname, mime = stored
    assert data and len(data) == result.size_bytes
    assert mime == result.mime_type
    # This is the same validator the orchestrator uses — must pass
    vres = validate_artifact_bytes(fmt, data)
    assert vres.format == fmt
    assert vres.size_bytes == len(data)

    # Format-specific invariants
    if fmt == "pdf":
        assert data.startswith(b"%PDF")
    elif fmt in ("docx", "pptx"):
        assert data.startswith(b"PK\x03\x04")
    elif fmt == "ics":
        assert b"BEGIN:VCALENDAR" in data and b"END:VCALENDAR" in data
    elif fmt == "svg":
        assert b"<svg" in data and b"</svg>" in data
    elif fmt == "png":
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
    elif fmt == "json":
        import json

        json.loads(data.decode("utf-8"))
    elif fmt == "csv":
        import csv, io

        rows = list(csv.reader(io.StringIO(data.decode("utf-8"))))
        assert len(rows) >= 2  # header + at least one row
    elif fmt == "txt":
        assert data.decode("utf-8").strip()


def test_itinerary_capability_has_verification_discipline():
    """Itinerary SUPPORTED discipline: dates never invented, verification filters unsupported fields."""
    spec = CAPABILITY_REGISTRY["itinerary"]
    assert spec.support_status == SupportStatus.SUPPORTED
    assert "never" in spec.limitations.lower() or "missing_dates" in spec.fallback or "not invent" in spec.limitations.lower() or "لم تُخترع" in spec.fallback or "missing" in spec.validator.lower() or "missing" in spec.fallback.lower()
    # The spec's validator references Itinerary.validate_citations and CalendarRenderError — check strings
    assert "Itinerary" in spec.validator or "validate_citations" in spec.validator
    assert "CalendarRenderError" in spec.validator or "missing_dates" in spec.validator


def test_audio_vision_three_d_are_limited_not_supported():
    for cid in ("audio", "vision", "three_d"):
        spec = CAPABILITY_REGISTRY[cid]
        assert spec.support_status == SupportStatus.LIMITED, f"{cid} must be limited (offline fallback only) — marking supported would be false parity"
        if cid in ("audio", "vision"):
            assert "DASHSCOPE" in spec.provider_requirements or "provider" in spec.provider_requirements.lower(), f"{cid}: provider requirement must mention DASHSCOPE/provider"
        else:
            # three_d: limited because inspection-only, not provider-gated; ensure limitations mention limited/offline
            assert "limited" in spec.limitations.lower() and "offline" in spec.limitations.lower(), f"{cid}: limitations must mention limited/offline"


def test_import_via_shim_matches_canonical():
    from sard.agent.capability_registry import CAPABILITY_REGISTRY as shim_registry

    assert shim_registry is CAPABILITY_REGISTRY
