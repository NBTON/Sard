"""Typed, source-grounded cultural and tourism recommendations.

Recommendations are a separate result from verified answer text. A proposal
is visible only when the current query has a strong matching topic citation and
an authoritative mandate citation for the selected organization. The bundled
mandate records are data, not prompt
templates, so adding a topic does not require branching on an exact question.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from sard.rag.normalize import normalize_arabic
from sard.rag.relevance import filter_relevant_evidence, query_profile


class ProposalStrength(str, Enum):
    """The only strength exposed by this product: weak proposals are hidden."""

    STRONG = "strong"


class OrganizationMandateProposal(BaseModel):
    """A recommendation linked to both the requested topic and an authority."""

    topic: str
    selected_organization: str
    organization_role: str
    rationale: str
    proposed_product: str
    strength: str = ProposalStrength.STRONG
    supporting_topic_citation_ids: list[str] = Field(default_factory=list)
    supporting_mandate_citation_ids: list[str] = Field(default_factory=list)
    supporting_citation_ids: list[str] = Field(default_factory=list)


class CulturalProposalResult(BaseModel):
    """Recommendation payload kept distinct from verified factual response text."""

    verified_fact_citation_ids: list[str] = Field(default_factory=list)
    topic_citation_ids: dict[str, list[str]] = Field(default_factory=dict)
    proposals: list[OrganizationMandateProposal] = Field(default_factory=list)


MANDATE_POLICIES: dict[str, dict[str, Any]] = {
    # Every mandate requires its *distinguishing* capabilities to be evidenced.
    # Generic dimensions such as ``documented_practice`` alone never qualify:
    # they must accompany the mandate's primary capability (e.g. culinary for
    # the Culinary Commission, place+visitor for Tourism, museum for Museums).
    "culinary": {
        "organization": "هيئة فنون الطهي",
        "role": "صون وتوثيق وتطوير فنون الطهي والمنتجات الغذائية المحلية",
        "dimensions": ("culinary", "documented_practice"),
        "required_all": ("culinary", "documented_practice"),
        "product_dimension": "culinary",
    },
    "heritage": {
        "organization": "هيئة التراث",
        "role": "حماية وتوثيق وتنمية عناصر التراث المادي وغير المادي",
        "dimensions": ("history", "documented_practice"),
        "required_all": ("history", "documented_practice"),
        "product_dimension": "documented_practice",
    },
    "tourism": {
        "organization": "الهيئة السعودية للسياحة",
        "role": "إبراز الوجهة وتطوير وتسويق تجربة أو منتج سياحي ثقافي",
        "dimensions": ("place", "visitor"),
        "required_all": ("place", "visitor"),
        "product_dimension": "place",
    },
    "fashion": {
        "organization": "هيئة الأزياء",
        "role": "دعم وتطوير قطاع الأزياء والممارسين والمنتجات الإبداعية",
        "dimensions": ("clothing", "textile"),
        "required_all": ("clothing", "textile"),
        "product_dimension": "clothing",
    },
    "crafts": {
        "organization": "هيئة التراث",
        "role": "حماية وتوثيق وتنمية الحرف والصناعات التقليدية وعناصرها المادية",
        "dimensions": ("craft", "tools_materials"),
        "required_all": ("craft", "tools_materials"),
        "product_dimension": "tools_materials",
    },
    "museums": {
        "organization": "هيئة المتاحف",
        "role": "حفظ المقتنيات وإتاحة السرد المتحفي والتجارب التعليمية",
        "dimensions": ("museum", "history", "documented_practice"),
        "required_all": ("museum",),
        "product_dimension": "museum",
    },
    "film": {
        "organization": "هيئة الأفلام",
        "role": "دعم الإنتاج السينمائي وتطوير المحتوى والصناعة الفيلمية",
        "dimensions": ("narrative", "documented_practice"),
        "required_all": ("narrative",),
        "product_dimension": "narrative",
    },
    "literature": {
        "organization": "هيئة الأدب والنشر والترجمة",
        "role": "دعم الأدب والنشر والترجمة وإتاحة المحتوى الثقافي",
        "dimensions": ("narrative", "oral_history", "documented_practice"),
        "required_any": ("narrative", "oral_history"),
        "product_dimension": "narrative",
    },
}

_CAPABILITY_PRODUCTS: dict[str, str] = {
    "culinary": "بطاقة تراث غذائي موثقة مرتبطة بالمصدر المحلي",
    "place": "مقترح مسار أو تجربة سياحية ثقافية موثقة",
    "museum": "مقترح قصة أو تجربة متحفية موثقة",
    "narrative": "مقترح معالجة لمحتوى ثقافي موثق",
    "clothing": "بطاقة أزياء موثقة مرتبطة بالخامات والأدوات",
    "textile": "بطاقة نسيج أو أزياء موثقة مرتبطة بالمادة",
    "craft": "بطاقة حرفة موثقة مرتبطة بالخامات والأدوات",
    "tools_materials": "بطاقة حرفة أو صناعة موثقة مرتبطة بالأدوات والخامات",
    "documented_practice": "موجز تراثي موثق قابل للتحويل إلى منتج ثقافي",
}

_RECOMMENDATION_REQUEST_RE = re.compile(
    r"(اقترح|اقتراح|توصية|توصيات|مقترح|منتج|مبادرة|برنامج|recommend|recommendation|proposal|product|initiative)",
    re.I,
)

_SUBSTANTIVE_FASHION_RE = re.compile(
    r"(بشت|مشلح|ثوب|عباءة|ملابس|أزياء|نسيج|صوف|وبر|خياطة|زري|خامة|خامات|أداة|أدوات|طين|ألوان|صبغة|مادة|سدو)",
    re.I,
)

_DIMENSION_PATTERNS: dict[str, tuple[str, ...]] = {
    "culinary": ("طعام", "أكلة", "وصفة", "طبخة", "مأكولات", "طبخ", "مخبوز", "خبز", "قهوة", "روبيان", "جمبري", "مطبخ", "غذاء", "culinary", "food", "recipe"),
    "place": ("مكان", "موقع", "واحة", "ينابيع", "عيون", "ساحل", "شاطئ", "مياه", "بيئة", "وجهة", "معلم", "springs", "oasis", "coastal", "place"),
    "visitor": ("زائر", "زوار", "سائح", "سياحة", "زيارة", "رحلة", "مسار", "تجربة", "وجهة", "tourist", "tourism", "visitor", "travel"),
    "documented_practice": ("تراث", "موروث", "تقليد", "تقليدية", "طريقة", "تحضير", "تقديم", "ممارسة", "صناعة", "توثيق", "دليل", "سجل", "heritage", "tradition", "documented"),
    "history": ("تاريخ", "تاريخي", "قديم", "أثري", "ذاكرة", "ماضي", "أجيال", "history", "historic"),
    "narrative": ("قصة", "حكاية", "سرد", "رواية", "محتوى", "فيلم", "تصوير", "سينما", "narrative", "story", "film"),
    "oral_history": ("شفوي", "حكواتي", "رواية شعبية", "ذكريات", "سيرة", "oral history", "memoir"),
    "museum": ("متحف", "مقتنى", "معرض", "قاعة عرض", "museum", "collection", "exhibition"),
    "tools_materials": ("أداة", "أدوات", "خامة", "خامات", "مادة", "مواد", "خشب", "طين", "صوف", "وبر", "زري", "معدن", "tools", "materials"),
    "craft": ("حرفة", "حرف", "صناعة يدوية", "صناعات تقليدية", "حرفي", "سدو", "نجارة", "نقش", "craft", "artisan", "handicraft"),
    "clothing": ("بشت", "مشلح", "ثوب", "عباءة", "ملابس", "أزياء", "لباس", "clothing", "fashion", "attire"),
    "textile": ("نسيج", "حياكة", "خياطة", "غزل", "قماش", "صوف", "وبر", "زري", "textile", "weaving"),
}


def _valid_http_url(value: Any) -> bool:
    try:
        parsed = urlparse(str(value or "").strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def _bundled_mandates() -> list[dict[str, Any]]:
    index_path = Path(__file__).resolve().parents[1] / "rag" / "bundled_index.json"
    try:
        records = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("record_type") == "mandate"
        and record.get("authoritative") is True
        and record.get("mandate_key") in MANDATE_POLICIES
        and _valid_http_url(record.get("source_url"))
        and record.get("citation_id")
    ]


def _citation_hit(citation: Mapping[str, Any]) -> dict[str, Any]:
    metadata = dict(citation.get("metadata") or {}) if isinstance(citation.get("metadata"), Mapping) else {}
    for key in ("topic", "sector", "region", "source_url", "citation_id"):
        if citation.get(key) is not None:
            metadata.setdefault(key, citation.get(key))
    return {
        "title": citation.get("title", ""),
        "chunk": citation.get("excerpt") or citation.get("snippet") or "",
        "source": citation.get("origin") or citation.get("source_name") or "",
        "score": citation.get("score", 0.0),
        "metadata": metadata,
    }


def _score(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _topic_citations(
    query: str,
    citations: Iterable[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Associate each valid strong citation with its matched current topic."""

    topic_map: dict[str, list[dict[str, Any]]] = {
        topic: [] for topic in query_profile(query).topics
    }
    for citation in citations:
        citation_id = str(citation.get("citation_id") or citation.get("id") or "").strip()
        source_url = citation.get("source_url") or citation.get("url")
        if not citation_id or not _valid_http_url(source_url) or _score(citation.get("score")) < 0.65:
            continue
        hit = _citation_hit(citation)
        filtered = filter_relevant_evidence(query, [hit])
        if not filtered:
            continue
        matched_topics = filtered[0].get("metadata", {}).get("matched_topics", [])
        if matched_topics:
            for topic in topic_map:
                if topic in matched_topics:
                    topic_map[topic].append(dict(citation))
        elif not topic_map:
            # Unseen topics are labelled by the source's own topic metadata
            # after the generic overlap gate accepts the current query. This
            # keeps organization selection feature-driven rather than tied to
            # a finite list of expected topic IDs.
            topic_label = str(
                citation.get("topic")
                or (citation.get("metadata") or {}).get("topic")
                or citation.get("title")
                or ""
            ).strip()
            if topic_label:
                topic_map.setdefault(topic_label, []).append(dict(citation))
    return topic_map


def _mandate_citation(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": record["citation_id"],
        "citation_id": record["citation_id"],
        "title": record.get("title", ""),
        "url": record["source_url"],
        "source_url": record["source_url"],
        "origin": record.get("source_name", ""),
        "source_type": "ministry",
        "chunk_id": f"CHUNK-{record.get('id', record['citation_id'])}",
        "source_id": record.get("id", record["citation_id"]),
        "excerpt": record.get("content", ""),
        "topic": record.get("topic", ""),
        "sector": "mandate",
        "score": 1.0,
        "record_type": "mandate",
        "mandate_key": record.get("mandate_key", ""),
        "capabilities": list(record.get("capabilities") or []),
    }


def _has_substantive_fashion_evidence(citations: Iterable[Mapping[str, Any]]) -> bool:
    evidence_text = " ".join(
        str(c.get("title", "")) + " " + str(c.get("excerpt") or c.get("snippet") or "")
        for c in citations
    )
    # Require at least two substantive terms so a generic “craft” label alone
    # cannot unlock a fashion/craft product.
    return len(_SUBSTANTIVE_FASHION_RE.findall(evidence_text)) >= 2


def _dimensions_for_evidence(
    query: str,
    citations: Iterable[Mapping[str, Any]],
) -> frozenset[str]:
    """Derive capability dimensions from current query and source evidence."""

    source_items = list(citations)
    text = normalize_arabic(query) + " " + normalize_arabic(" ".join(
        str(c.get("title", "")) + " " +
        str(c.get("excerpt") or c.get("snippet") or "") + " " +
        str(c.get("topic", "")) + " " +
        str(c.get("sector", "")) + " " +
        str((c.get("metadata") or {}).get("topic", "")) + " " +
        str((c.get("metadata") or {}).get("sector", ""))
        for c in source_items
    ))
    dimensions = {
        dimension
        for dimension, aliases in _DIMENSION_PATTERNS.items()
        if any(normalize_arabic(alias).casefold() in text.casefold() for alias in aliases)
    }
    # Structured sector metadata is authoritative when present, even if a
    # short excerpt uses a synonym not covered by the lexical vocabulary.
    sectors = {
        str(c.get("sector") or (c.get("metadata") or {}).get("sector") or "").casefold()
        for c in source_items
    }
    if sectors & {"food", "culinary"}:
        dimensions.add("culinary")
    if sectors & {"place", "tourism", "geography"}:
        dimensions.update({"place", "visitor"})
    if sectors & {"fashion"}:
        dimensions.update({"clothing", "textile"})
    if sectors & {"craft", "visual", "architecture"}:
        dimensions.update({"craft", "tools_materials"})
    if sectors & {"heritage", "history"}:
        dimensions.update({"documented_practice", "history"})
    return frozenset(dimensions)


def _select_product(
    evidence_dimensions: frozenset[str],
    mandate_dimensions: Iterable[str],
    policy: Mapping[str, Any],
) -> str:
    """Select the product tied to the selected mandate capability."""

    preferred = str(policy.get("product_dimension") or "")
    if preferred in evidence_dimensions and preferred in mandate_dimensions:
        return _CAPABILITY_PRODUCTS[preferred]
    candidates = (
        "culinary", "place", "museum", "narrative", "clothing", "textile",
        "craft", "tools_materials", "documented_practice",
    )
    for dimension in candidates:
        if dimension in evidence_dimensions and dimension in mandate_dimensions:
            return _CAPABILITY_PRODUCTS[dimension]
    for dimension in candidates:
        if dimension in mandate_dimensions and dimension in _CAPABILITY_PRODUCTS:
            return _CAPABILITY_PRODUCTS[dimension]
    return _CAPABILITY_PRODUCTS["documented_practice"]


def build_cultural_proposal_result(
    query: str,
    citations: Iterable[Mapping[str, Any]],
) -> tuple[CulturalProposalResult, list[dict[str, Any]]]:
    """Build strong-only proposals and return any mandate citations to expose.

    Topics are segmented independently. Missing evidence or mandate records
    suppresses only the affected topic/organization; it never contaminates or
    suppresses other topics in the same request.
    """

    topic_citations = _topic_citations(query, citations)
    all_topic_citations = [
        citation
        for topic in topic_citations
        for citation in topic_citations.get(topic, [])
    ]
    result = CulturalProposalResult(
        verified_fact_citation_ids=list(dict.fromkeys(
            str(c.get("citation_id") or c.get("id"))
            for c in all_topic_citations
            if str(c.get("citation_id") or c.get("id") or "").strip()
        )),
        topic_citation_ids={
            topic: list(dict.fromkeys(
                str(c.get("citation_id") or c.get("id"))
                for c in topic_hits
            ))
            for topic, topic_hits in topic_citations.items()
        },
    )
    if not topic_citations or not _RECOMMENDATION_REQUEST_RE.search(query):
        return result, []

    mandate_records = _bundled_mandates()
    mandate_by_key = {str(record["mandate_key"]): record for record in mandate_records}
    mandate_citations: list[dict[str, Any]] = []
    seen_mandate_ids: set[str] = set()

    for topic in sorted(topic_citations):
        selected_topic_citations = topic_citations.get(topic, [])
        if not selected_topic_citations:
            continue
        # Do not pass the combined query into this scorer: words such as food
        # or visitor from a sibling topic must not authorize this topic.
        evidence_dimensions = _dimensions_for_evidence("", selected_topic_citations)
        if not evidence_dimensions:
            continue
        for mandate_key, policy in MANDATE_POLICIES.items():
            mandate_record = mandate_by_key.get(mandate_key)
            if mandate_record is None or policy is None:
                # Fail closed for this organization only when its authoritative
                # mandate evidence is absent or malformed.
                continue
            mandate_dimensions = frozenset(policy.get("dimensions", ()))
            record_dimensions = frozenset(mandate_record.get("capabilities") or mandate_dimensions)
            supported_dimensions = evidence_dimensions & mandate_dimensions & record_dimensions
            if not supported_dimensions:
                continue
            # Required capabilities must be evidenced *within* the mandate's
            # authoritative capability set — not merely present somewhere in
            # the evidence text. A generic documented-practice signal alone
            # can never satisfy a mandate whose distinguishing capability
            # (e.g. museum, narrative, culinary) is absent.
            required_all = frozenset(policy.get("required_all", ()))
            required_any = frozenset(policy.get("required_any", ()))
            if required_all and not required_all <= supported_dimensions:
                continue
            if required_any and not (required_any & supported_dimensions):
                continue
            # Strong means the evidence describes a capability the selected
            # mandate actually covers, with a material query/evidence signal.
            capability_score = 0.50 + min(0.30, 0.15 * len(supported_dimensions))
            if supported_dimensions:
                capability_score += 0.15
            if capability_score < 0.65:
                continue
            if mandate_key in {"fashion", "crafts"} and not _has_substantive_fashion_evidence(selected_topic_citations):
                # Generic “craft” wording cannot unlock clothing or craft
                # products without substantive material/tool evidence.
                continue
            mandate_citation = _mandate_citation(mandate_record)
            mandate_id = str(mandate_citation["citation_id"])
            if mandate_id not in seen_mandate_ids:
                mandate_citations.append(mandate_citation)
                seen_mandate_ids.add(mandate_id)
            topic_ids = list(dict.fromkeys(
                str(c.get("citation_id") or c.get("id"))
                for c in selected_topic_citations
            ))
            topic_name = str(selected_topic_citations[0].get("title") or topic)
            rationale = (
                f"ارتباط قوي للموضوع «{topic_name[:70]}»: الشاهد الموضوعي يطابق الطلب، "
                f"وتغطي ولاية {policy['organization']} {policy['role']}."
            )
            proposal = OrganizationMandateProposal(
                topic=topic,
                selected_organization=policy["organization"],
                organization_role=policy["role"],
                rationale=rationale,
                proposed_product=_select_product(evidence_dimensions, mandate_dimensions, policy),
                strength=ProposalStrength.STRONG,
                supporting_topic_citation_ids=topic_ids,
                supporting_mandate_citation_ids=[mandate_id],
                supporting_citation_ids=[*topic_ids, mandate_id],
            )
            result.proposals.append(proposal)
    return result, mandate_citations
