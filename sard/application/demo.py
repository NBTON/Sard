"""Deterministic, explicitly offline cached-demo provider for the Step 7 UI.

Agent C scope: this module builds a complete, unmistakably simulated
``UIRunResult`` for the exact hero query using the frozen
``sard.application.contracts`` dataclasses and the existing provider-neutral
``sard.outputs`` renderers.

Guarantees (all proven by ``tests/application/test_demo.py``):

- No credentials, network, live model/RAG calls, or writes to a production
  Zvec repository. The module never imports ``sard.agent``, ``sard.rag``,
  ``sard.config``, or ``sard.ui``.
- Deterministic: the same run ID and trip dates always produce the same
  events, citations, itinerary, answer, artifact bytes, sizes and checksums.
- ``execution_mode`` is always ``CACHED_DEMO`` and every progress event has
  ``simulated=True`` so the cached-demo nature is unmistakable.
- Only the exact hero query is served; any other query raises
  :class:`DemoQueryUnavailable` instead of fabricating content or falling
  through to live dependencies.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, time as time_type, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

from sard.application.contracts import (
    UIArtifactView,
    UIExecutionMode,
    UIModeKind,
    UIModeStatus,
    UIModelRoute,
    UIProgressEvent,
    UIProgressState,
    UIRunRequest,
    UIRunResult,
    UISourceView,
    UIStage,
)
from sard.outputs.artifacts import (
    ArtifactManager,
    ArtifactWriteResult,
    failed_artifact,
    skipped_artifact,
)
from sard.outputs.calendar import CalendarRenderError, render_calendar
from sard.outputs.pdf import render_pdf
from sard.outputs.pdf_environment import locked_pdf_output_root
from sard.outputs.raw import render_raw_text
from sard.outputs.schemas import (
    CITATION_ID_RE,
    CitationSource,
    FieldSupport,
    Itinerary,
    ItineraryDay,
    ItineraryStop,
    TextBlock,
    VerificationStatus,
)

HERO_QUERY = "أنشئ برنامجًا سياحيًا تراثيًا لمدة يومين في المنطقة الشرقية"

DEMO_WARNING = (
    "وضع تجريبي بدون اتصال: هذه النتائج مولّدة من بيانات ثابتة لأغراض العرض فقط، "
    "وليست توصية سفر حقيقية."
)

DEMO_RETRIEVAL_MODE = "hybrid_reranked"

# Fixed, explicit demo dates used when the request supplies none. Dates are
# never inferred from free text such as "اليوم الأول".
DEMO_DEFAULT_DATES = (date(2026, 11, 1), date(2026, 11, 2))

# One fixed clock for the whole demo so events, itinerary and artifacts are
# byte-for-byte reproducible.
DEMO_GENERATED_AT = datetime(2026, 8, 13, 10, 0, 0, tzinfo=timezone.utc)

FINAL_ANSWER = (
    "المنطقة الشرقية تزخر بمواقع تراثية موثقة. ننصح بالبدء من قلعة تاروت، "
    "أحد أقدم الحصون المطلة على الخليج [CIT-DEMO-EAST-01]، ثم الانتقال إلى "
    "سوق القيصرية لاستكشاف الأسواق والحرف القديمة [CIT-DEMO-EAST-02]. في "
    "الأحساء، يقدّم متحف الأحساء الإقليمي عرضاً منظماً للتراث المحلي "
    "[CIT-DEMO-EAST-03]، ويُعد جبل القارة خياراً مناسباً للتجول بين الكهوف "
    "والمسارات الصخرية [CIT-DEMO-EAST-04]."
)


class DemoQueryUnavailable(ValueError):
    """The cached demo serves only the exact hero query; nothing is fabricated."""


@dataclass(frozen=True)
class DemoFixture:
    """Stable demo data selected for the hero query (demo-internal, not a UI contract)."""

    query: str
    run_id: str
    itinerary: Itinerary
    final_answer: str
    sources: tuple[CitationSource, ...]
    retrieval_mode: str
    model_routes: tuple[UIModelRoute, ...]
    coverage_ratio: float
    warnings: tuple[str, ...]


def _normalize_query(query: str) -> str:
    return " ".join((query or "").strip().split())


def is_hero_query(query: str) -> bool:
    """Return True only for the exact hero query (whitespace-insensitive)."""
    return _normalize_query(query) == HERO_QUERY


def make_demo_run_id(query: str = HERO_QUERY, trip_dates: tuple[date, ...] = ()) -> str:
    """Deterministic safe ASCII run ID for a demo request."""
    payload = "\x1f".join((_normalize_query(query), *(d.isoformat() for d in trip_dates)))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"demo-{digest}"


def _demo_sources() -> tuple[CitationSource, ...]:
    return (
        CitationSource(
            citation_id="CIT-DEMO-EAST-01",
            title="دليل قلعة تاروت التراثية",
            url="https://example.org/offline-demo/tarout-fort?lang=ar",
            page=4,
            section="التاريخ",
            publication_date=date(2024, 3, 10),
        ),
        CitationSource(
            citation_id="CIT-DEMO-EAST-02",
            title="سوق القيصرية العريق",
            url="https://example.org/offline-demo/al-qaisariya?lang=ar",
            page=7,
            section="جولة المشي",
            publication_date=date(2024, 5, 22),
        ),
        CitationSource(
            citation_id="CIT-DEMO-EAST-03",
            title="متحف الأحساء الإقليمي",
            url="https://example.org/offline-demo/al-ahsa-museum?lang=ar",
            page=3,
            section="المعارض",
            publication_date=date(2023, 11, 5),
        ),
        CitationSource(
            citation_id="CIT-DEMO-EAST-04",
            title="دليل جبل القارة",
            url="https://example.org/offline-demo/qara-mountain?lang=ar",
            page=9,
            section="الكهوف",
            publication_date=date(2024, 1, 18),
        ),
    )


def _demo_itinerary(
    *,
    run_id: str,
    sources: tuple[CitationSource, ...],
    explicit_dates: tuple[date, ...],
) -> Itinerary:
    day_one, day_two = explicit_dates
    day1 = ItineraryDay(
        title="اليوم الأول: تاروت والقلعة",
        date=day_one,
        relative_day_number=1,
        field_support=(
            FieldSupport("title", provenance="user_provided"),
            FieldSupport("date", provenance="user_provided"),
        ),
        stops=(
            ItineraryStop(
                time="09:00 - 11:00",
                title="جولة قلعة تاروت",
                location="قلعة تاروت، محافظة القطيف",
                stop_id="tarout-fort",
                start_time=time_type(9, 0),
                end_time=time_type(11, 0),
                location_name="قلعة تاروت، محافظة القطيف",
                citation_ids=("CIT-DEMO-EAST-01",),
                field_support=(
                    FieldSupport("title", ("CIT-DEMO-EAST-01",)),
                    FieldSupport("location", ("CIT-DEMO-EAST-01",)),
                    FieldSupport("description", ("CIT-DEMO-EAST-01",)),
                    FieldSupport("time", provenance="user_provided"),
                ),
                paragraphs=(
                    TextBlock(
                        "تُعد قلعة تاروت من أقدم الحصون التاريخية المطلة على ساحل "
                        "الخليج العربي [CIT-DEMO-EAST-01].",
                        ("CIT-DEMO-EAST-01",),
                    ),
                ),
                practical_notes=(
                    TextBlock("يُنصح بالوصول في الصباح الباكر وتجنب ساعات الذروة."),
                ),
            ),
            ItineraryStop(
                time="13:00 - 15:00",
                title="سوق القيصرية",
                location="القطيف",
                stop_id="al-qaisariya",
                start_time=time_type(13, 0),
                end_time=time_type(15, 0),
                location_name="سوق القيصرية، القطيف",
                citation_ids=("CIT-DEMO-EAST-02",),
                field_support=(
                    FieldSupport("title", ("CIT-DEMO-EAST-02",)),
                    FieldSupport("location", ("CIT-DEMO-EAST-02",)),
                    FieldSupport("description", ("CIT-DEMO-EAST-02",)),
                    FieldSupport("time", provenance="user_provided"),
                ),
                paragraphs=(
                    TextBlock(
                        "يتيح السوق القديم فرصة للتعرف على الحرف التقليدية "
                        "والمنتجات المحلية [CIT-DEMO-EAST-02].",
                        ("CIT-DEMO-EAST-02",),
                    ),
                ),
                practical_notes=(
                    TextBlock("الأسعار والمواعيد قابلة للتغيير؛ تحقق محلياً."),
                ),
            ),
        ),
    )
    day2 = ItineraryDay(
        title="اليوم الثاني: الأحساء وجبل القارة",
        date=day_two,
        relative_day_number=2,
        field_support=(
            FieldSupport("title", provenance="user_provided"),
            FieldSupport("date", provenance="user_provided"),
        ),
        stops=(
            ItineraryStop(
                time="10:00 - 12:00",
                title="متحف الأحساء الإقليمي",
                location="الهفوف، الأحساء",
                stop_id="al-ahsa-museum",
                start_time=time_type(10, 0),
                end_time=time_type(12, 0),
                location_name="متحف الأحساء الإقليمي، الهفوف",
                citation_ids=("CIT-DEMO-EAST-03",),
                field_support=(
                    FieldSupport("title", ("CIT-DEMO-EAST-03",)),
                    FieldSupport("location", ("CIT-DEMO-EAST-03",)),
                    FieldSupport("description", ("CIT-DEMO-EAST-03",)),
                    FieldSupport("time", provenance="user_provided"),
                ),
                paragraphs=(
                    TextBlock(
                        "يقدّم المتحف عرضاً منظماً لتراث الأحساء المدرج ضمن التراث "
                        "الإنساني [CIT-DEMO-EAST-03].",
                        ("CIT-DEMO-EAST-03",),
                    ),
                ),
                practical_notes=(
                    TextBlock("تحقق من ساعات العمل في يوم الزيارة."),
                ),
            ),
            ItineraryStop(
                time="15:30 - 17:30",
                title="جبل القارة",
                location="القرية الشرقية، الأحساء",
                stop_id="qara-mountain",
                start_time=time_type(15, 30),
                end_time=time_type(17, 30),
                location_name="جبل القارة، القرية الشرقية",
                citation_ids=("CIT-DEMO-EAST-04",),
                field_support=(
                    FieldSupport("title", ("CIT-DEMO-EAST-04",)),
                    FieldSupport("location", ("CIT-DEMO-EAST-04",)),
                    FieldSupport("description", ("CIT-DEMO-EAST-04",)),
                    FieldSupport("time", provenance="user_provided"),
                ),
                paragraphs=(
                    TextBlock(
                        "يمكن التمتع بالمشي بين الكهوف والمسارات الصخرية المحيطة "
                        "بالجبل [CIT-DEMO-EAST-04].",
                        ("CIT-DEMO-EAST-04",),
                    ),
                ),
                practical_notes=(
                    TextBlock("ارتدِ حذاءً مريحاً واحمل ماءً للجولة."),
                ),
            ),
        ),
    )
    return Itinerary(
        title="برنامج يومين في المنطقة الشرقية (عرض تجريبي)",
        summary="برنامج تراثي تجريبي معدّ من بيانات ثابتة لأغراض العرض فقط؛ لا يمثل توصية سفر حقيقية.",
        days=(day1, day2),
        sources=sources,
        generated_at=DEMO_GENERATED_AT,
        notes=(
            TextBlock("عرض تجريبي بدون اتصال؛ جميع التواريخ والمصادر افتراضية وثابتة."),
        ),
        run_id=run_id,
        timezone="Asia/Riyadh",
        explicit_dates=explicit_dates,
        verification_status=VerificationStatus.VERIFIED,
        retrieval_mode=DEMO_RETRIEVAL_MODE,
        model_fallback_used=False,
        warnings=(DEMO_WARNING,),
        citation_ids=tuple(source.citation_id for source in sources),
        field_support=(
            FieldSupport("title", provenance="user_provided"),
            FieldSupport("summary", provenance="user_provided"),
            FieldSupport("notes", provenance="user_provided"),
        ),
    )


def demo_fixture(*, run_id: str, trip_dates: tuple[date, ...] = ()) -> DemoFixture:
    """Return the stable, explicitly fixture-only demo data for the hero query.

    The fixed pair is used only when no dates are supplied. Explicit dates for
    this two-day fixture must be exactly two ordered values; they are never
    padded with stale fixture dates or inferred from free text.
    """
    sources = _demo_sources()
    if trip_dates:
        if len(trip_dates) != 2 or trip_dates[1] < trip_dates[0]:
            raise ValueError("two ordered explicit dates are required for the demo")
        explicit_dates = tuple(trip_dates)
    else:
        explicit_dates = DEMO_DEFAULT_DATES
    itinerary = _demo_itinerary(run_id=run_id, sources=sources, explicit_dates=explicit_dates)
    return DemoFixture(
        query=HERO_QUERY,
        run_id=run_id,
        itinerary=itinerary,
        final_answer=FINAL_ANSWER,
        sources=sources,
        retrieval_mode=DEMO_RETRIEVAL_MODE,
        model_routes=(
            UIModelRoute(use_case="understand", resolved_model="demo-understand"),
            UIModelRoute(use_case="plan", resolved_model="demo-plan"),
            UIModelRoute(use_case="retrieve", resolved_model="demo-retrieve"),
            UIModelRoute(use_case="compose", resolved_model="demo-compose"),
            UIModelRoute(use_case="verify", resolved_model="demo-verify"),
            UIModelRoute(use_case="render", resolved_model="demo-render"),
        ),
        coverage_ratio=1.0,
        warnings=(DEMO_WARNING,),
    )


def build_demo_progress(run_id: str) -> tuple[UIProgressEvent, ...]:
    """Deterministic simulated progress projection for the hero-query demo."""
    events: list[UIProgressEvent] = []
    sequence = 0

    def add(
        stage: UIStage,
        state: UIProgressState,
        event_kind: str,
        summary: str,
        **extra,
    ) -> None:
        nonlocal sequence
        sequence += 1
        timestamp = (DEMO_GENERATED_AT + timedelta(milliseconds=sequence * 400)).isoformat()
        events.append(
            UIProgressEvent(
                sequence=sequence,
                run_id=run_id,
                stage=stage,
                state=state,
                event_kind=event_kind,
                timestamp=timestamp,
                summary=summary,
                simulated=True,
                **extra,
            )
        )

    add(
        UIStage.UNDERSTAND,
        UIProgressState.WAITING,
        "waiting",
        "التحقق من الطلب في الوضع التجريبي بدون اتصال",
    )
    add(UIStage.UNDERSTAND, UIProgressState.ACTIVE, "started", "فهم الطلب (تجريبي)")
    add(
        UIStage.UNDERSTAND,
        UIProgressState.COMPLETED,
        "completed",
        "اكتمل فهم الطلب (تجريبي)",
        duration_ms=210.0,
    )
    add(UIStage.PLAN, UIProgressState.ACTIVE, "started", "إعداد خطة اليومين (تجريبي)")
    add(
        UIStage.PLAN,
        UIProgressState.COMPLETED,
        "completed",
        "اكتمل الإعداد (تجريبي)",
        duration_ms=180.0,
    )
    add(UIStage.RETRIEVE, UIProgressState.ACTIVE, "started", "استرجاع الأدلة من بيانات ثابتة (تجريبي)")
    add(
        UIStage.RETRIEVE,
        UIProgressState.COMPLETED,
        "completed",
        "تم استرجاع الأدلة (تجريبي)",
        duration_ms=340.0,
        source_count=4,
    )
    add(UIStage.COMPOSE, UIProgressState.ACTIVE, "started", "تأليف الإجابة العربية (تجريبي)")
    add(
        UIStage.COMPOSE,
        UIProgressState.COMPLETED,
        "completed",
        "اكتمل التأليف (تجريبي)",
        duration_ms=420.0,
    )
    add(UIStage.VERIFY, UIProgressState.ACTIVE, "started", "التحقق من الاستشهادات (تجريبي)")
    add(
        UIStage.VERIFY,
        UIProgressState.COMPLETED,
        "completed",
        "اكتمل التحقق (تجريبي)",
        duration_ms=260.0,
        coverage=1.0,
    )
    add(UIStage.RENDER, UIProgressState.ACTIVE, "started", "إنشاء المخرجات (تجريبي)")
    add(
        UIStage.RENDER,
        UIProgressState.COMPLETED,
        "graph_completed",
        "اكتملت المخرجات التجريبية",
        duration_ms=510.0,
        source_count=4,
    )
    add(
        UIStage.RENDER,
        UIProgressState.COMPLETED,
        "completed",
        "اكتمل إنشاء المخرجات (تجريبي)",
        duration_ms=510.0,
    )
    return tuple(events)


# ReportLab embeds wall-clock ``/CreationDate`` and ``/ModDate`` values in every
# document even when ``invariant`` is set, and Step 6's renderer derives the
# trailer ``/ID`` digest from that content. The demo pins both dates and then
# recomputes the ``/ID`` digest so identical runs yield identical PDF bytes
# regardless of the second in which they were rendered.
_PDF_DATE_FIELD_RE = re.compile(
    rb"/(CreationDate|ModDate) \(D:\d{14}[+-]\d{2}'\d{2}'\)"
)
_PDF_ID_RE = re.compile(
    rb"/ID\s*\[\s*<[0-9A-Fa-f]+>\s*<[0-9A-Fa-f]+>\s*\]",
    re.S,
)


def _normalize_demo_pdf(pdf_path: Path, generated_at: datetime) -> None:
    """Pin wall-clock metadata so demo PDF bytes are fully deterministic."""
    pinned_date = (
        generated_at.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S").encode("ascii")
    )

    def _pin(match: re.Match[bytes]) -> bytes:
        return b"/" + match.group(1) + b" (D:" + pinned_date + b"+00'00')"

    data = pdf_path.read_bytes()
    pinned = _PDF_DATE_FIELD_RE.sub(_pin, data)
    placeholder = b"/ID\n[<00000000000000000000000000000000><00000000000000000000000000000000>]"
    canonical = _PDF_ID_RE.sub(placeholder, pinned, count=1)
    if canonical == pinned:
        if canonical != data:
            pdf_path.write_bytes(canonical)
        return
    digest = hashlib.sha256(canonical).hexdigest()[:32].encode("ascii")
    normalized = _PDF_ID_RE.sub(
        b"/ID\n[<" + digest + b"><" + digest + b">]",
        canonical,
        count=1,
    )
    pdf_path.write_bytes(normalized)



def _read_contained_bytes(absolute_path: Optional[str], root: Optional[Path]) -> Optional[bytes]:
    if not absolute_path or root is None:
        return None
    path = Path(absolute_path).resolve()
    root_resolved = Path(root).resolve()
    try:
        path.relative_to(root_resolved)
    except ValueError:
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _artifact_view(result: ArtifactWriteResult, root: Optional[Path]) -> UIArtifactView:
    download_bytes = (
        _read_contained_bytes(result.absolute_path, root)
        if result.creation_status == "created"
        else None
    )
    return UIArtifactView(
        artifact_type=result.artifact_type,
        display_label=result.display_label,
        filename=result.filename,
        mime_type=result.mime_type,
        size_bytes=result.size_bytes,
        checksum=result.checksum,
        creation_status=result.creation_status,
        warnings=result.warnings,
        error_category=result.error_category,
        download_bytes=download_bytes,
    )


def _failed_view(artifact_type: str, label: str, filename: str, mime_type: str, exc: Exception) -> UIArtifactView:
    category = getattr(exc, "category", None) or type(exc).__name__.lower()
    result = failed_artifact(
        artifact_type=artifact_type,
        display_label=label,
        filename=filename,
        mime_type=mime_type,
        category=category,
        warning=f"تعذر إنشاء {label}: {category}.",
    )
    return _artifact_view(result, None)


def _skipped_view(artifact_type: str, label: str, filename: str, mime_type: str, category: str, warning: str) -> UIArtifactView:
    result = skipped_artifact(
        artifact_type=artifact_type,
        display_label=label,
        filename=filename,
        mime_type=mime_type,
        category=category,
        warning=warning,
    )
    return _artifact_view(result, None)


def _render_demo_artifacts(
    *,
    run_id: str,
    itinerary: Itinerary,
    final_answer: str,
    sources: tuple[CitationSource, ...],
    output_root: str | Path | None,
    render_checksums: bool,
) -> tuple[UIArtifactView, ...]:
    manager = ArtifactManager(output_root, run_id, checksums=render_checksums)
    views: list[UIArtifactView] = []

    raw = render_raw_text(
        final_answer,
        sources,
        verification_status=VerificationStatus.VERIFIED,
        retrieval_mode=itinerary.retrieval_mode,
        warnings=itinerary.warnings,
        degraded_notice=None,
    )
    try:
        raw_result = manager.write_bytes(
            raw.data,
            filename="answer.txt",
            artifact_type="raw_text",
            display_label="الإجابة العربية الخام",
            mime_type="text/plain; charset=utf-8",
            warnings=raw.warnings,
        )
        views.append(_artifact_view(raw_result, manager.root))
    except Exception as exc:
        views.append(_failed_view("raw_text", "الإجابة العربية الخام", "answer.txt", "text/plain; charset=utf-8", exc))

    temporary = manager.temporary_path(".pdf")
    try:
        try:
            with locked_pdf_output_root(manager.run_dir):
                render_pdf(itinerary, temporary)
            _normalize_demo_pdf(Path(temporary), itinerary.generated_at)
            pdf_result = manager.publish_generated_file(
                temporary,
                filename="itinerary.pdf",
                artifact_type="pdf",
                display_label="برنامج الرحلة PDF",
                mime_type="application/pdf",
                warnings=itinerary.warnings,
            )
            views.append(_artifact_view(pdf_result, manager.root))
        except Exception as exc:
            views.append(_failed_view("pdf", "برنامج الرحلة PDF", "itinerary.pdf", "application/pdf", exc))
    finally:
        temporary.unlink(missing_ok=True)

    try:
        calendar = render_calendar(itinerary)
        calendar_result = manager.write_bytes(
            calendar.data,
            filename="itinerary.ics",
            artifact_type="calendar",
            display_label="تقويم الرحلة",
            mime_type="text/calendar; charset=utf-8",
            warnings=calendar.warnings,
        )
        views.append(_artifact_view(calendar_result, manager.root))
    except CalendarRenderError as exc:
        views.append(
            _skipped_view(
                "calendar",
                "تقويم الرحلة",
                "itinerary.ics",
                "text/calendar; charset=utf-8",
                exc.category,
                "؛ ".join((str(exc), *exc.warnings)),
            )
        )
    except Exception as exc:
        views.append(_failed_view("calendar", "تقويم الرحلة", "itinerary.ics", "text/calendar; charset=utf-8", exc))

    return tuple(views)


def _source_view(source: CitationSource) -> UISourceView:
    metadata_complete = (
        source.page is not None
        and source.section is not None
        and source.publication_date is not None
    )
    return UISourceView(
        citation_id=source.citation_id,
        title=source.title,
        url=source.url,
        page=source.page,
        section=source.section,
        publication_date=source.publication_date,
        metadata_complete=metadata_complete,
        citation_verified=True,
    )


def build_demo_result(
    request: UIRunRequest,
    *,
    output_root: str | Path | None = None,
    render_checksums: bool = True,
) -> UIRunResult:
    """Build the complete cached-demo result for the hero query.

    Raises :class:`DemoQueryUnavailable` for non-hero queries (nothing is
    fabricated) and ``ValueError`` when the request is not explicitly
    ``CACHED_DEMO``.
    """
    if request.execution_mode is not UIExecutionMode.CACHED_DEMO:
        raise ValueError("demo provider requires execution_mode=CACHED_DEMO")
    if not is_hero_query(request.query):
        raise DemoQueryUnavailable(f"cached demo is only available for the exact hero query; got {request.query!r}")

    fixture = demo_fixture(run_id=request.run_id, trip_dates=request.trip_dates)
    artifacts = _render_demo_artifacts(
        run_id=request.run_id,
        itinerary=fixture.itinerary,
        final_answer=fixture.final_answer,
        sources=fixture.sources,
        output_root=output_root,
        render_checksums=render_checksums,
    )
    return UIRunResult(
        run_id=request.run_id,
        final_answer=fixture.final_answer,
        graph_outcome="completed",
        mode=UIModeStatus(
            kind=UIModeKind.CACHED_DEMO,
            retrieval_mode=fixture.retrieval_mode,
            model_fallback_used=False,
            execution_mode=UIExecutionMode.CACHED_DEMO,
            model_routes=fixture.model_routes,
        ),
        sources=tuple(_source_view(source) for source in fixture.sources),
        itinerary=fixture.itinerary,
        artifacts=artifacts,
        progress_events=build_demo_progress(request.run_id),
        coverage_ratio=fixture.coverage_ratio,
        warnings=fixture.warnings,
        error_message="",
    )


def stream_demo(
    request: UIRunRequest,
    *,
    output_root: str | Path | None = None,
    render_checksums: bool = True,
) -> Iterator[UIProgressEvent | UIRunResult]:
    """Yield simulated progress events followed by the terminal demo result."""
    progress = build_demo_progress(request.run_id)
    yield from progress
    yield build_demo_result(request, output_root=output_root, render_checksums=render_checksums)
