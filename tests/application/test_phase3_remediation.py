from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from importlib import import_module
from pathlib import Path

import pytest

from sard.agent.graph import GraphDependencies
from sard.application import SardApplicationService, UIExecutionMode, UIRunRequest
from sard.application.demo import HERO_QUERY, build_demo_result
from tests.application.test_service import FakeGraph, GraphBuilder, _final_updates
from tests.helpers.graph_harness import render_deps, render_itinerary, render_state


def test_concurrent_live_and_demo_pdfs_share_root_lock_and_restore_environment(
    monkeypatch, tmp_path
):
    import sard.application.demo as demo

    live_render = import_module("sard.agent.nodes.render")
    original = "sentinel-pdf-root"
    monkeypatch.setenv("SARD_PDF_OUTPUT_ROOT", original)
    real_render = demo.render_pdf
    active = 0
    maximum = 0
    observed_roots: list[tuple[Path, Path]] = []
    guard = threading.Lock()

    def slow_render(itinerary, output_path):
        nonlocal active, maximum
        expected = Path(output_path).parent.resolve()
        actual = Path(os.environ["SARD_PDF_OUTPUT_ROOT"]).resolve()
        with guard:
            active += 1
            maximum = max(maximum, active)
            observed_roots.append((expected, actual))
        try:
            time.sleep(0.04)
            return real_render(itinerary, output_path)
        finally:
            with guard:
                active -= 1

    monkeypatch.setattr(demo, "render_pdf", slow_render)
    monkeypatch.setattr(live_render, "render_pdf", slow_render)

    def run_demo():
        request = UIRunRequest(
            HERO_QUERY,
            "concurrent-demo",
            execution_mode=UIExecutionMode.CACHED_DEMO,
        )
        return build_demo_result(request, output_root=tmp_path / "demo")

    def run_live():
        return live_render.render(
            render_state(render_itinerary(), run_id="concurrent-live"),
            render_deps(tmp_path / "live"),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        demo_future = pool.submit(run_demo)
        live_future = pool.submit(run_live)
        demo_result = demo_future.result()
        live_result = live_future.result()

    assert maximum == 1
    assert observed_roots and all(expected == actual for expected, actual in observed_roots)
    assert os.environ["SARD_PDF_OUTPUT_ROOT"] == original
    assert next(
        item for item in demo_result.artifacts if item.artifact_type == "pdf"
    ).creation_status == "created"
    assert next(
        item for item in live_result["rendered_artifacts"] if item.artifact_type == "pdf"
    ).creation_status == "created"


def test_source_projection_fails_closed_for_signed_and_token_urls(tmp_path):
    malicious = (
        "https://example.org/private/nvapi-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA/document?view=1",
        "https://example.org/private/bearer-abc123/document",
        "https://example.org/private/token-abc123/document",
        "https://example.org/doc?sig=abcdef",
        "https://example.org/doc?X-Amz-Signature=abcdef",
        "https://example.org/doc?sv=1&SharedAccessSignature=abcdef",
        "https://user:password@example.org/doc",
        "https://example.org/doc#token-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "https://example.org/doc/aB9xQ2mN7pL4vR8sT1uW5yZ0cD3fG6hJ",
        "https://example.org/doc/qazwsxedcrfvtgbyhnujmikolpabcdefgh",
    )
    for index, url in enumerate(malicious):
        root = tmp_path / str(index)
        updates = _final_updates(root)
        itinerary = updates["itinerary"]
        first = replace(itinerary.sources[0], url=url)
        itinerary = replace(itinerary, sources=(first, *itinerary.sources[1:]))
        updates["sources"] = list(itinerary.sources)
        updates["itinerary"] = itinerary
        graph = FakeGraph(updates)
        service = SardApplicationService(
            GraphDependencies(output_root=str(root)), graph_builder=GraphBuilder(graph)
        )
        result = service.run(UIRunRequest("خطة", f"unsafe-url-{index}"))
        assert result.sources[0].url == ""


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/guide?lang=ar&view=1#section",
        "https://example.org/saudi-arabia-cultural-heritage-guide?lang=ar",
        "https://example.org/articles/understanding_saudi_heritage_sites",
    ],
)
def test_source_projection_preserves_safe_ordinary_and_long_slug_urls(tmp_path, url):
    updates = _final_updates(tmp_path)
    itinerary = updates["itinerary"]
    safe_source = replace(itinerary.sources[0], url=url)
    itinerary = replace(itinerary, sources=(safe_source, *itinerary.sources[1:]))
    updates["sources"] = list(itinerary.sources)
    updates["itinerary"] = itinerary
    service = SardApplicationService(
        GraphDependencies(output_root=str(tmp_path)),
        graph_builder=GraphBuilder(FakeGraph(updates)),
    )
    result = service.run(UIRunRequest("خطة", "safe-url"))
    assert result.sources[0].url == url


@pytest.mark.parametrize(
    "trip_dates",
    [
        (date(2027, 5, 20),),
        (date(2027, 5, 21), date(2027, 5, 20)),
    ],
)
def test_invalid_explicit_demo_dates_fail_truthfully_without_padded_default(
    tmp_path, trip_dates
):
    service = SardApplicationService(
        GraphDependencies(output_root=str(tmp_path)), cached_demo_provider=build_demo_result
    )
    result = service.run(
        UIRunRequest(
            HERO_QUERY,
            "one-date-demo",
            trip_dates=trip_dates,
            execution_mode=UIExecutionMode.CACHED_DEMO,
        )
    )
    assert result.graph_outcome == "failed"
    assert result.itinerary is None
    assert result.artifacts == ()
    assert "2026-11-01" not in repr(result)
