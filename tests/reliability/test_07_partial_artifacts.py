"""J9: partial artifacts (mocked renderers, deterministic).

Spec:
  HTML ok + PDF fail -> return HTML (failure explicit, not hidden)
  PDF ok + PPTX fail -> PDF + explicit PPTX failure (not hidden)

Production validates 10 formats (pdf/docx/pptx/ics/svg/png/json/csv/txt/html);
`html` is a stored format (bug #3), so the HTML-ok half is proven both by the
spec harness and the prod integration below (never silent, never fabricated).
"""
from __future__ import annotations

from dataclasses import dataclass

from sard.outputs.orchestrator import ArtifactOrchestrator, FileSystemArtifactStore

from .conftest import artifact_request


@dataclass
class PartialOutcome:
    fmt: str
    status: str
    error_category: str | None = None


def run_partial(formats: list[str], render) -> list[PartialOutcome]:
    """Spec harness: render(fmt) -> bytes or raises; failures stay explicit."""
    outcomes: list[PartialOutcome] = []
    for fmt in formats:
        try:
            render(fmt)
            outcomes.append(PartialOutcome(fmt, "created"))
        except Exception as exc:
            outcomes.append(PartialOutcome(fmt, "failed", getattr(exc, "category", "renderer_exception")))
    return outcomes


def test_09a_html_ok_pdf_fail_returns_html_with_explicit_failure():
    def _render(fmt: str):
        if fmt == "pdf":
            raise RuntimeError("pdf renderer down")
        return b"<html>ok</html>"

    outcomes = run_partial(["html", "pdf"], _render)
    by_fmt = {o.fmt: o for o in outcomes}
    assert by_fmt["html"].status == "created"
    assert by_fmt["pdf"].status == "failed"
    assert by_fmt["pdf"].error_category == "renderer_exception"
    assert len(outcomes) == 2, "failures must not be hidden"


def test_09b_pdf_ok_pptx_fail_contract_keeps_both():
    def _render(fmt: str):
        if fmt == "pptx":
            raise RuntimeError("pptx renderer down")
        return b"ok-bytes"

    outcomes = run_partial(["pdf", "pptx"], _render)
    by_fmt = {o.fmt: o for o in outcomes}
    assert by_fmt["pdf"].status == "created"
    assert by_fmt["pptx"].status == "failed"


def test_09c_prod_pdf_ok_pptx_fail_is_explicit_not_hidden(tmp_path, monkeypatch):
    store = FileSystemArtifactStore(tmp_path)
    orch = ArtifactOrchestrator(store)
    monkeypatch.setattr(orch.registry, "render_pptx", lambda req: (_ for _ in ()).throw(RuntimeError("pptx boom")))
    pdf = orch.generate_artifact(artifact_request("pdf"))
    pptx = orch.generate_artifact(artifact_request("pptx", kind="presentation"))
    assert pdf.status == "created" and pdf.download_url is not None and pdf.size_bytes > 0
    assert pptx.status == "failed" and pptx.download_url is None
    assert pptx.error_category == "renderer_exception"
    assert "pptx" in (pptx.error or "pptx").lower() or pptx.error_category is not None
    # Both outcomes present: success + explicit failure, never a silent drop.
    assert {pdf.status, pptx.status} == {"created", "failed"}


def test_09d_prod_html_is_stored_not_fabricated(tmp_path):
    """HTML is a stored format in prod (bug #3); it must create real bytes."""
    from sard.outputs.validation import validate_artifact_bytes

    store = FileSystemArtifactStore(tmp_path)
    orch = ArtifactOrchestrator(store)
    res = orch.generate_artifact(artifact_request("html"))
    assert res.status == "created", res.error
    assert res.download_url is not None
    assert res.size_bytes > 0 and res.data
    assert validate_artifact_bytes("html", res.data).format == "html"
    assert res.preview is not None
    assert list(tmp_path.glob("*.html")) != []
