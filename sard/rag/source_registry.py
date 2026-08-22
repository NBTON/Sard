"""Verified Saudi cultural source registry with authority tiers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

@dataclass(frozen=True)
class SourceRecord:
    name: str
    owner: str
    tier: int  # 1=institutional Saudi, 2=international, 3=supporting
    ar_available: bool
    en_available: bool
    update_frequency: str
    access_method: str  # api, feed, dataset, sitemap, licensed
    license_status: str
    topics: tuple[str, ...]
    freshness: str  # static, daily, live
    restrictions: str

REGISTRY: List[SourceRecord] = [
    SourceRecord("وزارة الثقافة السعودية", "Ministry of Culture", 1, True, True, "daily", "api", "official", ("تراث","فعاليات","متاحف"), "live", "prefer official API"),
    SourceRecord("هيئة التراث", "Heritage Commission", 1, True, True, "weekly", "dataset", "official", ("مواقع تراثية","تسجيل"), "live", "official register"),
    SourceRecord("سعوديبيديا", "Saudipedia", 1, True, True, "daily", "sitemap", "CC BY-SA", ("موسوعة","تاريخ","جغرافيا"), "daily", "sitemap preferred"),
    SourceRecord("وكالة الأنباء السعودية - واس", "SPA", 1, True, True, "live", "feed", "official", ("أخبار رسمية","فعاليات"), "live", "feed"),
    SourceRecord("Discover Culture", "MOC Discover Culture", 1, True, True, "live", "api", "official", ("فعاليات ثقافية"), "live", "live for freshness"),
    SourceRecord("البوابة الوطنية للبيانات المفتوحة", "Open Data Platform", 1, True, True, "monthly", "dataset", "open", ("إحصاءات","ثقافة"), "daily", "dataset"),
    SourceRecord("الهيئة العامة للإحصاء", "GASTAT", 1, True, True, "quarterly", "dataset", "open", ("إحصاءات"), "daily", "dataset"),
    SourceRecord("دارة الملك عبدالعزيز", "Darah", 1, True, False, "weekly", "licensed", "official", ("تاريخ","وثائق"), "static", "licensed"),
    SourceRecord("UNESCO World Heritage Centre", "UNESCO", 2, False, True, "weekly", "api", "open", ("تراث عالمي"), "static", "api"),
    SourceRecord("UNESCO Intangible Heritage", "UNESCO ICH", 2, False, True, "weekly", "api", "open", ("تراث غير مادي"), "static", "api"),
    SourceRecord("Peer-reviewed research", "Academic", 3, True, True, "static", "licensed", "licensed", ("بحث","دراسات"), "static", "license required"),
]

def get_by_tier(tier: int) -> List[SourceRecord]:
    return [s for s in REGISTRY if s.tier == tier]

def get_verified_sources(topics: Optional[List[str]] = None) -> List[SourceRecord]:
    if not topics:
        return REGISTRY
    out = []
    for s in REGISTRY:
        if any(t in s.topics for t in topics):
            out.append(s)
    return sorted(out, key=lambda r: (r.tier, r.name))
