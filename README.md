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

## Streamlit application

```bash
uv run streamlit run sard/ui/app.py
```

The UI preserves the existing Step 2 chat experience. Its RAG toggle calls
only `RAGService`; it does not call Zvec or NVIDIA integrations directly. If a
collection is unavailable, the UI reports the condition and retains the
existing direct-chat fallback.

## Known limitations

- The pilot corpus is intentionally small and does not cover shrimp drying.
- The golden set lacks verified chunk/document relevance labels, so its term
  metrics are diagnostic rather than a defensible production benchmark.
- Live NVIDIA model availability depends on the account, catalog, or
  self-hosted deployment and must be checked rather than assumed.
- Scanned pages require an explicitly configured, verifiable vision extraction
  path; otherwise they remain quarantined.
