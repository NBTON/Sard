"""Fixture-only Arabic PDF sample. This is not generated travel advice."""

from __future__ import annotations

import argparse
from datetime import date, datetime, time as time_type, timezone

from sard.outputs.fonts import download_pinned_font
from sard.outputs.artifacts import ArtifactManager
from sard.outputs.calendar import render_calendar
from sard.outputs.pdf import render_pdf
from sard.outputs.raw import render_raw_text
from sard.outputs.schemas import CitationSource, FieldSupport, Itinerary, ItineraryDay, ItineraryStop, TextBlock


def representative_fixture() -> Itinerary:
    """Return invented layout content with real-looking but explicitly fake URLs."""

    sources = (
        CitationSource(
            citation_id="CIT-DEMO-SPRING-001",
            title="مصدر تجريبي عن العيون المائية",
            url="https://example.org/arabic-springs?lang=ar&ref=PDF",
            page=12,
            section="وصف الموقع",
            publication_date=date(2024, 2, 15),
        ),
        CitationSource(
            citation_id="CIT-DEMO-MARKET-002",
            title="Sample Heritage Market Guide - دليل تجريبي",
            url="https://example.com/guides/heritage-market/2025",
            section="Visitor information",
        ),
    )
    return Itinerary(
        title="نموذج تجريبي فقط: رحلة يومين في الواحة",
        summary=(
            "هذه بيانات ثابتة لا تمثل توصية سفر. تختبر العربية من اليمين إلى اليسار، "
            "English labels، الأرقام ١٢٣ و123، وعلامات الترقيم: (؟)، فاصلة، ونقطتان."
        ),
        generated_at=datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc),
        sources=sources,
        notes=(TextBlock("تحقق من ساعات العمل محلياً؛ الروابط أدناه للاختبار فقط."),),
        days=(
            ItineraryDay(
                title="الماء والنخيل",
                date=date(2026, 11, 1),
                stops=(
                    ItineraryStop(
                        time="08:30 صباحاً",
                        title="جولة العين التاريخية",
                        location="بوابة الواحة - Gate A",
                        stop_id="spring-tour",
                        start_time=time_type(8, 30),
                        end_time=time_type(10, 0),
                        citation_ids=("CIT-DEMO-SPRING-001",),
                        field_support=(
                            FieldSupport("title", ("CIT-DEMO-SPRING-001",)),
                            FieldSupport("location", ("CIT-DEMO-SPRING-001",)),
                            FieldSupport("time", provenance="user_provided"),
                            FieldSupport("description", ("CIT-DEMO-SPRING-001",)),
                            FieldSupport("practical_notes", provenance="user_provided"),
                            FieldSupport("notes", provenance="user_provided"),
                        ),
                        paragraphs=(
                            TextBlock(
                                "نبدأ بمسار هادئ قرب قنوات الري، مع نص عربي طويل لاختبار التفاف "
                                "الأسطر والمحاذاة والمسافات بين الكلمات. الإحالة ظاهرة هنا "
                                "[CIT-DEMO-SPRING-001].",
                                ("CIT-DEMO-SPRING-001",),
                            ),
                        ),
                        bullets=(
                            TextBlock("رابط القراءة: https://example.org/arabic-springs?lang=ar&ref=PDF"),
                            TextBlock("Bring water - واحمل معك الماء."),
                        ),
                        notes=(TextBlock("قد تكون بعض الأرضيات رطبة؛ ارتدِ حذاءً مناسباً."),),
                    ),
                    ItineraryStop(
                        time="13:15",
                        title="استراحة الغداء",
                        location="ساحة النخيل رقم ٢",
                        stop_id="lunch",
                        start_time=time_type(13, 15),
                        end_time=time_type(14, 15),
                        field_support=(
                            FieldSupport("title", provenance="user_provided"),
                            FieldSupport("location", provenance="user_provided"),
                            FieldSupport("time", provenance="user_provided"),
                            FieldSupport("description", provenance="user_provided"),
                        ),
                        paragraphs=(TextBlock("وقت مرن للطعام والراحة، من دون ادعاء توفر مطعم محدد."),),
                    ),
                ),
                notes=(),
                field_support=(
                    FieldSupport("title", provenance="user_provided"),
                    FieldSupport("date", provenance="user_provided"),
                    FieldSupport("notes", provenance="user_provided"),
                ),
            ),
            ItineraryDay(
                title="الحِرف والسوق",
                date=date(2026, 11, 2),
                stops=(
                    ItineraryStop(
                        time="09:00",
                        title="ورشة الحِرف - Workshop",
                        location="مركز الزوار / Visitor Center",
                        stop_id="workshop",
                        start_time=time_type(9, 0),
                        end_time=time_type(10, 30),
                        citation_ids=("CIT-DEMO-MARKET-002",),
                        field_support=(
                            FieldSupport("title", ("CIT-DEMO-MARKET-002",)),
                            FieldSupport("location", ("CIT-DEMO-MARKET-002",)),
                            FieldSupport("time", provenance="user_provided"),
                            FieldSupport("description", ("CIT-DEMO-MARKET-002",)),
                            FieldSupport("practical_notes", provenance="user_provided"),
                        ),
                        paragraphs=(
                            TextBlock(
                                "محتوى تمثيلي يختبر المزج بين Arabic وEnglish والرموز %50 و+3، "
                                "مع إحالة مستقرة [CIT-DEMO-MARKET-002].",
                                ("CIT-DEMO-MARKET-002",),
                            ),
                        ),
                        bullets=(
                            TextBlock("الوصول قبل الموعد بـ15 دقيقة."),
                            TextBlock("لا تُخترع أسعار أو بيانات حجز في هذا النموذج."),
                        ),
                    ),
                    ItineraryStop(
                        time="14:45 مساءً",
                        title="المشي في السوق القديم",
                        location="المدخل الشرقي - East Entrance",
                        stop_id="market-walk",
                        start_time=time_type(14, 45),
                        end_time=time_type(16, 0),
                        field_support=(
                            FieldSupport("title", provenance="user_provided"),
                            FieldSupport("location", provenance="user_provided"),
                            FieldSupport("time", provenance="user_provided"),
                            FieldSupport("description", provenance="user_provided"),
                            FieldSupport("notes", provenance="user_provided"),
                        ),
                        paragraphs=(
                            TextBlock(
                                "فقرة إضافية طويلة لضمان اختبار صفحة ثانية بهوامش مريحة، وتباعد "
                                "واضح، وعناوين بارزة، وتذييل يحوي رقم الصفحة ومصادرها."
                            ),
                        ),
                        notes=(TextBlock("اترك وقتاً احتياطياً قدره ٣٠ دقيقة."),),
                    ),
                ),
                field_support=(
                    FieldSupport("title", provenance="user_provided"),
                    FieldSupport("date", provenance="user_provided"),
                ),
            ),
        ),
        field_support=(
            FieldSupport("title", provenance="user_provided"),
            FieldSupport("summary", provenance="user_provided"),
            FieldSupport("notes", provenance="user_provided"),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-font", action="store_true")
    parser.add_argument("--output", default="step4-arabic-rtl-sample.pdf")
    parser.add_argument("--step6", action="store_true", help="Generate the three Step 6 artifacts")
    parser.add_argument("--run-id", default="step6-sample")
    parser.add_argument("--output-root", default="output/runs")
    parser.add_argument("--date", action="append", default=[])
    args = parser.parse_args()
    if args.download_font:
        print(f"Verified font: {download_pinned_font()}")
    fixture = representative_fixture()
    if not args.step6:
        artifact = render_pdf(fixture, args.output)
        print(f"Rendered {artifact.path} ({artifact.size_bytes} bytes)")
        return
    from dataclasses import replace
    dates = tuple(date.fromisoformat(value) for value in args.date)
    fixture = replace(fixture, run_id=args.run_id, explicit_dates=dates)
    manager = ArtifactManager(args.output_root, args.run_id)
    pdf_temp = manager.temporary_path(".pdf")
    try:
        import os
        old = os.environ.get("SARD_PDF_OUTPUT_ROOT")
        os.environ["SARD_PDF_OUTPUT_ROOT"] = str(manager.run_dir)
        try:
            render_pdf(fixture, pdf_temp)
        finally:
            if old is None: os.environ.pop("SARD_PDF_OUTPUT_ROOT", None)
            else: os.environ["SARD_PDF_OUTPUT_ROOT"] = old
        result = manager.publish_generated_file(pdf_temp, filename="itinerary.pdf", artifact_type="pdf", display_label="برنامج الرحلة PDF", mime_type="application/pdf")
        print(f"Rendered PDF: {result.absolute_path} ({result.size_bytes} bytes)")
    finally:
        pdf_temp.unlink(missing_ok=True)
    raw = render_raw_text(
        "نموذج تجريبي موثق [CIT-DEMO-SPRING-001] و[CIT-DEMO-MARKET-002].",
        fixture.sources,
    )
    raw_result = manager.write_bytes(raw.data, filename="answer.txt", artifact_type="raw_text", display_label="الإجابة العربية الخام", mime_type="text/plain; charset=utf-8")
    print(f"Rendered text: {raw_result.absolute_path} ({raw_result.size_bytes} bytes)")
    try:
        calendar = render_calendar(fixture)
        calendar_result = manager.write_bytes(calendar.data, filename="itinerary.ics", artifact_type="calendar", display_label="تقويم الرحلة", mime_type="text/calendar; charset=utf-8", warnings=calendar.warnings)
        print(f"Rendered calendar: {calendar_result.absolute_path} ({calendar_result.size_bytes} bytes)")
    except Exception as exc:
        print(f"Calendar skipped: {exc}")


if __name__ == "__main__":
    main()
