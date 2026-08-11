# RAG corpus manifest (docs)

The ingestion pipeline requires every corpus file to be accompanied by a
metadata sidecar so that provenance is explicit and auditable rather than
guessed from file content. This page documents the sidecar contract; the
current corpus state (verified documents and honest gaps) is tracked in
`data/corpus/MANIFEST.md`, which is the authoritative corpus ledger.

## Sidecar contract

For each source file `name.ext` (`.pdf`, `.html`/`.htm`, `.md`/`.markdown`,
or `.txt`), place a sibling JSON file named `name.ext.meta.json` with at
least:

```json
{
  "source_name": "The publishing body, e.g. صحيفة الاقتصادية",
  "source_url": "https://exact-url-that-was-verified",
  "title": "Article/document title as published",
  "topic": "The corpus topic slug or Arabic topic label",
  "publication_date": "YYYY-MM-DD, when known",
  "language": "ar"
}
```

- `source_name`, `source_url`, `title`, and `topic` are required; ingestion
  fails that file (and records it in the report's `errors`) when any is
  missing or empty.
- Extra keys are preserved in `DocumentMetadata.extra`.
- `source_url` + `title` deterministically derive the stable `document_id`
  (see `sard/rag/chunking.py`); do not invent or reuse URLs.

## Rules (no fabrication)

1. Only include documents that were actually fetched and whose text is
   verifiable at the recorded `source_url`.
2. Never create placeholder/synthetic content to make a topic "covered".
   If a topic has no verified source, it stays uncovered and its golden-set
   questions must be reported as failing retrieval — see
   `data/corpus/MANIFEST.md` for the shrimp-drying pilot topic.
3. When a document is updated, replace the file and its sidecar; ingestion
   detects the content-hash change, removes the old chunk set, and re-embeds
   the new one.

## Scanned PDFs

Pages with insufficient extractable text are flagged as likely-scanned and
quarantined for manual review (never fabricated into text). See the
ingestion report's `scanned_pages_quarantined` list. A vision-language model
pass is only attempted when one is configured and reachable; otherwise the
page remains quarantined.
