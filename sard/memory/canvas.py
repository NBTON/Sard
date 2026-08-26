"""Canvas Task Graph for Sard's Isnād Memory.

Provides a compact, inspectable task graph with node_ids for the isnād planning stages:
classify -> locate -> retrieve -> assemble_isnad -> score_chain -> decide -> generate.

Supports progressive disclosure:
- High-level canvas view for execution & UI inspection (to_mermaid).
- Deep drill-down: given a node_id or claim_id, retrieve linked L1 atoms and L0 raw excerpts.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

from sard.memory.l0_evidence import L0EvidenceStore
from sard.memory.l1_atoms import L1AtomStore
from sard.schemas.isnad import ClaimAtom, Evidence

CanvasNodeStatus = Literal["pending", "running", "completed", "failed", "skipped"]


class CanvasNode(BaseModel):
    """A discrete execution step in the isnād planning canvas."""

    node_id: str
    stage: str
    label_ar: str
    label_en: str
    status: CanvasNodeStatus = "pending"
    evidence_ids: List[str] = Field(default_factory=list)
    atom_ids: List[str] = Field(default_factory=list)
    details: Dict[str, Any] = Field(default_factory=dict)


class IsnadCanvas:
    """Task graph canvas for Isnād Planning with drill-down capability."""

    def __init__(
        self,
        request_id: str,
        l0_store: Optional[L0EvidenceStore] = None,
        l1_store: Optional[L1AtomStore] = None,
    ):
        self.request_id = request_id
        self.l0_store = l0_store or L0EvidenceStore()
        self.l1_store = l1_store or L1AtomStore()
        self.nodes: Dict[str, CanvasNode] = {}
        self._init_standard_stages()

    def _init_standard_stages(self):
        stages = [
            ("node-1-classify", "classify", "تصنيف السؤال والوسائط", "Classify Request & Media"),
            ("node-2-locate", "locate", "تحديد المنطقة والسياق", "Locate Region & Occasion"),
            ("node-3-retrieve", "retrieve", "استرجاع المصادر المعتمدة", "Retrieve Authoritative Sources"),
            ("node-4-assemble", "assemble_isnad", "تجميع سلسلة الإسناد", "Assemble Isnād Chain"),
            ("node-5-score", "score_chain", "تقييم الإسناد وفحص التعارض", "Score Chain & Detect Conflicts"),
            ("node-6-decide", "decide", "قرار التوثيق (توليد/تحوط/رفض)", "Verification Decision"),
            ("node-7-generate", "generate", "صياغة الرواية مع بيان السند", "Generate Grounded Narrative"),
        ]
        for nid, stage, ar, en in stages:
            self.nodes[nid] = CanvasNode(
                node_id=nid,
                stage=stage,
                label_ar=ar,
                label_en=en,
                status="pending",
            )

    def set_stage_status(
        self,
        stage: str,
        status: CanvasNodeStatus,
        evidence_ids: Optional[List[str]] = None,
        atom_ids: Optional[List[str]] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Optional[CanvasNode]:
        """Update node status and attach evidence/atom pointers."""
        for node in self.nodes.values():
            if node.stage == stage:
                node.status = status
                if evidence_ids:
                    node.evidence_ids = list(set(node.evidence_ids + evidence_ids))
                if atom_ids:
                    node.atom_ids = list(set(node.atom_ids + atom_ids))
                if details:
                    node.details.update(details)
                return node
        return None

    def get_node(self, node_id: str) -> Optional[CanvasNode]:
        """Retrieve node by ID."""
        return self.nodes.get(node_id)

    def drill_down(self, node_id: str) -> Dict[str, Any]:
        """Drill down from canvas node to linked L1 atoms and exact L0 raw excerpts.
        
        Returns:
            Dict containing:
            - node: CanvasNode
            - atoms: List[ClaimAtom]
            - evidence: List[Evidence]
            - raw_excerpts: List[str]
        """
        node = self.nodes.get(node_id)
        if not node:
            # Fallback check if user passed stage name instead of node_id
            for n in self.nodes.values():
                if n.stage == node_id:
                    node = n
                    break
        if not node:
            return {"error": f"Node {node_id} not found in canvas"}

        # Collect atoms
        atoms: List[ClaimAtom] = []
        for aid in node.atom_ids:
            atom = self.l1_store.get_atom(aid)
            if atom:
                atoms.append(atom)

        # Collect evidence IDs from node and atoms
        all_ev_ids = set(node.evidence_ids)
        for a in atoms:
            all_ev_ids.update(a.source_ids)

        evidence_list: List[Evidence] = []
        raw_excerpts: List[Dict[str, str]] = []

        for eid in all_ev_ids:
            ev = self.l0_store.get_evidence(eid)
            if ev:
                evidence_list.append(ev)
                raw_excerpts.append({
                    "source_id": ev.source_id,
                    "origin": ev.origin,
                    "region": ev.region,
                    "source_type": ev.source_type,
                    "excerpt": ev.excerpt,
                    "raw_ref": ev.raw_ref,
                })

        return {
            "node_id": node.node_id,
            "stage": node.stage,
            "status": node.status,
            "label_ar": node.label_ar,
            "details": node.details,
            "atoms": [a.model_dump() for a in atoms],
            "evidence": [e.model_dump() for e in evidence_list],
            "raw_excerpts": raw_excerpts,
        }

    def to_mermaid(self) -> str:
        """Render the isnād plan canvas as a Mermaid flowchart diagram."""
        lines = ["graph TD", "    classDef done fill:#E8E0D2,stroke:#4A513C,stroke-width:2px,color:#141210;", "    classDef running fill:#BE4A24,stroke:#141210,stroke-width:2px,color:#FFFFFF;", "    classDef pending fill:#FAF7F1,stroke:#D4CBBD,stroke-width:1px,color:#8A8178;", "    classDef failed fill:#741E1D,stroke:#BE4A24,stroke-width:2px,color:#FFFFFF;"]

        prev_id = None
        for nid, n in self.nodes.items():
            label = f'"{n.label_ar} ({n.label_en})"'
            lines.append(f"    {nid}[{label}]:::{n.status}")
            if prev_id:
                lines.append(f"    {prev_id} --> {nid}")
            prev_id = nid

        return "\n".join(lines)
