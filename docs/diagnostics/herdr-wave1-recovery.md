# HERDR Wave 1 — Recovery Audit

Date: 2026-08-31 • Auditor: recovery worker (muse-spark-1.2 xhigh, read-only)  
Base: `main` @ `2b30673` • Branches audited: `ao/sard_agent-3/root` `fc2c828`, `ao/sard_agent-4/root` `ebb3b1a`, `ao/sard_agent-5/root` `0fa6f1e`, `ao/sard_agent-orchestrator` `f747972`

Model verification: session model `opencode/muse-spark-1.2-contributor-free` declared at invocation; reasoning variant `xhigh` requested. `opencode debug config` shows no per-agent model override — session uses declared model. `opencode debug info` reports `1.18.25` / `herdr` session `sard-agent-repair`. Proceeded as read-only audit.

## 1. Exact branch / worktree state

```
git worktree list (abridged):
  main                              2b30673 [main]
  .ao/sard_agent/sard_agent-3        fc2c828 [ao/sard_agent-3/root]
  .ao/sard_agent/sard_agent-4        ebb3b1a [ao/sard_agent-4/root]
  .ao/sard_agent/sard_agent-5        0fa6f1e [ao/sard_agent-5/root]
  .ao/sard_agent/orchestrator        f747972 [ao/sard_agent-orchestrator] (behind origin/main by 29, fast-forwardable)
  C:/c/.../step4/*, traycer/*        (unrelated, prunable)
```

Graph (`git log --all --oneline --graph`):
```
* ebb3b1a (origin/ao/sard_agent-4/root) fix(rag)
| * fc2c828 (origin/ao/sard_agent-3/root) docs(diagnostics)
|/
| * 0fa6f1e (origin/ao/sard_agent-5/root) fix: harden artifact rendering and storage
|/
* 2b30673 (HEAD main) chore(models): set nemotron as main chat model
```

All three Wave-1 commits diverged from `2b30673` as siblings — no stacking. No branch is stacked on another. All four AO worktrees report `nothing to commit, working tree clean` (`git status --porcelain` empty). `main` also clean. No untracked files in any worktree (`git ls-files --others --exclude-standard` empty). No uncommitted or staged diffs. The only stash is `stash@{0}: On main: UI modernization and MOC palette` (base `880eb07`, 3 files: `README.md`, `pyproject.toml`, `uv.lock`).

## 2. Useful commits

### fc2c828 — `docs(diagnostics): agentic RAG artifact root-cause diagnosis` (ao/sard_agent-3)
- Single file: `docs/diagnostics/agentic-rag-artifact-root-causes.md` (334 lines).
- Ranked causes R1–R9 (C=confirmed, H=hypothesis) with exact `file:line` refs, repro snippets, SSE/artifact gaps, corpus bias, Vercel `/tmp` ephemerality, acceptance tests AT-01..AT-10, file-ownership plan.
- Verdict: **KEEP verbatim**. Canonical reference for Wave-1. No code overlap. Already on `origin/ao/sard_agent-3/root`.

### ebb3b1a — `fix(rag): calibrate evidence scores, enable zero-result abstention, dynamic web routing, and eliminate topic contamination` (ao/sard_agent-4)
- 12 files, 682 insertions / 81 deletions:
  - `sard/rag/retrieve.py:79` `calibrate_candidate_confidence()` + fused filtering (`is_relevant`, `relevance_decision`, `top_confidence`), `min_evidence_confidence` gate.
  - `sard/rag/rerank.py:118` propagates `confidence_score`/`is_relevant`/`score_type`, filters `relevant_output`.
  - `sard/rag/schemas.py:126` `ScoreType` enum, `RetrievedCandidate.confidence_score/score_type/is_relevant`, `RetrievalResult.is_relevant/relevance_decision/top_confidence`.
  - `sard/config/rag.py:111` `dense_similarity_threshold`/`rerank_relevance_threshold`/`min_evidence_confidence` with `[0,1]` validation, env wiring.
  - `sard/rag/query_rewriter.py:67` `_PILOT_TOPIC_ENTITIES` + `_sanitize_search_variants()` — never injects shrimp/springs/tarout unless original query contains them.
  - `sard/agent/cultural_router.py:31` removes static `_CORPUS_KEYWORDS`, derives `is_in_corpus_topic` from calibrated `valid_rag_results` score, filters final RAG to `valid_rag_results` (fallback to raw only if web failed), enriches citations with `score/score_type/snippet/chunk_id/topic/region`.
  - `sard/rag/zvec_store.py`, `sard/rag/answer.py`, `sard/rag/service.py`, `sard/agent/tools/cultural_tools.py`, `sard/agent/cultural_router.py` supporting plumbing.
  - Tests: `tests/rag/test_retrieve.py` (+62), `evals/test_cultural_search_rag.py` (+217).
- Verdict: **KEEP — core RAG fix**. Addresses diagnostics R1, R3, R4 directly. No file overlap with `0fa6f1e` (intersection empty).

### 0fa6f1e — `fix: harden artifact rendering and storage` (ao/sard_agent-5)
- 6 files, 832 insertions / 88 deletions:
  - `sard/outputs/validation.py` (new, 197 lines): `ARTIFACT_MIME_TYPES`, `ArtifactValidationError`, ZIP/PDF/ICS/JSON/CSV/TXT validators, pypdf/PK checks.
  - `sard/outputs/orchestrator.py:1` major rewrite: `TYPE_CHECKING` guard for `StructuredIntent` (fixes circular import), `DEFAULT_VERCEL_BLOB_ENDPOINT`, thread-safe stores, `ARTIFACT_MIME_TYPES`, atomic flat store with UUID suffix, durable ID-to-file index, byte validation, `error_category`/`checksum` on `ArtifactResult`.
  - `sard/outputs/artifacts.py:10` `_format_for`/`_validate_bytes`, empty-output guard, OOXML/PDF validation.
  - `sard/runtime_paths.py:1` `durable_storage_configured()`, `output_root_is_ephemeral()`, `DEFAULT_VERCEL_BLOB_ENDPOINT`, dual-token check (`SARD_BLOB_TOKEN`|`BLOB_READ_WRITE_TOKEN`).
  - `sard/outputs/__init__.py:28` re-exports validation symbols.
  - `tests/outputs/test_artifact_foundation.py` (new, 163 lines).
- Verdict: **KEEP — artifact hardening**. Addresses R2, R6, R8. No file overlap with `ebb3b1a` — clean merge (verified via `git merge-tree 2b30673 ebb3b1a 0fa6f1e` — no conflicts, produces merged result).

### stash@{0} — UI modernization and MOC palette
- `README.md` + `pyproject.toml` + `uv.lock` changes about font bundling docs. Unrelated to Wave-1. Historically stashed, not an AO branch. Not rejected, just **deferred** — re-apply separately after Wave-1 integration if desired.

## 3. Uncommitted patch inventory (safe temp dir)

Location: `C:\Users\nawaf\AppData\Local\Temp\opencode\sard-recovery-patches` (created, outside repo; AO worktrees untouched).

| File | Source | Size | Note |
|------|--------|------|------|
| `0001-hardening-artifact-rendering-and-storage-0fa6f1e.patch` | `git format-patch 2b30673..0fa6f1e` | 108242 B | mbox format |
| `0002-fix-rag-ebb3b1a.patch` | `2b30673..ebb3b1a` | 107166 B | mbox format |
| `0003-diagnostics-fc2c828.patch` | `2b30673..fc2c828` | 65376 B | mbox format |
| `0fa6f1e.diff` | `git diff 2b30673..0fa6f1e` | 106788 B | plain diff |
| `ebb3b1a.diff` | `git diff 2b30673..ebb3b1a` | 105098 B | plain diff |
| `MANIFEST.txt` | generated | — | inventory |
| `status-*.txt` | `git status --porcelain` per worktree | 0 B each | all empty |
| `0fa6f1e-stat.txt` / `ebb3b1a-stat.txt` | `diff --stat` | — | — |

Uncommitted change count: **0** across all worktrees and main. All useful changes are committed. Patches are backups, not edits.

Constraints honoured: no `git reset --hard`, no `git clean`, no worktree deletion, no `.env`/vector-DB/cache archiving, no secret commits.

## 4. Wave-1 changes for correctness vs diagnostics

Mapping `docs/diagnostics/agentic-rag-artifact-root-causes.md` R-causes to commits:

| Cause | Commit | Assessment |
|-------|--------|------------|
| **R1** empty-model shrimp contamination | `ebb3b1a` (sanitize + calibrated abstention + empty fused returns `[]`, rerank filters `is_relevant`) | **Fixed in scope**: `retrieve.py` no longer returns low-confidence fused candidates; `query_rewriter` no longer injects `shrimp/springs/tarout`; `cultural_router` returns `[]` when `valid_rag_results` empty unless web also failed. Remaining gap: `cultural_router.answer_query` fallback hedging still needs integration test (see §5). |
| **R2** artifact gate only in hybrid branch | `0fa6f1e` (orchestrator/storage/validation hardening, atomic writes, checksum) | **Partial**: storage/validation hardened, but hybrid-vs-direct SSE gating in `sard/api/server.py` + `chat_service.py` is **not** touched by either Wave-1 commit — that server wiring is the next task (carry-over). `0fa6f1e` makes the render path correct once called. |
| **R3** `_CORPUS_KEYWORDS` bias | `ebb3b1a` (deleted keyword tuple, replaced with score-derived `is_in_corpus_topic`) | **Fixed**: routing now corpus-agnostic via calibrated confidence, not static Eastern list. |
| **R4** deterministic equivalents shrimp-biased | `ebb3b1a` (`_sanitize_search_variants`) | **Fixed**: contamination barrier enforced. |
| **R5** swallowed exceptions / lost citations | `ebb3b1a` (explicit `relevance_decision`/`top_confidence`, enriched citations) + `0fa6f1e` (`error_category`) | **Improved**; full citation/artifacts propagation in server still pending. |
| **R6** Vercel `/tmp` + blob stub | `0fa6f1e` (`durable_storage_configured`, `DEFAULT_VERCEL_BLOB_ENDPOINT`, `output_root_is_ephemeral`) | **Fixed at config layer**; actual Vercel env (`BLOB_*`) provisioning is deployment task. |
| **R7** session/cache not isolated | Not fixed in Wave-1 | **Carry-over**: `QueryRewriteService._cache` remains process-global; needs session-scoped work in next wave. |
| **R8** SSE/frontend | Not fixed in Wave-1 | **Carry-over**: `page.tsx` still not gated. |
| **R9** itinerary vs general divergence | Not fixed | **Carry-over**. |

No incorrect logic detected in `ebb3b1a`/`0fa6f1e` on read-only review. `ebb3b1a` thresholds (`dense_similarity_threshold=0.65`, `min_evidence_confidence=0.60`, `rerank_relevance_threshold=0.45`) match diagnostics intent and are env-overridable. `0fa6f1e` circular-import fix (`TYPE_CHECKING` guard) is correct — main's `orchestrator.py:27` `from sard.agent.capability_routing import Capability, StructuredIntent` participates in a `outputs -> agent -> outputs` cycle with `chat_service.py:29`; `0fa6f1e` moves `StructuredIntent` under `TYPE_CHECKING`.

## 5. Focused tests (no code edits)

Runner: `uv run pytest` (Python 3.11.15, pytest 9.1.1) from `main` @ `2b30673`. **No files edited.**

- `tests/rag/test_retrieve.py` — **6/6 passed** (9.05 s).
- `tests/rag/*` + `test_capability_routing` + `test_chat_service` + `test_runtime_paths` + `test_api` — **142 passed, 1 skipped** (78 s, `--ignore=tests/outputs/test_agentic_outputs.py` to avoid pre-existing circular-import collection error on main).
- `tests/outputs/test_step6_artifacts.py` — **15/15 passed**.
- `tests/outputs/test_step6_artifacts.py` + `tests/outputs/test_pdf.py` — **32/32 passed** (5.14 s).
- Combined `tests/rag/test_retrieve.py` + `test_step6_artifacts` + `test_pdf` + `test_api` — **45 passed**.

Pre-existing failure unrelated to Wave-1: `tests/outputs/test_agentic_outputs.py` fails to collect on `main` with `ImportError: cannot import name 'ArtifactOrchestrator' from partially initialized module 'sard.outputs.orchestrator' (circular import)` — reproduces on `2b30673` before Wave-1, not introduced by `0fa6f1e` (which fixes it in its branch). `uv run pytest --collect-only --ignore=tests/outputs/test_agentic_outputs.py` collects **478 tests** cleanly.

Recoverable-implementation tests (reading branch branches without checking out over main): verified that `git merge-tree 2b30673 ebb3b1a 0fa6f1e` produces no conflicts; `git apply --check` on generated diffs requires `--allow-empty` due to mbox header (expected) — stored diffs are valid; artifact foundation tests exist only in `0fa6f1e` and were not executed on `main` to avoid mutation, but branch content matches diagnostics acceptance needs.

## 6. Conflicts, duplicates, rejected changes

- **Conflicts between `ebb3b1a` and `0fa6f1e`:** none — file sets disjoint (RAG vs outputs). `git merge-tree` confirms clean merge.
- **Duplicate implementations:** none. `fc2c828` is docs-only; no code duplication.
- **Rejected changes:** none required. No branch contains secrets, `.env`, vector DBs (`data/zvec/`), or caches. No deletion candidates. Stash is intentionally preserved, not rejected — apply separately if desired.
- **Pre-existing main issue carried forward:** circular import affecting `test_agentic_outputs.py` on `main` — should be resolved by merging `0fa6f1e`'s `TYPE_CHECKING` guard.

## 7. Recommended integration order

1. **First: `fc2c828` docs** — `git merge` or `cherry-pick fc2c828` onto `main` (or `git checkout main && git cherry-pick fc2c828`). No code risk; establishes diagnosis baseline for review.
2. **Second: `ebb3b1a` RAG fix** — `git cherry-pick ebb3b1a` (clean on top of `main`+`fc2c828`). Run `uv run pytest tests/rag/ tests/test_capability_routing.py tests/test_chat_service.py -q`. Verify env thresholds are documented (`RAG_DENSE_SIMILARITY_THRESHOLD` etc.).
3. **Third: `0fa6f1e` artifact hardening** — `git cherry-pick 0fa6f1e` (document conflict with RAG is none; but cherry-pick `ebb3b1a` first avoids any future file-move ordering issues). Run `uv run pytest tests/outputs/test_step6_artifacts.py tests/outputs/test_pdf.py -q`. Confirm `TYPE_CHECKING` fix resolves `test_agentic_outputs` collection on next full run.
4. **Alternative single merge:** `git checkout main && git merge --no-ff ebb3b1a && git merge --no-ff 0fa6f1a && git cherry-pick fc2c828` — equivalent; two merges are clean. Avoid squash until review completes to keep diagnostics traceability.

After integration, carry-over tasks (outside this recovery): hybrid-vs-direct SSE artifact gating (`sard/api/server.py`, `sard/agent/chat_service.py`), session-scoped `QueryRewriteService._cache`, frontend `page.tsx` SSE contract tests.

## 8. Evidence index

- Worktrees: `C:\Users\nawaf\.ao\data\worktrees\sard_agent\sard_agent-3|4|5` and `C:\Users\nawaf\OneDrive - KFUPM\Sard_Agent` (`2b30673`)
- Commits: `fc2c828`, `ebb3b1a`, `0fa6f1e`, `2b30673`, `f747972` (`ao/sard_agent-orchestrator`)
- Patch backups: `C:\Users\nawaf\AppData\Local\Temp\opencode\sard-recovery-patches\`
- Tests: `tests/rag/test_retrieve.py`, `tests/outputs/test_step6_artifacts.py`, `tests/outputs/test_pdf.py`, `tests/test_api.py` via `uv run pytest`
- Diagnostics reference: `.ao/sard_agent/sard_agent-3/docs/diagnostics/agentic-rag-artifact-root-causes.md` (also backed up)

## 9. Limitations

- No live LLM/RAG network calls, no `SARD_*`/`BLOB_*` secret inspection beyond existence, no Vercel cold-start reproduction.
- Artifact foundation tests in `0fa6f1e` not executed on `main` (would require code mutation) — verified by branch read and stat only.
- `uv run pytest` executed on `main`; branch-specific branch execution would require worktree-local `uv` venv activation (AO venvs not invoked to keep audit read-only and isolated).
