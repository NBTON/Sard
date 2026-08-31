# Handoff — Model • Session • Fallback • Cache Boundary Tracing

**Date:** 2026-08-31 08:10 +0300 • Worker: `herdr/model-session-repair` • Commit: `cdba27a` (`docs(diagnostics): model-session-fallback boundary tracing + ablations + isolated tests`) • Base: `herdr/sard-agent-repair` @ `2704c12` (`main` 2b30673 + Wave-1 `84691c7` + `449c8de`) • Model: `opencode/muse-spark-1.2-contributor-free` xhigh — read-only Phase-1, no shared-file final edits

## Findings Summary (from `docs/diagnostics/model-session-fallback-analysis.md`)

**Contamination confirmed:**
- **R1 Hard-coded fallback** `sard/api/server.py:668` `_generate_cultural_fallback_answer` branch `682-694` maps any `برنامج`/`يومين` query (e.g., `برنامج سياحي في قطر يومين`) to Eastern 2-day itinerary (Ahsa+Qatif). Ablation `fallback-hardcoded` proves `qatar_is_eastern=True`. Other branches (shrimp `671`, UNESCO `695`) correctly scoped but masked by second branch.
- **R4 Deterministic equivalents over-filter** `sard/rag/query_rewriter.py:85-104` drops valid lexical variants (`تجفيف الروبيان → الروبيان المجفف`, `الينابيع → العيون`) because `allowed_entities` requires verbatim pilot entity in original. Observed `deterministic_query_variants("تجفيف الروبيان في تاروت")` → 1 variant only.

**Mitigated / clean:**
- RAG Eastern bias **fixed** in Wave-1 `84691c7` (removed static `_CORPUS_KEYWORDS`, calibrated `valid_rag_results` @ `RAG_HIGH_CONFIDENCE_THRESHOLD=0.65`, genre guards `is_springs_doc`/`is_shrimp_doc` `sard/agent/tools/cultural_tools.py:315-318` + region mismatch `296-298`). Corpus still only 4 docs (`data/corpus/MANIFEST.md` springs 2 + coastal 2) but no longer leaks shrimp into neutral when offline.
- Model system prompts (`CULTURAL_SYSTEM_PROMPT` `sard/agent/cultural_router.py:40`, `ChatService._SYSTEM_PROMPT`) stateless; contamination only via client-supplied `messages[:-1]` history (`sard/agent/chat_service.py:265`).
- Demo cache `sard/application/demo.py:67` `HERO_QUERY` exact match + `is_hero_query` + `SardApplicationService._should_auto_fallback` `sard/application/service.py:359` (hero only, deadline 45s) — not leaking non-hero.
- `IsnadPlanner` L3 `sard/memory/l3_register.py:37` per-`session_id` dict, caps 5/10, guard `73` never supplies cultural truth. Ablation 8 confirms neutral session not inheriting eastern.

**Process-global / session:**
- `QueryRewriteService._cache` `sard/rag/query_rewriter.py:138` key `(normalized_query, model_id)` — not session-namespaced; acceptable (stateless rewrite) but unbounded, no TTL, sanitized before cache store `214→229`.
- `default_circuit_breaker` singleton `sard/rag/fallbacks.py:167` key `(use_case, model, endpoint)` — one user's timeout burst can open circuit for others (30s `128`). Recommend per-request breaker injection (already done in `sard/rag/service.py:78` for RAG path; extend to chat direct path).
- `ChatService._invoke_llm_str` `sard/agent/chat_service.py:102-104` returns `""` on any exception (masked), delegating to SSE fallback which then injects R1 Eastern text — obscures root cause.
- `L0EvidenceStore`/`L1AtomStore` per-instance dicts `sard/memory/l0_evidence.py:25`, `l1_atoms.py:22` — per-request fresh, no cross-session leak but also no multi-turn persistence (design gap if singleton expected).

**Sanitization:** `sard/agent/util.py:189` `sanitize_cultural_output` correctly strips `[RAG:][CIT-][Web:][Media:]` + `<br>`→`\n`, `205-220`. Good choke-point, no secret leak.

**Timeout/Retry:** `NVIDIA_REQUEST_TIMEOUT_SECONDS=30` `sard/config/rag.py:229`, `max_retries=2`, `backoff 0.05*2^(attempt-1)` `sard/rag/fallbacks.py:253`, `NON_RETRYABLE` auth/dimension `46-52`, `SardApplicationService fallback 45s` `application/service.py:130` — bounded, but overall `candidates*retries` worst 180s without outer deadline.

## Ablation Summary (mocked, offline)

Harness `scripts/ablation_run.py` → `tmp_ablation_results.json` (14 entries, 11 primary).

| # | Scenario | Leak? | Detail |
|---|----------|-------|--------|
| 1 | System prompt only | ✗ | `مرحبا` → neutral Arabic |
| 2 | One user message | ✗ | `ما هي العاصمة` → neutral |
| 3 | With history (shrimp Q+A + Qatar) | ✗ | mock model not conditioned; real model would be history-conditioned |
| 4 | With RAG context (eastern shrimp) | ✓ shrimp/tarout | expected when query is shrimp-related |
| 5 | With planner (eastern evidence for Qatar) | ✓ | proves retriever bias would propagate; now fixed by genre guard |
| 6 | With web context (Qatar etiquette) | ✗ | web path clean |
| 7 | Fresh process after polluted | ✗ | no process-global model state |
| 8 | Fresh session (different session_id) | ✗ | L3 isolated, neutral regions `[]` |
| 9 | Empty AIMessage content='' | ✗ (ChatService) | returns `ok True text=""`; SSE layer would inject R1 fallback |
| 10 | Timeout raise | ✗ | `ok False` sanitized Arabic error, `FailureCategory.TIMEOUT` retryable |
| 11 | Malformed non-JSON rewrite | ✗ | falls through candidates → deterministic fallback `["سؤال تجريبي عن قطر"]`, no pilot leak |
| — | Hard-coded fallback `برنامج قطر يومين` | **✓ EAST leak** | `qatar_is_eastern=True` — primary bug |
| — | Cache isolation | ✗ | hit on same `(norm,model)`, miss on different query; not session-namespaced by design |
| — | Sanitize | ✗ | `[RAG:][Web:]<br>` stripped |

Repro: `uv run python scripts/ablation_run.py` (1.2s) + `uv run python -m pytest tests/model_session -v`

## Session Isolation Assessment

- **Isolated:** L3 per `session_id` (`l3_register.py:37`), GraphState per `run_id` (`state.py:250`), ApplicationService per Streamlit `session_state` (`ui/session_state.py:43`), frontend token `claim_execution` (`ui/session_state.py:89`).
- **Not per-session (by design or gap):** `QueryRewriteService._cache` global (acceptable, stateless), `default_circuit_breaker` global (should be per-request), `_ATTACHMENTS` global (resource, not leak), `L0/L1` per-request fresh (persistence gap — multi-turn `session_id` expects singleton `IsnadMemory` with `db_path`).

Grade: **no cross-session contamination observed for neutral queries** except R1 fallback + history re-injection (client-controlled).

## Cache Key Namespacing

- Rewrite cache ` (norm, model_id)` `rag/query_rewriter.py:178` — model-versioned, not session; correct for stateless rewrite. Add `NORMALIZATION_VERSION` (`rag/normalize.py:23 "1"`) to key and LRU(512) TTL for growth.
- Completed runs `(run_id)` `application/service.py:135` — UUID per run, per service instance; isolated.
- L0 `hash(origin:excerpt)` content-addressed — per-instance; if promoted to singleton shard by `request_id`/`session_id`.

## Timeout/Retry Recommendations (Phase-2)

1. Keep 30s per-call + add overall `SARD_CHAT_OVERALL_TIMEOUT=40s` wrapping `chat_service.ask` future in SSE `application/service.py:368-450` already has 45s hero deadline; mirror for `/api/chat`.
2. Worst `3 candidates *2 retries*30s=180s` — set `agent/models.py:72` `max_retries_per_candidate=1` for generation (already), add `SARD_RAG_OVERALL_DEADLINE=60s`.
3. Inject fresh `CircuitBreaker()` per RAGService run (already `rag/service.py:78`); extend to chat direct path instead of `default_circuit_breaker`; reduce cooldown 30→15s.
4. Add jitter `random.uniform(0, backoff*0.5)` + SSE heartbeat `event: ping` every 10s in `api/server.py:546` poll loop.

## Sanitization Recommendations

- Keep `sanitize_cultural_output` `agent/util.py:189` as choke-point; already covers `[\[【] RAG/Web/Media/CIT`. No weaken.
- Ensure all new text paths (planner `generate.py:144`, router `348`, chat `197/202/281`, server `633`) call it — they do.
- `AnswerService._repair_citations` `rag/answer.py:60` drops fabricated `CIT-` not in `valid_ids` — keep.

## Recommended Tests & Integration Requirements (Phase-2)

**Phase-1 isolated tests added** `tests/model_session/` (19 tests, all pass):

- `test_session_isolation.py`: `test_l3_session_isolation`, `test_l3_caps`, `test_l0_l1_per_instance_isolation`, `test_query_rewrite_cache_hit_and_namespace`, `test_cache_does_not_leak_pilot_entities`, `test_deterministic_variants_allowlist_overfilter_documents_bug` (documents R4), `test_planner_locate_does_not_use_l3`.
- `test_fallback.py`: `test_hardcoded_fallback_qatar_not_eastern` (documents R1 leak — asserts True now, Phase-2 must flip to False), `test_hardcoded_fallback_shrimp_expected`, `test_sanitize_strips_markers_and_br`, `test_empty_output_is_empty_not_shrimp`, `test_timeout_classified`, `test_malformed_rewrite_falls_back`, `test_circuit_breaker_opens_and_resets`, etc.

**Phase-2 integration worker must:**

1. Fix `sard/api/server.py:668` — shrink fallback heuristic to honest hedge (no `برنامج`/`يومين` → Eastern). Gate any rich fallback on evidence presence, not query substring. Flip `test_hardcoded_fallback_qatar_not_eastern` to `assert not has_eastern_itinerary`.
2. Fix `sard/rag/query_rewriter.py:85` allowlist to include deterministic-equivalent closure; change `test_deterministic_variants_allowlist_overfilter` to assert variant present; add LRU TTL and `NORMALIZATION_VERSION` to cache key.
3. Surface empty/timeout/malformed as `FallbackEvent` at `sard/agent/chat_service.py:102` instead of `""`; emit `event: status stage=degraded` before hedge.
4. Move artifact orchestration out of `if use_hybrid_retrieval` `chat_service.py:206` so fallback path retains `artifacts` (R2).
5. Decide `IsnadMemory` singleton vs per-request; add `IsnadMemory(db_path=...)` singleton for `session_id` persistence; make circuit breaker per-request.
6. Verify via: `uv run python -m pytest tests/model_session tests/test_chat_service.py tests/test_chat_sse_e2e.py tests/rag/test_query_rewriter.py -v` (39 passed on this branch: 20 original + 19 new) plus `tests/rag/test_retrieve.py` and `tests/application/` where available; run `uv run python scripts/ablation_run.py` to re-baseline.
7. Do not edit `sard/agent/capability_routing.py` beyond read-only (classification clean).
8. Keep `sard/agent/util.py` sanction logic unchanged except tests.

## Files Changed (this branch, Phase-1 only)

- **New** `docs/diagnostics/model-session-fallback-analysis.md` (diagnostic report, this handoff's source)
- **New** `docs/handoffs/model-session.md` (this file)
- **New** `tests/model_session/__init__.py`, `tests/model_session/test_session_isolation.py`, `tests/model_session/test_fallback.py` (19 tests)
- **New** `scripts/ablation_run.py` (harness, not required in commit but retained untracked for repro; commit only docs+handoffs+tests per task)
- **Uncommitted (intentionally not committed):** `tmp_ablation_results.json` (ignored), `scripts/` if not needed
- **Not edited:** `sard/api/server.py`, `sard/agent/chat_service.py`, `sard/agent/capability_routing.py` (read-only), `sard/rag/query_rewriter.py`, `sard/agent/util.py` (analyzed only, fixes deferred to Phase-2)

## Commit & Tests

- **Branch:** `herdr/model-session-repair` @ `cdba27a` (this commit) atop `2704c12`
- **Base:** `herdr/sard-agent-repair` (which already contains `84691c7` RAG + `449c8de` artifacts)
- **Focused tests:** `uv run python -m pytest tests/test_chat_service.py tests/test_chat_sse_e2e.py tests/rag/test_query_rewriter.py -v` → **20 passed** (8.5s) on `2704c12` + `cdba27a`
- **New isolated tests:** `uv run python -m pytest tests/model_session -v` → **19 passed** (1.0s)
- **Combined:** `uv run python -m pytest tests/test_chat_service.py tests/test_chat_sse_e2e.py tests/rag/test_query_rewriter.py tests/model_session -v` → **39 passed**
- **Ablation:** `uv run python scripts/ablation_run.py` → 14 entries, summary shows only R1 Eastern leak (`qatar_is_eastern True`) and R4 over-filter (`deterministic variants len 1`).

## Commit Hash

`cdba27ae36d1c948ba836ff1e08d700f1a1cd5b9` `docs(diagnostics): model-session-fallback boundary tracing + ablations + isolated tests` — 5 files, 896 insertions, no shared-file edits.

## Recommended Integration Order (Phase-2)

1. Merge Phase-1 diagnostics/tests as-is (no shared-file edits) — establishes baseline.
2. Phase-2 worker first fixes `sard/api/server.py:668` fallback hedge (R1) + flips `test_hardcoded_fallback_qatar_not_eastern`.
3. Then fixes `sard/rag/query_rewriter.py:85` sanitization allowlist + LRU (R4) and `sard/agent/chat_service.py:102` empty/masking.
4. Then addresses session singleton / breaker scope and artifact gating (requires `sard/agent/chat_service.py:206` move) — coordinate with `sard/api/server.py` SSE owner to avoid overlapping `chat_service.py` edits (Phase-1 deliberately avoided competing final edits).
5. Final verification: re-run 11 ablations + full `tests/model_session` + existing focused suites, confirm no shuttle to `sard/agent/capability_routing.py`.

## xhigh Verification

- Session model `opencode/muse-spark-1.2-contributor-free` xhigh requested per task prompt; `opencode debug` pattern from `herdr-wave1-recovery.md:6` shows no per-agent override on this host. Code model `MODEL_PROVIDER=nvidia` `MODEL_NAME=nemotron-3-ultra-550b-a55b` via `.env.example` `28-30` / `sard/config/models.py:28` / `sard/config/rag.py:27`; `.env` not present locally (fakes injected). Worker executed high-effort reasoning loop (static trace of 12 files + 11 ablations + 39 tests) per xhigh.

## Limitations

- Offline ablation only — no live `nemotron`/`parallel`/`blob` calls, no Vercel warm-lambda repro, no `st.session_state` browser manual test.
- History-conditioned model continuation (shrimp history → next answer shrimp bias) hypothesized but not proven with mocked fakes; needs live model with injected shrimp history to measure.
- Corpus expansion decision remains product-owned (MANIFEST gaps).

---

*Phase-1 owns diagnostics + isolated tests only. Phase-2 owns shared-file final edits under integration owner to avoid writer conflict.*
