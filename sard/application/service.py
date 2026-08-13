"""Session-scoped application service consumed by the Streamlit layer."""

from __future__ import annotations

import hashlib
import math
import os
import re
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Generator, Iterator, Optional

from sard.agent.events import EVENT_WAITING, make_event
from sard.agent.graph import GraphDependencies, build_graph, default_dependencies
from sard.agent.state import RAGMode, initial_state
from sard.application.contracts import (
    CalendarAfterDateRequest,
    UIArtifactView,
    UIExecutionMode,
    UIModeKind,
    UIModeStatus,
    UIModelRoute,
    UIProgressEvent,
    UIRunRequest,
    UIRunResult,
    UISourceView,
)
from sard.application.events import adapt_safe_event, sanitize_ui_text
from sard.outputs.artifacts import (
    DEFAULT_OUTPUT_ROOT,
    ArtifactManager,
    failed_artifact,
    skipped_artifact,
)
from sard.outputs.calendar import CalendarRenderError, MIME_TYPE as CALENDAR_MIME_TYPE, render_calendar
from sard.outputs.schemas import CitationSource, Itinerary
from sard.url_policy import safe_external_url


_GRAPH_OUTCOMES = frozenset({"completed", "partial", "failed"})
_RETRIEVAL_MODES = frozenset(item.value for item in RAGMode)
_DEGRADED_RETRIEVAL_MODES = frozenset({"hybrid_fused", "dense_only", "full_text_only"})
_ARTIFACT_STATUSES = frozenset({"created", "skipped", "failed"})
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_SAFE_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")


class ApplicationServiceError(RuntimeError):
    """Application-boundary error with a stable category and safe message."""

    def __init__(self, category: str, safe_message: str):
        super().__init__(safe_message)
        self.category = category
        self.safe_message = safe_message


@dataclass(frozen=True)
class _CompletedRunSnapshot:
    result: UIRunResult
    itinerary: Optional[Itinerary]
    sources: tuple[UISourceView, ...]
    artifacts: tuple[UIArtifactView, ...]


class SardApplicationService:
    """The only graph/output boundary a presentation layer needs.

    The instance owns run idempotency and retained verified snapshots, so it
    should live in one Streamlit session. ``cached_demo_provider`` is an
    optional offline factory owned by the demo module; cached-demo requests
    never fall through to live dependencies.
    """

    def __init__(
        self,
        dependencies: Optional[GraphDependencies] = None,
        *,
        graph_builder: Callable[[GraphDependencies], object] = build_graph,
        cached_demo_provider: Optional[Callable[[UIRunRequest], UIRunResult]] = None,
    ) -> None:
        # Resolve live dependencies only when a live run actually starts.  An
        # explicitly cached-demo request must not open RAG or initialize model
        # routing merely because the session service was constructed.
        self._dependencies = dependencies
        root_value = (
            getattr(dependencies, "output_root", None)
            or os.environ.get("SARD_OUTPUT_ROOT")
            or DEFAULT_OUTPUT_ROOT
        )
        self._output_root = Path(root_value).expanduser().resolve()
        self._render_checksums = bool(
            getattr(dependencies, "render_checksums", False)
        )
        self._graph_builder = graph_builder
        self._cached_demo_provider = cached_demo_provider
        self._lock = threading.RLock()
        self._calendar_generation_lock = threading.Lock()
        self._started_run_ids: set[str] = set()
        self._active_run_ids: set[str] = set()
        self._completed: dict[str, _CompletedRunSnapshot] = {}
        self._calendar_cache: dict[
            tuple[str, tuple[object, ...], bool], UIArtifactView
        ] = {}

    def stream_run(
        self, request: UIRunRequest
    ) -> Iterator[UIProgressEvent | UIRunResult]:
        """Yield safe progress and exactly one terminal result for a new run."""

        if not isinstance(request, UIRunRequest):
            raise TypeError("request must be UIRunRequest")
        cached_result: Optional[UIRunResult] = None
        with self._lock:
            completed = self._completed.get(request.run_id)
            if completed is not None:
                cached_result = completed.result
            elif request.run_id in self._started_run_ids:
                raise ApplicationServiceError(
                    "duplicate_run",
                    "معرّف التشغيل مستخدم بالفعل؛ استخدم معرّفًا جديدًا لإعادة المحاولة.",
                )
            else:
                self._started_run_ids.add(request.run_id)
                self._active_run_ids.add(request.run_id)
        if cached_result is not None:
            yield cached_result
            return

        progress: list[UIProgressEvent] = []
        try:
            if request.execution_mode is UIExecutionMode.CACHED_DEMO:
                result = self._run_cached_demo(request)
                for event in result.progress_events:
                    progress.append(event)
                    yield event
            else:
                result, progress = yield from self._stream_live(request)
            snapshot = self._snapshot(result)
            with self._lock:
                self._completed[request.run_id] = snapshot
            yield result
        finally:
            with self._lock:
                self._active_run_ids.discard(request.run_id)

    def run(
        self,
        request: UIRunRequest,
        on_progress: Optional[Callable[[UIProgressEvent], None]] = None,
    ) -> UIRunResult:
        """Consume :meth:`stream_run`, optionally forwarding progress events."""

        terminal: Optional[UIRunResult] = None
        for item in self.stream_run(request):
            if isinstance(item, UIProgressEvent):
                if on_progress is not None:
                    on_progress(item)
            elif isinstance(item, UIRunResult):
                if terminal is not None:
                    raise ApplicationServiceError(
                        "invalid_stream", "تعذّر إكمال التشغيل بأمان."
                    )
                terminal = item
        if terminal is None:
            raise ApplicationServiceError("invalid_stream", "تعذّر إكمال التشغيل بأمان.")
        return terminal

    def create_calendar_after_dates(
        self, request: CalendarAfterDateRequest
    ) -> UIArtifactView:
        """Render one calendar from a retained verified itinerary only."""

        if not isinstance(request, CalendarAfterDateRequest):
            raise TypeError("request must be CalendarAfterDateRequest")
        with self._calendar_generation_lock:
            return self._create_calendar_after_dates_serialized(request)

    def _create_calendar_after_dates_serialized(
        self, request: CalendarAfterDateRequest
    ) -> UIArtifactView:
        if not request.dates:
            raise ApplicationServiceError(
                "missing_dates", "يلزم إدخال تاريخ صريح واحد على الأقل لإنشاء التقويم."
            )
        cache_key = (request.run_id, request.dates, request.preview)
        with self._lock:
            cached = self._calendar_cache.get(cache_key)
            if cached is not None:
                return cached
            snapshot = self._completed.get(request.run_id)
        if snapshot is None:
            raise ApplicationServiceError(
                "unknown_run", "لا توجد نتيجة مكتملة بهذا المعرّف في الجلسة الحالية."
            )
        if snapshot.itinerary is None:
            raise ApplicationServiceError(
                "no_verified_itinerary", "لا يتوفر برنامج رحلة موثق لإنشاء التقويم."
            )

        itinerary = replace(snapshot.itinerary, explicit_dates=request.dates)
        digest_input = "|".join(
            (*(value.isoformat() for value in request.dates), str(int(request.preview)))
        )
        digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:12]
        suffix = f"-calendar-{digest}"
        base = request.run_id[: 80 - len(suffix)].rstrip("._-") or "run"
        sub_run_id = base + suffix

        try:
            calendar = render_calendar(itinerary, preview=request.preview)
            manager = ArtifactManager(
                self._output_root,
                sub_run_id,
                checksums=self._render_checksums,
            )
            published = manager.write_bytes(
                calendar.data,
                filename="itinerary.ics",
                artifact_type="calendar",
                display_label="تقويم الرحلة",
                mime_type=CALENDAR_MIME_TYPE,
                warnings=calendar.warnings,
            )
        except CalendarRenderError as exc:
            published = skipped_artifact(
                artifact_type="calendar",
                display_label="تقويم الرحلة",
                filename="itinerary.ics",
                mime_type=CALENDAR_MIME_TYPE,
                category=_safe_category(exc.category),
                warning="تعذر إنشاء تقويم صالح من التواريخ والمحطات المتاحة.",
            )
        except Exception:
            published = failed_artifact(
                artifact_type="calendar",
                display_label="تقويم الرحلة",
                filename="itinerary.ics",
                mime_type=CALENDAR_MIME_TYPE,
                category="calendar_publication",
                warning="تعذر نشر ملف التقويم بأمان.",
            )
        view = self._artifact_view(published)
        if view is None:
            view = UIArtifactView(
                artifact_type="calendar",
                display_label="تقويم الرحلة",
                filename="itinerary.ics",
                mime_type=CALENDAR_MIME_TYPE,
                size_bytes=0,
                checksum=None,
                creation_status="failed",
                warnings=("تعذر نشر ملف التقويم بأمان.",),
                error_category="calendar_publication",
            )
        with self._lock:
            existing = self._calendar_cache.setdefault(cache_key, view)
        return existing

    def _stream_live(
        self, request: UIRunRequest
    ) -> Generator[UIProgressEvent, None, tuple[UIRunResult, list[UIProgressEvent]]]:
        progress: list[UIProgressEvent] = []
        effective_request = _effective_request(request)
        caller_dates = tuple(value.isoformat() for value in request.trip_dates)
        base_dependencies = self._dependencies
        if base_dependencies is None:
            base_dependencies = default_dependencies(open_rag=True)
            self._dependencies = base_dependencies
        deps = replace(
            base_dependencies,
            render_artifacts=request.render_artifacts,
            output_root=str(self._output_root),
            caller_dates=caller_dates,
        )
        state = initial_state(
            effective_request,
            run_id=request.run_id,
            compose_max_retries=deps.compose_max_retries,
        )
        state["caller_dates"] = list(caller_dates)
        state["preview_calendar"] = deps.preview_calendar
        state["output_root"] = str(self._output_root)
        state["render_checksums"] = deps.render_checksums
        state["progress_events"] = [
            make_event(
                EVENT_WAITING,
                request.run_id,
                "pipeline",
                "waiting",
                summary="في الانتظار",
            )
        ]
        final_state = state
        seen_event_count = 0
        try:
            graph = self._graph_builder(deps)
            for value in graph.stream(state, stream_mode="values"):
                if not isinstance(value, dict):
                    continue
                final_state = value
                raw_events = list(value.get("progress_events") or ())
                if len(raw_events) < seen_event_count:
                    seen_event_count = 0
                for raw_event in raw_events[seen_event_count:]:
                    try:
                        adapted = adapt_safe_event(
                            raw_event,
                            sequence=len(progress),
                            expected_run_id=request.run_id,
                            graph_outcome=value.get("graph_outcome"),
                        )
                    except (TypeError, ValueError):
                        continue
                    progress.append(adapted)
                    yield adapted
                seen_event_count = len(raw_events)
            result = self._result_from_state(request, final_state, progress)
        except Exception:
            result = self._failed_result(request, progress)
        return result, progress

    def _run_cached_demo(self, request: UIRunRequest) -> UIRunResult:
        if self._cached_demo_provider is None:
            return self._failed_result(request, [], cached_demo=True)
        try:
            provided = self._cached_demo_provider(request)
        except Exception:
            return self._failed_result(request, [], cached_demo=True)
        if type(provided) is not UIRunResult or provided.run_id != request.run_id:
            return self._failed_result(request, [], cached_demo=True)
        events = tuple(
            replace(
                event,
                sequence=index,
                run_id=request.run_id,
                timestamp=sanitize_ui_text(event.timestamp, limit=48),
                summary=sanitize_ui_text(event.summary),
                simulated=True,
            )
            for index, event in enumerate(provided.progress_events)
            if type(event) is UIProgressEvent
        )
        mode = replace(
            provided.mode,
            kind=UIModeKind.CACHED_DEMO,
            execution_mode=UIExecutionMode.CACHED_DEMO,
        )
        return replace(provided, mode=mode, progress_events=events)

    def _result_from_state(
        self,
        request: UIRunRequest,
        state: dict,
        progress: list[UIProgressEvent],
    ) -> UIRunResult:
        outcome = str(state.get("graph_outcome") or "failed")
        if outcome not in _GRAPH_OUTCOMES:
            outcome = "failed"
        sources = tuple(
            view
            for source in state.get("sources") or ()
            if (view := _source_view(source)) is not None
        )
        itinerary = _verified_itinerary(state.get("itinerary"), sources)
        artifacts = tuple(
            view
            for artifact in state.get("rendered_artifacts") or ()
            if (view := self._artifact_view(artifact)) is not None
        )
        coverage = getattr(state.get("coverage"), "coverage_ratio", None)
        coverage_ratio = _coverage_ratio(coverage)
        warnings = _safe_warnings(state.get("warnings") or ())
        return UIRunResult(
            run_id=request.run_id,
            final_answer=_safe_answer(state.get("final_answer") or ""),
            graph_outcome=outcome,
            mode=_mode_status(request, state),
            sources=sources,
            itinerary=itinerary,
            artifacts=artifacts,
            progress_events=tuple(progress),
            coverage_ratio=coverage_ratio,
            warnings=warnings,
            error_message=(
                "تعذّر إكمال الطلب بأمان. يرجى المحاولة مجددًا بمعرّف تشغيل جديد."
                if outcome == "failed"
                else ""
            ),
        )

    def _failed_result(
        self,
        request: UIRunRequest,
        progress: list[UIProgressEvent],
        *,
        cached_demo: bool = False,
    ) -> UIRunResult:
        execution_mode = (
            UIExecutionMode.CACHED_DEMO if cached_demo else request.execution_mode
        )
        kind = UIModeKind.CACHED_DEMO if cached_demo else UIModeKind.UNAVAILABLE
        return UIRunResult(
            run_id=request.run_id,
            final_answer="",
            graph_outcome="failed",
            mode=UIModeStatus(
                kind=kind,
                retrieval_mode="unavailable",
                model_fallback_used=False,
                execution_mode=execution_mode,
            ),
            sources=(),
            itinerary=None,
            artifacts=(),
            progress_events=tuple(progress),
            warnings=("تعذر إكمال التشغيل ضمن الوضع المحدد.",),
            error_message="تعذّر إكمال الطلب بأمان. يرجى المحاولة مجددًا بمعرّف تشغيل جديد.",
        )

    def _artifact_view(self, artifact: object) -> Optional[UIArtifactView]:
        status = str(getattr(artifact, "creation_status", "") or "")
        if status not in _ARTIFACT_STATUSES:
            return None
        download: Optional[bytes] = None
        if status == "created":
            raw_path = getattr(artifact, "absolute_path", None) or getattr(
                artifact, "path", None
            )
            if isinstance(raw_path, str) and raw_path:
                try:
                    path = Path(raw_path).expanduser().resolve(strict=True)
                    path.relative_to(self._output_root)
                    if path.is_file():
                        download = path.read_bytes()
                except (OSError, ValueError):
                    download = None
        return UIArtifactView(
            artifact_type=sanitize_ui_text(
                getattr(artifact, "artifact_type", ""), limit=64
            ),
            display_label=sanitize_ui_text(
                getattr(artifact, "display_label", ""), limit=120
            ),
            filename=Path(str(getattr(artifact, "filename", "") or "")).name[:128],
            mime_type=sanitize_ui_text(
                getattr(artifact, "mime_type", "application/octet-stream"), limit=96
            ),
            size_bytes=max(0, int(getattr(artifact, "size_bytes", 0) or 0)),
            checksum=_safe_checksum(getattr(artifact, "checksum", None)),
            creation_status=status,
            warnings=_safe_warnings(getattr(artifact, "warnings", ()) or ()),
            error_category=(
                _safe_category(getattr(artifact, "error_category", None))
                if getattr(artifact, "error_category", None)
                else None
            ),
            download_bytes=download,
        )

    @staticmethod
    def _snapshot(result: UIRunResult) -> _CompletedRunSnapshot:
        return _CompletedRunSnapshot(
            result=result,
            itinerary=result.itinerary,
            sources=result.sources,
            artifacts=result.artifacts,
        )


def _effective_request(request: UIRunRequest) -> str:
    if not request.preferences:
        return request.query
    preferences = "\n".join(f"- {value}" for value in request.preferences)
    return f"{request.query}\n\nالتفضيلات المصرّح بها:\n{preferences}"


def _source_view(source: object) -> Optional[UISourceView]:
    if type(source) is not CitationSource:
        return None
    return UISourceView(
        citation_id=source.citation_id,
        title=sanitize_ui_text(source.title, limit=240),
        url=_safe_source_url(source.url),
        page=source.page,
        section=(sanitize_ui_text(source.section, limit=240) if source.section else None),
        publication_date=source.publication_date,
        metadata_complete=(
            source.page is not None
            and source.section is not None
            and source.publication_date is not None
        ),
        citation_verified=True,
    )


def _verified_itinerary(
    itinerary: object, sources: tuple[UISourceView, ...]
) -> Optional[Itinerary]:
    if type(itinerary) is not Itinerary:
        return None
    try:
        mapping = itinerary.validate_citations()
    except ValueError:
        return None
    visible_ids = {source.citation_id for source in sources}
    if set(mapping) - visible_ids:
        return None
    return itinerary


def _safe_answer(value: object) -> str:
    if not isinstance(value, str):
        return ""
    lines = [sanitize_ui_text(line, limit=4_000) for line in value.splitlines()]
    safe = "\n".join(line for line in lines if line).strip()
    return safe[:50_000]


def _safe_source_url(value: object) -> str:
    return safe_external_url(value)


def _safe_warnings(values: object) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values if isinstance(values, (list, tuple)) else ():
        warning = sanitize_ui_text(value, limit=320)
        if warning and warning not in seen:
            seen.add(warning)
            result.append(warning)
        if len(result) == 32:
            break
    return tuple(result)


def _safe_category(value: object) -> str:
    text = str(value or "artifact_error").lower()
    cleaned = re.sub(r"[^a-z0-9_-]", "_", text)[:64].strip("_-")
    return cleaned or "artifact_error"


def _safe_checksum(value: object) -> Optional[str]:
    if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", value):
        return None
    return value.lower()


def _coverage_ratio(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        return None
    return result


def _safe_model_route(use_case: object, model: object, fallback_cases: set[str]) -> Optional[UIModelRoute]:
    if not isinstance(use_case, str) or not _SAFE_TOKEN_RE.fullmatch(use_case):
        return None
    if not isinstance(model, str) or not _SAFE_MODEL_RE.fullmatch(model) or "://" in model:
        return None
    used_fallback = use_case in fallback_cases or f"agent_{use_case}" in fallback_cases
    return UIModelRoute(
        use_case=use_case,
        resolved_model=model,
        used_fallback=used_fallback,
    )


def _model_routes(state: dict) -> tuple[UIModelRoute, ...]:
    fallback_cases: set[str] = set()
    for event in state.get("fallback_events") or ():
        if (
            str(getattr(event, "outcome", "")) == "success"
            and (
                bool(getattr(event, "degraded", False))
                or getattr(event, "selected_fallback", None) not in {None, "primary"}
            )
        ):
            use_case = getattr(event, "use_case", None)
            if isinstance(use_case, str):
                fallback_cases.add(use_case)
    routes: list[UIModelRoute] = []
    raw_routes = state.get("model_routes")
    if not isinstance(raw_routes, dict):
        return ()
    for use_case, model in raw_routes.items():
        if isinstance(model, dict):
            for nested_use_case, nested_model in model.items():
                route = _safe_model_route(
                    nested_use_case, nested_model, fallback_cases
                )
                if route is not None:
                    routes.append(route)
        else:
            route = _safe_model_route(use_case, model, fallback_cases)
            if route is not None:
                routes.append(route)
    return tuple(sorted(routes, key=lambda item: (item.use_case, item.resolved_model)))


def _mode_status(request: UIRunRequest, state: dict) -> UIModeStatus:
    retrieval_mode = str(state.get("retrieval_mode") or RAGMode.UNAVAILABLE.value)
    if retrieval_mode not in _RETRIEVAL_MODES:
        retrieval_mode = RAGMode.UNAVAILABLE.value
    routes = _model_routes(state)
    fallback_used = bool(state.get("model_fallback_used")) or any(
        route.used_fallback for route in routes
    )
    if request.execution_mode is UIExecutionMode.CACHED_DEMO:
        kind = UIModeKind.CACHED_DEMO
    elif retrieval_mode == RAGMode.UNAVAILABLE.value:
        kind = UIModeKind.UNAVAILABLE
    elif fallback_used:
        kind = UIModeKind.MODEL_FALLBACK
    elif retrieval_mode in _DEGRADED_RETRIEVAL_MODES:
        kind = UIModeKind.DEGRADED_RETRIEVAL
    else:
        kind = UIModeKind.LIVE
    return UIModeStatus(
        kind=kind,
        retrieval_mode=retrieval_mode,
        model_fallback_used=fallback_used,
        execution_mode=request.execution_mode,
        model_routes=routes,
    )
