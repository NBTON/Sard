# Renderer QA — HTML/PDF/DOCX/PPTX + RTL (2026-09-15)

Branch: `release/sard-artifact-overhaul` · workstream: renderers.
Consumes canonical `ArtifactDocument` from `sard/outputs/document.py`
(artifact agent owns that shape; renderers adapt, never redefine it).

## 1. HTML → PDF deployment verdict (verified read-only)

- Installed: `reportlab 5.0.0`, `python-docx 1.2.0`, `python-pptx 1.0.2`,
  `arabic-reshaper`, `python-bidi`, `pypdf`, `pymupdf`.
- NOT installed: `playwright`/`chromium`, `weasyprint`, `pdfkit`,
  `xhtml2pdf` (all `find_spec` → False).
- Decision: server-side HTML→PDF **rejected** — a headless-Chromium stack
  would add huge native deps incompatible with the Vercel serverless target.
  `sard/outputs/html.py` is the canonical browser path (users can
  print-to-PDF client-side); `render_document_pdf` (enhanced ReportLab) is
  the server-side PDF renderer; itinerary `render_pdf` kept as fallback.
  Re-evaluate only if a deployment-compatible HTML→PDF option is verified.

## 2. Centralized RTL policy (in `arabic.py` module docstring)

| Renderer | Rule |
|---|---|
| HTML/browser | Logical Unicode only, `dir=rtl`, logical CSS. NEVER reshaper/bidi (`test_html_never_reshapes`). |
| ReportLab PDF | Logical text through `wrap_logical_lines`; `shape_rtl()` ONLY in `Flowable.draw`; `visual_runs` font-split kept. |
| DOCX/PPTX | Native Unicode + `w:bidi`/`w:rtl`, `a:pPr rtl=1`. No pre-reshape. |

Fonts: `ensure_fonts_registered()` is now strict-by-default — raises
`ArabicFontError` instead of silently returning Helvetica (tofu). Callers
translate to `failed` artifacts; partial files deleted on failure
(`pdf.py` pattern extended to report/DOCX/PPTX/HTML writers).

## 3. What changed

- `sard/outputs/html.py` (NEW): `ArtifactDocument` → sanitized standalone
  HTML. All model text escaped; `https?`/`mailto:` links only with
  `rel=noopener`; images `https?`/`data:image:` only; CSP meta, no scripts.
  Shares Sard tokens with the PDF palette.
- `sard/outputs/pdf.py`: NEW `parse_markdown_blocks()` /
  `markdown_to_flowables()` (hierarchy preserved — headings/bullets/quotes/
  tables become blocks instead of being stripped; `clean_pdf_text` kept for
  inline markers per `test_production_contract.py`); NEW
  `render_document_pdf()` — cover, subtitle, TOC section listing, headings,
  RTL tables, callout/takeaway cards, images (best-effort local), page
  numbers + headers/footers, bibliography, `CondPageBreak` flow.
  Itinerary `render_pdf` untouched (fallback).
- `sard/outputs/pdf_report.py`: `ReportSection.table_data` now renders
  (was dead schema) as RTL platypus table w/ header row + repeat header;
  strict fonts; partial-file delete on `output_path` failure.
- `sard/outputs/office_docx.py`: hand-rolled OOXML ZIP replaced with
  python-docx. Heading/paragraph styles, `List Bullet` numbering, RTL tables
  (`tblBidiVisual`), images, sources, margins, `w:sectPr/w:bidi`,
  header/footer. Legacy `render_cultural_docx_report` signature kept;
  `build_from_document(ArtifactDocument)` added; missing content raises
  instead of rendering filler.
- `sard/outputs/office.py`: REMOVED `cards[:3]`/`timeline[:4]` caps —
  `_expand_pagination` continues onto “(تابع)” slides (5 cards → 2 slides,
  6 timeline → 2 slides, verified). REMOVED hallucinated overview bullets,
  default summary bullets, default quote — `create_cultural_briefing_deck`
  raises `DeckBuildError` on empty content. NEW slide types
  `table`/`image`/`sources` + `build_from_document(ArtifactDocument)`.
  Body never shrinks below 13pt; overflow paginates.
- `tests/outputs/test_html_pdf_docx_pptx_rtl.py` (NEW, 16 tests): torture
  corpus (pure AR/EN, mixed Diriyah, ١٢٣/123, Hijri+Gregorian, parens/[CIT],
  URLs, bullets/tables, Saudi places, long URL + emoji); round-trip asserts
  (pypdf pages, python-docx/pptx reopen); RTL props (`w:bidi`/`w:rtl`,
  `rtl=1`, `dir=rtl`); caps-removed; filler-absent; strict fonts.

## 4. Test results

- `uv run pytest tests/outputs -q` → **89 passed** (73 pre-existing + 16 new).
- `tests/test_production_contract.py` + `tests/test_artifact_pipeline.py` → 14 passed.
- `ruff check` on all touched files → clean.

## 5. Visual checklist (human pass on generated samples)

- [ ] HTML sample: open in browser — AR paragraphs right-aligned, table
      columns right-first, cards grid responsive at 360px width, no tofu.
- [ ] PDF sample: cover → TOC → sections → tables → takeaways → bibliography;
      page numbers Arabic (“الصفحة N”), header only p2+, no orphan headers.
- [ ] DOCX sample: open in Word — RTL paragraphs, bullets, shaded table
      header, header/footer present, no compatibility warnings.
- [ ] PPTX sample: 16:9, title cover readable, comparison/timeline
      continuations titled “(تابع)”, min body 13pt, RTL `rtl=1` ordering.
- [ ] Torture page in all four: mixed “الدرعية Diriyah”, “١٢٣/123”, Hijri +
      Gregorian dates, `(Salwa Palace) [CIT-…]`, inline URL, emoji — shaping
      correct, URL LTR-intact, no overflow off-page.
- [ ] Failure paths: missing font → loud `ArabicFontError` → `failed`
      artifact (never tofu); empty deck/doc → `DeckBuildError`/`DocxRenderError`
      with no partial file left behind.

## 6. Integration notes for the artifact agent

- Renderers accept `ArtifactDocument` via `build_from_document` (DOCX/PPTX),
  `render_html_document` (HTML), and legacy-kwarg adapters
  (`render_html_from_request`, `render_document_pdf`,
  `render_cultural_docx_report`, `create_cultural_briefing_deck`) so the
  orchestrator needs no changes from this workstream.
- No orchestrator/storage/verify/server/src files touched.
