"""Fixture-only Arabic PDF sample. This is not generated travel advice."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone

from sard.outputs.fonts import download_pinned_font
from sard.outputs.pdf import render_pdf
from sard.outputs.schemas import CitationSource, Itinerary, ItineraryDay, ItineraryStop, TextBlock


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
                        paragraphs=(
                            TextBlock(
                                "نبدأ بمسار هادئ قرب قنوات الري، مع نص عربي طويل لاختبار التفاف "
                                "الأسطر والمحاذاة والمسافات بين الكلمات. الإحالة ظاهرة هنا "
                                "[CIT-DEMO-SPRING-001].",
                                ("CIT-DEMO-SPRING-001",),
                            ),
                        ),
                        bullets=(
                            TextBlock("مدة الزيارة: ٩٠ دقيقة تقريباً."),
                            TextBlock("رابط القراءة: https://example.org/arabic-springs?lang=ar&ref=PDF"),
                            TextBlock("Bring water - واحمل معك الماء."),
                        ),
                        notes=(TextBlock("قد تكون بعض الأرضيات رطبة؛ ارتدِ حذاءً مناسباً."),),
                    ),
                    ItineraryStop(
                        time="13:15",
                        title="استراحة الغداء",
                        location="ساحة النخيل رقم ٢",
                        paragraphs=(TextBlock("وقت مرن للطعام والراحة، من دون ادعاء توفر مطعم محدد."),),
                    ),
                ),
                notes=(TextBlock("نهاية اليوم الأول عند 17:00 تقريباً."),),
            ),
            ItineraryDay(
                title="الحِرف والسوق",
                date=date(2026, 11, 2),
                stops=(
                    ItineraryStop(
                        time="09:00",
                        title="ورشة الحِرف - Workshop",
                        location="مركز الزوار / Visitor Center",
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
                        paragraphs=(
                            TextBlock(
                                "فقرة إضافية طويلة لضمان اختبار صفحة ثانية بهوامش مريحة، وتباعد "
                                "واضح، وعناوين بارزة، وتذييل يحوي رقم الصفحة ومصادرها."
                            ),
                        ),
                        notes=(TextBlock("اترك وقتاً احتياطياً قدره ٣٠ دقيقة."),),
                    ),
                ),
            ),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-font", action="store_true")
    parser.add_argument("--output", default="step4-arabic-rtl-sample.pdf")
    args = parser.parse_args()
    if args.download_font:
        print(f"Verified font: {download_pinned_font()}")
    artifact = render_pdf(representative_fixture(), args.output)
    print(f"Rendered {artifact.path} ({artifact.size_bytes} bytes)")


if __name__ == "__main__":
    main()
