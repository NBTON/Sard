from __future__ import annotations

import time

from sard.agent.graph import GraphDependencies
from sard.application.contracts import UIExecutionMode, UIModeKind, UIRunRequest, UIRunResult
from sard.application.demo import HERO_QUERY, build_demo_result, load_precached_artifacts
from sard.application.service import SardApplicationService


class FailingGraph:
    def stream(self, state, stream_mode):
        raise ConnectionError("provider unavailable")
        yield state


class SlowGraph:
    def stream(self, state, stream_mode):
        time.sleep(0.2)
        yield state


def _request(run_id: str) -> UIRunRequest:
    return UIRunRequest(HERO_QUERY, run_id, execution_mode=UIExecutionMode.LIVE)


def test_packaged_cache_has_complete_download_bundle():
    artifacts = load_precached_artifacts()
    assert {item.artifact_type for item in artifacts} == {"raw_text", "pdf", "calendar"}
    assert all(item.creation_status == "created" and item.download_bytes for item in artifacts)


def test_live_failure_automatically_uses_visible_cached_fallback(tmp_path):
    service = SardApplicationService(
        GraphDependencies(output_root=str(tmp_path)),
        graph_builder=lambda _deps: FailingGraph(),
        cached_demo_provider=build_demo_result,
        auto_demo_fallback=True,
        fallback_timeout_seconds=1,
    )
    items = list(service.stream_run(_request("step8-failure")))
    result = next(item for item in items if isinstance(item, UIRunResult))
    assert result.mode.kind is UIModeKind.CACHED_DEMO
    assert result.graph_outcome == "completed"
    assert any("المحفوظة مسبقًا" in warning for warning in result.warnings)
    assert all(artifact.download_bytes for artifact in result.artifacts)


def test_live_timeout_automatically_uses_cached_fallback(tmp_path):
    service = SardApplicationService(
        GraphDependencies(output_root=str(tmp_path)),
        graph_builder=lambda _deps: SlowGraph(),
        cached_demo_provider=build_demo_result,
        auto_demo_fallback=True,
        fallback_timeout_seconds=0.05,
    )
    started = time.monotonic()
    items = list(service.stream_run(_request("step8-timeout")))
    elapsed = time.monotonic() - started
    result = next(item for item in items if isinstance(item, UIRunResult))
    assert elapsed < 0.18
    assert result.mode.kind is UIModeKind.CACHED_DEMO
    assert any("مهلة" in warning for warning in result.warnings)
