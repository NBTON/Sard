"""Canonical ArtifactDocument schema for Sard artifact overhaul.

Single source of truth for preview generation across all artifact formats.
Renderers keep producing bytes; this module owns the *document model* that
preview assembly and API/UI contracts consume.

Evidence rule (fail-closed): a block with empty ``source_ids`` + empty
``evidence_ids`` renders only when ``verification_status`` is
``user_provided`` or ``uncertain``.  Everything else uncited is dropped from
``to_preview()`` items.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sard.outputs.schemas import CITATION_ID_RE, INLINE_CITATION_RE, CitationSource

_ALLOWED_BLOCK_STATUS = frozenset({"verified", "user_provided", "uncertain", "evidence_limited"})
_RENDERABLE_UNCITED = frozenset({"user_provided", "uncertain"})

# Canonical renderer-independent block vocabulary. Every renderer must
# handle each canonical type explicitly (HTML/PDF/DOCX/PPTX branches);
# legacy aliases normalize to canonical at block creation. Truly unknown
# types pass through untouched and degrade to escaped paragraphs
# downstream — never raw HTML, never silent loss of text.
BLOCK_TYPES = frozenset({
    "heading", "paragraph", "list", "table", "callout", "quote", "code",
    "timeline", "image", "sources", "page-break", "slide", "event",
    "summary", "attachment", "takeaway",
})

_BLOCK_TYPE_ALIASES = {
    "bullet": "list",
    "item": "list",
    "point": "list",
    "text": "paragraph",
    "prose": "paragraph",
    "description": "paragraph",
}


def normalize_block_type(raw: object) -> str:
    """Lower/strip a block type and fold legacy aliases to canonical."""
    btype = str(raw or "").lower().strip()
    return _BLOCK_TYPE_ALIASES.get(btype, btype)


def parse_markdown_table(paragraph: str) -> list[list[str]] | None:
    """Parse a GitHub-style markdown table paragraph into rows, else None.

    Shared by from_request so HTML/PDF/DOCX/PPTX all see one TableBlock
    instead of each renderer re-parsing (or ignoring) markdown text.
    """
    lines = [ln.strip() for ln in (paragraph or "").strip().splitlines() if ln.strip()]
    if len(lines) < 2 or any("|" not in ln for ln in lines):
        return None

    def _cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    body_start = 1
    sep = _cells(lines[1])
    if sep and all(re.fullmatch(r":?-{1,}:?", c or "") for c in sep):
        body_start = 2
    elif len({_cells_count(ln) for ln in lines}) != 1:
        return None
    rows = [_cells(lines[0])] + [_cells(ln) for ln in lines[body_start:]]
    if not any(rows[0]):
        return None
    width = max(len(r) for r in rows)
    return [r + [""] * (width - len(r)) for r in rows]


def _cells_count(line: str) -> int:
    return len([c for c in line.strip().strip("|").split("|")])


def _as_tuple(values: Any) -> tuple:
    if values is None:
        return ()
    if isinstance(values, tuple):
        return values
    if isinstance(values, list):
        return tuple(values)
    return tuple(values)


def stable_artifact_id(run_id: str, fmt: str, topic: str) -> str:
    """Deterministic idempotent artifact identity for one run+format+topic.

    Retries with the same ``run_id`` reuse the same stable ID instead of
    minting duplicates; distinct runs/topics stay distinct.
    """
    import hashlib as _hashlib

    seed = f"{(run_id or '').strip()}|{(fmt or '').strip().lower()}|{(topic or '').strip()[:200]}"
    digest = _hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    return f"art-{digest}"


def stable_run_key(session_id: str, query: str, formats: Any) -> str:
    """Deterministic idempotent run key for chat retries (session+query+formats)."""
    import hashlib as _hashlib

    fmts = ",".join(sorted(str(f or "").lower() for f in (formats or ()))) if formats else "text"
    seed = f"{(session_id or '').strip()}|{(query or '').strip()[:500]}|{fmts}"
    return f"chat-{_hashlib.sha256(seed.encode('utf-8')).hexdigest()[:10]}"


@dataclass(frozen=True)
class ArtifactBlock:
    """One renderable unit inside a section."""

    block_id: str
    block_type: str
    text: str = ""
    data: Optional[Dict[str, Any]] = None
    source_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    verification_status: str = "verified"

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_ids", _as_tuple(self.source_ids))
        object.__setattr__(self, "evidence_ids", _as_tuple(self.evidence_ids))
        if not self.block_id or not str(self.block_id).strip():
            raise ValueError("ArtifactBlock requires a block_id.")
        if not self.block_type or not str(self.block_type).strip():
            raise ValueError("ArtifactBlock requires a block_type.")
        # Fold legacy aliases (bullet/item/point -> list, ...) so every
        # renderer sees the canonical vocabulary.
        object.__setattr__(self, "block_type", normalize_block_type(self.block_type))
        if self.verification_status not in _ALLOWED_BLOCK_STATUS:
            raise ValueError(f"Unknown block verification_status: {self.verification_status!r}")

    def is_renderable(self) -> bool:
        """Evidence rule: uncited blocks render only when user-provided/uncertain.

        Structural ``page-break`` blocks carry no claims and always render.
        """
        if normalize_block_type(self.block_type) == "page-break":
            return True
        if self.source_ids or self.evidence_ids:
            return True
        return self.verification_status in _RENDERABLE_UNCITED

    def inline_citation_ids(self) -> tuple[str, ...]:
        return tuple(INLINE_CITATION_RE.findall(self.text or ""))

    def all_ids(self) -> tuple[str, ...]:
        seen: list[str] = []
        for value in (*self.source_ids, *self.evidence_ids, *self.inline_citation_ids()):
            if value not in seen:
                seen.append(value)
        return tuple(seen)


@dataclass(frozen=True)
class ArtifactSection:
    section_id: str
    title: str = ""
    blocks: tuple[ArtifactBlock, ...] = ()
    source_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    verification_status: str = "verified"

    def __post_init__(self) -> None:
        object.__setattr__(self, "blocks", _as_tuple(self.blocks))
        object.__setattr__(self, "source_ids", _as_tuple(self.source_ids))
        object.__setattr__(self, "evidence_ids", _as_tuple(self.evidence_ids))
        if not self.section_id or not str(self.section_id).strip():
            raise ValueError("ArtifactSection requires a section_id.")
        if self.verification_status not in _ALLOWED_BLOCK_STATUS:
            raise ValueError(f"Unknown section verification_status: {self.verification_status!r}")

    def renderable_blocks(self) -> tuple[ArtifactBlock, ...]:
        return tuple(b for b in self.blocks if b.is_renderable())


@dataclass(frozen=True)
class ArtifactTheme:
    palette: str = "sard-warm"
    direction: str = "rtl"
    locale: str = "ar-SA"
    variant: str = "default"


@dataclass(frozen=True)
class ArtifactMetadata:
    artifact_id: str = ""
    run_id: str = ""
    version: int = 1
    status: str = "created"
    format: str = ""
    kind: str = ""
    title: str = ""
    topic: str = ""
    region: str = "المملكة العربية السعودية"
    verification_status: str = "verified"
    retrieval_mode: str = ""
    model_fallback_used: bool = False
    warnings: tuple[str, ...] = ()
    checksum: Optional[str] = None
    provenance: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "warnings", _as_tuple(self.warnings))
        object.__setattr__(self, "provenance", _as_tuple(self.provenance))
        object.__setattr__(self, "evidence_ids", _as_tuple(self.evidence_ids))
        try:
            version = int(self.version or 1)
        except (TypeError, ValueError):
            version = 1
        if version < 1:
            version = 1
        object.__setattr__(self, "version", version)
        if not str(self.status or "").strip():
            object.__setattr__(self, "status", "created")


@dataclass(frozen=True)
class ArtifactDocument:
    metadata: ArtifactMetadata
    theme: ArtifactTheme = field(default_factory=ArtifactTheme)
    sections: tuple[ArtifactSection, ...] = ()
    sources: tuple[CitationSource, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "sections", _as_tuple(self.sections))
        object.__setattr__(self, "sources", _as_tuple(self.sources))

    # -- citation helpers (delegates to Itinerary logic) ---------------------

    def all_source_ids(self) -> tuple[str, ...]:
        ids: list[str] = []
        seen: set[str] = set()

        def add(values: Any) -> None:
            for value in values or ():
                if value not in seen:
                    seen.add(value)
                    ids.append(value)

        for section in self.sections:
            add(section.source_ids)
            add(section.evidence_ids)
            for block in section.blocks:
                add(block.source_ids)
                add(block.evidence_ids)
                add(block.inline_citation_ids())
        return tuple(ids)

    def validate_citations(self) -> dict[str, CitationSource]:
        """Reject duplicate/unknown references; mirrors Itinerary.validate_citations."""
        mapping: dict[str, CitationSource] = {}
        for source in self.sources:
            if source.citation_id in mapping:
                raise ValueError(f"Duplicate citation ID: {source.citation_id}")
            mapping[source.citation_id] = source
        unknown = set(self.all_source_ids()) - set(mapping.keys())
        # Evidence ids may reference non-citation claims; only enforce CIT-* ids.
        unknown_citations = {u for u in unknown if CITATION_ID_RE.fullmatch(str(u))}
        if unknown_citations:
            raise ValueError(f"Unknown citation ID(s): {', '.join(sorted(unknown_citations))}")
        return mapping

    def renderable_sections(self) -> tuple[ArtifactSection, ...]:
        return tuple(s for s in self.sections if s.renderable_blocks() or s.source_ids or s.evidence_ids)

    # -- preview --------------------------------------------------------------

    def _preview_type(self) -> str:
        kind = (self.metadata.kind or "").lower().strip()
        fmt = (self.metadata.format or "").lower().strip()
        if fmt == "pptx" or kind == "presentation":
            return "slides"
        if fmt == "ics" or kind == "calendar":
            return "calendar"
        if fmt == "png" or kind == "image":
            return "image"
        if fmt == "svg" or kind == "diagram":
            return "diagram"
        if fmt == "csv" or kind == "table":
            return "table"
        if fmt == "txt" or kind == "text":
            return "text"
        if fmt == "json" or kind in ("json", "interactive"):
            return "json"
        if kind in ("card", "recipe", "memoir", "greeting"):
            return "card"
        return "document"

    def to_preview(self) -> Dict[str, Any]:
        """Single preview generator producing {type,title,counts,items} superset.

        The result always contains the canonical ``type``/``title``/``counts``/
        ``items`` keys plus compat keys covering every legacy ``preview_data``
        shape (document paragraphs/sections, slides, calendar events, image
        dims, table rows, text characters) and deprecated aliases
        ``card_data``/``diagram_data``/``slides`` kept for one release.
        Only evidence-rule-renderable blocks appear in ``items``.
        """
        preview_type = self._preview_type()
        renderable: List[Dict[str, Any]] = []
        total_blocks = 0
        for section in self.sections:
            for block in section.blocks:
                total_blocks += 1
                if not block.is_renderable():
                    continue
                renderable.append(
                    {
                        "id": block.block_id,
                        "type": block.block_type,
                        "section": section.section_id,
                        "text": block.text,
                        "data": dict(block.data) if isinstance(block.data, dict) else block.data,
                        "source_ids": list(block.source_ids),
                        "evidence_ids": list(block.evidence_ids),
                        "verification_status": block.verification_status,
                    }
                )

        def _of_type(*names: str) -> List[Dict[str, Any]]:
            wanted = {n.lower() for n in names}
            return [i for i in renderable if str(i.get("type", "")).lower() in wanted]

        paragraphs = _of_type("paragraph", "text", "description", "summary", "prose")
        slides = _of_type("slide")
        events = _of_type("event", "calendar_event")
        images = _of_type("image", "diagram", "card")
        rows = _of_type("row", "table_row")

        # Slide compat: fall back to one item per section when no explicit slide blocks.
        slides_compat: List[Any] = [
            {"index": idx + 1, "title": (s.get("text") or "")[:120], "type": s.get("type", "slide")}
            for idx, s in enumerate(slides)
        ]
        if not slides_compat and preview_type == "slides":
            slides_compat = [
                {"index": idx + 1, "title": (sec.title or "")[:120], "type": "slide"}
                for idx, sec in enumerate(self.sections)
            ]
        events_compat: List[Any] = [
            {"title": (e.get("text") or "")[:120], "data": e.get("data"), "source_ids": e.get("source_ids", [])}
            for e in events
        ]
        text_excerpt = "\n".join(str(i.get("text") or "") for i in renderable)[:2000]
        characters = sum(len(str(i.get("text") or "")) for i in renderable)

        # Deprecated aliases: always present for one release (None when n/a).
        card_like = preview_type in ("card", "document") and bool(renderable)
        diagram_like = preview_type in ("diagram", "image") and bool(renderable)
        card_data = (
            {"title": self.metadata.title, "topic": self.metadata.topic, "items": renderable}
            if card_like
            else None
        )
        # Diagram alias carries node-ish payload when the doc is diagram/image shaped.
        diagram_data = (
            {
                "diagram_type": self.metadata.kind or preview_type,
                "title": self.metadata.title,
                "nodes": renderable,
            }
            if diagram_like or preview_type == "diagram"
            else None
        )
        # Preserve raw diagram/card block data on the alias when available.
        for item in renderable:
            if isinstance(item.get("data"), dict) and isinstance(card_data, dict) and "raw" not in card_data:
                raw = item["data"]
                if any(k in raw for k in ("card_type", "occasion", "ingredients_or_materials", "steps")):
                    card_data = {**card_data, "raw": raw}
            if isinstance(item.get("data"), dict) and isinstance(diagram_data, dict) and "raw" not in diagram_data:
                raw = item["data"]
                if any(k in raw for k in ("diagram_type", "nodes", "timeline_milestones")):
                    diagram_data = {**diagram_data, "raw": raw}

        preview: Dict[str, Any] = {
            "type": preview_type,
            "title": self.metadata.title,
            "counts": {
                "sections": len(self.sections),
                "blocks": total_blocks,
                "renderable_blocks": len(renderable),
                "sources": len(self.sources),
                "paragraphs": len(paragraphs),
                "slides": len(slides_compat),
                "events": len(events_compat),
                "images": len(images),
                "rows": len(rows),
                "characters": characters,
            },
            "items": renderable,
            # Compat superset covering every legacy preview_data shape.
            "paragraphs_count": len(paragraphs) or len(renderable),
            "sections_count": len(self.sections),
            "slides_count": len(slides_compat),
            "slides": slides_compat,
            "events_count": len(events_compat),
            "events": events_compat,
            "rows": len(rows) or (len(renderable) if preview_type == "table" else 0),
            "characters": characters,
            "text": text_excerpt,
            # Deprecated aliases (one release).
            "card_data": card_data,
            "diagram_data": diagram_data,
            "_deprecated_aliases": ["card_data", "diagram_data", "slides"],
        }
        if preview_type in ("image", "diagram"):
            width = 1200
            height = 800
            for item in renderable:
                data = item.get("data") if isinstance(item.get("data"), dict) else None
                if data and isinstance(data.get("width"), int):
                    width = int(data["width"])
                if data and isinstance(data.get("height"), int):
                    height = int(data["height"])
            preview["width"] = width
            preview["height"] = height
        if self.metadata.format.lower() == "json" or preview_type == "json":
            preview["payload"] = {
                "title": self.metadata.title,
                "topic": self.metadata.topic,
                "text": text_excerpt,
                "items": renderable,
            }
        return preview

    # -- shims ------------------------------------------------------------------

    def to_artifact_request(self) -> Any:
        """Convert back to an orchestrator ArtifactRequest (lazy import)."""
        from sard.outputs.orchestrator import ArtifactRequest as _ArtifactRequest

        raw_text = "\n\n".join(
            b.text for s in self.sections for b in s.blocks if b.is_renderable() and b.text
        )
        content_data: Dict[str, Any] = {
            "sections": [
                {
                    "id": s.section_id,
                    "title": s.title,
                    "blocks": [
                        {
                            "id": b.block_id,
                            "type": b.block_type,
                            "text": b.text,
                            "data": b.data,
                            "source_ids": list(b.source_ids),
                            "verification_status": b.verification_status,
                        }
                        for b in s.blocks
                        if b.is_renderable()
                    ],
                }
                for s in self.sections
            ],
            "items": [
                {"id": b.block_id, "type": b.block_type, "text": b.text, "data": b.data}
                for s in self.sections
                for b in s.blocks
                if b.is_renderable()
            ],
        }
        sources = tuple(
            {"citation_id": s.citation_id, "title": s.title, "url": s.url} for s in self.sources
        )
        metadata: Dict[str, Any] = {"run_id": self.metadata.run_id}
        if self.metadata.warnings:
            metadata["warnings"] = list(self.metadata.warnings)
        return _ArtifactRequest(
            format=self.metadata.format or "pdf",
            kind=self.metadata.kind or "document",
            title=self.metadata.title,
            topic=self.metadata.topic,
            content_data=content_data,
            raw_text=raw_text,
            sources=sources,  # type: ignore[arg-type]
            metadata=metadata,
            region=self.metadata.region,
        )

    @classmethod
    def from_itinerary(
        cls,
        itinerary: Any,
        *,
        artifact_id: str = "",
        run_id: str = "",
        format: str = "pdf",
        kind: str = "document",
        region: Optional[str] = None,
        theme: Optional[ArtifactTheme] = None,
        checksum: Optional[str] = None,
    ) -> "ArtifactDocument":
        """Build a document from a typed Itinerary, preserving citations/provenance."""
        days = tuple(getattr(itinerary, "days", ()) or ())
        sources = tuple(getattr(itinerary, "sources", ()) or ())
        sections: List[ArtifactSection] = []

        def _support_map(supports: Any) -> Dict[str, Any]:
            return {s.field_name: s for s in (supports or ())}

        itin_supports = _support_map(getattr(itinerary, "field_support", ()))

        def _status_for(field_name: str, has_ids: bool) -> str:
            support = itin_supports.get(field_name)
            if support is not None and getattr(support, "provenance", "") in ("user_provided", "uncertain"):
                return str(support.provenance)
            if has_ids:
                return "verified"
            # Uncited itinerary-level text stays renderable only when explicitly
            # user-provided/uncertain; default to uncertain to preserve content
            # without claiming verification.
            return "uncertain"

        # Summary section keeps title/summary round-trippable.
        summary_text = str(getattr(itinerary, "summary", "") or "")
        if summary_text:
            has_ids = bool(getattr(itinerary, "citation_ids", ()))
            sections.append(
                ArtifactSection(
                    section_id="summary",
                    title=str(getattr(itinerary, "title", "") or ""),
                    blocks=(
                        ArtifactBlock(
                            block_id="summary-1",
                            block_type="summary",
                            text=summary_text,
                            source_ids=tuple(getattr(itinerary, "citation_ids", ()) or ()),
                            verification_status=_status_for("summary", has_ids),
                        ),
                    ),
                    source_ids=tuple(getattr(itinerary, "citation_ids", ()) or ()),
                    verification_status=_status_for("summary", has_ids),
                )
            )

        for day_idx, day in enumerate(days, start=1):
            blocks: List[ArtifactBlock] = []
            for stop_idx, stop in enumerate(getattr(day, "stops", ()) or (), start=1):
                stop_supports = _support_map(getattr(stop, "field_support", ()))
                prefix = f"day{day_idx}-stop{stop_idx}"
                header = f"{getattr(stop, 'title', '')} — {getattr(stop, 'effective_location_name', getattr(stop, 'location', ''))}".strip(" —")
                if header.strip(" —"):
                    title_ids = tuple(getattr(stop_supports.get("title"), "citation_ids", ()) or ())
                    title_prov = getattr(stop_supports.get("title"), "provenance", "verified")
                    blocks.append(
                        ArtifactBlock(
                            block_id=f"{prefix}-header",
                            block_type="heading",
                            text=header,
                            source_ids=title_ids,
                            verification_status=title_prov if title_prov in _ALLOWED_BLOCK_STATUS else "verified",
                        )
                    )
                for field_name, texts, btype in (
                    ("description", tuple(getattr(stop, "effective_description", ()) or ()), "paragraph"),
                    ("practical_notes", tuple(getattr(stop, "effective_practical_notes", ()) or ()), "bullet"),
                    ("accessibility_notes", tuple(getattr(stop, "effective_accessibility_notes", ()) or ()), "bullet"),
                    ("notes", tuple(getattr(stop, "notes", ()) or ()), "note"),
                ):
                    support = stop_supports.get(field_name)
                    prov = getattr(support, "provenance", "verified") if support else "verified"
                    fallback_ids = tuple(getattr(support, "citation_ids", ()) or ()) if support else ()
                    for k, tb in enumerate(texts, start=1):
                        inline = tuple(INLINE_CITATION_RE.findall(getattr(tb, "text", "") or ""))
                        ids = tuple(dict.fromkeys((*getattr(tb, "citation_ids", ()) , *inline, *fallback_ids)))
                        status = prov if prov in _ALLOWED_BLOCK_STATUS else "verified"
                        if not ids and status == "verified":
                            status = "uncertain"
                        blocks.append(
                            ArtifactBlock(
                                block_id=f"{prefix}-{field_name}-{k}",
                                block_type=btype,
                                text=str(getattr(tb, "text", "") or ""),
                                source_ids=ids,
                                verification_status=status,
                            )
                        )
            for k, note in enumerate(getattr(day, "notes", ()) or (), start=1):
                inline = tuple(INLINE_CITATION_RE.findall(getattr(note, "text", "") or ""))
                ids = tuple(dict.fromkeys((*getattr(note, "citation_ids", ()), *inline)))
                blocks.append(
                    ArtifactBlock(
                        block_id=f"day{day_idx}-note-{k}",
                        block_type="note",
                        text=str(getattr(note, "text", "") or ""),
                        source_ids=ids,
                        verification_status="verified" if ids else "uncertain",
                    )
                )
            day_title = str(getattr(day, "title", "") or f"اليوم {day_idx}")
            sections.append(
                ArtifactSection(
                    section_id=f"day-{day_idx}",
                    title=day_title,
                    blocks=tuple(blocks),
                )
            )

        for k, note in enumerate(getattr(itinerary, "notes", ()) or (), start=1):
            inline = tuple(INLINE_CITATION_RE.findall(getattr(note, "text", "") or ""))
            ids = tuple(dict.fromkeys((*getattr(note, "citation_ids", ()), *inline)))
            sections.append(
                ArtifactSection(
                    section_id=f"note-{k}",
                    title="ملاحظات",
                    blocks=(
                        ArtifactBlock(
                            block_id=f"note-{k}-1",
                            block_type="note",
                            text=str(getattr(note, "text", "") or ""),
                            source_ids=ids,
                            verification_status="verified" if ids else "uncertain",
                        ),
                    ),
                )
            )

        verification = getattr(getattr(itinerary, "verification_status", "verified"), "value", getattr(itinerary, "verification_status", "verified"))
        metadata = ArtifactMetadata(
            artifact_id=artifact_id or "",
            run_id=run_id or str(getattr(itinerary, "run_id", "") or ""),
            format=format,
            kind=kind,
            title=str(getattr(itinerary, "title", "") or ""),
            topic=str(getattr(itinerary, "summary", "") or "")[:200],
            region=region or "المملكة العربية السعودية",
            verification_status=str(verification or "verified"),
            retrieval_mode=str(getattr(itinerary, "retrieval_mode", "") or ""),
            model_fallback_used=bool(getattr(itinerary, "model_fallback_used", False)),
            warnings=tuple(getattr(itinerary, "warnings", ()) or ()),
            checksum=checksum,
        )
        return cls(metadata=metadata, theme=theme or ArtifactTheme(), sections=tuple(sections), sources=sources)

    @classmethod
    def from_request(
        cls,
        request: Any,
        *,
        artifact_id: str = "",
        run_id: str = "",
        version: int = 0,
        status: str = "",
        verification_status: str = "verified",
        retrieval_mode: str = "",
        model_fallback_used: bool = False,
        warnings: tuple[str, ...] = (),
        preview: Optional[Dict[str, Any]] = None,
        theme: Optional[ArtifactTheme] = None,
        checksum: Optional[str] = None,
        provenance: Any = (),
        evidence_ids: Any = (),
    ) -> "ArtifactDocument":
        """Build a document from an orchestrator ArtifactRequest (or duck-typed equiv).

        Preserves run_id/artifact_id/version/format/status/metadata/provenance/
        evidence IDs across chat -> request -> document -> renderer -> store.
        Explicit kwargs win; otherwise values fall back to ``request.metadata``.
        """
        content = getattr(request, "content_data", None) or {}
        raw_text = str(getattr(request, "raw_text", "") or "")
        fmt = str(getattr(request, "format", "") or "")
        kind = str(getattr(request, "kind", "") or "document")
        title = str(getattr(request, "title", "") or "")
        topic = str(getattr(request, "topic", "") or "")
        region = str(getattr(request, "region", "") or "المملكة العربية السعودية")
        meta_dict = getattr(request, "metadata", None) or {}
        if not isinstance(meta_dict, dict):
            meta_dict = {}
        resolved_run = run_id or str(meta_dict.get("run_id", "") or "")
        resolved_artifact = artifact_id or str(meta_dict.get("artifact_id", "") or "")
        try:
            resolved_version = int(version or meta_dict.get("version", 1) or 1)
        except (TypeError, ValueError):
            resolved_version = 1
        if resolved_version < 1:
            resolved_version = 1
        resolved_status = str(status or meta_dict.get("status", "") or "created")
        resolved_provenance = tuple(provenance or ()) or tuple(meta_dict.get("provenance", ()) or ())
        resolved_evidence: List[str] = []
        seen_ev: set[str] = set()
        for value in (*tuple(evidence_ids or ()), *tuple(meta_dict.get("evidence_ids", ()) or ())):
            text = str(value or "").strip()
            if text and text not in seen_ev:
                seen_ev.add(text)
                resolved_evidence.append(text)
        # Carry per-source evidence IDs from request sources when present.
        for entry in (getattr(request, "sources", ()) or ()):
            if isinstance(entry, dict):
                for key in ("evidence_id", "chunk_id", "source_id"):
                    text = str(entry.get(key, "") or "").strip()
                    if text and text not in seen_ev:
                        seen_ev.add(text)
                        resolved_evidence.append(text)

        sources: List[CitationSource] = []
        for entry in (getattr(request, "sources", ()) or ()):
            try:
                if isinstance(entry, CitationSource):
                    sources.append(entry)
                    continue
                if isinstance(entry, dict):
                    cid = str(entry.get("citation_id", "") or "")
                    stitle = str(entry.get("title", "") or "")
                    url = str(entry.get("url", "") or "")
                    if cid and stitle and url and CITATION_ID_RE.fullmatch(cid):
                        sources.append(CitationSource(citation_id=cid, title=stitle, url=url))
            except ValueError:
                continue

        sections: List[ArtifactSection] = []
        raw_sections = content.get("sections") if isinstance(content, dict) else None
        if isinstance(raw_sections, list) and raw_sections:
            for idx, sec in enumerate(raw_sections, start=1):
                if not isinstance(sec, dict):
                    continue
                sec_blocks: List[ArtifactBlock] = []
                for j, b in enumerate(sec.get("blocks", []) if isinstance(sec.get("blocks"), list) else [], start=1):
                    if not isinstance(b, dict):
                        continue
                    text = str(b.get("text", "") or "")
                    # page-break is structural and textless by nature: it must
                    # survive even with empty text and no data.
                    is_break = normalize_block_type(b.get("type")) == "page-break"
                    if not text.strip() and not b.get("data") and not is_break:
                        continue
                    sec_blocks.append(
                        ArtifactBlock(
                            block_id=str(b.get("id") or f"sec{idx}-block{j}"),
                            block_type=str(b.get("type") or "paragraph"),
                            text=text,
                            data=b.get("data") if isinstance(b.get("data"), dict) else None,
                            source_ids=tuple(b.get("source_ids", ()) or ()),
                            verification_status=str(b.get("verification_status", verification_status) or verification_status),
                        )
                    )
                # Legacy flat section shape: {"title":..., "paragraphs":[...]} etc.
                for key, btype in (("paragraphs", "paragraph"), ("bullets", "bullet"), ("points", "bullet"), ("items", "item")):
                    vals = sec.get(key)
                    if isinstance(vals, list):
                        for j, v in enumerate(vals, start=1):
                            text = v if isinstance(v, str) else str((v or {}).get("text") or (v or {}).get("title") or "") if isinstance(v, dict) else str(v)
                            if not text.strip():
                                continue
                            sec_blocks.append(
                                ArtifactBlock(
                                    block_id=f"sec{idx}-{key}-{j}",
                                    block_type=btype,
                                    text=text[:2000],
                                    data=v if isinstance(v, dict) else None,
                                    verification_status="uncertain",
                                )
                            )
                if sec_blocks or str(sec.get("title", "") or "").strip():
                    sections.append(
                        ArtifactSection(
                            section_id=str(sec.get("id") or f"section-{idx}"),
                            title=str(sec.get("title", "") or ""),
                            blocks=tuple(sec_blocks),
                            verification_status=verification_status if verification_status in _ALLOWED_BLOCK_STATUS else "verified",
                        )
                    )
        else:
            blocks: List[ArtifactBlock] = []
            # Structured content_data lists (items/cards/rows/points/bullets).
            if isinstance(content, dict):
                for key in ("items", "cards", "rows", "points", "bullets", "slides", "events"):
                    vals = content.get(key)
                    if isinstance(vals, list) and vals:
                        for j, v in enumerate(vals, start=1):
                            if isinstance(v, dict):
                                text = str(v.get("title") or v.get("name") or v.get("text") or "")[:2000]
                                data = v
                            else:
                                text = str(v)[:2000]
                                data = None
                            if not text.strip():
                                continue
                            btype = "slide" if key == "slides" else ("event" if key == "events" else "item")
                            blocks.append(
                                ArtifactBlock(
                                    block_id=f"content-{key}-{j}",
                                    block_type=btype,
                                    text=text,
                                    data=data,
                                    verification_status="uncertain",
                                )
                            )
                        break
            if not blocks:
                paras = [p.strip() for p in raw_text.split("\n\n") if p.strip()] if raw_text else []
                if not paras and isinstance(content, dict):
                    for key in ("paragraphs", "summary", "text"):
                        val = content.get(key)
                        if isinstance(val, str) and val.strip():
                            paras = [val.strip()]
                            break
                        if isinstance(val, list) and val and all(isinstance(x, str) for x in val):
                            paras = [x.strip() for x in val if x.strip()]
                            break
                if not paras:
                    paras = [topic] if topic.strip() else [title]
                for j, para in enumerate(paras, start=1):
                    table_rows = parse_markdown_table(para)
                    if table_rows:
                        blocks.append(
                            ArtifactBlock(
                                block_id=f"p{j}-table",
                                block_type="table",
                                text=" | ".join(table_rows[0])[:500],
                                data={"rows": table_rows},
                                verification_status="uncertain",
                            )
                        )
                        continue
                    blocks.append(
                        ArtifactBlock(
                            block_id=f"p{j}",
                            block_type="paragraph",
                            text=para[:2000],
                            verification_status="uncertain",
                        )
                    )
            # Carry raw renderer preview payload (card/diagram/slides) as block data
            # so to_preview() deprecated aliases stay faithful without new APIs.
            if isinstance(preview, dict) and preview:
                blocks.append(
                    ArtifactBlock(
                        block_id="renderer-payload",
                        block_type="attachment",
                        text="",
                        data={k: preview.get(k) for k in ("card_type", "diagram_type", "occasion", "slides", "events", "nodes", "items") if k in preview},
                        verification_status="uncertain",
                    )
                )
            sections.append(ArtifactSection(section_id="main", title=title, blocks=tuple(blocks)))

        request_warnings = tuple(warnings or ())
        if isinstance(content, dict) and isinstance(content.get("key_takeaways"), list):
            pass  # takeaways already folded into items when present

        metadata = ArtifactMetadata(
            artifact_id=resolved_artifact,
            run_id=resolved_run,
            version=resolved_version,
            status=resolved_status,
            format=fmt,
            kind=kind,
            title=title,
            topic=topic,
            region=region,
            verification_status=verification_status if verification_status in _ALLOWED_BLOCK_STATUS else "verified",
            retrieval_mode=retrieval_mode or str(meta_dict.get("retrieval_mode", "") or ""),
            model_fallback_used=model_fallback_used or bool(meta_dict.get("model_fallback_used", False)),
            warnings=request_warnings or tuple(meta_dict.get("warnings", ()) or ()),
            checksum=checksum or (str(meta_dict.get("checksum", "") or "") or None),
            provenance=resolved_provenance,
            evidence_ids=tuple(resolved_evidence),
        )
        return cls(metadata=metadata, theme=theme or ArtifactTheme(), sections=tuple(sections), sources=tuple(sources))


    # -- persistence (revision / conversion source of truth) ------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the canonical document for durable versioned storage."""
        return {
            "metadata": {
                "artifact_id": self.metadata.artifact_id,
                "run_id": self.metadata.run_id,
                "version": int(self.metadata.version or 1),
                "status": self.metadata.status,
                "format": self.metadata.format,
                "kind": self.metadata.kind,
                "title": self.metadata.title,
                "topic": self.metadata.topic,
                "region": self.metadata.region,
                "verification_status": self.metadata.verification_status,
                "retrieval_mode": self.metadata.retrieval_mode,
                "model_fallback_used": bool(self.metadata.model_fallback_used),
                "warnings": list(self.metadata.warnings),
                "checksum": self.metadata.checksum,
                "provenance": list(self.metadata.provenance),
                "evidence_ids": list(self.metadata.evidence_ids),
            },
            "theme": {
                "palette": self.theme.palette,
                "direction": self.theme.direction,
                "locale": self.theme.locale,
                "variant": self.theme.variant,
            },
            "sections": [
                {
                    "section_id": section.section_id,
                    "title": section.title,
                    "source_ids": list(section.source_ids),
                    "evidence_ids": list(section.evidence_ids),
                    "verification_status": section.verification_status,
                    "blocks": [
                        {
                            "block_id": block.block_id,
                            "block_type": block.block_type,
                            "text": block.text,
                            "data": dict(block.data) if isinstance(block.data, dict) else block.data,
                            "source_ids": list(block.source_ids),
                            "evidence_ids": list(block.evidence_ids),
                            "verification_status": block.verification_status,
                        }
                        for block in section.blocks
                    ],
                }
                for section in self.sections
            ],
            "sources": [
                {
                    "citation_id": source.citation_id,
                    "title": source.title,
                    "url": source.url,
                }
                for source in self.sources
            ],
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ArtifactDocument":
        """Rehydrate a persisted canonical document (unknown fields ignored)."""
        from sard.outputs.schemas import CitationSource as _CitationSource

        data = dict(payload or {})
        meta = dict(data.get("metadata", {}) or {})
        theme_data = dict(data.get("theme", {}) or {})
        sections: List[ArtifactSection] = []
        for sec in (data.get("sections", []) or []):
            if not isinstance(sec, dict):
                continue
            blocks: List[ArtifactBlock] = []
            for item in (sec.get("blocks", []) or []):
                if not isinstance(item, dict):
                    continue
                try:
                    blocks.append(
                        ArtifactBlock(
                            block_id=str(item.get("block_id") or item.get("id") or "block"),
                            block_type=str(item.get("block_type") or item.get("type") or "paragraph"),
                            text=str(item.get("text", "") or ""),
                            data=item.get("data") if isinstance(item.get("data"), dict) else None,
                            source_ids=tuple(item.get("source_ids", ()) or ()),
                            evidence_ids=tuple(item.get("evidence_ids", ()) or ()),
                            verification_status=str(item.get("verification_status", "verified") or "verified"),
                        )
                    )
                except ValueError:
                    continue
            try:
                sections.append(
                    ArtifactSection(
                        section_id=str(sec.get("section_id") or sec.get("id") or "section"),
                        title=str(sec.get("title", "") or ""),
                        blocks=tuple(blocks),
                        source_ids=tuple(sec.get("source_ids", ()) or ()),
                        evidence_ids=tuple(sec.get("evidence_ids", ()) or ()),
                        verification_status=str(sec.get("verification_status", "verified") or "verified"),
                    )
                )
            except ValueError:
                continue
        sources: List[Any] = []
        for entry in (data.get("sources", []) or []):
            if not isinstance(entry, dict):
                continue
            try:
                sources.append(
                    _CitationSource(
                        citation_id=str(entry.get("citation_id", "")),
                        title=str(entry.get("title", "")),
                        url=str(entry.get("url", "")),
                    )
                )
            except ValueError:
                continue
        metadata = ArtifactMetadata(
            artifact_id=str(meta.get("artifact_id", "") or ""),
            run_id=str(meta.get("run_id", "") or ""),
            version=int(meta.get("version", 1) or 1),
            status=str(meta.get("status", "") or "created"),
            format=str(meta.get("format", "") or ""),
            kind=str(meta.get("kind", "") or ""),
            title=str(meta.get("title", "") or ""),
            topic=str(meta.get("topic", "") or ""),
            region=str(meta.get("region", "") or "المملكة العربية السعودية"),
            verification_status=str(meta.get("verification_status", "") or "verified"),
            retrieval_mode=str(meta.get("retrieval_mode", "") or ""),
            model_fallback_used=bool(meta.get("model_fallback_used", False)),
            warnings=tuple(meta.get("warnings", ()) or ()),
            checksum=meta.get("checksum"),
            provenance=tuple(meta.get("provenance", ()) or ()),
            evidence_ids=tuple(meta.get("evidence_ids", ()) or ()),
        )
        theme = ArtifactTheme(
            palette=str(theme_data.get("palette", "") or "sard-warm"),
            direction=str(theme_data.get("direction", "") or "rtl"),
            locale=str(theme_data.get("locale", "") or "ar-SA"),
            variant=str(theme_data.get("variant", "") or "default"),
        )
        return cls(metadata=metadata, theme=theme, sections=tuple(sections), sources=tuple(sources))


__all__ = [
    "ArtifactBlock",
    "ArtifactSection",
    "ArtifactTheme",
    "ArtifactMetadata",
    "ArtifactDocument",
    "stable_artifact_id",
    "stable_run_key",
]
