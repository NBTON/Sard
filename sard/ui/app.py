"""Step 7 Arabic-first RTL Streamlit interface for Sard.

Presentation layer only. It talks exclusively to
:class:`sard.application.SardApplicationService` (the single application
boundary) and to the pure helpers in :mod:`sard.ui.presentation`. It never
imports LangGraph, Zvec, an NVIDIA integration, ``sard.config``,
``sard.rag``, or the Step 6 renderers.

Run with:

    uv run streamlit run sard/ui/app.py

Session-state keys (owned by the integration owner after Phase 1) are all
prefixed with ``step7_`` and documented in :data:`SS`. Buttons only set an
intent; :meth:`SardApplicationService.stream_run` is invoked exactly once
per new run token.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Allow `streamlit run sard/ui/app.py` to work even if the package isn't
# installed (editable or otherwise) in the current environment.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sard.application import (  # noqa: E402
    ApplicationServiceError,
    HERO_QUERY,
    SardApplicationService,
    build_demo_result,
)
from sard.application.contracts import (  # noqa: E402
    CalendarAfterDateRequest,
    UIExecutionMode,
    UIProgressEvent,
    UIRunRequest,
    UIRunResult,
)
from sard.ui import presentation as ui  # noqa: E402
from sard.ui import session_state as session  # noqa: E402

_PAGE_TITLE = "سرد | Sard"
_MAX_QUERY_LENGTH = 2000
_BACKUP_QUERIES = (
    "ما هي أبرز المواقع التراثية السعودية المسجلة في قائمة اليونسكو للتراث العالمي؟",
    "اقترح خطة زيارة ليوم واحد لاستكشاف حي الطريف التاريخي في الدرعية",
)

# Stable Streamlit session keys.
SS = session.KEYS

_DEFAULT_PREFERENCES = (
    "طعام محلي",
    "تسوّق وهدايا",
    "متاحف وتراث",
    "طبيعة ومسارات مشي",
    "أنشطة عائلية",
    "ميزانية اقتصادية",
    "أماكن تصوير",
    "حياة ليلية هادئة",
)

# Step 7 RTL theme. Selectors are minimal and documented here:
# .sard-hero / .sard-hero-title / .sard-hero-subtitle / .sard-hero-value
# .sard-mode-banner (demo) / .sard-badge (.sard-badge-ok, .sard-badge-warn)
# .sard-source-grid / .sard-source-card / .sard-source-title
# .sard-source-meta / .sard-source-link / .sard-source-cite / .sard-muted
_RTL_CSS = """
<style>
:root {
    --sard-green: #0b6e4f;
    --sard-green-dark: #075e43;
    --sard-bg: #f6f8f7;
    --sard-ink: #1f2937;
    --sard-muted: #55635c;
    --sard-border: #dbe3df;
    --sard-card: #ffffff;
    --sard-warn-bg: #fdf3e3;
    --sard-warn-ink: #8a5a00;
    --sard-danger-ink: #b3261e;
}
html, body, .stApp {
    direction: rtl;
    text-align: right;
}
.stApp {
    background: var(--sard-bg);
    color: var(--sard-ink);
}
.sard-hero { padding: 1.25rem 0 0.5rem; }
.sard-hero-title {
    color: var(--sard-green-dark);
    font-size: 1.9rem;
    margin: 0 0 0.25rem;
    line-height: 1.3;
}
.sard-hero-subtitle { color: var(--sard-muted); margin: 0 0 0.5rem; }
.sard-hero-value {
    color: var(--sard-ink);
    font-size: 1.05rem;
    margin: 0;
    line-height: 1.6;
}
.sard-mode-banner {
    border-inline-start: 4px solid var(--sard-green);
    background: #eef4f1;
    padding: 0.6rem 0.85rem;
    border-radius: 8px;
    margin: 0.5rem 0;
    color: var(--sard-ink);
}
.sard-mode-banner.demo {
    border-color: var(--sard-warn-ink);
    background: var(--sard-warn-bg);
}
.sard-badge {
    display: inline-block;
    font-size: 0.72rem;
    padding: 0.15rem 0.45rem;
    border-radius: 999px;
    background: #eef4f1;
    color: var(--sard-green-dark);
    border: 1px solid var(--sard-border);
    margin-inline-start: 0.3rem;
}
.sard-badge-ok { background: #e8f5ee; }
.sard-badge-warn { background: var(--sard-warn-bg); color: var(--sard-warn-ink); }
.sard-source-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 0.75rem;
    margin: 0.5rem 0;
}
.sard-source-card {
    background: var(--sard-card);
    border: 1px solid var(--sard-border);
    border-radius: 10px;
    padding: 0.75rem 0.9rem;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}
.sard-source-title {
    margin: 0 0 0.4rem;
    font-size: 1rem;
    color: var(--sard-green-dark);
    line-height: 1.5;
}
.sard-source-meta { margin: 0.2rem 0; color: var(--sard-muted); font-size: 0.85rem; }
.sard-source-link { margin: 0.35rem 0 0; }
.sard-source-link a { color: var(--sard-green); font-weight: 600; }
.sard-source-cite { margin-top: 0.35rem; color: var(--sard-muted); font-size: 0.72rem; }
.sard-muted { color: var(--sard-muted); }
.stButton > button, .stDownloadButton > button {
    min-height: 2.6rem;
    border-radius: 8px;
}
a:focus-visible, button:focus-visible, input:focus-visible,
textarea:focus-visible, [tabindex]:focus-visible {
    outline: 3px solid var(--sard-green);
    outline-offset: 2px;
    border-radius: 4px;
}
@media (max-width: 720px) {
    .sard-source-grid { grid-template-columns: 1fr; }
    .sard-hero-title { font-size: 1.5rem; }
}
@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation: none !important; transition: none !important; }
}
</style>
"""


def _init_session() -> None:
    """Ensure all Step 7 session keys exist with stable defaults."""
    session.initialize_session(
        st.session_state,
        lambda: SardApplicationService(cached_demo_provider=build_demo_result),
    )


def _current_dates() -> tuple[object, ...]:
    """Collect optional start/end dates as an ordered tuple of ``date``."""
    start = st.session_state.get("step7_date_start")
    end = st.session_state.get("step7_date_end")
    return session.inclusive_dates(start, end)


def _current_preferences() -> tuple[str, ...]:
    return tuple(st.session_state.get("step7_pref_select") or ())


def _start_run(mode: UIExecutionMode, query: str) -> str:
    """Validate and stage a run; returns ``ok``, ``blank`` or ``too_long``."""
    cleaned = (query or "").strip()
    if not cleaned:
        return "blank"
    if len(cleaned) > _MAX_QUERY_LENGTH:
        return "too_long"
    start = st.session_state.get("step7_date_start")
    end = st.session_state.get("step7_date_end")
    if start is not None and end is not None and end < start:
        return "invalid_dates"
    dates = _current_dates()
    if mode is UIExecutionMode.CACHED_DEMO and dates and len(dates) != 2:
        return "demo_dates"
    run_id = ui.new_run_id()
    request = UIRunRequest(
        query=cleaned,
        run_id=run_id,
        trip_dates=dates,
        preferences=_current_preferences(),
        execution_mode=mode,
        render_artifacts=True,
    )
    session.begin_run(st.session_state, request)
    return "ok"


def _execute_request() -> None:
    """Consume stream_run once for the staged request; store progress+result."""
    if not session.claim_execution(st.session_state):
        return
    service = st.session_state[SS["service"]]
    request = st.session_state[SS["request"]]
    result: UIRunResult | None = None
    with st.status("جارٍ تجهيز رحلتك...", expanded=True) as status:
        bar = st.progress(0.0)
        for item in service.stream_run(request):
            if isinstance(item, UIProgressEvent):
                session.append_progress(st.session_state, item)
                label = f"{ui.stage_label(item.stage)} — {ui.state_label(item.state)}"
                bar.progress(min(item.sequence / 25.0, 1.0), text=label)
            elif isinstance(item, UIRunResult):
                result = item
        terminal_label, terminal_state = session.terminal_status(result)
        bar.progress(1.0, text=terminal_label)
        status.update(
            label=terminal_label,
            state=terminal_state,
            expanded=terminal_state == "error",
        )
    if result is None:
        st.session_state[SS["error"]] = terminal_label
        return
    session.finish_run(st.session_state, result)


def _render_hero() -> tuple[str, bool, bool]:
    st.markdown(
        ui.branded_header_html(
            "سَرد",
            subtitle="مستشار رحلاتك باللغة العربية",
            value_prop=(
                "من سؤالك إلى خطة مُنسّقة بمصادر موثّقة وملفات جاهزة للتحميل — "
                "بأسلوب عربي من اليمين إلى اليسار."
            ),
        ),
        unsafe_allow_html=True,
    )
    st.caption("استعلامات جاهزة للعرض")
    quick_columns = st.columns(3)
    quick_queries = (HERO_QUERY, *_BACKUP_QUERIES)
    quick_labels = ("الاستعلام الرئيسي", "احتياطي: اليونسكو", "احتياطي: الطريف")
    for index, (column, label, quick_query) in enumerate(
        zip(quick_columns, quick_labels, quick_queries)
    ):
        column.button(
            label,
            key=f"step7_quick_{index}",
            use_container_width=True,
            on_click=lambda value=quick_query: st.session_state.update(step7_query=value),
        )
    query = st.text_area(
        "صف رحلتك",
        placeholder=(
            "مثال: أنشئ لي برنامجًا ليومين في العُلا مع التركيز على التراث "
            "والطعام المحلي."
        ),
        height=110,
        key="step7_query",
        label_visibility="collapsed",
    )
    col_run, col_demo = st.columns(2)
    with col_run:
        run_clicked = st.button(
            "ابدأ التخطيط",
            type="primary",
            key="step7_btn_run",
            use_container_width=True,
        )
    with col_demo:
        demo_clicked = st.button(
            "تشغيل النسخة الاحتياطية يدويًا",
            key="step7_btn_demo",
            use_container_width=True,
        )
    return query, run_clicked, demo_clicked


def _render_optional_inputs() -> None:
    col_dates, col_prefs = st.columns(2)
    with col_dates:
        st.caption("تواريخ الرحلة (اختياري)")
        st.date_input("من", value=None, key="step7_date_start")
        st.date_input("إلى", value=None, key="step7_date_end")
    with col_prefs:
        st.multiselect(
            "التفضيلات (اختياري)",
            _DEFAULT_PREFERENCES,
            key="step7_pref_select",
        )


def _render_mode_and_metrics(result: UIRunResult) -> None:
    demo = bool(st.session_state.get(SS["demo_flag"], False))
    st.markdown(ui.mode_banner_html(result.mode, demo=demo), unsafe_allow_html=True)
    if demo:
        st.caption(
            "هذه نسخة احتياطية محفوظة مسبقًا: لم يُنشأ المحتوى عند عرضه، ولا توجد استدعاءات مباشرة للمصادر أو النماذج."
        )
    col_a, col_b = st.columns(2)
    with col_a:
        st.metric("حالة الجولة", ui.graph_outcome_label(result.graph_outcome))
    with col_b:
        if result.coverage_ratio is not None:
            st.metric("تغطية المصادر", ui.format_coverage(result.coverage_ratio))
    if result.mode.model_routes and not demo:
        with st.expander("مسارات النماذج (تقني)"):
            for route in result.mode.model_routes:
                st.write(ui.model_route_label(route))


def _render_answer(result: UIRunResult) -> None:
    st.subheader("الإجابة")
    # Citations and sources are produced by the application service.  The UI
    # renders the verified answer verbatim and never derives citation records
    # by parsing its text.
    st.markdown(result.final_answer)
    if result.sources:
        st.subheader("المصادر")
        cards = "".join(ui.source_card_html(source) for source in result.sources)
        st.markdown(f'<div class="sard-source-grid">{cards}</div>', unsafe_allow_html=True)
    else:
        st.caption("لا توجد مصادر مُتحقَّقة لهذه الجولة.")


def _render_artifacts(result: UIRunResult) -> None:
    st.subheader("ملفات الرحلة")
    created = [
        artifact
        for artifact in result.artifacts
        if artifact.creation_status == "created" and artifact.download_bytes
    ]
    if created:
        st.caption("الملفات الجاهزة للتحميل:")
        columns = st.columns(len(created))
        for column, artifact in zip(columns, created):
            column.download_button(
                label=ui.artifact_download_label(artifact),
                data=artifact.download_bytes,
                file_name=artifact.filename,
                mime=artifact.mime_type,
                key=f"step7_dl_{artifact.artifact_type}",
            )
    other = [
        artifact
        for artifact in result.artifacts
        if artifact.creation_status != "created"
    ]
    if other:
        notes = "؛ ".join(
            f"{ui.artifact_type_label(artifact.artifact_type)}: "
            f"{ui.artifact_status_label(artifact.creation_status)}"
            for artifact in other
        )
        st.caption(notes)
    if result.itinerary is not None and result.run_id:
        _render_calendar_after_dates(result)


def _render_calendar_after_dates(result: UIRunResult) -> None:
    st.markdown("---")
    st.caption("إنشاء تقويم (ICS) بمواعيد جديدة:")
    col_a, col_b = st.columns(2)
    with col_a:
        start = st.date_input("بداية الرحلة الجديدة", value=None, key="step7_cal_start")
    with col_b:
        end = st.date_input("نهاية الرحلة الجديدة", value=None, key="step7_cal_end")
    ready = start is not None and end is not None
    if st.button("إنشاء التقويم", disabled=not ready, key="step7_btn_cal"):
        service = st.session_state[SS["service"]]
        dates = session.inclusive_dates(start, end)
        if not dates:
            st.warning("يجب أن يكون تاريخ النهاية مساويًا لتاريخ البداية أو بعده.")
        else:
            try:
                view = service.create_calendar_after_dates(
                    CalendarAfterDateRequest(
                        run_id=result.run_id,
                        dates=dates,
                        preview=False,
                    )
                )
            except ApplicationServiceError as exc:
                st.warning(exc.safe_message)
            else:
                st.session_state[SS["cal_view"]] = view
    view = st.session_state.get(SS["cal_view"])
    if view is not None:
        if view.creation_status == "created" and view.download_bytes:
            st.download_button(
                label=ui.artifact_download_label(view),
                data=view.download_bytes,
                file_name=view.filename,
                mime=view.mime_type,
                key="step7_dl_cal",
            )
        elif view.error_category:
            st.warning(
                f"{ui.artifact_status_label(view.creation_status)}: {view.error_category}"
            )


def _render_failure_backup(result: UIRunResult) -> None:
    """Show the sanitized error plus the two backup buttons (retry / demo)."""
    st.error(result.error_message or ui.graph_outcome_label(result.graph_outcome))
    last_query = st.session_state.get(SS["last_query"]) or ""
    st.caption(
        "يمكنك إعادة المحاولة مباشرة بنفس الطلب، أو تشغيل النسخة الاحتياطية "
        "المحفوظة مسبقًا يدويًا. الاستعلام الرئيسي يتحول إليها تلقائيًا عند انتهاء المهلة أو فشل خدمة خارجية."
    )
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button(
            "إعادة المحاولة (نفس الطلب)",
            type="primary",
            key="step7_btn_retry",
            use_container_width=True,
            disabled=not last_query,
        ):
            session.stage_intent(st.session_state, UIExecutionMode.LIVE, last_query)
            st.rerun()
    with col_b:
        if st.button(
            "تشغيل النسخة الاحتياطية",
            key="step7_btn_demo_fallback",
            use_container_width=True,
            disabled=False,
        ):
            session.stage_intent(st.session_state, UIExecutionMode.CACHED_DEMO, HERO_QUERY)
            st.rerun()


def _render_result() -> None:
    result = st.session_state.get(SS["result"])
    if result is None:
        return
    _render_mode_and_metrics(result)
    visible_warnings = ui.warning_messages(result)
    if visible_warnings:
        with st.expander("تنبيهات التشغيل", expanded=result.graph_outcome != "completed"):
            for warning in visible_warnings:
                st.warning(warning)
    _render_answer(result)
    _render_artifacts(result)
    if result.error_message or result.graph_outcome == "failed":
        _render_failure_backup(result)


def _render_sidebar() -> None:
    with st.sidebar:
        st.subheader("وضع الجولة الحالي")
        request = st.session_state.get(SS["request"])
        if request is not None:
            st.write(f"وضع التنفيذ: **{ui.execution_mode_label(request.execution_mode)}**")
            st.caption(f"المعرّف: `{request.run_id}`")
        else:
            st.write("لا توجد جولة بعد.")
        result = st.session_state.get(SS["result"])
        if result is not None:
            st.write(ui.mode_status_line(result.mode))
        if bool(st.session_state.get(SS["demo_flag"], False)):
            st.warning("نسخة احتياطية محفوظة مسبقًا — ليست نتيجة مولّدة الآن.")
        st.markdown("---")
        st.caption("التحكم")
        if st.button(
            "بدء جلسة جديدة (تصفير)",
            key="step7_btn_reset",
            use_container_width=True,
        ):
            for key in list(st.session_state.keys()):
                if key.startswith("step7_"):
                    del st.session_state[key]
            st.rerun()


def main() -> None:
    st.set_page_config(page_title=_PAGE_TITLE, page_icon="🧭", layout="wide")
    st.markdown(_RTL_CSS, unsafe_allow_html=True)
    _init_session()
    _render_sidebar()

    query, run_clicked, demo_clicked = _render_hero()
    _render_optional_inputs()

    intent = st.session_state.get(SS["intent"])
    if intent is None:
        if run_clicked:
            session.stage_intent(st.session_state, UIExecutionMode.LIVE, query)
            st.rerun()
        elif demo_clicked:
            session.stage_intent(st.session_state, UIExecutionMode.CACHED_DEMO, HERO_QUERY)
            st.rerun()
    else:
        run_intent = session.consume_intent(st.session_state)
        if run_intent is None:
            return
        outcome = _start_run(run_intent.mode, run_intent.query)
        if outcome == "blank":
            st.warning("اكتب وصف رحلتك قبل البدء.")
        elif outcome == "too_long":
            st.warning(
                f"وصف الرحلة طويل جدًا (الحد الأقصى {_MAX_QUERY_LENGTH} حرفًا)."
            )
        elif outcome == "invalid_dates":
            st.warning("يجب أن يكون تاريخ النهاية مساويًا لتاريخ البداية أو بعده.")
        elif outcome == "demo_dates":
            st.warning("النسخة الاحتياطية ليومين تتطلب تاريخي بداية ونهاية متتاليين.")
        else:
            _execute_request()

    _render_result()


if __name__ == "__main__":
    main()
