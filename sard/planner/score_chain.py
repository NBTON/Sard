"""Isnād Chain Scorer for Sard.

Scores the confidence of an assembled Isnād chain into:
- high: >= 2 independent sources, same region, at least one ministry|museum|dated_text, no open conflict
- medium: 1 strong source, or 2 weaker sources, region clear, date fuzzy, or single-source
- low: user upload only, undated web, region unknown, or sources disagree / open conflict
"""

from __future__ import annotations

from sard.schemas.isnad import Confidence, IsnadChain


def score_isnad_chain(chain: IsnadChain) -> Confidence:
    """Calculate the confidence score of the isnād chain following canonical rules."""
    # Rule 1: Low if there is no evidence, or if there is an unresolved regional/lineage conflict
    if not chain.evidence:
        return "low"

    if chain.conflicts:
        return "low"

    # Count distinct origins / independent sources
    distinct_origins = {ev.origin for ev in chain.evidence}
    official_sources = [
        ev for ev in chain.evidence if ev.source_type in ("ministry", "museum", "dated_text")
    ]
    news_or_valid_web = [ev for ev in chain.evidence if ev.source_type in ("news", "living_source", "oral_account")]

    # Check if only user upload
    only_user_upload = all(ev.source_type == "user_upload" for ev in chain.evidence)
    if only_user_upload:
        return "low"

    # Rule 2: High confidence requires >= 2 independent sources, clear region, at least 1 official, no open conflict
    if (
        len(distinct_origins) >= 2
        and chain.region != "unknown"
        and len(official_sources) >= 1
    ):
        return "high"

    # Rule 3: Medium confidence: 1 strong official source, or 2 weaker sources with clear region
    if (
        (len(official_sources) >= 1 and chain.region != "unknown")
        or (len(news_or_valid_web) >= 2 and chain.region != "unknown")
    ):
        return "medium"

    # Default fallback: low
    return "low"
