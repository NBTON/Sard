"""Hybrid Retrieval for Sard's Isnād Memory.

Searches across L0 Evidence and L1 ClaimAtoms using combined lexical keywords,
regional filtering, and metadata constraints.
"""

from __future__ import annotations

import re
from typing import List, Optional

from sard.memory.l0_evidence import L0EvidenceStore
from sard.memory.l1_atoms import L1AtomStore
from sard.schemas.isnad import ClaimAtom, Evidence, Region


class HybridMemoryRetriever:
    """Hybrid keyword + metadata retriever over isnād memory stores."""

    def __init__(self, l0_store: L0EvidenceStore, l1_store: L1AtomStore):
        self.l0 = l0_store
        self.l1 = l1_store

    def search_evidence(
        self,
        query: str,
        region: Optional[Region] = None,
        limit: int = 10,
    ) -> List[Evidence]:
        """Search L0 evidence by lexical match and optional regional filter."""
        tokens = [t.lower() for t in re.split(r"[\s,;،؟\?]+", query.strip()) if len(t) >= 2]
        all_ev = self.l0.list_all()

        scored: List[tuple[float, Evidence]] = []
        for ev in all_ev:
            if region and region != "unknown" and ev.region != region and ev.region != "national":
                continue

            text_to_search = f"{ev.origin} {ev.excerpt} {ev.region} {ev.date_or_period or ''}".lower()
            score = 0.0
            for t in tokens:
                if t in text_to_search:
                    score += 1.0

            if score > 0:
                # Bonus for ministry / museum sources
                if ev.source_type in ("ministry", "museum", "dated_text"):
                    score += 1.5
                scored.append((score, ev))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:limit]]

    def search_atoms(
        self,
        query: str,
        region: Optional[Region] = None,
        limit: int = 10,
    ) -> List[ClaimAtom]:
        """Search L1 claim atoms by query and optional region."""
        tokens = [t.lower() for t in re.split(r"[\s,;،؟\?]+", query.strip()) if len(t) >= 2]
        all_atoms = self.l1.list_all()

        scored: List[tuple[float, ClaimAtom]] = []
        for atom in all_atoms:
            if region and region != "unknown" and atom.region != region and atom.region != "national":
                continue

            text_to_search = f"{atom.text} {atom.region}".lower()
            score = 0.0
            for t in tokens:
                if t in text_to_search:
                    score += 1.0

            if score > 0:
                if atom.confidence == "high":
                    score += 1.0
                scored.append((score, atom))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:limit]]
