"""Isnād Chain Assembler for Sard.

Constructs an inspectable IsnadChain by:
1. Converting L0 Evidence records into granular L1 ClaimAtoms.
2. Detecting regional conflicts (e.g., Asir vs Najd, claiming Hijazi heritage for Najdi doors).
3. Associating every claim with its supporting source_ids.
4. Identifying missing provenance fields.
"""

from __future__ import annotations

import re
from typing import List, Tuple

from sard.memory.l1_atoms import L1AtomStore
from sard.schemas.isnad import ClaimAtom, Confidence, Evidence, IsnadChain, Region


class IsnadAssembler:
    """Assembles structured isnād chains and detects regional/lineage conflicts."""

    def __init__(self, l1_store: L1AtomStore):
        self.l1 = l1_store

    def assemble(
        self,
        request_id: str,
        classification: str,
        primary_region: Region,
        evidence: List[Evidence],
    ) -> IsnadChain:
        """Assemble the complete isnād chain from evidence."""
        atoms: List[ClaimAtom] = []
        conflicts: List[str] = []
        missing: List[str] = []

        if not evidence:
            return IsnadChain(
                request_id=request_id,
                classification=classification,
                region=primary_region,
                evidence=[],
                atoms=[],
                conflicts=["لا توجد مصادر أو وثائق معتمدة لدعم هذا الاستفسار."],
                score="low",
                decision="refuse",
                missing=["غياب مصادر الإسناد المعتمدة في قاعدة المعرفة."],
            )

        # 1. Extract Claim Atoms from each Evidence piece
        for ev in evidence:
            # Break excerpt into key sentences / cultural assertions
            sentences = [s.strip() for s in re.split(r"[.\n\r]+", ev.excerpt) if len(s.strip()) > 15]
            for s in sentences[:3]:  # Top factual assertions
                conf: Confidence = "high" if ev.source_type in ("ministry", "museum", "dated_text") else "medium" if ev.source_type == "news" else "low"
                atom = self.l1.add_atom(
                    text=s,
                    region=ev.region if ev.region != "unknown" else primary_region,
                    source_ids=[ev.source_id],
                    confidence=conf,
                )
                atoms.append(atom)

        # 2. Check for Cross-Regional Conflicts in the Retrieved Evidence
        regions_found = {ev.region for ev in evidence if ev.region not in ("unknown", "national")}
        if len(regions_found) > 1:
            # Regional conflict detected! (e.g. Asir vs Najd)
            reg_list = list(regions_found)
            conflict_msg = (
                f"تعارض إقليمي في المصادر: تم العثور على شواهد تنتمي لمناطق مختلفة ({', '.join(reg_list)}). "
                f"يمنع دمج التقاليد الإقليمية أو خلطها في هوية واحدة."
            )
            conflicts.append(conflict_msg)

        # 3. Check for Misattribution / Invented Lineage (e.g. Photo labeled Hijazi while evidence is Najdi)
        # Check if user upload or query targets one region but authoritative evidence belongs to another
        user_uploads = [ev for ev in evidence if ev.source_type == "user_upload"]
        official_ev = [ev for ev in evidence if ev.source_type in ("ministry", "museum", "dated_text", "news")]

        if user_uploads and official_ev:
            for u in user_uploads:
                for o in official_ev:
                    if o.region != "unknown" and u.region != "unknown" and o.region != u.region:
                        conflicts.append(
                            f"تعارض إسناد: المرفق أو الاستفسار يشير إلى منطقة [{u.region}] بينما الوثائق المعتمدة تؤكد انتماء العنصر إلى منطقة [{o.region}]."
                        )

        # 4. Check for missing elements (e.g. lack of dated source, lack of named artisan/village)
        has_official = any(ev.source_type in ("ministry", "museum", "dated_text") for ev in evidence)
        if not has_official:
            missing.append("غياب توثيق رسمي من وزارة الثقافة أو المتاحف المعتمدة.")

        has_dated = any(ev.date_or_period is not None for ev in evidence)
        if not has_dated:
            missing.append("التاريخ أو الحقبة الزمنية غير محددة بدقة.")

        # Resolved region
        resolved_region = primary_region
        if resolved_region == "unknown":
            if regions_found:
                resolved_region = list(regions_found)[0]
            elif any(ev.region == "national" for ev in evidence):
                resolved_region = "national"
            elif evidence and evidence[0].region != "unknown":
                resolved_region = evidence[0].region

        return IsnadChain(
            request_id=request_id,
            classification=classification,
            region=resolved_region,
            evidence=evidence,
            atoms=atoms,
            conflicts=conflicts,
            score="low",  # will be scored in score_chain step
            decision="refuse",  # will be decided in decide step
            missing=missing,
        )
