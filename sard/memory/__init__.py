"""Isnād Memory System for Sard.

Integrates L0 (Evidence), L1 (ClaimAtoms), L2 (Scenes), L3 (UserRegister),
and Canvas for progressive disclosure and verifiable provenance.
"""

from __future__ import annotations

from typing import Optional

from sard.memory.canvas import IsnadCanvas
from sard.memory.l0_evidence import L0EvidenceStore
from sard.memory.l1_atoms import L1AtomStore
from sard.memory.l2_scenes import L2SceneStore
from sard.memory.l3_register import L3UserRegister
from sard.memory.retrieve_hybrid import HybridMemoryRetriever


class IsnadMemory:
    """Unified Isnād Memory hub."""

    def __init__(self, db_path: Optional[str] = None):
        self.l0 = L0EvidenceStore(db_path=db_path)
        self.l1 = L1AtomStore()
        self.l2 = L2SceneStore()
        self.l3 = L3UserRegister()
        self.retriever = HybridMemoryRetriever(self.l0, self.l1)

    def create_canvas(self, request_id: str) -> IsnadCanvas:
        """Create a new task canvas bound to this memory hub."""
        return IsnadCanvas(request_id=request_id, l0_store=self.l0, l1_store=self.l1)


__all__ = [
    "IsnadMemory",
    "IsnadCanvas",
    "L0EvidenceStore",
    "L1AtomStore",
    "L2SceneStore",
    "L3UserRegister",
    "HybridMemoryRetriever",
]
