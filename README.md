# Sard (سرد) — MVP

Sard is an Arabic-first Saudi cultural-travel assistant. Step 3 adds a
provider-independent RAG foundation while preserving the provider-neutral Step
2 chat service. It does not include the later LangGraph agent, RTL PDF
generation, or other post-MVP stages.

## Requirements

- Windows, Linux, or macOS with Python 3.11 or newer.
- `uv` for reproducible environment management.
- An NVIDIA API Catalog key or reachable self-hosted NVIDIA NIM deployments
  for live generation, query rewriting, embeddings, and reranking.
- No network access is required for the normal automated test suite.

Install the project and optional NVIDIA/dev dependencies:

```bash
uv sync --extra nvidia --extra dev
Copy-Item .env.example .env       # PowerShell; edit the copy afterwards
```

Never commit `.env`, API keys, authorization headers, generated Zvec files, or
temporary manifests.

## Why Zvec

Zvec is a local, embedded vector database. It keeps the MVP self-contained,
avoids operating a separate database service, supports dense vector search,
full-text search, metadata filters, and persistence, and works offline once a
collection has been built. The application exposes Zvec only through
`ZvecRepository`; the UI and public RAG service never depend on Zvec objects.

## NVIDIA NIM configuration

NVIDIA is the only AI provider used by the RAG subsystem. The configured routes
are:

| Use case | Route |
|---|---|
| Generation | `nemotron-3-super-120b-a12b` → `qwen3-next-80b-a3b-instruct` → `llama-3.3-70b-instruct` → extractive fallback |
| Query rewrite | `nemotron-3-nano-30b-a3b` → `nvidia-nemotron-nano-9b-v2` → `llama-3.1-8b-instruct` → deterministic normalization |
| Embeddings | `nemotron-3-embed-1b`; `nv-embed-v1` requires a separate collection |
| Reranking | `rerank-qa-mistral-4b` → RRF → dense → full text |
| Vision | `llama-3.1-nemotron-nano-vl-8b-v1` → `nemotron-nano-12b-v2-vl` → `muse-glimmer-30b` → quarantine |
| Translation | `riva-translate-4b-instruct-v2` → `riva-translate-4b-instruct-v1_1` → preserve original and flag |
| Safety | `nemotron-3.5-content-safety` → `llama-3.1-nemotron-safety-guard-8b-v3` → `llama-guard-4-12b` → conservative fallback |

These are configured model IDs, not claims that every ID is available to every
account or deployment. Check the actual catalog/deployment IDs with:

```bash
uv run python -m sard.cli.rag models
```

The command reports configured IDs separately from discovered IDs. It does not
silently add prefixes, substitute models, or print secrets. If discovery is
unavailable, use the exact ID documented by the NVIDIA API Catalog or your NIM
deployment.

Hosted NVIDIA uses `NVIDIA_API_KEY`. Self-hosted deployments can set separate
URLs:

```dotenv
NVIDIA_CHAT_BASE_URL=http://chat-nim:8000/v1
NVIDIA_EMBEDDING_BASE_URL=http://embed-nim:8000/v1
NVIDIA_RERANK_BASE_URL=http://rerank-nim:8000/v1
```

The route values in `.env.example` are the requested logical defaults. Hosted
Catalog or self-hosted NIM deployments may expose different concrete IDs (for
example, a Catalog-style `nvidia/...` name). Run `models` first, then override
the corresponding `NVIDIA_*_MODEL_*` variables in `.env`; the application does
not silently add prefixes or substitute an embedding model.

The RAG factories construct `ChatNVIDIA`, `NVIDIAEmbeddings`, and
`NVIDIARerank`. Timeout and retry settings are bounded and observable through
sanitized fallback events.

Inspect all configuration without exposing the key:

```bash
uv run python -m sard.cli.rag doctor
```

## Corpus preparation

Every supported source needs a sidecar named `<source>.<ext>.meta.json` with at
least:

```json
{
  "source_name": "Institution or publication",
  "source_url": "https://example.org/verifiable-source",
  "title": "Source title",
  "topic": "Corpus topic",
  "publication_date": "2024-01-01",
  "language": "ar"
}
```

Supported formats are PDF, HTML, Markdown, and plain text. Source text is
cleaned conservatively for citation, Arabic query normalization is kept
separate, repeated PDF headers/footers are removed only when strongly
repeated, and chunks retain source title, URL, topic, publication date, page,
section, stable IDs, and sidecar metadata.

Scanned PDF pages are detected from insufficient extracted text. Without a
verified vision extractor, they are quarantined and reported; no page content
is invented.

The current pilot corpus is documented in
[`data/corpus/MANIFEST.md`](data/corpus/MANIFEST.md):

- Hot springs in Al-Ahsa/Eastern Province: two verified documents.
- Traditional shrimp drying: no verified Saudi Eastern Province source, so the
  absence remains explicit and all related evidence gaps must remain failures.

## Collection versioning and embedding migration

Each collection path is isolated by:

- Zvec schema version;
- embedding model ID;
- embedding dimension;
- Arabic normalization version; and
- chunking version.

The collection metadata records the same axes. A collection refuses vectors or
queries from another embedding model/dimension and rejects incompatible live
schemas. `nv-embed-v1` is never silently mixed into a `nemotron-3-embed-1b`
collection.

Create or verify a collection, discovering its dimension through an actual
embedding probe:

```bash
uv run python -m sard.cli.rag create-collection
```

When changing the embedding model, normalization, chunking, or schema, build a
new collection and re-ingest. Do not rename an old directory or overwrite a
collection with vectors from another model:

```powershell
$env:NVIDIA_EMBEDDING_MODEL_PRIMARY="nv-embed-v1"
$env:ZVEC_COLLECTION_PATH="data/zvec/sard-nv-embed-v1"
uv run python -m sard.cli.rag create-collection
uv run python -m sard.cli.rag ingest data/corpus
```

Restore the normal settings and rebuild again when migrating back. Generated
collections under `data/zvec/` are ignored by version control.

## Ingestion

```bash
uv run python -m sard.cli.rag ingest data/corpus
uv run python -m sard.cli.rag resume-ingest data/corpus
uv run python -m sard.cli.rag info
uv run python -m sard.cli.rag list-sources
```

Ingestion is resumable and idempotent. A per-collection manifest stores source
hashes, metadata hashes, chunk IDs, and version information. A failed embedding
run preserves the previous indexed document; a successful changed-source run
upserts new chunks and then removes only the stale old chunk IDs.

## Retrieval, reranking, and cited answers

```bash
uv run python -m sard.cli.rag dense-search "أين تقع الينابيع الحارة؟" --k 6
uv run python -m sard.cli.rag fts-search "الينابيع الحارة" --k 6
uv run python -m sard.cli.rag hybrid-search "أين تقع الينابيع الحارة؟" --k 6
uv run python -m sard.cli.rag rerank-preview "أين تقع الينابيع الحارة؟" --k 6
uv run python -m sard.cli.rag ask "أين تقع الينابيع الحارة في المنطقة الشرقية؟"
```

Optional filters are available on search and ask commands:
`--topic`, `--source-name`, `--language`, and `--publication-date`.

The public service remains provider-independent:

```python
from sard.rag.service import RAGService

service = RAGService.open_readonly()
try:
    result = service.answer(
        question="أين تقع الينابيع الحارة؟",
        filters={"topic": "الينابيع الحارة في المنطقة الشرقية"},
    )
finally:
    service.close()
```

The result exposes the original question, rewritten queries, dense/FTS/fused
chunks and scores, selected context, reranking scores, citations with titles
and URLs, active routes, fallback events, retrieval mode, timings, and
warnings. If embeddings fail, FTS-only emergency retrieval can still answer
from indexed text. If generation fails, the answer is an Arabic extractive
summary of retrieved evidence. Unknown citation IDs are removed, and uncited
model output is rejected in favor of a grounded fallback.

## Golden evaluation

Evaluation reads `evals/golden.json` and grades retrieval separately from
answer fluency:

```bash
uv run python -m sard.cli.rag evaluate evals/golden.json --k 6
```

The JSON report includes Recall@K for dense, FTS, fused, and reranked results;
MRR; binary term-proxy nDCG; per-question evidence; source titles and URLs;
chunk/citation IDs; routes; latency; and questions needing corpus improvement.

The current golden file explicitly declares that its relevance labels are only
term proxies and that it is not eligible for the 8/10 gate. The shrimp topic
also has no verified corpus source. Therefore a successful-looking term or
answer cannot be reported as a genuine gate pass. Add verified documents and
explicit relevance labels before enabling a future gate.

## Diagnostics and tests

```bash
uv run python -m sard.cli.rag --help
uv run python -m sard.cli.rag doctor
uv run pytest -q
uv run python -m compileall -q sard
```

The optional live smoke test is skipped unless explicitly enabled and valid
NVIDIA credentials/deployments are configured. Run it deliberately with:

```powershell
$env:RAG_LIVE_SMOKE="true"
uv run pytest -q -m live
```

The offline suite mocks NVIDIA interfaces and must not require network access or
live credentials.

## Step 4 Arabic RTL PDF rendering spike

The PDF layer is intentionally isolated in `sard/outputs/`: it imports no UI,
LangChain, NVIDIA, or Zvec code and does not make network calls. It accepts the
typed `Itinerary` contract and returns a `RenderedArtifact` containing the safe
path, filename, MIME type, byte size, and warnings. Calendar generation and
LangGraph orchestration are outside this spike.

### Font setup and license

The repository deliberately bundles `NotoNaskhArabic-Regular.ttf` (178,388
bytes) and the `NotoSans-Regular.ttf` Latin companion (569,208 bytes) so
Arabic plus URLs/English render deterministically and offline. Both are pinned to
notofonts/noto-fonts commit
`ffebf8c1ee449e544955a7e813c54f9b73848eac`, SHA-256
`2f4b88e6ee50fa82c617e2d1d4ba18281cb1c6cd71c3af3ec64970c23995db4b`
and `b85c38ecea8a7cfb39c24e395a4007474fa5a4fc864f6ee33309eb4948d232d5`,
and distributed under the SIL Open Font License 1.1 in
`sard/outputs/assets/OFL.txt`. The upstream source is:

https://github.com/notofonts/noto-fonts/tree/ffebf8c1ee449e544955a7e813c54f9b73848eac/hinted/ttf/NotoNaskhArabic

Verify the bundled file (or restore the exact pinned download if it was
removed) with:

```bash
uv run python -m sard.outputs.sample --download-font --output font-check.pdf
```

Set `SARD_ARABIC_FONT_PATH` to switch deliberately to another Arabic-capable
TTF. A missing configured file raises `ArabicFontError`; there is no silent
Helvetica or system-font fallback. Any alternative font's embedding and
distribution license must be reviewed by the deployer.

### Generate and inspect the fixture

The sample is prominently labeled fixture-only and uses invented
`example.org`/`example.com` sources; it is not travel advice:

```bash
uv sync --extra dev
uv run python -m sard.outputs.sample --output step4-arabic-rtl-sample.pdf
```

The default output is
`output/pdf/step4-arabic-rtl-sample.pdf`. Set `SARD_PDF_OUTPUT_ROOT` to choose
another root. Relative output paths are resolved beneath that root; absolute
paths outside it, non-PDF suffixes, traversal, and overwriting an existing file
are rejected. Generated PDFs and page images under `output/pdf/` and
`tmp/pdfs/` are ignored by Git.

Render every page for visual review with PyMuPDF:

```bash
uv run python -c "import fitz,pathlib; d=fitz.open('output/pdf/step4-arabic-rtl-sample.pdf'); o=pathlib.Path('tmp/pdfs'); o.mkdir(parents=True,exist_ok=True); [p.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False).save(o/f'page-{i+1}.png') for i,p in enumerate(d)]"
```

Inspection should confirm joined Arabic glyphs, right alignment, readable
mixed Arabic/English, unchanged URLs and citation IDs, wrapped long lines,
portrait A4 margins, non-overlapping footers, and sequential page numbers.
The checked Step 4 artifact was rendered as three 595 x 842 point pages and
rasterized to three 1191 x 1684 PNGs at 2x. Visual review found joined Arabic,
complete Latin runs/URLs/IDs, clean wrapping and margins, and no clipping,
overlap, tofu glyphs, or black squares. PyMuPDF extraction also recovered both
full citation IDs and both fixture URLs; all 285 text spans remained within the
physical page bounds.

### Citation flow and fallback decision

Each `TextBlock` carries stable citation IDs and may also display them inline.
Before any file is created, `Itinerary.validate_citations()` rejects duplicate
source IDs and every unknown declared or inline `CIT-*` ID. The renderer uses
only the supplied title, URL, page, section, and publication date; absent
optional metadata stays absent. IDs appear inline, in per-page source notes,
and in the final source list. ReportLab markup metacharacters are escaped when
markup is needed; itinerary content is currently drawn as plain canvas text,
so it is never interpreted as markup.

ReportLab meets the spike criteria with explicit line-level Arabic reshaping
and bidi processing. The proposed HTML-to-PDF fallback is therefore not
implemented and remains disabled; adding a browser rendering stack would add
deployment and reproducibility costs without solving a demonstrated gap.

## Streamlit application

```bash
uv run streamlit run sard/ui/app.py
```

The UI preserves the existing Step 2 chat experience. Its RAG toggle calls
only `RAGService`; it does not call Zvec or NVIDIA integrations directly. If a
collection is unavailable, the UI reports the condition and retains the
existing direct-chat fallback.

## Step 5 LangGraph orchestration

The typed orchestration layer runs `understand -> plan -> retrieve -> compose
-> verify -> render`. Failed verification returns structured feedback to
`compose`; the re-composition count is capped and exhaustion returns only an
honest verified subset. Agent nodes use the centralized LangChain model service
and the public `RAGService` boundary, never NVIDIA endpoints or Zvec directly.

Run the deterministic, network-free hero-query trace:

```bash
uv run sard-agent --demo
```

Run against configured NVIDIA NIM routes and an existing Step 3 collection:

```bash
uv run sard-agent "أنشئ برنامجًا سياحيًا تراثيًا لمدة يومين في المنطقة الشرقية"
```

The trace contains only safe node status, retrieval mode/source count, resolved
model routes, fallback count, verification coverage/retries, and latency. It
never prints prompts, reasoning, keys, headers, or provider payloads.

## Known limitations

- The pilot corpus is intentionally small and does not cover shrimp drying.
- The golden set lacks verified chunk/document relevance labels, so its term
  metrics are diagnostic rather than a defensible production benchmark.
- Live NVIDIA model availability depends on the account, catalog, or
  self-hosted deployment and must be checked rather than assumed.
- Scanned pages require an explicitly configured, verifiable vision extraction
  path; otherwise they remain quarantined.
