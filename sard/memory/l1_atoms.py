"""L1 Claim Atom Layer for Sard's Isnād Memory.

Stores granular cultural claims extracted from L0 evidence. Each ClaimAtom
links directly to one or more durable source_ids and records regional grounding,
confidence, and detected conflicts.
"""

from __future__ import annotations

import hashlib
import threading
from typing import Dict, List, Optional, Set

from sard.schemas.isnad import ClaimAtom, Confidence, Region


class L1AtomStore:
    """Store for L1 structured Claim Atoms."""

    def __init__(self):
        self._lock = threading.Lock()
        self._atoms: Dict[str, ClaimAtom] = {}
        self._region_index: Dict[Region, Set[str]] = {}

    @staticmethod
    def generate_claim_id(text: str, region: Region) -> str:
        """Generate a deterministic claim ID from normalized text and region."""
        h = hashlib.sha256(f"{region}:{text.strip().lower()}".encode("utf-8")).hexdigest()[:10]
        return f"claim-{region}-{h}"

    def add_atom(
        self,
        text: str,
        region: Region,
        source_ids: List[str],
        confidence: Confidence = "medium",
        conflicts_with: Optional[List[str]] = None,
    ) -> ClaimAtom:
        """Add or update a claim atom in the store."""
        claim_id = self.generate_claim_id(text, region)
        atom = ClaimAtom(
            claim_id=claim_id,
            text=text.strip(),
            region=region,
            source_ids=list(set(source_ids)),
            confidence=confidence,
            conflicts_with=conflicts_with or [],
        )

        with self._lock:
            self._atoms[claim_id] = atom
            if region not in self._region_index:
                self._region_index[region] = set()
            self._region_index[region].add(claim_id)

        return atom

    def get_atom(self, claim_id: str) -> Optional[ClaimAtom]:
        """Retrieve an atom by its claim_id."""
        with self._lock:
            return self._atoms.get(claim_id)

    def get_atoms_by_region(self, region: Region) -> List[ClaimAtom]:
        """Retrieve all atoms for a given region."""
        with self._lock:
            ids = self._region_index.get(region, set())
            return [self._atoms[cid] for cid in ids if cid in self._atoms]

    def list_all(self) -> List[ClaimAtom]:
        """List all stored claim atoms."""
        with self._lock:
            return list(self._atoms.values())

    def find_conflicts(self, candidate_atom: ClaimAtom) -> List[str]:
        """Detect conflicts between the candidate atom and existing stored atoms.
        
        For example: attributing a Najdi architectural motif or recipe to Hijaz or vice-versa.
        """
        conflicts: List[str] = []
        with self._lock:
            for existing in self._atoms.values():
                # Cross-region contradiction check on overlapping keywords
                if existing.region != candidate_atom.region and existing.region != "national" and candidate_atom.region != "national":
                    # Check for significant text overlap
                    cand_words = set(candidate_atom.text.lower().split())
                    exist_words = set(existing.text.lower().split())
                    overlap = cand_words.intersection(exist_words)
                    # If high word overlap but conflicting regions
                    if len(overlap) >= 3:
                        conflicts.append(
                            f"تناقض إسناد إقليمي: الادعاء '{candidate_atom.text[:40]}' مسند إلى [{candidate_atom.region}] بينما الوثيقة '{existing.text[:40]}' تسنده إلى [{existing.region}]"
                        )
        return conflicts
