"""L2 Scene Layer for Sard's Isnād Memory.

Constructs and stores structured cultural contexts / scenarios
(occasion + region + object type/craft, e.g. "Najdi Door Craftsmanship / Najd",
"Eid Hospitality / Asir"). Every scene is a markdown narrative directly
derived from L1 claim atoms and traceable back to L0 sources.
"""

from __future__ import annotations

from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from sard.schemas.isnad import ClaimAtom, Region


class CulturalScene(BaseModel):
    """Structured L2 Scene model representing a regional cultural scenario."""

    scene_id: str
    title: str
    region: Region
    occasion_or_topic: str
    summary_markdown: str
    atom_ids: List[str] = Field(default_factory=list)
    source_ids: List[str] = Field(default_factory=list)


class L2SceneStore:
    """Store for L2 structured cultural scenes."""

    def __init__(self):
        self._scenes: Dict[str, CulturalScene] = {}

    def build_scene(
        self,
        scene_id: str,
        title: str,
        region: Region,
        occasion_or_topic: str,
        atoms: List[ClaimAtom],
        prose_summary: Optional[str] = None,
    ) -> CulturalScene:
        """Create an L2 Scene combining claim atoms into a coherent markdown scenario."""
        atom_ids = [a.claim_id for a in atoms]
        all_sources = list({src_id for a in atoms for src_id in a.source_ids})

        if prose_summary:
            markdown = prose_summary
        else:
            bullets = "\n".join(f"- {a.text} (مصادر: {', '.join(a.source_ids)})" for a in atoms)
            markdown = f"### {title} ({region.upper()})\n**الموضوع/المناسبة**: {occasion_or_topic}\n\n{bullets}"

        scene = CulturalScene(
            scene_id=scene_id,
            title=title,
            region=region,
            occasion_or_topic=occasion_or_topic,
            summary_markdown=markdown,
            atom_ids=atom_ids,
            source_ids=all_sources,
        )
        self._scenes[scene_id] = scene
        return scene

    def get_scene(self, scene_id: str) -> Optional[CulturalScene]:
        """Get scene by ID."""
        return self._scenes.get(scene_id)

    def list_scenes_for_region(self, region: Region) -> List[CulturalScene]:
        """List scenes matching a specific region."""
        return [s for s in self._scenes.values() if s.region == region]
