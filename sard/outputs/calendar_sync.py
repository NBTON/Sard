"""Smart Heritage Calendar & Temporal Sync for Sard.

Provides dual Hijri-Gregorian cultural synchronization, traditional astronomical
seasons (Suhail, Murba'aniyah, Wasm, Kharif), major Saudi festival databases,
RFC 5545 (.ics) file generation, and direct 1-click Google Calendar sync links.
"""

from __future__ import annotations

import datetime
import io
import logging
import urllib.parse
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from icalendar import Calendar, Event

logger = logging.getLogger("sard.outputs.calendar_sync")

TIMEZONE = "Asia/Riyadh"


@dataclass
class HeritageCalendarEvent:
    """Represents a heritage festival, astronomical season, or cultural holiday."""
    id: str
    title_ar: str
    title_en: str
    category: str  # festival, astronomical_season, national_holiday, religious_season, agricultural
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD
    hijri_start: str = ""
    hijri_end: str = ""
    region: str = "المملكة العربية السعودية"
    location_name: str = ""
    description_ar: str = ""
    description_en: str = ""
    cultural_prep_tips: List[str] = field(default_factory=list)
    astronomical_stars: List[str] = field(default_factory=list)
    organizer: str = "وزارة الثقافة / الهيئة العامة للترفيه / هيئة التراث"
    official_url: str = ""

    def google_calendar_url(self) -> str:
        """Generates a direct 1-click Google Calendar event creation URL."""
        start_fmt = self.start_date.replace("-", "")
        end_dt = datetime.date.fromisoformat(self.end_date) + datetime.timedelta(days=1)
        end_fmt = end_dt.strftime("%Y%m%d")
        dates_param = f"{start_fmt}/{end_fmt}"

        details_parts = [self.description_ar]
        if self.hijri_start:
            details_parts.append(f"\nالتاريخ الهجري: {self.hijri_start}")
        if self.cultural_prep_tips:
            details_parts.append("\nنصائح الاستعداد الثقافي:")
            for tip in self.cultural_prep_tips:
                details_parts.append(f"• {tip}")
        if self.official_url:
            details_parts.append(f"\nالموقع الرسمي: {self.official_url}")

        params = {
            "action": "TEMPLATE",
            "text": self.title_ar,
            "dates": dates_param,
            "details": "\n".join(details_parts),
            "location": f"{self.location_name}, {self.region}" if self.location_name else self.region,
            "ctz": TIMEZONE,
        }
        return f"https://calendar.google.com/calendar/render?{urllib.parse.urlencode(params)}"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["google_calendar_url"] = self.google_calendar_url()
        return d


# ---------------------------------------------------------------------------
# Curated Heritage & Seasonal Database (2026-2027 Baseline)
# ---------------------------------------------------------------------------

HERITAGE_EVENTS_DATABASE: List[HeritageCalendarEvent] = [
    # 1. National & Historical Holidays
    HeritageCalendarEvent(
        id="saudi-foundation-day-2026",
        title_ar="يوم التأسيس السعودي 1139هـ",
        title_en="Saudi Foundation Day",
        category="national_holiday",
        start_date="2026-02-22",
        end_date="2026-02-22",
        hijri_start="4 شعبان 1447هـ",
        hijri_end="4 شعبان 1447هـ",
        region="المملكة العربية السعودية",
        location_name="الدرعية التاريخية وجميع مناطق المملكة",
        description_ar="ذكرى تأسيس الدولة السعودية الأولى على يد الإمام محمد بن سعود عام 1727م (1139هـ)، احتفاءً بالجذور الراسخة والعراقة.",
        description_en="Commemoration of the founding of the First Saudi State in 1727 by Imam Muhammad bin Saud.",
        cultural_prep_tips=[
            "ارتداء الأزياء التقليدية التراثية الخاصة بكل منطقة (المردون، الثوب العسيري، الدقلة، البرقع).",
            "زيارة حي الطريف التاريخي بالدرعية والفعاليات التراثية الحية.",
            "إعداد الأطباق السعودية الأصيلة مثل الجريش والقرصان.",
        ],
        official_url="https://www.foundingday.sa",
    ),
    HeritageCalendarEvent(
        id="saudi-national-day-2026",
        title_ar="اليوم الوطني السعودي 96",
        title_en="Saudi National Day 96",
        category="national_holiday",
        start_date="2026-09-23",
        end_date="2026-09-23",
        hijri_start="12 ربيع الأول 1448هـ",
        hijri_end="12 ربيع الأول 1448هـ",
        region="المملكة العربية السعودية",
        location_name="كافة مناطق ومدن المملكة",
        description_ar="ذكرى توحيد المملكة العربية السعودية على يد الملك المؤسس عبد العزيز بن عبد الرحمن آل سعود عام 1932م.",
        description_en="Celebration of the unification of the Kingdom of Saudi Arabia by King Abdulaziz.",
        cultural_prep_tips=[
            "تزيين المنازل والمجالس بالأعلام الوطنية واللون الأخضر التراثي.",
            "حضور العروض الجوية والبحرية ومسيرات الفنون الشعبية (العرضة السعودية).",
        ],
        official_url="https://nd.gea.gov.sa",
    ),

    # 2. Traditional Astronomical Seasons (الأنواء والمواسم الفلكية التراثية)
    HeritageCalendarEvent(
        id="season-suhail-2026",
        title_ar="طلوع نجم سهيل (انكسار حدة الحرارة)",
        title_en="Rising of Suhail Star",
        category="astronomical_season",
        start_date="2026-08-24",
        end_date="2026-10-15",
        hijri_start="11 صفر 1448هـ",
        hijri_end="4 ربيع الآخر 1448هـ",
        region="شبه الجزيرة العربية",
        location_name="المملكة العربية السعودية",
        description_ar="بشير انكسار القيظ والحر الشديد وبداية برودة الليل، وفيه تبدأ هجرة الطيور وجني أواخر التمور.",
        description_en="The appearance of Canopus (Suhail) marking the end of peak summer heat and the beginning of cooler nights.",
        astronomical_stars=["الطرف", "الجبهة", "الزبرة", "الصرفة"],
        cultural_prep_tips=[
            "تهيئة المزارع والواحات لموسم الشتاء ونقل المواشي.",
            "الاستعداد لجلسات السمر المسائية في الهواء الطلق مع انخفاض درجات الحرارة ليلاً.",
        ],
    ),
    HeritageCalendarEvent(
        id="season-wasm-2026",
        title_ar="موسم الوسم (موسم أمطار الخير ونمو الفقع)",
        title_en="Al-Wasm Rain Season",
        category="astronomical_season",
        start_date="2026-10-16",
        end_date="2026-12-06",
        hijri_start="5 ربيع الآخر 1448هـ",
        hijri_end="26 جمادى الأولى 1448هـ",
        region="المملكة العربية السعودية",
        location_name="صحاري ورياض المملكة (الصمان، الدهناء، نجد)",
        description_ar="أفضل أوقات نزول الأمطار النافعة للأرض؛ حيث ينبت به نبات الفقع (الكمأة) والأعشاب البرية العطرية (الخزامى، النفل).",
        description_en="The blessed autumn rain season that fosters wild desert truffles (Faq'a) and lavender blooms.",
        astronomical_stars=["العواء", "السماك", "الغفر", "الزبانا"],
        cultural_prep_tips=[
            "الاستعداد لموسم الكشتات والرحلات البرية في الفياض والرياض.",
            "صيانة أدوات التخييم التراثية ودلال القهوة والشاي على الحطب.",
        ],
    ),
    HeritageCalendarEvent(
        id="season-murbaaniyah-2026",
        title_ar="موسم المربعانية (ذروة برد الشتاء)",
        title_en="Al-Murba'aniyah (Winter Peak)",
        category="astronomical_season",
        start_date="2026-12-07",
        end_date="2027-01-15",
        hijri_start="27 جمادى الأولى 1448هـ",
        hijri_end="7 رجب 1448هـ",
        region="المملكة العربية السعودية",
        location_name="المناطق الشمالية والوسطى",
        description_ar="أربعون يوماً تمثل أشد فترات الشتاء برودة، يتخللها الصقيع وهبوب الرياح الشمالية الباردة (رياح النعائم).",
        description_en="The 40 coldest days of winter across the Arabian Peninsula.",
        astronomical_stars=["الإكليل", "القلب", "الشولة"],
        cultural_prep_tips=[
            "ارتداء الفروة الشتوية والبشت الصوفي الثقيل.",
            "إعداد الأكلات الشتوية الغنية بالطاقة كالحنيني، المرقوق، والمطازيز، وحليب الزنجبيل.",
            "إشعال شبّة النار والسمر حول الوجار في المجالس والمخيمات.",
        ],
    ),

    # 3. Cultural Festivals & Seasons
    HeritageCalendarEvent(
        id="alula-moments-2026",
        title_ar="لحظات العلا الثقافية ومهرجان طنطورة",
        title_en="AlUla Moments & Winter at Tantora",
        category="festival",
        start_date="2026-11-20",
        end_date="2027-02-15",
        hijri_start="10 جمادى الأولى 1448هـ",
        hijri_end="8 شعبان 1448هـ",
        region="منطقة المدينة المنورة",
        location_name="محافظة العلا التاريخية (الحجر، البلدة القديمة، مسرح مرايا)",
        description_ar="مهرجان شتوي ثقافي يحتفي بتاريخ دادان والأنباط، متضمناً فعاليات فنية وموسيقية وعروض التراث الحي.",
        description_en="Cultural festival celebrating the ancient Nabataean and Dadanite heritage of AlUla.",
        cultural_prep_tips=[
            "حجز تذاكر الحجر (أول موقع تراث عالمي لليونسكو في السعودية) مسبقاً.",
            "حضور احتفال شمس الشتاء التراثي عند ساعة الطنطورة بالبلدة القديمة.",
        ],
        official_url="https://www.experiencealula.com",
    ),
    HeritageCalendarEvent(
        id="souq-okaz-2026",
        title_ar="سوق عكاظ الثقافي التاريخي",
        title_en="Souq Okaz Cultural Festival",
        category="festival",
        start_date="2026-08-10",
        end_date="2026-08-25",
        hijri_start="27 محرم 1448هـ",
        hijri_end="12 صفر 1448هـ",
        region="منطقة مكة المكرمة",
        location_name="محافظة الطائف",
        description_ar="إحياء لأشهر أسواق العرب القديمة، يجمع شعراء الفصحى والحرفيين وقوافل الإبل التراثية ومسارح المعلقات.",
        description_en="Revival of the ancient Arabian market with classical poetry recitations, crafts, and heritage caravans.",
        cultural_prep_tips=[
            "الاطلاع على معلقات شعراء عكاظ (امرؤ القيس، عنترة، طرفة بن العبد).",
            "شراء المنتجات الحرفية الأصيلة ودهن الورد الطائفي.",
        ],
        official_url="https://www.moc.gov.sa",
    ),
    HeritageCalendarEvent(
        id="buraidah-date-festival-2026",
        title_ar="مهرجان بريدة للتمور (أكبر كرنفال للتمور عالمياً)",
        title_en="Buraidah Date Festival",
        category="agricultural",
        start_date="2026-08-01",
        end_date="2026-08-30",
        hijri_start="17 صفر 1448هـ",
        hijri_end="17 ربيع الأول 1448هـ",
        region="منطقة القصيم",
        location_name="مدينة التمور ببريدة",
        description_ar="أضخم تجمع زراعي تجاري لتمور السكري والصقعي والإخلاص، يعكس تاريخ زراعة النخيل في نجد.",
        description_en="The world's largest date festival showcasing Sukkari, Sag'ai, and Khalas harvests.",
        cultural_prep_tips=[
            "زيارة السوق في ساعات الصباح الباكر بعد صلاة الفجر لمشاهدة حراج التمور التقليدي.",
            "تذوق الرطب السكري الطازج مع القهوة السعودية المتبلة بالهيل والزعفران.",
        ],
        official_url="https://www.qassim.gov.sa",
    ),
    HeritageCalendarEvent(
        id="red-sea-film-festival-2026",
        title_ar="مهرجان البحر الأحمر السينمائي الدولي",
        title_en="Red Sea International Film Festival",
        category="festival",
        start_date="2026-11-28",
        end_date="2026-12-07",
        hijri_start="18 جمادى الأولى 1448هـ",
        hijri_end="27 جمادى الأولى 1448هـ",
        region="منطقة مكة المكرمة",
        location_name="جدة التاريخية (البلد)",
        description_ar="تظاهرة سينمائية عالمية تحتفي بالرواة وصناع الأفلام وسط أزقة وبيوت الروشان التراثية بجدة التاريخية.",
        description_en="International film festival hosted in the historic UNESCO heritage district of Jeddah Al-Balad.",
        cultural_prep_tips=[
            "التجول بين بيوت الروشان الحجازية كبيت نصيف وبيت باعشن.",
            "تذوق المأكولات الحجازية التقليدية في حارات الشام والمظلوم واليمن.",
        ],
        official_url="https://redseafilmfest.com",
    ),
]


class HeritageCalendarSync:
    """Provides lookup, filtering, and export of heritage calendars."""

    def __init__(self, events: Optional[List[HeritageCalendarEvent]] = None):
        self.events = events or list(HERITAGE_EVENTS_DATABASE)

    def search_events(
        self,
        query: str = "",
        category: Optional[str] = None,
        region: Optional[str] = None,
        month: Optional[int] = None,
    ) -> List[HeritageCalendarEvent]:
        """Search and filter heritage events."""
        results = []
        q_norm = query.strip().lower()
        for ev in self.events:
            if category and ev.category != category:
                continue
            if region and region not in ev.region:
                continue
            if month:
                try:
                    s_m = int(ev.start_date.split("-")[1])
                    e_m = int(ev.end_date.split("-")[1])
                    if not (s_m <= month <= e_m or (s_m > e_m and (month >= s_m or month <= e_m))):
                        continue
                except Exception:
                    pass
            if q_norm:
                match_str = f"{ev.title_ar} {ev.title_en} {ev.description_ar} {ev.region} {ev.location_name}".lower()
                if q_norm not in match_str:
                    continue
            results.append(ev)
        return results

    def generate_ics_data(self, events: Optional[List[HeritageCalendarEvent]] = None) -> bytes:
        """Generates standard RFC 5545 .ics calendar data."""
        ev_list = events if events is not None else self.events
        cal = Calendar()
        cal.add("prodid", "-//Sard Cultural Agent//Saudi Heritage Calendar 2.0//AR")
        cal.add("version", "2.0")
        cal.add("x-wr-calname", "التقويم الثقافي السعودي | سرد")
        cal.add("x-wr-timezone", TIMEZONE)

        for ev in ev_list:
            ical_ev = Event()
            ical_ev.add("uid", f"{ev.id}@sard.culture.sa")
            ical_ev.add("summary", ev.title_ar)
            
            s_date = datetime.date.fromisoformat(ev.start_date)
            e_date = datetime.date.fromisoformat(ev.end_date) + datetime.timedelta(days=1)
            ical_ev.add("dtstart", s_date)
            ical_ev.add("dtend", e_date)
            
            loc = f"{ev.location_name}, {ev.region}" if ev.location_name else ev.region
            ical_ev.add("location", loc)

            desc_parts = [ev.description_ar]
            if ev.hijri_start:
                desc_parts.append(f"التاريخ الهجري: {ev.hijri_start}")
            if ev.cultural_prep_tips:
                desc_parts.append("\nالاستعداد الثقافي:")
                for tip in ev.cultural_prep_tips:
                    desc_parts.append(f"- {tip}")
            if ev.official_url:
                desc_parts.append(f"الرابط: {ev.official_url}")

            ical_ev.add("description", "\n".join(desc_parts))
            cal.add_component(ical_ev)

        return cal.to_ical()

    def export_ics(self, events: Optional[List[HeritageCalendarEvent]] = None) -> str:
        data = self.generate_ics_data(events)
        return data.decode("utf-8", errors="replace")

    def get_google_calendar_url(self, event: HeritageCalendarEvent) -> str:
        return event.google_calendar_url()

    def save_ics_file(
        self,
        filename: Optional[str] = None,
        output_dir: Optional[Path] = None,
        events: Optional[List[HeritageCalendarEvent]] = None,
    ) -> Tuple[Path, str]:
        out_dir = output_dir or Path("output")
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_name = filename or "heritage_calendar.ics"
        if not safe_name.endswith(".ics"):
            safe_name += ".ics"
        target_path = out_dir / safe_name
        data = self.generate_ics_data(events)
        target_path.write_bytes(data)
        logger.info("Saved Heritage Calendar .ics: %s (%d bytes)", target_path, len(data))
        return target_path, safe_name
