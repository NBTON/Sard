"""Canonical schemas for Isnād-style provenance planning in Sard.

Ensures every cultural claim, narrative, and action binds to an inspectable
source chain with verified origins, regional grounding, and confidence scores.
"""

from __future__ import annotations

from typing import List, Literal, Optional
from pydantic import BaseModel, Field

SourceType = Literal[
    "ministry",
    "museum",
    "dated_text",
    "oral_account",
    "living_source",
    "news",
    "user_upload",
    "unknown",
]

Confidence = Literal["high", "medium", "low"]

Decision = Literal["generate", "hedge", "ask", "refuse"]

Region = Literal[
    "najd",
    "hijaz",
    "asir",
    "eastern",
    "north",
    "south",
    "national",
    "unknown",
]

UserStance = Literal["visitor", "local", "researcher", "unknown"]

RequestClassification = Literal[
    "story",
    "place",
    "ritual",
    "food",
    "dialect",
    "etiquette",
    "object_from_image",
    "itinerary",
    "other",
]


class Evidence(BaseModel):
    """L0 grounded evidence item extracted from RAG, Parallel Search, or Multimodal media."""

    source_id: str = Field(..., description="Unique durable ID for this evidence item")
    origin: str = Field(..., description="Entity or institution providing the source")
    region: Region = Field(default="unknown", description="Geographic cultural region")
    date_or_period: Optional[str] = Field(None, description="Historical period or publication date")
    source_type: SourceType = Field(default="unknown", description="Categorical source type")
    url_or_doc_id: Optional[str] = Field(None, description="URL or internal document identifier")
    excerpt: str = Field(..., description="Exact textual excerpt supporting the claim")
    raw_ref: str = Field(..., description="Pointer to the underlying L0 raw record or file chunk")


class ClaimAtom(BaseModel):
    """L1 granular cultural claim atom linked back to evidence sources."""

    claim_id: str = Field(..., description="Unique ID for this claim atom")
    text: str = Field(..., description="Clear textual statement of the cultural fact or claim")
    region: Region = Field(default="unknown", description="Cultural region associated with the claim")
    source_ids: List[str] = Field(default_factory=list, description="List of supporting L0 source_ids")
    confidence: Confidence = Field(default="medium", description="Confidence level of this claim")
    conflicts_with: List[str] = Field(
        default_factory=list, description="Claim IDs or regional labels that conflict with this claim"
    )


class IsnadChain(BaseModel):
    """Assembled provenance chain representing the full verification trace."""

    request_id: str = Field(..., description="Identifier of the query/request")
    classification: str = Field(..., description="Classified topic category")
    region: Region = Field(default="unknown", description="Primary resolved cultural region")
    evidence: List[Evidence] = Field(default_factory=list, description="Grounded evidence items")
    atoms: List[ClaimAtom] = Field(default_factory=list, description="Extracted claim atoms")
    conflicts: List[str] = Field(default_factory=list, description="Detected source/region conflicts")
    score: Confidence = Field(default="low", description="Aggregated confidence score")
    decision: Decision = Field(default="refuse", description="Planner decision: generate | hedge | ask | refuse")
    missing: List[str] = Field(
        default_factory=list, description="Information or provenance gaps preventing full generation"
    )


class PlannerResult(BaseModel):
    """Final output of the isnād provenance planner."""

    chain: IsnadChain = Field(..., description="The complete provenance chain")
    answer_ar: Optional[str] = Field(None, description="Arabic narrative response if approved")
    answer_en: Optional[str] = Field(None, description="English narrative response if approved")
    visible_sources: List[Evidence] = Field(default_factory=list, description="Sources exposed to the user")
    follow_up: Optional[str] = Field(None, description="Clarifying question or suggested next step")
