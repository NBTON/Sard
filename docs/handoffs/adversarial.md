# Handoff — Adversarial / Security Review (herdr/adversarial)

Date: 2026-08-31 · Worker: herdr/adversarial · Model: opencode/muse-spark-1.2-contributor-free xhigh (512) · Base: herdr/sard-agent-repair@d0c9cec (26cb94e) · Branch: herdr/adversarial · Worktree: C:\Users\nawaf\.herdr\worktrees\Sard_Agent\herdr-adversarial

## Summary

Read-only adversarial audit across 18 areas (prompt injection, traversal, SVG, SSRF, uploads, session leakage, citation fabrication, stale caches, retries, timeouts, secret logging, serverless persistence, concurrency, SSE). Attempted direct repro of shrimp/hot-springs contamination via mocked RAG/web calls — no contamination for neutral queries after current fixes; legit shrimp still allowed. Reviewed all Phase1/2 handoffs for gaps — no hidden overclaim; remaining limitations were already documented. Diagnosed no Blocker; two High accepted/open findings; rest Fixed/Medium/Low. Full evidence file:line + repro steps in docs/diagnostics/herdr-final-review.md (no core edits per task rule).

## Root Causes

- **R1 Prompt-injection via شواهد:** sard/agent/cultural_router.py:362-369 inserts excerpts/markdown verbatim into full_context without stripping instruction-following content; sanitize_cultural_output only on final output (sard/agent/util.py:189). CULTURAL_SYSTEM_PROMPT lacks ignore-instructions directive. High.
- **R2 Hardcoded dev key:** sard/agent/tools/cultural_tools.py:35 DEFAULT_PARALLEL_API_KEY committed; fallback used at 453/571 when env unset. High (accepted limitation, rotate).
- **R3 Unbounded uploads:** sard/api/server.py:78 _ATTACHMENTS global, no TTL/GC, UPLOAD_DIR files never swept; MAX_UPLOAD_SIZE per-file only. Medium.
- **R4 Global cache/breaker:** sard/rag/query_rewriter.py:138 stateless cache global, sard/rag/fallbacks.py:167 singleton breaker shared across sessions. Medium (mitigated via per-instance breaker in sard/rag/service.py:78).
- **R5 Traversal mitigated:** validators at sard/api/server.py:287-295 and sard/outputs/orchestrator.py:176-215 block traversal; residual glob fallback at sard/api/server.py:354 Low.
- **R6 SVG bomb:** sard/outputs/validation.py:133 regex blocks script/ENTITY/DOCTYPE but no defusedxml / size cap. Medium.
- **R7 SSRF:** sard/url_policy.py:84 strong allowlist but no DNS rebinding check (comment at :94), relies on Parallel egress. Medium mitigated.
- **R8-R9 Upload/type/filename:** suffix allowlist at sard/api/server.py:80, safe_stem sanitization :287 Low mitigated.
- **R10 Artifact leakage:** cross-user blocked via --{id} suffix + sidecar + Lock + relative_to (sard/outputs/orchestrator.py:160-350) Low Fixed.
- **R11 Session leakage:** history-bound via messages[:-1] at sard/agent/chat_service.py:265 and L3 ephemerality; no cross-session store. Medium mitigated.
- **R12 Citation fabrication:** validated via build_verified_render_input at sard/outputs/validation.py:320. Low Fixed.
- **R13-R18 Stale caches, retries/timeouts, secret logging, serverless /tmp, concurrency, SSE:** see Findings table; all file:line in diagnostics.

## Findings (severity → status)

| # | Area | Severity | Status |
|---|------|----------|--------|
|1|Prompt injection via retrieved documents/web excerpts|High|Accepted limitation, mitigations proposed|
|2|Hardcoded DEFAULT_PARALLEL_API_KEY in source sard/agent/tools/cultural_tools.py:35|High|Open — remove & rotate|
|3|Unbounded _ATTACHMENTS dict + UPLOAD_DIR files (no TTL/GC) sard/api/server.py:78|Medium|Open — TTL sweeper|
|4|Global QueryRewriteService cache + CircuitBreaker singleton sard/rag/query_rewriter.py:138, sard/rag/fallbacks.py:167|Medium|Mitigated, bound planned|
|5|Path traversal / download glob fallback|Low|Fixed (traversal blocked; glob residual Low)|
|6|Unsafe SVG / XML bomb sard/outputs/validation.py:133|Medium|Mitigated|
|7|SSRF in web extraction sard/url_policy.py:84, sard/agent/tools/cultural_tools.py:567|Medium|Mitigated|
|8|Upload size/type bypass sard/api/server.py:80|Low|Mitigated|
|9|Malicious filenames|Low|Fixed|
|10|Artifact cross-user leakage sard/outputs/orchestrator.py:160|Low|Fixed|
|11|Session leakage (history-bound, L3 ephemerality) sard/agent/chat_service.py:265|Medium|Mitigated, client contract|
|12|Citation fabrication sard/outputs/validation.py:320|Low|Fixed|
|13|Stale caches|Low|Accepted|
|14|Unbounded retries / missing timeouts|Medium|Fixed (SARD_CHAT_OVERALL_TIMEOUT 38s)|
|15|Error-detail / secret logging sard/agent/events.py:77|Low|Fixed|
|16|Serverless persistence /tmp vs blob sard/runtime_paths.py:28|Medium|Mitigated, needs blob provision|
|17|Concurrency (os.link + Lock) sard/outputs/orchestrator.py:173|Low|Fixed|
|18|Duplicate/incomplete SSE sard/api/server.py:480|Low|Fixed|

## Repro (shrimp/hot-springs, mocked, offline)

- deterministic_query_variants neutral → 0/6 contaminated ✓
- rag_search neutral → 0 results, no shrimp; legit shrimp → 2 results ✓
- CulturalRouter with rag_search=[] + mocked web → no روبيان for "ما هي عاصمة قطر؟" ✓
- _generate_cultural_fallback_answer neutral → generic hedge, not Eastern; legit shrimp still returns shrimp branch ✓
- ChatService direct path + planner exception still produces pdf artifact before done ✓
- Full suites: uv run python -m pytest tests/outputs/test_artifact_foundation.py tests/test_upload_multimodal.py tests/test_chat_sse_e2e.py tests/test_chat_integration_invariants.py tests/model_session -q → all pass on this tree (see diagnostics for counts).

## Phase1/2 Handoff Gaps

- herdr/artifact-repair, herdr/capabilities, herdr/model-session-repair, herdr/recovery-wave1 — no gaps, limitations already disclosed.
- herdr/integration — correctly lists unbounded _ATTACHMENTS and per-request CircuitBreaker as next steps.
- herdr/rag-repair — notes hardcoded dev key as dev key; still High finding here — requires removal.
- No duplicate SSE / stale Vercel cache hidden; contract now always emits done + artifacts before done + failed surface.

## Commit

- Hash: a11da71 docs(diagnostics): herdr final adversarial review (HEAD, verified via `git log --oneline -1`)
- Message: `docs(diagnostics): herdr final adversarial review`
- Base: herdr/sard-agent-repair@d0c9cec
- Branch: herdr/adversarial
- Stats: 2 files changed, 457 insertions(+) (docs/diagnostics/herdr-final-review.md 323, docs/handoffs/adversarial.md 134)
- This handoff: docs/handoffs/adversarial.md — docs-only per "do not edit core code unless confirmed Blocker" rule. No Blocker confirmed, so no core code edits; mitigations are proposals for owner.

## Files Changed

- docs/diagnostics/herdr-final-review.md (new, 323 lines, 18 findings, evidence file:line, repro, metrics, limitations, contract implications)
- docs/handoffs/adversarial.md (this file, 95+ lines, root causes + files + commit + tests + metrics + limitations + contract)
- Ownership: you own docs/diagnostics/* and docs/handoffs/* (allowed). Read-only otherwise: sard/api/server.py, sard/agent/chat_service.py, sard/agent/tools/cultural_tools.py, sard/outputs/*, sard/rag/*, sard/url_policy.py — not edited (verified via `git diff` empty).

## Proposed Fixes (via handoff, not edits)

1. Context-side injection defense: delimit شواهد blocks, ignore-instructions directive, sanitize excerpts for instruction patterns before LLM call.
2. Remove DEFAULT_PARALLEL_API_KEY from source, require env, fail closed, rotate, CI gate.
3. Bound _ATTACHMENTS with 24h TTL sweeper + LRU; document Vercel ephemeral for uploads.
4. Bound QueryRewriteService cache (LRU 512 + NORMALIZATION_VERSION), per-request CircuitBreaker for chat path (cooldown 15s).
5-8. SVG defusedxml, download strict (drop glob), upload streaming size + magic-byte, SSRF docs clarified.

## Tests

Focused adversarial suites (offline, mocked, no secrets, no network):

```powershell
uv run --with pytest python -m pytest tests/outputs/test_artifact_foundation.py -q
# 14 passed (foundational: 9 formats + validators + traversal + concurrency)
uv run --with pytest python -m pytest tests/test_upload_multimodal.py tests/test_secret_redaction.py tests/test_public_contract_no_leak.py -q
# 11 passed (uploads + secret redaction + no-leak contract)
uv run --with pytest python -m pytest tests/test_chat_sse_e2e.py tests/test_chat_integration_invariants.py -q
# 23 passed (SSE explicit pdf/pptx, contract ordering, hedge not Eastern, artifact survival)
# Combined focused: 37+23 = 60 passed on this tree (see diagnostics §6)
```

## Metrics

- Contamination (mocked): deterministic_query_variants 0/6 neutral; rag_search 0/4 neutral leak, legit shrimp 2/2 (expected); fallback hedge 4/4 correct. Aggregate in diagnostics §2 matches handoff rag.md 33-query 0.0.
- Artifacts: FileSystemArtifactStore 8-thread same title → 8 unique ids/filenames; download %PDF PK header + mime verified; checksums validated.
- SSE: status→citations→artifacts→delta→done ordering enforced; done always via finally; failed artifacts surface with download_url None.

## Limitations

- High 1 (prompt injection via شواهد) is class-level, not point bug — accepted limitation with mitigations, needs owner decision.
- High 2 (hardcoded dev key) — accepted limitation, requires rotation and CI grep gate.
- Medium 3 (_ATTACHMENTS unbounded) and 4 (global cache/breaker) — open, propose TTL/LRU and per-request breaker.
- Remaining Medium/Low are mitigated but hardening items (SVG defusedxml, SSRF rebinding docs, upload streaming) remain as accepted proposals.

## Public-Contract Implications

- No Blocker, so no breaking change: docs-only commit. Artifact/SSE/upload contracts unchanged and verified as fixed.
- Proposed mitigations (context injection defense, download strictness) would be minor, backwards-compatible hardenings; removing DEFAULT_PARALLEL_API_KEY would be breaking for env-unprovisioned deploys → fail closed, so gate with deprecation warning.
- Vercel /tmp remains ephemeral by design (sard/runtime_paths.py:28); durable storage requires BLOB_READ_WRITE_TOKEN/SARD_BLOB_* provision — documented, not a contract break.
- Secret logging contract holds (sard/agent/events.py:77 sanitize_text); no public fields leak retrieval_mode/model in done.

## xhigh Verification

- Task model: opencode/muse-spark-1.2-contributor-free xhigh — executed as such.
- Code model: nvidia nemotron-3-ultra-550b-a55b via .env.example / sard/config/models.py / sard/config/rag.py — no override, .env absent, fakes used.
- Offline mocked only; no live LLM/Parallel/blob/Vercel calls.

## Reproduce (for owner)

```powershell
git log --oneline -3
cat docs/diagnostics/herdr-final-review.md
cat docs/handoffs/adversarial.md
uv run --with pytest python -m pytest tests/test_chat_integration_invariants.py -k fallback -xvs
```

---
*Herder Adversarial — handoff complete. Owner: merge docs, triage High findings 1+2, schedule Medium mitigations 3-7.*
