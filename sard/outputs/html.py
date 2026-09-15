"""Canonical HTML renderer: ``ArtifactDocument`` -> sanitized HTML/CSS.

BROWSER RTL POLICY: this module emits LOGICAL (unshaped) Unicode and lets
the browser shape Arabic natively — ``dir="rtl"``, ``direction: rtl``,
logical CSS properties.  It MUST NOT import ``arabic_reshaper``/bidi.

TRUST BOUNDARY: model text is untrusted.  Every value is HTML-escaped and
validated (no arbitrary model HTML becomes trusted DOM).  Output is
CSP-friendly: a single inline ``<style>`` block, no ``<script>``, no inline
event handlers.  Links allow ``https?``/``mailto`` only, with
``rel="noopener noreferrer"``.  Images allow ``https?`` URLs and
``data:image/`` URIs only.

HTML and PDF share the Sard token concepts (paper/ink/clay/date/olive/gold,
card) so the two renderers stay visually coherent.

Consumes the canonical :class:`ArtifactDocument
<sard.outputs.document.ArtifactDocument>` owned by ``document.py`` (artifact
agent).  Renderers render the blocks they are given — verification filtering
happens upstream at the verify boundary — and never invent filler content.
"""

from __future__ import annotations

import html as _html
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from sard.outputs.arabic import contains_arabic
from sard.outputs.document import ArtifactBlock, ArtifactDocument, ArtifactSection

MIME_TYPE = "text/html; charset=utf-8"

# Sard design tokens shared conceptually with the ReportLab palette.
TOKENS = {
    "paper": "#F3EEE4",
    "paper2": "#E8E0D2",
    "ink": "#141210",
    "clay": "#BE4A24",
    "date": "#6E1F1F",
    "olive": "#4A513C",
    "gold": "#C4A46A",
    "card": "#FAF7F1",
    "border": "#D4CBBD",
    "muted": "#8A8178",
}

_SAFE_URL_RE = re.compile(r"^(https?://|mailto:)[^\s<>\"]+$", re.IGNORECASE)
_SAFE_IMG_RE = re.compile(
    r"^(https://[^\s<>\"]+|data:image/(png|jpeg|gif|webp|svg\+xml);base64,[A-Za-z0-9+/=]+)$"
)

_STYLES = """:root{--paper:%(paper)s;--paper2:%(paper2)s;--ink:%(ink)s;--clay:%(clay)s;--date:%(date)s;--olive:%(olive)s;--gold:%(gold)s;--card:%(card)s;--border:%(border)s;--muted:%(muted)s}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:"Noto Naskh Arabic","IBM Plex Sans Arabic","Segoe UI",Arial,sans-serif;line-height:1.9}
.sard-sheet{max-inline-size:72rem;margin-inline:auto;padding:2rem 1.5rem 4rem}
.sard-cover{border-block-start:.35rem solid var(--gold);padding-block-start:1.25rem;margin-block-end:1.5rem}
.sard-kicker{color:var(--clay);font-weight:700;font-size:.95rem}
.sard-title{font-size:2.1rem;line-height:1.5;margin:.4rem 0;color:var(--ink)}
.sard-subtitle{color:var(--muted);font-size:1.1rem}
.sard-meta{color:var(--muted);font-size:.9rem;border-block-end:1px solid var(--gold);padding-block-end:1rem}
.sard-notice{background:#FFF8E1;border-inline-start:.3rem solid var(--gold);padding:.75rem 1rem;border-radius:.5rem;margin-block:1rem}
.sard-summary{background:var(--card);border:1px solid var(--gold);border-radius:.75rem;padding:1rem 1.25rem;margin-block:1.25rem}
.sard-summary h2{color:var(--clay);font-size:1.15rem;margin:0 0 .5rem}
.sard-section{margin-block:1.75rem}
.sard-section>h2{color:var(--date);font-size:1.4rem;border-block-end:1px solid var(--border);padding-block-end:.35rem}
.sard-badge{color:var(--olive);font-size:.85rem;font-weight:700}
.sard-callout{background:var(--card);border-inline-start:.3rem solid var(--clay);border-radius:.5rem;padding:.8rem 1rem;margin-block:.9rem}
.sard-quote{border-inline-start:.3rem solid var(--gold);margin-inline:0;padding:.6rem 1rem;color:var(--date);font-style:italic;background:var(--card);border-radius:.5rem}
.sard-grid{display:grid;gap:1rem;grid-template-columns:repeat(auto-fit,minmax(min(18rem,100%%),1fr))}
.sard-card{background:var(--card);border:1px solid var(--border);border-radius:.75rem;padding:1rem 1.1rem}
table.sard-table{border-collapse:collapse;inline-size:100%%;margin-block:1rem;background:#fff}
.sard-table th,.sard-table td{border:1px solid var(--border);padding:.5rem .7rem;text-align:start;vertical-align:top}
.sard-table thead th{background:var(--paper2);color:var(--date)}
.sard-takeaways{background:var(--paper2);border:1px solid var(--olive);border-radius:.75rem;padding:1rem 1.25rem}
.sard-sources{font-size:.92rem;color:var(--muted)}
.sard-sources a{color:var(--clay);overflow-wrap:anywhere}
.sard-footer{margin-block-start:2.5rem;border-block-start:1px solid var(--border);padding-block-start:.8rem;color:var(--muted);font-size:.85rem}
img.sard-img{max-inline-size:100%%;block-size:auto;border-radius:.75rem;border:1px solid var(--border)}
ul.sard-list{padding-inline-start:1.4rem}
ul.sard-list li{margin-block:.3rem}
@media (max-width:40rem){.sard-sheet{padding:1.25rem .9rem 3rem}.sard-title{font-size:1.6rem}}
@media print{.sard-sheet{max-inline-size:none}body{background:#fff}}
""" % TOKENS


class HtmlRenderError(ValueError):
    """HTML rendering failed loudly (missing content, unsafe path)."""


def _esc(value: object) -> str:
    return _html.escape(str(value or ""), quote=True)


def _safe_url(url: str) -> str | None:
    candidate = (url or "").strip()
    if _SAFE_URL_RE.match(candidate):
        return candidate
    return None


def _safe_img(src: str) -> str | None:
    candidate = (src or "").strip()
    if _SAFE_IMG_RE.match(candidate):
        return candidate
    return None


def _dir_attr(text: str) -> str:
    return ' dir="rtl" lang="ar"' if contains_arabic(text or "") else ' dir="ltr"'


def _render_table(rows: Sequence[Sequence[object]], direction: str = "rtl") -> str:
    if not rows:
        return ""
    cleaned = [[_esc(c or "") for c in row] for row in rows]
    head, body = cleaned[0], cleaned[1:]
    doc_dir = direction if direction in {"rtl", "ltr"} else "rtl"
    parts = [f'<div dir="{doc_dir}"><table class="sard-table">']
    parts.append(
        "<thead><tr>" + "".join(f"<th scope=\"col\">{c}</th>" for c in head) + "</tr></thead>"
    )
    if body:
        parts.append(
            "<tbody>"
            + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in body)
            + "</tbody>"
        )
    parts.append("</table></div>")
    return "".join(parts)


def _block_table_rows(block: ArtifactBlock) -> list[list[str]] | None:
    data = block.data if isinstance(block.data, dict) else None
    if not data:
        return None
    for key in ("rows", "table_data", "table"):
        rows = data.get(key)
        if isinstance(rows, (list, tuple)) and rows:
            out: list[list[str]] = []
            for row in rows:
                if isinstance(row, (list, tuple)):
                    out.append([str(c or "") for c in row][:12])
                elif isinstance(row, dict):
                    out.append([str(v or "") for v in row.values()][:12])
            if out:
                return out[:100]
    headers = data.get("headers") or data.get("columns")
    if isinstance(headers, (list, tuple)) and headers:
        return [[str(h or "") for h in headers]]
    return None


def _block_image(block: ArtifactBlock) -> tuple[str, str] | None:
    data = block.data if isinstance(block.data, dict) else None
    src = ""
    alt = block.text or ""
    if data:
        for key in ("src", "url", "href", "path"):
            candidate = str(data.get(key) or "").strip()
            if candidate:
                src = candidate
                break
        alt = str(data.get("alt") or data.get("caption") or alt or "")
    if not src:
        # Plain-text image block without a safe src carries no renderable image.
        return None
    if _safe_img(src) is None:
        return None
    return src, alt


_LIST_BLOCK_TYPES = {"bullet", "item", "point", "takeaway"}


def _render_block(block: ArtifactBlock, table_direction: str = "rtl") -> str:
    btype = str(block.kind if hasattr(block, "kind") else block.block_type or "").lower().strip()
    text = block.text or ""
    if btype == "heading":
        level = 2
        data = block.data if isinstance(block.data, dict) else None
        if isinstance(data, dict) and data.get("level") in (1, 2, 3, "1", "2", "3"):
            level = int(data["level"])
        tag = f"h{min(max(level, 1), 3)}"
        return f"<{tag}{_dir_attr(text)}>{_esc(text)}</{tag}>"
    if btype in {"paragraph", "text", "description", "summary", "prose", "note"}:
        if btype == "note":
            return f"<div class=\"sard-callout\" role=\"note\"{_dir_attr(text)}>{_esc(text)}</div>"
        if btype == "summary":
            return f"<p{_dir_attr(text)}><strong>{_esc(text)}</strong></p>"
        return f"<p{_dir_attr(text)}>{_esc(text)}</p>"
    if btype in _LIST_BLOCK_TYPES:
        # Single blocks still render a valid list; contiguous runs are
        # coalesced into one <ul> by _render_section (HTML-Q1).
        return f"<ul class=\"sard-list\"{_dir_attr(text)}><li>{_esc(text)}</li></ul>"
    if btype in {"quote", "callout"}:
        cls = "sard-quote" if btype == "quote" else "sard-callout"
        role = "" if btype == "quote" else ' role="note"'
        tag = "blockquote" if btype == "quote" else "div"
        return f"<{tag} class=\"{cls}\"{role}{_dir_attr(text)}>{_esc(text)}</{tag}>"
    if btype == "code":
        # LTR code: escape, never shape/transform.
        return f'<pre dir="ltr"><code>{_esc(text)}</code></pre>'
    if btype in {"table", "table_row", "row"}:
        rows = _block_table_rows(block)
        if rows:
            return _render_table(rows, table_direction)
        return f"<p{_dir_attr(text)}>{_esc(text)}</p>" if text.strip() else ""
    if btype in {"image", "diagram", "card"}:
        image = _block_image(block)
        if image is None:
            return f"<p{_dir_attr(text)}>{_esc(text)}</p>" if text.strip() else ""
        src, alt = image
        caption = f"<figcaption>{_esc(alt)}</figcaption>" if alt else ""
        return (
            f"<figure><img class=\"sard-img\" src=\"{_esc(src)}\" "
            f"alt=\"{_esc(alt)}\" loading=\"lazy\">{caption}</figure>"
        )
    if btype in {"slide", "event", "calendar_event"}:
        data = block.data if isinstance(block.data, dict) else None
        extra = ""
        if isinstance(data, dict):
            details = {k: v for k, v in data.items() if k not in {"src", "url"} and v}
            if details:
                extra = f"<p{_dir_attr(str(details))}>{_esc(str(details))}</p>"
        return f"<div class=\"sard-card\"{_dir_attr(text)}><strong>{_esc(text)}</strong>{extra}</div>"
    if btype == "attachment":
        # Renderer-payload attachments are preview metadata, not visible content.
        return ""
    # Unknown kinds degrade to an escaped paragraph, never raw HTML.
    return f"<p{_dir_attr(text)}>{_esc(text)}</p>" if text.strip() else ""


def _render_section(section: ArtifactSection, index: int, table_direction: str = "rtl") -> str:
    anchor = f"sard-sec-{index}"
    # HTML-B1: honor the fail-closed evidence rule — only renderable blocks
    # reach downloaded HTML, matching the UI preview.
    blocks = section.renderable_blocks()
    # HTML-Q1: coalesce contiguous list blocks into a single <ul>.
    chunks: list[str] = []
    pending_items: list[str] = []

    def _flush_list() -> None:
        if not pending_items:
            return
        chunks.append(f'<ul class="sard-list">{"".join(pending_items)}</ul>')
        pending_items.clear()

    for block in blocks:
        btype = str(block.block_type or "").lower().strip()
        if btype in _LIST_BLOCK_TYPES:
            pending_items.append(f"<li{_dir_attr(block.text or '')}>{_esc(block.text or '')}</li>")
            continue
        _flush_list()
        chunks.append(_render_block(block, table_direction))
    _flush_list()
    rendered_blocks = "".join(chunks)
    if not section.title.strip() and not rendered_blocks.strip():
        return ""
    badge = ""
    data_badge = ""
    if section.title.strip():
        parts = [
            f'<section class="sard-section" id="{anchor}" aria-labelledby="{anchor}-t">',
            f'<h2 id="{anchor}-t"{_dir_attr(section.title)}>{_esc(section.title)}{badge}</h2>',
            rendered_blocks,
            "</section>",
        ]
        return "".join(parts)
    return f'<section class="sard-section" id="{anchor}">{rendered_blocks}{data_badge}</section>'


def render_html_document(doc: ArtifactDocument) -> str:
    """Render a canonical ArtifactDocument to a standalone sanitized HTML page."""

    meta = doc.metadata
    title = (meta.title or "").strip()
    topic = (meta.topic or "").strip()
    if not title:
        raise HtmlRenderError("Artifact title is required; refusing to render filler.")
    if not topic and not doc.sections:
        raise HtmlRenderError("Artifact topic/content is missing; refusing to render filler.")
    direction = (doc.theme.direction or "rtl").lower()
    doc_dir = direction if direction in {"rtl", "ltr"} else "rtl"
    lang = "ar" if doc_dir == "rtl" else "en"
    if "ar" in (doc.theme.locale or "").lower():
        lang = "ar"

    parts: list[str] = [
        "<!DOCTYPE html>",
        f'<html lang="{lang}" dir="{doc_dir}">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_esc(title)}</title>",
        '<meta http-equiv="Content-Security-Policy" '
        'content="default-src \'none\'; img-src https: data:; style-src \'unsafe-inline\'; '
        'font-src https: data:; base-uri \'none\'; form-action \'none\'">',
        "<style>",
        _STYLES,
        "</style>",
        "</head>",
        "<body>",
        '<main class="sard-sheet" role="main">',
        '<header class="sard-cover">',
        f'<div class="sard-kicker"{_dir_attr(meta.region)}>المملكة العربية السعودية • {_esc(meta.region)}</div>',
        f'<h1 class="sard-title"{_dir_attr(title)}>{_esc(title)}</h1>',
    ]
    if topic and topic != title:
        parts.append(f'<p class="sard-subtitle"{_dir_attr(topic)}>{_esc(topic)}</p>')
    meta_bits = []
    if meta.verification_status:
        meta_bits.append(f"التحقق: {_esc(meta.verification_status)}")
    if meta.run_id:
        meta_bits.append(f"التشغيل: {_esc(meta.run_id)}")
    if meta_bits:
        parts.append(f'<p class="sard-meta">{" | ".join(meta_bits)}</p>')
    parts.append("</header>")

    if meta.warnings:
        items = "".join(f"<li{_dir_attr(w)}>{_esc(w)}</li>" for w in meta.warnings if str(w).strip())
        if items:
            parts.append(
                f'<div class="sard-notice" role="status"><strong>تنبيهات:</strong><ul class="sard-list">{items}</ul></div>'
            )
    for index, section in enumerate(doc.sections, 1):
        parts.append(_render_section(section, index, doc_dir))

    if doc.sources:
        items = []
        for source in doc.sources:
            safe = _safe_url(source.url or "")
            label = _esc(source.title or source.citation_id)
            # HTML-Q4: surface citation metadata (page/section/date) when present.
            details: list[str] = []
            if getattr(source, "page", None):
                details.append(f"صفحة {_esc(source.page)}")
            if getattr(source, "section", None):
                details.append(_esc(source.section))
            pub_date = getattr(source, "publication_date", None)
            if pub_date:
                details.append(_esc(str(pub_date)))
            suffix = f" ({'، '.join(details)})" if details else ""
            if safe:
                items.append(
                    f"<li>[{_esc(source.citation_id)}] {label}{suffix} — "
                    f"<a href=\"{_esc(safe)}\" rel=\"noopener noreferrer\">{_esc(safe)}</a></li>"
                )
            else:
                items.append(f"<li>[{_esc(source.citation_id)}] {label}{suffix}</li>")
        parts.append(
            '<section class="sard-sources" aria-label="sources">'
            f'<h2{_dir_attr("المراجع")}>المراجع والتوثيق [{len(items)}]</h2>'
            f"<ol>{''.join(items)}</ol></section>"
        )

    parts.append(
        '<footer class="sard-footer">سرد — المستشار الثقافي للمملكة العربية السعودية</footer>'
    )
    parts.append("</main></body></html>")
    return "".join(parts)


def render_html_bytes(doc: ArtifactDocument) -> bytes:
    return render_html_document(doc).encode("utf-8")


def _legacy_section_blocks(section: Mapping[str, Any], start: int) -> tuple[list[ArtifactBlock], int]:
    """Adapt one legacy section dict to canonical blocks (shared with tests)."""

    from sard.outputs.document import ArtifactBlock as _Block

    blocks: list[ArtifactBlock] = []
    counter = start
    content = str(section.get("content") or "").strip()
    if content:
        for para in [p for p in content.split("\n\n") if p.strip()]:
            counter += 1
            blocks.append(
                _Block(
                    block_id=f"legacy-p{counter}",
                    block_type="paragraph",
                    text=para.strip()[:2000],
                    verification_status="uncertain",
                )
            )
    bullets = section.get("bullets") or []
    if isinstance(bullets, list):
        for bullet in bullets:
            if str(bullet or "").strip():
                counter += 1
                blocks.append(
                    _Block(
                        block_id=f"legacy-b{counter}",
                        block_type="bullet",
                        text=str(bullet).strip()[:2000],
                        verification_status="uncertain",
                    )
                )
    table_data = section.get("table_data")
    if isinstance(table_data, list) and table_data:
        counter += 1
        blocks.append(
            _Block(
                block_id=f"legacy-t{counter}",
                block_type="table",
                text=str(section.get("title") or ""),
                data={"rows": table_data},
                verification_status="uncertain",
            )
        )
    badge = str(section.get("badge") or "").strip()
    if badge:
        counter += 1
        blocks.append(
            _Block(
                block_id=f"legacy-badge{counter}",
                block_type="note",
                text=f"شارة: {badge}",
                verification_status="uncertain",
            )
        )
    return blocks, counter


def render_html_from_request(
    *,
    title: str,
    topic: str,
    content_paragraphs: Sequence[str] | None = None,
    sections: Sequence[Mapping[str, Any]] | None = None,
    key_takeaways: Sequence[str] | None = None,
    sources: Sequence[Any] | None = None,
    region: str = "المملكة العربية السعودية",
    summary: str = "",
    subtitle: str = "",
) -> str:
    """Convenience adapter: orchestrator-shaped kwargs -> sanitized HTML string."""

    from sard.outputs.document import (
        ArtifactBlock as _Block,
        ArtifactDocument as _Doc,
        ArtifactMetadata as _Meta,
        ArtifactSection as _Section,
    )

    clean_title = str(title or "").strip()
    clean_topic = str(topic or "").strip()
    if not clean_title:
        raise HtmlRenderError("Artifact title is required.")
    if not clean_topic:
        raise HtmlRenderError("Artifact topic is required.")
    paragraphs = [str(p or "").strip() for p in (content_paragraphs or []) if str(p or "").strip()]
    takeaways = [str(t or "").strip() for t in (key_takeaways or []) if str(t or "").strip()]
    if not paragraphs and not list(sections or []) and not takeaways and not str(summary or "").strip():
        raise HtmlRenderError("Artifact content is missing; refusing to render filler.")

    blocks: list[ArtifactBlock] = []
    counter = 0
    if str(summary or "").strip():
        counter += 1
        blocks.append(
            _Block(
                block_id="intro-summary",
                block_type="summary",
                text=str(summary).strip()[:8000],
                verification_status="uncertain",
            )
        )
    for para in paragraphs:
        counter += 1
        blocks.append(
            _Block(
                block_id=f"intro-p{counter}",
                block_type="paragraph",
                text=para[:2000],
                verification_status="uncertain",
            )
        )
    doc_sections: list[ArtifactSection] = []
    if blocks:
        doc_sections.append(_Section(section_id="intro", title="", blocks=tuple(blocks)))
    for idx, section in enumerate(sections or [], start=1):
        if not isinstance(section, Mapping):
            continue
        sec_blocks, counter = _legacy_section_blocks(section, counter)
        sec_title = str(section.get("title") or "").strip()
        if sec_blocks or sec_title:
            doc_sections.append(
                _Section(
                    section_id=f"section-{idx}",
                    title=sec_title,
                    blocks=tuple(sec_blocks),
                )
            )
    if takeaways:
        doc_sections.append(
            _Section(
                section_id="takeaways",
                title="الخلاصات المعرفية",
                blocks=tuple(
                    _Block(
                        block_id=f"takeaway-{i}",
                        block_type="bullet",
                        text=t,
                        verification_status="uncertain",
                    )
                    for i, t in enumerate(takeaways, 1)
                ),
            )
        )
    src_objs = []
    for entry in sources or ():
        if isinstance(entry, Mapping):
            stitle = str(entry.get("title") or entry.get("source_name") or entry.get("id") or "").strip()
            url = str(entry.get("url") or entry.get("source_url") or "").strip()
            cid = str(entry.get("citation_id") or entry.get("id") or "").strip()
            if stitle:
                src_objs.append({"citation_id": cid, "title": stitle, "url": url})
    # Sources are attached without CIT-* enforcement here; citation-strict
    # paths go through VerifiedRenderInput upstream.
    from sard.outputs.schemas import CitationSource as _CS

    valid_sources = []
    for entry in src_objs:
        try:
            if entry["citation_id"]:
                valid_sources.append(
                    _CS(citation_id=entry["citation_id"], title=entry["title"], url=entry["url"] or "https://example.invalid/")
                )
        except ValueError:
            continue
    doc = _Doc(
        metadata=_Meta(title=clean_title, topic=clean_topic or subtitle or clean_title, region=region or "المملكة العربية السعودية"),
        sections=tuple(doc_sections),
        sources=tuple(valid_sources),
    )
    return render_html_document(doc)


def write_html(doc: ArtifactDocument, output_path: str | Path) -> Path:
    """Write HTML to disk; delete any partial file on failure."""

    destination = Path(output_path)
    if destination.suffix.lower() not in {".html", ".htm"}:
        raise HtmlRenderError("HTML output path must end in .html")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.write_text(render_html_document(doc), encoding="utf-8")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination
