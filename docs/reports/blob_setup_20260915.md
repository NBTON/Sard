# Vercel Blob setup — workstream F (2026-09-15)

Branch: `release/sard-artifact-overhaul`. No secrets in this file.

## What changed in code (this workstream)

- `VercelBlobArtifactStore.put` now calls the official SDK correctly:
  `client.put(key, bytes, access="private", content_type=...)` (kwargs, not dict).
- `PutBlobResult` is a dataclass (`url` / `download_url` / `pathname`); the
  old `res.get("downloadUrl")` path is gone. SDK urls are retained server-side
  only and never sent to browsers.
- New writes use the idempotent key `runs/{run_id}/{artifact_id}/v{version}/{safe}`;
  reads fall back to legacy `artifacts/{id}/{file}` keys.
- Local FS mirror kept for same-process verification; REST → FS fallback kept.
  `SARD_BLOB_ENDPOINT` / `SARD_BLOB_TOKEN` compat reads kept until migration proven.
- Token aliases read (in order): `SARD_BLOB_TOKEN`, `BLOB_READ_WRITE_TOKEN`,
  `VERCEL_BLOB_READ_WRITE_TOKEN`.
- `GET /api/artifacts/{filename}` keeps the local `FileResponse` fast path and
  adds a `store.get_bytes` streaming branch for blob-configured instances
  (authenticated server-side proxy; write creds never reach the browser).
- `ArtifactStore` ABC extended additively only:
  `put` / `get` aliases plus `delete` / `signed_url` / `get_metadata`
  (FS + REST + SDK implementations).
- Local dev stays FS (`SARD_OUTPUT_ROOT=output/runs`); unit tests never require Blob
  (fake in-memory `BlobClient`, no network).

## SDK version compat (verified 2026-09-15)

- `pyproject.toml`: `vercel>=0.10.0`, `vercel-blob>=0.4.2`.
- Installed and tested against: `vercel 0.10.0`, `vercel-blob 0.4.2`.
- Verified from installed source (`vercel/blob/client.py`, `vercel/_internal/blob/types.py`):
  `BlobClient.put(path, body, *, access, content_type, ...)` returns the
  `PutBlobResult` dataclass; `get`/`head`/`delete`/`list_objects` signatures match
  the store implementation (private access supported).

## Read-only Vercel check (2026-09-15)

Ran (non-destructive, read-only):

```powershell
npx vercel env ls
```

Result:

```text
Vercel CLI 59.17.0 (Node.js 24.19.0)
Error: Your codebase isn’t linked to a project on Vercel. Pass --project <name>, or run `vercel link` to begin.
```

No destructive action was run. Per workstream rules, verification stopped here.

## Required owner actions (Vercel dashboard / CLI)

1. Link the project (owner runs; needs Vercel login):
   ```powershell
   npx vercel link
   npx vercel env ls
   ```
2. Create a **private** Blob store named `sard-blob` in region **IAD1**
   (Vercel Dashboard → Storage → Create → Blob, or `vercel blob` CLI once linked).
3. Add the env var `BLOB_READ_WRITE_TOKEN` (value from the `sard-blob` store):
   - Mark **Sensitive**, enable for **Preview + Production** (not only Development).
   - `VERCEL_BLOB_READ_WRITE_TOKEN` is also accepted by code as an alias, but the
     canonical var to set is `BLOB_READ_WRITE_TOKEN`.
4. Redeploy the preview deployment so the new env var takes effect.
5. Cross-instance check: generate an artifact on preview instance A (note its SHA-256),
   then `GET /api/artifacts/{filename}` routed to a fresh instance B and compare
   bytes + `Content-Type` + `Content-Disposition` filename.
6. Optional compat cleanup (later, after migration proven — NOT this workstream):
   ADR Decision 3 store removal and `SARD_BLOB_*` compat retirement.

## Tests

```powershell
uv run pytest tests/test_gap_g3_blob_durable.py tests/test_runtime_paths.py tests/test_api.py -q
```

16 passed (2026-09-15), including the new cross-process tests:
FS fresh-instance read and fake-Blob fresh-instance + HTTP streaming
(bytes + Content-Type + filename asserted, no network).
