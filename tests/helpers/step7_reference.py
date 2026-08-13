"""Spec-derived reference projections and service semantics for Step 7.

These are test-side implementations of the *pure* logic the frozen contract
requires.  They never call the graph, RAG, or a model; the calendar-after-date
reference path calls only the existing offline ``render_calendar`` and
``ArtifactManager``.  When Agent A's ``sard.application.service`` and Agent B's
``sard.ui.presentation`` land, integration should reconcile these behaviors;
the functions here document the expected shapes and edge rules.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional

from sard.agent.events import SAFE_EVENT_KINDS, SafeEvent
from sard.agent.state import GraphOutcome, RAGMode
from sard.outputs.artifacts import ArtifactManager
from sard.outputs.calendar import render_calendar
from sard.outputs.schemas import CitationSource, Itinerary
from sard.outputs.sample import representative_fixture

from tests.helpers.step7_contracts import (
    CalendarAfterDateRequest,
    UIArtifactView,
    UIExecutionMode,
    UIModeKind,
    UIModeStatus,
    UIModelRoute,
    UIProgressEvent,
    UIProgressState,
    UIRunRequest,
    UIRunResult,
    UIStage,
    UISourceView,
)


# ---------------------------------------------------------------------------
# Progress mapping (frozen UI state table)
# ---------------------------------------------------------------------------

_NODE_STAGES = frozenset(stage.value for stage in UIStage)

_DEGRADED_RETRIEVAL_MODES = frozenset(
    {RAGMode.DENSE_ONLY.value, RAGMode.FULL_TEXT_ONLY.value, RAGMode.UNAVAILABLE.value}
)


def ui_stage_for_node(node: str, kind: str) -> UIStage:
    """Map a SafeEvent node to a UI stage; the pipeline waiting event is
    understood as the initial ``understand`` stage.

    Unknown nodes are rejected, never displayed.
    """
    if kind == "waiting" and node == "pipeline":
        return UIStage.UNDERSTAND
    try:
        return UIStage(node)
    except ValueError:
        raise ValueError(f"Unknown UI stage for node: {node!r}") from None


def map_progress_state(kind: str, status: str, degraded: bool = False) -> UIProgressState:
    """Frozen table: existing event -> UI state."""
    if kind == "waiting":
        return UIProgressState.WAITING
    if kind == "started":
        return UIProgressState.ACTIVE
    if kind == "completed":
        return UIProgressState.COMPLETED
    if kind == "retried":
        return UIProgressState.RETRIED
    if kind in ("degraded", "model_fallback_activated"):
        return UIProgressState.DEGRADED
    if kind == "retrieval_mode_changed":
        return (
            UIProgressState.DEGRADED
            if status in _DEGRADED_RETRIEVAL_MODES
            else UIProgressState.COMPLETED
        )
    if kind == "failed":
        return UIProgressState.FAILED
    if kind == "graph_completed":
        if status == GraphOutcome.PARTIAL.value:
            return UIProgressState.PARTIALLY_COMPLETED
        if status == GraphOutcome.FAILED.value:
            return UIProgressState.FAILED
        return UIProgressState.COMPLETED
    if kind == "citation_coverage_calculated":
        return UIProgressState.COMPLETED
    raise ValueError(f"Unknown event kind: {kind!r}")


def project_progress_event(safe: SafeEvent, sequence: int) -> UIProgressEvent:
    """Allowlisted projection of a SafeEvent into the frozen UI contract."""
    if safe.kind not in SAFE_EVENT_KINDS:
        raise ValueError(f"Unknown safe event kind: {safe.kind!r}")
    return UIProgressEvent(
        sequence=sequence,
        run_id=safe.run,
        stage=ui_stage_for_node(safe.node, safe.kind),
        state=map_progress_state(safe.kind, safe.status, bool(safe.degraded)),
        event_kind=safe.kind,
        timestamp=safe.timestamp,
        summary=safe.summary,
        duration_ms=safe.duration_ms,
        source_count=safe.source_count,
        coverage=safe.coverage,
        retry_count=safe.retry,
        degraded=bool(safe.degraded),
        simulated=False,
    )


# ---------------------------------------------------------------------------
# Mode projection (precedence cached_demo > unavailable > model_fallback >
# degraded_retrieval > live)
# ---------------------------------------------------------------------------


def project_mode_kind(
    *,
    execution_mode: UIExecutionMode,
    retrieval_mode: str,
    model_fallback_used: bool,
) -> UIModeKind:
    if execution_mode == UIExecutionMode.CACHED_DEMO:
        return UIModeKind.CACHED_DEMO
    if retrieval_mode == RAGMode.UNAVAILABLE.value:
        return UIModeKind.UNAVAILABLE
    if model_fallback_used:
        return UIModeKind.MODEL_FALLBACK
    if retrieval_mode in _DEGRADED_RETRIEVAL_MODES:
        return UIModeKind.DEGRADED_RETRIEVAL
    return UIModeKind.LIVE


def project_model_routes(model_routes: dict, fallback_events=()) -> tuple[UIModelRoute, ...]:
    """Expose only allowlisted resolved models; attribute real fallback usage.

    ``model_routes`` is the graph's merged ``{use_case: model_id | dict}`` map;
    ``fallback_events`` are the graph-safe ``SafeFallbackEvent`` records.  A use
    case is marked ``used_fallback`` only when a recorded attempt resolved to a
    different (degraded) model.
    """
    resolved: dict[str, str] = {}
    for use_case, value in (model_routes or {}).items():
        if isinstance(value, dict):
            model = value.get("generation")
        else:
            model = value
        if model:
            resolved[str(use_case)] = str(model)

    fallback_used: set[str] = set()
    for event in fallback_events or ():
        if getattr(event, "outcome", "") != "success":
            continue
        degraded = bool(getattr(event, "degraded", False))
        selected = getattr(event, "selected_fallback", None)
        if degraded or (selected and selected != "primary"):
            fallback_used.add(str(getattr(event, "use_case", "")))

    return tuple(
        UIModelRoute(
            use_case=use_case,
            resolved_model=resolved[use_case],
            used_fallback=use_case in fallback_used,
        )
        for use_case in sorted(resolved)
    )


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def build_source_view(source: CitationSource) -> UISourceView:
    """Project one final verified CitationSource; completeness is a label,
    never a trust score, and no source-name field is fabricated."""
    complete = (
        source.page is not None and bool((source.section or "").strip()) and source.publication_date is not None
    )
    return UISourceView(
        citation_id=source.citation_id,
        title=source.title,
        url=source.url,
        page=source.page,
        section=source.section,
        publication_date=source.publication_date,
        metadata_complete=complete,
        citation_verified=True,
    )


# ---------------------------------------------------------------------------
# URL sanitization and HTML escaping (pure presentation helpers)
# ---------------------------------------------------------------------------

_URL_SCHEME_RE = re.compile(r"^\s*([a-zA-Z][a-zA-Z0-9+.-]*)\s*:", re.I)
_DISALLOWED_SCHEMES = frozenset({"javascript", "data", "vbscript", "file"})
_MAX_URL_LENGTH = 512

_HTML_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}


def sanitize_source_url(url: str) -> str:
    """Strip control chars, reject active schemes, and bound length.

    Returns ``""`` for unsafe/blank URLs so the UI can drop the link.
    """
    if not url or not isinstance(url, str):
        return ""
    value = re.sub(r"[\x00-\x1f\x7f]", "", url).strip()
    if not value:
        return ""
    match = _URL_SCHEME_RE.match(value)
    if match and match.group(1).lower() in _DISALLOWED_SCHEMES:
        return ""
    if len(value) > _MAX_URL_LENGTH:
        value = value[:_MAX_URL_LENGTH]
    return value


def escape_html(text: str) -> str:
    """Escape the five HTML metacharacters; Arabic and digits pass through."""
    if not text:
        return ""
    return "".join(_HTML_ESCAPES.get(character, character) for character in text)


# ---------------------------------------------------------------------------
# Calendar-after-date deterministic sub-run ID
# ---------------------------------------------------------------------------

_SAFE_RUN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_CALENDAR_SUFFIX_LENGTH = len("-calendar-") + 12


def calendar_sub_run_id(run_id: str, dates, preview: bool = False) -> str:
    """Deterministic safe sub-run id ``<run_id>-calendar-<sha256(iso_dates+preview)[:12]>``.

    The id must satisfy ``ArtifactManager``'s safe ASCII rule; the run-id
    segment is bounded so the total never exceeds 80 characters.
    """
    iso = "|".join(d.isoformat() for d in (dates or ()))
    digest = hashlib.sha256(f"{iso}|preview={int(bool(preview))}".encode("utf-8")).hexdigest()[:12]
    candidate = f"{run_id}-calendar-{digest}"
    if len(candidate) > 80:
        bounded = run_id[: 80 - _CALENDAR_SUFFIX_LENGTH]
        candidate = f"{bounded}-calendar-{digest}"
    return candidate


# ---------------------------------------------------------------------------
# Artifact-button states
# ---------------------------------------------------------------------------


def artifact_button_state(view: UIArtifactView) -> tuple[str, bool]:
    """Return ``(label_key, download_enabled)``; only ``created`` may download."""
    if view.creation_status == "created":
        return "download", True
    if view.creation_status == "skipped":
        return "skipped", False
    return "failed", False


def download_payload(
    view: UIArtifactView,
    resolved_path: Optional[str | Path] = None,
    output_root: Optional[str | Path] = None,
) -> Optional[bytes]:
    """Bytes are available only for safe created artifacts under the root."""
    if view.creation_status != "created" or not resolved_path:
        return None
    path = Path(resolved_path).resolve()
    if output_root is not None and not path.is_relative_to(Path(output_root).resolve()):
        return None
    if not path.is_file():
        return None
    return path.read_bytes()


# ---------------------------------------------------------------------------
# Fixture selection
# ---------------------------------------------------------------------------

DEMO_CITATION_ID = "CIT-DEMO01"


def select_demo_fixture(execution_mode: UIExecutionMode) -> Optional[str]:
    """Explicit demo mode picks the offline fixture; live mode never does."""
    if execution_mode == UIExecutionMode.CACHED_DEMO:
        return DEMO_CITATION_ID
    return None


# ---------------------------------------------------------------------------
# Sanitized user-facing errors
# ---------------------------------------------------------------------------


def ui_error_message(errors, outcome: str) -> str:
    """Generic sanitized Arabic message; internal records never cross."""
    if outcome == GraphOutcome.COMPLETED.value or not errors:
        return ""
    return "حدث خطأ أثناء معالجة طلبك؛ يرجى المحاولة مرة أخرى."


# ---------------------------------------------------------------------------
# Result assembly from a real final graph state
# ---------------------------------------------------------------------------


def build_result_from_state(request: UIRunRequest, state: dict) -> UIRunResult:
    progress = tuple(
        project_progress_event(event, sequence)
        for sequence, event in enumerate(state.get("progress_events") or [])
    )
    sources = tuple(build_source_view(source) for source in state.get("sources") or ())
    retrieval_mode = str(state.get("retrieval_mode") or "")
    model_fallback_used = bool(state.get("model_fallback_used"))
    mode = UIModeStatus(
        kind=project_mode_kind(
            execution_mode=request.execution_mode,
            retrieval_mode=retrieval_mode,
            model_fallback_used=model_fallback_used,
        ),
        retrieval_mode=retrieval_mode,
        model_fallback_used=model_fallback_used,
        execution_mode=request.execution_mode,
        model_routes=project_model_routes(
            state.get("model_routes") or {}, state.get("fallback_events") or ()
        ),
    )
    artifacts = tuple(
        UIArtifactView(
            artifact_type=item.artifact_type,
            display_label=item.display_label,
            filename=item.filename,
            mime_type=item.mime_type,
            size_bytes=item.size_bytes,
            checksum=item.checksum,
            creation_status=item.creation_status,
            warnings=tuple(item.warnings),
            error_category=item.error_category,
            download_bytes=None,
        )
        for item in state.get("rendered_artifacts") or ()
    )
    coverage = state.get("coverage")
    return UIRunResult(
        run_id=request.run_id,
        final_answer=str(state.get("final_answer") or ""),
        graph_outcome=str(state.get("graph_outcome") or ""),
        mode=mode,
        sources=sources,
        itinerary=state.get("itinerary"),
        artifacts=artifacts,
        progress_events=progress,
        coverage_ratio=getattr(coverage, "coverage_ratio", None),
        warnings=tuple(state.get("warnings") or ()),
        error_message=ui_error_message(
            state.get("errors") or (), str(state.get("graph_outcome") or "")
        ),
    )


# ---------------------------------------------------------------------------
# Reference application service: idempotency, duplicate prevention, session
# isolation, calendar-after-date.  Test-only model of SardApplicationService.
# ---------------------------------------------------------------------------


class DuplicateRunError(Exception):
    """A second execution for an already-executing run id."""


class UnknownRunError(Exception):
    category = "unknown_run"


class IncompleteRunError(Exception):
    category = "incomplete_run"


class MissingCalendarDatesError(Exception):
    category = "missing_dates"


class _CompletedRunSnapshot:
    __slots__ = ("result",)

    def __init__(self, result: UIRunResult):
        self.result = result


class ReferenceApplicationService:
    """Mirrors the frozen service boundary without real provider calls.

    ``live_runner`` must be a ``callable(UIRunRequest) -> final graph state
    dict`` (the offline harness).  Demo runs never touch it.
    """

    def __init__(
        self,
        *,
        live_runner: Optional[Callable[[UIRunRequest], dict]] = None,
        output_root: Optional[str | Path] = None,
        demo_itinerary: Optional[Itinerary] = None,
    ) -> None:
        self._live_runner = live_runner
        self._output_root = Path(output_root).resolve() if output_root else None
        self._demo_itinerary = demo_itinerary
        self._snapshots: dict[str, _CompletedRunSnapshot] = {}
        self._inflight: set[str] = set()
        self._calendar_cache: dict[tuple, UIArtifactView] = {}
        self.graph_invocations: dict[str, int] = {}

    def run(self, request: UIRunRequest) -> UIRunResult:
        if request.run_id in self._snapshots:
            return self._snapshots[request.run_id].result
        if request.run_id in self._inflight:
            raise DuplicateRunError(request.run_id)
        self._inflight.add(request.run_id)
        try:
            if request.execution_mode == UIExecutionMode.CACHED_DEMO:
                result = self._demo_result(request)
            else:
                result = self._live_result(request)
            self._snapshots[request.run_id] = _CompletedRunSnapshot(result)
            return result
        finally:
            self._inflight.discard(request.run_id)

    def _demo_result(self, request: UIRunRequest) -> UIRunResult:
        itinerary = self._demo_itinerary or representative_fixture()
        if request.trip_dates:
            itinerary = replace(itinerary, explicit_dates=tuple(request.trip_dates))
        sources = tuple(build_source_view(source) for source in itinerary.sources)
        progress = (
            UIProgressEvent(0, request.run_id, UIStage.UNDERSTAND, UIProgressState.WAITING, "waiting", "", simulated=True),
            UIProgressEvent(1, request.run_id, UIStage.RENDER, UIProgressState.COMPLETED, "graph_completed", "", simulated=True),
        )
        mode = UIModeStatus(
            kind=UIModeKind.CACHED_DEMO,
            retrieval_mode=RAGMode.UNAVAILABLE.value,
            model_fallback_used=False,
            execution_mode=request.execution_mode,
        )
        return UIRunResult(
            run_id=request.run_id,
            final_answer=itinerary.summary,
            graph_outcome=GraphOutcome.COMPLETED.value,
            mode=mode,
            sources=sources,
            itinerary=itinerary,
            artifacts=(),
            progress_events=progress,
            coverage_ratio=1.0,
        )

    def _live_result(self, request: UIRunRequest) -> UIRunResult:
        if self._live_runner is None:
            raise RuntimeError("No live runner configured for reference service.")
        self.graph_invocations[request.run_id] = self.graph_invocations.get(request.run_id, 0) + 1
        state = self._live_runner(request)
        return build_result_from_state(request, state)

    def create_calendar_after_dates(self, request: CalendarAfterDateRequest) -> UIArtifactView:
        snapshot = self._snapshots.get(request.run_id)
        if snapshot is None:
            raise UnknownRunError(request.run_id)
        if not request.dates:
            raise MissingCalendarDatesError("At least one explicit date is required.")
        if snapshot.result.itinerary is None:
            raise IncompleteRunError("A verified itinerary is required.")
        cache_key = (request.run_id, tuple(request.dates), request.preview)
        cached = self._calendar_cache.get(cache_key)
        if cached is not None:
            return cached

        itinerary = replace(snapshot.result.itinerary, explicit_dates=tuple(request.dates))
        calendar = render_calendar(itinerary, preview=request.preview)
        sub_run = calendar_sub_run_id(request.run_id, request.dates, request.preview)
        if self._output_root is None:
            raise RuntimeError("Reference service requires output_root for calendar-after-date.")
        manager = ArtifactManager(self._output_root, sub_run)
        written = manager.write_bytes(
            calendar.data,
            filename="itinerary.ics",
            artifact_type="calendar",
            display_label="تقويم الرحلة",
            mime_type="text/calendar; charset=utf-8",
            warnings=tuple(calendar.warnings),
        )
        view = UIArtifactView(
            artifact_type=written.artifact_type,
            display_label=written.display_label,
            filename=written.filename,
            mime_type=written.mime_type,
            size_bytes=written.size_bytes,
            checksum=written.checksum,
            creation_status=written.creation_status,
            warnings=tuple(written.warnings),
            error_category=written.error_category,
            download_bytes=Path(written.absolute_path).read_bytes(),
        )
        self._calendar_cache[cache_key] = view
        return view
