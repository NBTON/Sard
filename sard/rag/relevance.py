"""Query/evidence relevance gates for cultural retrieval.

Retrieval scores answer *how close* a document is in a search index.  They do
not answer whether the document belongs to the user's entity, region, or
cultural mandate.  This module supplies that second, deliberately
conservative check.  It is shared by the bundled/local retrievers and the
agent/router boundary so a high lexical score cannot turn an unrelated
document into a verified answer.

The vocabulary below is an index of aliases, not answer content.  It is used
to recognize entities across Arabic paraphrases and reordered questions; all
facts still come from the retrieved source excerpt and its metadata.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from sard.rag.normalize import normalize_arabic

logger = logging.getLogger(__name__)


_STOP_WORDS = {
    "في", "من", "على", "عن", "إلى", "الى", "مع", "كيف", "ما", "ماذا", "هل",
    "هو", "هي", "هذا", "هذه", "التي", "الذي", "الذين", "و", "أو", "أي", "اين",
    "أين", "متى", "لماذا", "ماهي", "ماهي", "the", "a", "an", "in", "of", "and",
    "how", "what", "is", "are", "for", "to", "about", "tell", "me", "please",
    "traditional", "tradition", "cultural", "heritage", "saudi", "السعودية", "السعودي",
    "تراث", "تراثية", "تقليدية", "تقليدي", "معلومات", "موضوع", "أريد", "اريد", "أعطني",
    "اعطني", "اشرح", "ماهو", "ماهي",
}

_TOPIC_ALIASES: dict[str, tuple[str, ...]] = {
    "shrimp_drying": (
        "روبيان", "ربيان", "جمبري", "قريدس", "shrimp", "prawns", "تجفيف الروبيان",
        "تجفيف الربيان", "الروبيان المجفف", "الربيان المجفف", "حفظ الروبيان",
        "تخزين الروبيان", "dried shrimp", "drying prawns", "coastal shrimp preservation",
    ),
    "al_ahsa_springs": (
        "ينابيع", "ينابيع حارة",
        "الينابيع الحارة", "العيون الحارة", "العيون المائية", "عين حارة",
        "عيون الأحساء", "عيون الاحساء", "مياه كبريتية", "hot springs",
    ),
    "saudi_coffee_majlis": (
        "القهوة السعودية", "القهوة العربية", "saudi coffee", "arabic coffee",
        "قهوة سعودية", "دلة", "فنجان", "هيل", "مجلس", "majlis", "hospitality",
        "ضيافة", "صب القهوة", "صبة الحشمة",
    ),
    "southern_bread": (
        "خبز الجنوب", "الخبز الجنوبي", "خبز عسيري", "خبز جازان", "خبز نجران",
        "خبز جنوبي", "southern bread", "asir bread", "jazan bread", "najran bread",
    ),
    "known_food": (
        "جريش", "المقشوش", "كبسة", "حنيني", "سليق", "مقشوش", "مرقوق",
        "jareesh", "kabsa", "hanini", "saleeg",
    ),
    "fashion_craft": (
        "سدو", "السدو", "بشت", "البشت", "بشت حساوي", "البشت الحساوي", "مشلح",
        "قط عسيري", "القط العسيري", "ورد طائفي", "حرفة", "حرف تقليدية", "craft",
        "handicraft", "artisan", "sadu", "bisht",
    ),
}

_TOPIC_SECTORS: dict[str, frozenset[str]] = {
    "shrimp_drying": frozenset({"food", "culinary", "heritage"}),
    "al_ahsa_springs": frozenset({"heritage", "place", "tourism", "geography"}),
    "saudi_coffee_majlis": frozenset({"food", "culinary", "etiquette", "ritual", "heritage"}),
    "southern_bread": frozenset({"food", "culinary", "heritage"}),
    "known_food": frozenset({"food", "culinary", "heritage"}),
    "fashion_craft": frozenset({"fashion", "visual", "craft", "heritage"}),
}

_REGION_ALIASES: dict[str, tuple[str, ...]] = {
    "eastern": (
        "المنطقة الشرقية", "الشرقية", "الأحساء", "الاحساء", "الهفوف", "القطيف",
        "تاروت", "الدمام", "الظهران", "الخبر", "الساحل الشرقي", "ساحل", "الساحل",
        "ساحلي", "الساحلية", "سواحل", "eastern province", "al-ahsa", "qatif",
    ),
    # Keep the southern cluster and its provinces as separate keys.  The
    # broad ``south`` key is compatible with any province, while an explicit
    # Jazan/Najran/Asir query is not compatible with another province.
    "south": ("الجنوب", "جنوب المملكة", "southern", "south"),
    "asir": ("عسير", "عسيري", "أبها", "ابها", "رجال ألمع", "فيفاء", "asir"),
    "jazan": ("جازان", "جيزان", "فرسان", "صبيا", "أبو عريش", "صامطة", "jazan"),
    "najran": ("نجران", "نجراني", "najran"),
    "najd": ("نجد", "الرياض", "الدرعية", "القصيم", "طريف", "najd", "riyadh", "diriyah"),
    "hijaz": ("الحجاز", "مكة", "جدة", "المدينة", "الطائف", "العلا", "hijaz", "jeddah", "alula"),
    "north": ("حائل", "تبوك", "الجوف", "عرعر", "hail", "tabuk", "jouf"),
    "national": ("السعودية", "السعودي", "المملكة", "saudi", "national", "all regions"),
}

_SECTOR_ALIASES: dict[str, tuple[str, ...]] = {
    "food": ("طعام", "أكلة", "وصفة", "طبخة", "مأكولات", "طبخ", "food", "dish", "recipe", "cuisine"),
    "etiquette": ("إتيكيت", "آداب", "ضيافة", "مجلس", "سلوك", "etiquette", "hospitality", "majlis"),
    "fashion": ("أزياء", "ملابس", "بشت", "مشلح", "fashion", "attire", "dress"),
    "craft": ("حرفة", "حرف", "سدو", "صناعة يدوية", "craft", "artisan", "handicraft"),
    "place": ("موقع", "مكان", "واحة", "ينابيع", "عيون", "place", "oasis", "springs"),
}


@dataclass(frozen=True)
class CulturalQueryProfile:
    """The entity/mandate/region constraints extracted from a query."""

    topics: frozenset[str]
    regions: frozenset[str]
    sectors: frozenset[str]
    terms: frozenset[str]


_REGION_GROUPS: dict[str, frozenset[str]] = {
    "south": frozenset({"south", "asir", "jazan", "najran"}),
}

# These terms identify coffee itself.  Generic hospitality/majlis wording is
# deliberately not enough: otherwise a municipal-council question or an
# unrelated tourism/craft page can be promoted to coffee evidence.
_COFFEE_STRONG_ALIASES = (
    "القهوة", "قهوة", "دلة", "فنجان", "هيل", "بن", "صب القهوة", "صبة الحشمة",
    "saudi coffee", "arabic coffee",
)


def _norm(value: Any) -> str:
    return normalize_arabic(str(value or "")).casefold().strip()


# Ultra-short aliases must never match as substrings of longer words: بن
# (coffee beans) appears inside بنفحاته (fragrance), البنت (girl), البنوك
# (banks); لبن alone is yogurt, not coffee. These match only as standalone
# tokens with an optional leading conjunction/preposition/article.
_STRICT_TOKEN_ALIASES: dict[str, str] = {
    "بن": r"(?<![\u0600-\u06FF])و?(?:[بف]|لل)?(?:ال)?بن(?![\u0600-\u06FF])",
}


def _contains(text: str, phrase: str) -> bool:
    text_n = _norm(text)
    phrase_n = _norm(phrase)
    if not text_n or not phrase_n:
        return False
    strict = _STRICT_TOKEN_ALIASES.get(phrase_n)
    if strict is not None:
        return bool(re.search(strict, text_n))
    if re.search(r"[a-z]", phrase_n):
        return bool(re.search(rf"(?<![a-z]){re.escape(phrase_n)}(?![a-z])", text_n))
    return phrase_n in text_n


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[\w\u0600-\u06ff]+", _norm(text))
    return {word for word in words if len(word) > 2 and word not in _STOP_WORDS}


def _keys_for_aliases(text: str, aliases: Mapping[str, Iterable[str]]) -> frozenset[str]:
    return frozenset(
        key for key, values in aliases.items() if any(_contains(text, alias) for alias in values)
    )


def query_profile(query: str) -> CulturalQueryProfile:
    """Extract conservative constraints from the current query only."""

    topics = set(_keys_for_aliases(query, _TOPIC_ALIASES))
    regions = set(_keys_for_aliases(query, _REGION_ALIASES))
    sectors = set(_keys_for_aliases(query, _SECTOR_ALIASES))

    # Plain "bread" becomes a southern-bread entity only when the query also
    # names the southern region.  This prevents a generic bread mention in an
    # unrelated source from being promoted to a southern tradition.
    if ("south" in regions or "asir" in regions) and any(
        _contains(query, alias) for alias in ("خبز", "bread")
    ):
        topics.add("southern_bread")

    # مجلس/ضيافة/آداب are sector hints, not a coffee entity.  A coffee topic
    # requires at least one coffee-specific lexeme in the current text.
    if "saudi_coffee_majlis" in topics and not any(
        _contains(query, alias) for alias in _COFFEE_STRONG_ALIASES
    ):
        topics.remove("saudi_coffee_majlis")

    return CulturalQueryProfile(
        topics=frozenset(topics),
        regions=frozenset(regions),
        sectors=frozenset(sectors),
        terms=frozenset(_tokens(query)),
    )


def alias_group_phrases() -> tuple[frozenset[str], ...]:
    """Alias groups as raw phrases across topic/region/sector tables.

    Verification layers reuse this so paraphrases that share an alias
    group (e.g. ``الينابيع الحارة`` ~ ``العيون الحارة``) are not scored as
    pure token misses. Callers tokenize phrases with their own normalizer;
    this function adds no new topic, only the documented spellings.
    """
    groups: list[frozenset[str]] = []
    for table in (_TOPIC_ALIASES, _REGION_ALIASES, _SECTOR_ALIASES):
        try:
            tables = table.values()
        except Exception as exc_reason:
            logger.debug(
                "Alias table values skipped (%s).",
                type(exc_reason).__name__,
            )
            continue
        for aliases in tables:
            try:
                groups.append(frozenset(aliases))
            except Exception as exc_reason:
                logger.debug(
                    "Alias group skipped (%s).",
                    type(exc_reason).__name__,
                )
                continue
    return tuple(groups)


def expanded_query_terms(query: str) -> frozenset[str]:
    """Return shared lexical terms for bundled and local retrieval.

    The expansion is derived from the current query's recognized aliases; it
    does not add a different topic.  In particular, جمبري expands to the
    documented روبيان/ربيان spellings so the local corpus and bundled index
    use the same Arabic entity normalization.
    """

    profile = query_profile(query)
    terms = set(profile.terms)
    for topic in profile.topics:
        for alias in _TOPIC_ALIASES.get(topic, ()):
            terms.update(_tokens(alias))
    for region in profile.regions:
        for alias in _REGION_ALIASES.get(region, ()):
            terms.update(_tokens(alias))
    for sector in profile.sectors:
        for alias in _SECTOR_ALIASES.get(sector, ()):
            terms.update(_tokens(alias))
    return frozenset(terms)


def _document_text(hit: Mapping[str, Any]) -> str:
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), Mapping) else {}
    fields = (
        hit.get("title", ""),
        hit.get("chunk", ""),
        hit.get("text", ""),
        hit.get("content", ""),
        hit.get("excerpts", ""),
        hit.get("source", ""),
        hit.get("topic", ""),
        hit.get("sector", ""),
        *(metadata.get(key, "") for key in ("title", "topic", "sector", "region", "region_code", "culture", "keywords", "source_name")),
    )
    return " ".join(str(field or "") for field in fields)


def evidence_profile(hit: Mapping[str, Any]) -> CulturalQueryProfile:
    """Profile a hit using structured metadata first, then its excerpt."""

    return query_profile(_document_text(hit))


def _metadata(hit: Mapping[str, Any]) -> Mapping[str, Any]:
    value = hit.get("metadata")
    return value if isinstance(value, Mapping) else {}


def _explicit_sector(hit: Mapping[str, Any]) -> str:
    metadata = _metadata(hit)
    return _norm(hit.get("sector") or metadata.get("sector"))


def _explicit_region_keys(hit: Mapping[str, Any]) -> frozenset[str]:
    metadata = _metadata(hit)
    region_text = " ".join(
        str(hit.get(key) or metadata.get(key) or "")
        for key in ("region", "region_code", "culture")
    )
    # Metadata only: body text often mentions several regions illustratively
    # (e.g. a national coffee record naming Najd/Hijaz/Eastern/South), which
    # must not be mistaken for the record's own regional scope.
    return _keys_for_aliases(region_text, _REGION_ALIASES)


def _regions_compatible(query_regions: frozenset[str], evidence_regions: frozenset[str]) -> bool:
    q_regions = query_regions - {"national"}
    d_regions = evidence_regions - {"national"}
    if not q_regions or not d_regions:
        return True
    southern_provinces = frozenset({"asir", "jazan", "najran"})
    explicit_q_provinces = q_regions & southern_provinces
    if explicit_q_provinces:
        # A broad southern label must not make an Asir document answer a
        # Jazan/Najran question.  Require the same explicit province in the
        # evidence metadata/text.
        return bool(explicit_q_provinces & d_regions)
    for q_region in q_regions:
        for d_region in d_regions:
            if q_region == d_region:
                return True
            if d_region in _REGION_GROUPS.get(q_region, frozenset()):
                return True
            if q_region in _REGION_GROUPS.get(d_region, frozenset()):
                return True
    return False


def _regions_for_matched_topics(
    profile: CulturalQueryProfile,
    matched_topics: frozenset[str],
) -> frozenset[str]:
    """Select regional constraints belonging to the matched topic.

    Combined requests can mention Eastern springs/shrimp and an Asir bread
    topic in the same turn. Applying every query region to every document
    would incorrectly reject valid independent evidence. Province isolation
    remains strict within the southern-bread topic.
    """

    if not profile.regions:
        return frozenset()
    if len(profile.topics) == 1:
        return profile.regions
    # Per-topic regional scope for combined requests. A mapped empty scope
    # (None) marks a national practice such as coffee hospitality: it imposes
    # no scoped constraint in multi-topic requests. Single-topic requests
    # still enforce the full regional constraint via the branch above.
    # Unmapped (unseen) topics fail closed on the full regional constraint.
    topic_region_keys: dict[str, frozenset[str] | None] = {
        "shrimp_drying": frozenset({"eastern"}),
        "al_ahsa_springs": frozenset({"eastern"}),
        "southern_bread": frozenset({"south", "asir", "jazan", "najran"}),
        "saudi_coffee_majlis": None,
        "known_food": None,
        "fashion_craft": None,
    }
    selected: set[str] = set()
    scoped = False
    for topic in matched_topics:
        if topic not in topic_region_keys:
            continue
        scoped = True
        keys = topic_region_keys[topic]
        if keys is not None:
            selected.update(profile.regions & keys)
    if not scoped:
        return profile.regions
    return frozenset(selected)


def relevance_details(query: str, hit: Mapping[str, Any]) -> dict[str, Any]:
    """Return an auditable relevance decision for one candidate.

    A recognized query entity must match the document entity.  A document with
    an explicit conflicting sector/entity is rejected even when generic words
    such as “heritage” or “Saudi” overlap.
    """

    q = query_profile(query)
    d = evidence_profile(hit)
    doc_text = _document_text(hit)
    doc_terms = _tokens(doc_text)
    overlap = len(q.terms & doc_terms)
    matched_topics = q.topics & d.topics
    topic_conflict = bool(q.topics and d.topics and not matched_topics)
    explicit_sector = _explicit_sector(hit)
    allowed_sectors = frozenset().union(*(
        (_TOPIC_SECTORS.get(topic, frozenset()) for topic in q.topics)
    )) if q.topics else frozenset()
    sector_conflict = bool(explicit_sector and allowed_sectors and explicit_sector not in allowed_sectors)

    d_regions = _explicit_region_keys(hit)
    matched_region_query = _regions_for_matched_topics(q, matched_topics)
    region_conflict = bool(d_regions and not _regions_compatible(matched_region_query, d_regions))

    if q.topics:
        topic_match = bool(matched_topics)
        # Sparse test/federated hits may not carry topic metadata; an explicit
        # entity in the excerpt is still enough to keep them, but generic
        # source names are never enough. Hospitality wording (مجلس/ضيافة)
        # alone is not a coffee anchor: coffee requires a coffee-specific
        # lexeme, mirroring the query-side guard above.
        def _anchor(topic: str) -> bool:
            aliases = _TOPIC_ALIASES.get(topic, ())
            if topic == "saudi_coffee_majlis":
                aliases = _COFFEE_STRONG_ALIASES
            return any(_contains(doc_text, alias) for alias in aliases)

        anchor_match = any(_anchor(topic) for topic in q.topics)
        accepted = not topic_conflict and not sector_conflict and not region_conflict and (topic_match or anchor_match)
        if not topic_match and not anchor_match and overlap < 2:
            accepted = False
    else:
        sector_conflict = bool(q.sectors and explicit_sector and explicit_sector not in q.sectors)
        accepted = not sector_conflict and not region_conflict and overlap >= 2

    return {
        "accepted": bool(accepted),
        "topic_match": bool(matched_topics),
        "matched_topics": sorted(matched_topics),
        "region_match": not region_conflict,
        "mandate_match": not sector_conflict,
        "term_overlap": overlap,
        "query_topics": sorted(q.topics),
    }


def is_evidence_relevant(query: str, hit: Mapping[str, Any]) -> bool:
    """Return whether a candidate is safe to use for this current query."""

    return bool(relevance_details(query, hit)["accepted"])


def filter_relevant_evidence(query: str, hits: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Filter candidates and attach the decision metadata without changing source text."""

    filtered: list[dict[str, Any]] = []
    for hit in hits:
        details = relevance_details(query, hit)
        if not details["accepted"]:
            continue
        copied = dict(hit)
        metadata = dict(_metadata(hit))
        metadata.update(
            {
                "entity_relevance": details["topic_match"],
                "region_relevance": details["region_match"],
                "mandate_relevance": details["mandate_match"],
                "matched_topics": details["matched_topics"],
                "term_overlap": details["term_overlap"],
            }
        )
        copied["metadata"] = metadata
        filtered.append(copied)
    return filtered


def requires_medical_qualification(query: str) -> bool:
    """Detect a medical/therapeutic claim request without asserting a benefit."""

    terms = (
        "علاج", "علاجي", "شفاء", "تشفي", "فوائد طبية", "فوائد علاجية", "مرض", "أمراض",
        "روماتيزم", "جلد", "medical", "therapeutic", "treat", "cure", "health benefit",
    )
    return any(_contains(query, term) for term in terms)


def strong_product_grounding(query: str, hits: Iterable[Mapping[str, Any]]) -> bool:
    """Whether a generated cultural product has a strong, entity-matched basis."""

    profile = query_profile(query)
    if not profile.topics:
        return False
    for hit in filter_relevant_evidence(query, hits):
        try:
            score = float(hit.get("score", 0.0) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        metadata = hit.get("metadata") if isinstance(hit.get("metadata"), Mapping) else {}
        if score >= 0.65 and metadata.get("entity_relevance") and metadata.get("mandate_relevance"):
            return True
    return False
