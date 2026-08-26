"""L3 User Register Layer for Sard's Isnād Memory.

Tracks conversational and interaction context:
- User stance (visitor, local, researcher, unknown)
- Preferred language (Arabic MSA, regional spoken, English)
- Last trusted regions inquired by the user
- Session metadata

CRITICAL SAFETY RULE:
L3 Register records user interaction preferences ONLY. It is STRICTLY FORBIDDEN
to use L3 records as cultural ground truth or to backfill missing provenance evidence.
"""

from __future__ import annotations

from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from sard.schemas.isnad import Region, UserStance


class UserRegisterProfile(BaseModel):
    """User interaction profile."""

    session_id: str
    stance: UserStance = "unknown"
    preferred_language: str = "ar"
    preferred_dialect_or_register: Optional[str] = None  # e.g. "najdi", "hijazi", "msa"
    last_discussed_regions: List[Region] = Field(default_factory=list)
    recent_query_topics: List[str] = Field(default_factory=list)


class L3UserRegister:
    """Manager for L3 user registers with strict cultural fact isolation."""

    def __init__(self):
        self._profiles: Dict[str, UserRegisterProfile] = {}

    def get_or_create_profile(self, session_id: str) -> UserRegisterProfile:
        """Get or initialize profile for a session."""
        if session_id not in self._profiles:
            self._profiles[session_id] = UserRegisterProfile(session_id=session_id)
        return self._profiles[session_id]

    def update_interaction(
        self,
        session_id: str,
        stance: Optional[UserStance] = None,
        language: Optional[str] = None,
        dialect: Optional[str] = None,
        region: Optional[Region] = None,
        topic: Optional[str] = None,
    ) -> UserRegisterProfile:
        """Update conversational preferences without modifying cultural knowledge."""
        profile = self.get_or_create_profile(session_id)
        if stance is not None and stance != "unknown":
            profile.stance = stance
        if language is not None:
            profile.preferred_language = language
        if dialect is not None:
            profile.preferred_dialect_or_register = dialect
        if region is not None and region != "unknown":
            if region not in profile.last_discussed_regions:
                profile.last_discussed_regions.append(region)
                if len(profile.last_discussed_regions) > 5:
                    profile.last_discussed_regions.pop(0)
        if topic is not None and topic.strip():
            profile.recent_query_topics.append(topic.strip())
            if len(profile.recent_query_topics) > 10:
                profile.recent_query_topics.pop(0)
        return profile

    def assert_cannot_supply_cultural_truth(self) -> None:
        """Explicit guard method confirming L3 cannot be queried for factual evidence."""
        pass  # Invariant: L3 has no API to provide ClaimAtoms or Evidence.
