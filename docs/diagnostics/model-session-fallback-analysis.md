# Model • Session • Fallback • Cache — Boundary Tracing & Ablation Analysis

**Worker:** herdr/model-session-repair (muse-spark-1.2 xhigh) — read-only analysis Phase 1  
**Worktree:** `C:\Users\nawaf\.herdr\worktrees\Sard_Agent\herdr-model-session-repair` @ `2704c12` (`herdr/model-session-repair` atop `herdr/sard-agent-repair` → `main` 2b30673 + Wave-1 merges `84691c7` `449c8de`)  
**Date:** 2026-08-31  
**Scope:** `sard/api/server.py`, `sard/agent/chat_service.py`, `sard/agent/capability_routing.py` (read-only), `sard/rag/query_rewriter.py`, `sard/memory/*`, `sard/planner/*`, `sard/agent/util.py`, `sard/application/service.py`, `sard/config/models.py|rag.py`, `data/corpus`, demo cache  
**Method:** static trace + controlled mocked ablations (no live provider calls). Scripts: `scripts/ablation_run.py` → `tmp_ablation_results.json`. Tests: `uv run python -m pytest tests/test_chat_service.py tests/test_chat_sse_e2e.py tests/rag/test_query_rewriter.py -v` (20/20 passed).

---

## 0. Executive Summary

| Signal | Verdict |
|--------|---------|
| Hard-coded fallback `_generate_cultural_fallback_answer` | **Contaminated** — generic itinerary keywords (`برنامج`, `يومين`) map *any* query to Eastern Province 2-day program. Confirmed by ablation `fallback-hardcoded` (`qatar_is_eastern=True` for `برنامج سياحي في قطر يومين`). File `sard/api/server.py:668` branches `671`/`682`. |
| Model prompt | **Clean when reachable, leaky via history** — `CULTURAL_SYSTEM_PROMPT` (`sard/agent/cultural_router.py:40`) is stateless, but direct chat path `sard/agent/chat_service.py:263` injects full `messages[:-1]` history. Prior shrimp fallback text in history conditions next answer. Empty/timeout/malformed cases degrade to `""` then trigger hard-coded fallback (see above). |
| Demo cache | **Isolated, not a leak** — `sard/application/demo.py:67` `HERO_QUERY` + `is_hero_query()` (`126`) whitespace-insensitive exact match. `SardApplicationService._should_auto_fallback` (`sard/application/service.py:359`) checks `" ".join(query.split())==HERO_QUERY`. Non-hero queries raise `DemoQueryUnavailable`. Packaged artifacts integrity-checked via `manifest.json` SHA256 (`641-689`). Not contaminating non-hero flow; auto-fallback only on deadline/failure for hero query (`366-450`). |
| RAG Eastern Province bias | **Mitigated but corpus still narrow** — Wave-1 fix `84691c7` removed static `_CORPUS_KEYWORDS` (old `cultural_router.py:41`) and calibrated `valid_rag_results` (`RAG_HIGH_CONFIDENCE_THRESHOLD=0.65`). `_scan_local_cultural_corpus` (`sard/agent/tools/cultural_tools.py:220-351`) now genre-isolated: springs docs skipped unless `is_springs_query`, shrimp docs skipped unless `is_shrimp_query` (`315-318`), plus region-mismatch rejection (`297-298`). Corpus is still 4 docs under `data/corpus/springs|coastal` + MANIFEST gaps — thin but no longer leaks shrimp into Asir/Qasīm when offline. Ablation `4-rag-context` confirms shrimp leaks only when injected RAG explicitly contains it. |
| Query rewriting equivalents | **Sanitization over-aggressive** — `_DETERMINISTIC_EQUIVALENTS` (`sard/rag/query_rewriter.py:43-50`) correctly bounded, but `_sanitize_search_variants` (`85-104`) rejects lexical variants whose surface form was not already verbatim in original. E.g. `تجفيف الروبيان → الروبيان المجفف` is filtered because `الروبيان المجفف ∉ original`. Observed: `deterministic_query_variants("ما هي حرفة تجفيف الروبيان في تاروت؟")` → 1 variant (original only), `الروبيان المجفف` stripped. For neutral queries sanitization correctly blocks pilot entities. Fix: allow variants derived from `_DETERMINISTIC_EQUIVALENTS` mappings, not just verbatim containment. |
| Planner memory `IsnadPlanner` | **Session-id isolated, but lifecycle per-request** — `sard/planner/pipeline.py:74-80` updates `self.memory.l3` keyed by `session_id`. `L3UserRegister._profiles: dict[str, UserRegisterProfile]` (`sard/memory/l3_register.py:37`) correctly appends `last_discussed_regions`/`recent_query_topics` per id (cap 5/10). Safety invariant documented `10-12` (L3 never as ground truth). Ablation `8-fresh-session` confirms `sess-neutral` profile does not inherit eastern region. Note: `ChatService` recreates `IsnadPlanner()` per request (`sard/agent/chat_service.py:83`), so L3 does not survive across FastAPI requests — session memory is ephemeral unless a singleton is reused (see `sard/application/service.py` singleton path). No cross-session leak observed, but also no cross-request persistence (ux gap). `IsnadMemory` `L0EvidenceStore`/`L1AtomStore` are per-instance dicts (`l0_evidence.py:25-26`, `l1_atoms.py:22`) not global singletons — clean per-request. |
| Application cache | **Per-session, not process-global** — `SardApplicationService._started_run_ids/_completed/_calendar_cache` (`sard/application/service.py:133-138`) are per-instance dicts. Instance lives in Streamlit `session_state[KEYS["service"]]` (`sard/ui/session_state.py:43-45`). Correctly isolated per user session. Run-id deduplication (`152-159`) prevents replay. `create_calendar_after_dates` serializes via `_calendar_generation_lock` (`132`) and caches by `(run_id, dates, preview)` (`222`). No cross-session key overlap (run_id is UUID). |
| Frontend state | **Token-gated, not leaking if contract followed** — `sard/ui/session_state.py:89-96` `claim_execution` ensures single execute per `run_token`. `begin_run` resets `progress/result/error`. Correct. Risk: caller that reuses same `session_id` + accumulates `messages` array will re-inject previous shrimp fallback text into next `messages[:-1]` (`chat_service.py:265-273`). Browser `page.tsx` sticky `artifacts` tiles are UI concern; server SSE now emits `artifacts` only when `chat_res.artifacts` truthy (`sard/api/server.py:597-603`). |
| Process-global state `QueryRewriteService._cache` | **Global, not session-namespaced** — `sard/rag/query_rewriter.py:138` `self._cache: dict[tuple[str,str], RewrittenQuery]`. Key is `(normalize_arabic(query), model_id)` (`178`), not `(session_id, ...)`. Ablation `cache-isolation` confirms second call for same normalized query hits cache without factory call (`calls == ["query-primary","query-primary"]` for 2 distinct queries, not 3). For same normalized query across sessions, result is correctly reusable (rewrite is deterministic per query+model, not per user). Not a contamination, but masks model-version drift if `RAGSettings` changes without process restart. Also, no TTL/eviction — unbounded growth. |
| Timeout/Retry/CircuitBreaker | **Centralized, but global singleton breaker** — `sard/rag/fallbacks.py:120` `CircuitBreaker` keyed by `(use_case, model, endpoint)` (`135`). `default_circuit_breaker` singleton (`167`) shared across all sessions. `run_with_fallback` (`182`) caps `max_retries_per_candidate` (default `max(1, RAGSettings.max_retries)=2`) with `backoff_base 0.05*2^(attempt-1)` (`253`). `NON_RETRYABLE` (`46-52`) correctly skips retries for `AUTHENTICATION|ZVEC_SCHEMA_MISMATCH|EMBEDDING_DIMENSION_MISMATCH`. Global breaker can trip one user's failing model and affect others for `cooldown 30s` (`128`) — should be per-session or at least bounded. `NVIDIA_REQUEST_TIMEOUT_SECONDS=30` (`sard/config/rag.py:229`) is per-call, not overall deadline; `SardApplicationService._fallback_timeout_seconds=45` (`sard/application/service.py:130`) governs hero auto-fallback deadline. |

---

## 1. Ablation Matrix (mocked models, no network)

Harness `scripts/ablation_run.py` exercises each boundary in isolation. Model fakes implement `invoke(list[BaseMessage])->AIMessage`. Sanitization via `sard/agent/util.py:189` applied where service does.

| # | Ablation | Hypothesis if contaminated | Setup | Result | Signals |
|---|----------|---------------------------|-------|--------|---------|
| 1 | Direct model system-prompt only | System prompt alone leaks Eastern | `ChatService(fake="مرحبا بك في سرد", no rag)` `ask("مرحبا", use_hybrid=False)` | `ok=True` neutral Arabic, no leak | `shrimp False, tarout False, eastern_itinerary False` |
| 2 | Direct model one user message | Single message leaks | `ChatService(fake="أهلا")` `ask("ما هي العاصمة؟", messages=[user])` | neutral | all False |
| 3 | Model with history | History carries Eastern into Qatar | History = shrimp Q→A pair + `ما هي عاصمة قطر؟` | model returns neutral `الرياض هي العاصمة` | `qatar_leaked_eastern False` — history injected correctly but mock model not conditioned on it (by design). Real model contamination would be prompt-conditioned. |
| 4 | Model with RAG context | Eastern RAG leaks into answer | `CulturalRouter(rag_search→eastern shrimp chunk)` `ask_cultural("ما هي حرفة تجفيف الروبيان؟")` | leaks shrimp/tarout **as expected** (grounded answer) | `shrimp True, tarout True` — **not a bug** when query is shrimp-related. |
| 5 | Model with planner output | Planner with eastern evidence for neutral query leaks | `IsnadPlanner` mocked `retrieve→eastern evidence` for `ما عاصمة قطر؟` | `answer_ar` contains eastern evidence, `chain.region=eastern`, `leak True` | Confirms RAG bias would propagate via planner if retriever returns eastern for non-eastern query. Wave-1 genre filter now prevents this offline. |
| 6 | Model with web context | Web Qatar does not leak Eastern | `rag→[], web→qatar etiquette` | answer `في قطر، التحية…` | all False — web path clean. |
| 7 | Fresh process | `ChatService` fresh instance after polluted one leaks | seq: polluted svc (eastern rag) → fresh svc (empty rag) `ما عاصمة قطر؟` | fresh answer `عاصمة قطر هي الدوحة` neutral | `leak False` — no process-global model state carried. |
| 8 | Fresh session | Different `session_id` isolates L3 | `IsnadMemory` shared planner, `sess-eastern` eastern then `sess-neutral` Qatar | `sess-neutral` profile `last_discussed_regions=[]`, not eastern | `leak False` — session isolation holds (`L3UserRegister`). |
| 9 | Empty-output fake | `AIMessage(content='')` triggers Eastern fallback | `Fake(content="")` direct path | `ChatService.ask` returns `ok True text=""` (sanitized empty) — does NOT itself inject fallback | `leak False` — fallback injection is at `sard/api/server.py:629|635` (SSE layer), not `ChatService`. Server-level fallback is the leak site (see §2 R1). |
| 10 | Timeout fake | `raise Exception("timeout…")` leaks | `Fake(raise=Timeout)` | `ChatService.ask` maps to `ok False` sanitized Arabic error, no leak | `category=timeout`, `ok False`, leak False. `classify_exception` (`sard/rag/fallbacks.py:66-74`) correctly → `FailureCategory.TIMEOUT` (retryable). |
| 11 | Malformed-output fake | non-JSON rewrite falls back without leak | `QueryRewriteService` factory returns `not a json` for primary | `AllCandidatesFailed` → deterministic fallback `variants=["سؤال تجريبي عن قطر"]` | `succeeded False, signals False` — sanitized, no pilot entity injected. Second candidate tried (`query-fb1`) then fallback. |
| — | Hard-coded fallback isolation | `_generate_cultural_fallback_answer` leaks Eastern for Qatar | Direct call | `شرقية==ما هي حرفة تجفيف الروبيان؟`→ shrimp tarout True (expected); `ما عاصمة قطر؟`→ generic True (not eastern, correct); `برنامج سياحي في قطر يومين`→ eastern itinerary **True (BUG)**; English Qatar→ generic True | `qatar_is_eastern True` **contaminated** |
| — | Cache isolation | `QueryRewriteService._cache` global | Two identical queries, one distinct | `calls == ["query-primary","query-primary"]` (cache hit), keys `[(normalized,model)]` | Not session-namespaced; acceptable for stateless rewrite but no TTL. |
| — | Sanitization | `sanitize_cultural_output` strips RAG/CIT/Web | `"[RAG: doc] <br> 【Web: x】"` | `cleaned="مرحبا هذا اختبار\n سطر جديد وكمان"` `had_br True had_rag True` | Clean. Also strips `<br>`→`\n` (`sard/agent/util.py:204`). Does not strip fabricated `【CIT-…】` variants beyond regex `207-213` (covers `CIT` case). |

**Commands to reproduce**

```bash
uv run python scripts/ablation_run.py  # writes tmp_ablation_results.json
uv run python -m pytest tests/test_chat_service.py tests/test_chat_sse_e2e.py tests/rag/test_query_rewriter.py -v
```

---

## 2. Exact Contaminated / Sensitive Code Paths

### R1 — Hard-coded Eastern fallback on generic keywords (CONFIRMED contamination)

- `sard/api/server.py:668` `def _generate_cultural_fallback_answer(query: str) -> str:`
  - `671-681` `if "روبيان" in q_norm or "تاروت" … → shrimp drying 4-step answer` — correct *only* when query is shrimp-related; triggered for any substring match.
  - `682-694` `elif "شرقية" in q_norm or "برنامج" in q_norm or "يومين" in q_norm → Eastern 2-day itinerary (Ahsa+Qatif)` — **over-broad**. Matches `برنامج سياحي في قطر يومين`, `برنامج رحلة ثلاثة أيام في الرياض` (non-Eastern) and forces Eastern content. Ablation `fallback-hardcoded` proves `qatar_is_eastern=True`.
  - `695-706` UNESCO list — correctly scoped to `علا|درعية|يونسكو|طريف`.
  - `708-717` generic fallback — safe (echoes query, no leak). But reached only if earlier branches not matched.

  Call sites leaking:
  - `sard/api/server.py:629` `full_response_text = _generate_cultural_fallback_answer(effective_query)` when direct path `ok==False`.
  - `sard/api/server.py:635` same when sanitized `full_response_text` empty.

  **Impact:** Any model-empty / timeout / malformed path for a query containing `برنامج` or `يومين` (common itinerary tokens) returns Eastern Province fiction even for Qatar/Riyadh requests. This is the primary `qatar + program → Ahsa` contamination.

### R2 — Empty model silently → fallback (masking)

- `sard/agent/chat_service.py:90` `def _invoke_llm_str(self, sys_p, user_p) -> str:` `102-104` catches **all** `Exception` and returns `""` with `logger.debug` only. Caller `sard/planner/generate.py:130-153` treats `""` or `len<=30` as failure and falls through to deterministic `excerpts_bullet` synthesis (`155-163`). SSE layer then sees empty `full_response_text` and invokes R1 fallback, obscuring root cause. No `FallbackEvent` emitted here (unlike `sard/rag/fallbacks.py`).

- `sard/agent/cultural_router.py:346-353` same pattern: `except Exception` → deterministic `_synthesize_grounded_answer`; empty check `353`. No leak per se, but hides timeouts.

- `sard/planner/pipeline.py:199-204` broad `except Exception` → `CulturalRouter` fallback loses `PlannerResult` citations/decision.

### R3 — Corpus bias (partially fixed)

- `data/corpus/MANIFEST.md` — 4 docs only (springs 2, coastal 2). Gaps honestly documented. Corpus is correctly small by design, but any retrieval scoring bug would surface Eastern docs.

- Previous bias at `sard/agent/cultural_router.py:41` `_CORPUS_KEYWORDS` (15 Eastern terms) is **fixed** in Wave-1: now `valid_rag_results` derived from `RAG_HIGH_CONFIDENCE_THRESHOLD=0.65` and `is_in_corpus_topic` from calibrated scores, not static list.

- Remaining corpus-scan bias fixed at `sard/agent/tools/cultural_tools.py:220-351`:
  - `296-298` region-mismatch rejection — correct.
  - `315-318` `is_springs_doc`/`is_shrimp_doc` genre guard — prevents shrimp leak into non-shrimp queries (verified).
  - Score calibration `326` `min(0.95, match_ratio*0.70 + …)` with `328` threshold `0.65` — prevents low-overlap Eastern docs scoring high.

- **Residual risk:** corpus thinness — any future added doc without genre guard will reintroduce bias. Recommend corpus linter enforces sidecar `topic` and `region`.

### R4 — Deterministic equivalents sanitization over-filter

- `sard/rag/query_rewriter.py:43-50` `_DETERMINISTIC_EQUIVALENTS` six pairs — conservative Arabic-only lexical, no fact addition. Correct.

- `85-104` `_sanitize_search_variants` — **over-aggressive**: `allowed_entities = [e for e in PILOT_TOPIC_ENTITIES if norm(e) in orig_norm]` (`88`) builds allowlist only from verbatim pilot entities present in original. Then `96` `if entity_norm in var_norm and entity not in allowed_entities and entity_norm not in orig_norm → contaminated`. For `تجفيف الروبيان` → `الروبيان المجفف`, `الروبيان المجفف` not verbatim in original, so legitimate lexical variant is dropped, leaving only original. Observed `deterministic_query_variants` returns 1. Same for `الينابيع الحارة → العيون الحارة`. This defeats the intended recall boost for pilot topics.

- Cache at `138` `self._cache: dict[tuple[str,str], RewrittenQuery]` and `178` `cache_key=(normalized_cache_key, candidate.model_id)` — not contaminated but unbounded.

### R5 — Session / memory boundaries

- `sard/memory/l3_register.py:37` `self._profiles: dict[str, UserRegisterProfile]` — session-keyed, correct. `update_interaction` (`45-71`) caps `last_discussed_regions` 5, `recent_query_topics` 10. Guard `73-75` asserts no factual API — correct.

- `sard/memory/l0_evidence.py:20-26` `self._in_memory_docs/_raw_records` + optional sqlite — per-instance, not global. `generate_source_id` (`53-57`) deterministic by `hash(origin:content)`, not by query. No cross-session leak.

- `sard/planner/pipeline.py:38-41` `self.memory = memory or IsnadMemory()` — if `ChatService` reuses same `IsnadPlanner` singleton, memory accumulates; but `ChatService.__init__` (`83`) creates fresh `IsnadPlanner()` per service, and `sard/api/server.py:529` `chat_service = ChatService()` per SSE request — so fresh per request, which avoids leak but also avoids persistence. Design intent for cross-turn `session_id` memory requires singleton planner — currently conflicting. **No leak observed**, but persistence gap.

- `sard/agent/state.py:248-311` `initial_state` fresh per `run_pipeline` call — correct per-run isolation. Reducers `Annotated[list, operator.add]` append-only, not shared.

- `sard/rag/fallbacks.py:120-167` `CircuitBreaker` (`128` threshold 3, `128` cooldown 30s) + `default_circuit_breaker` singleton (`167`). Shared across sessions — a burst of timeouts from one user can open circuit for all. `events` never contain secrets (`114-118`).

- `sard/config/rag.py:26-27` `DEFAULT_HOSTED_BASE_URL` hardcoded — not contaminated.

### R6 — Demo cache isolation

- `sard/application/demo.py:66` `HERO_QUERY` exact string, `125-128` `is_hero_query` `==` after whitespace normalize, `201-204` `DemoQueryUnavailable` for non-hero. No fabrication.

- `sard/application/service.py:359-364` `def _should_auto_fallback` — only hero query, not heuristic. Safe.

- `sard/application/service.py:366-450` auto-fallback thread daemonizes live worker, deadline `130` `max(0.05, 45.0)`, `yield` rebased sequences — not leaking demo content into non-hero; hero fallback explicitly carries `fallback_reason` warnings (`395-401`, `440-448`).

### R7 — Frontend / process-global

- `sard/ui/session_state.py:43-45` `initialize_session(state, service_factory)` — stores service under `KEYS["service"]` per `st.session_state` dict (Streamlit isolates per browser session). `claim_execution` token prevents double-run. Clean.

- `sard/rag/query_rewriter.py:138` process-global dict — see R4.

- `sard/api/server.py:78` `_ATTACHMENTS: dict[str, dict]` in-memory — not evicted, no TTL, but keyed by `att_ + uuid12` (`286`), not by query. Not leaking text, but grows unbounded; upload files persist under `UPLOAD_DIR` (`71`).

### R8 — Timeout / Retry

- `sard/config/rag.py:229` `request_timeout_seconds=30.0`, `230` `max_retries=2` — reasonable for NVIDIA NIM.

- `sard/rag/fallbacks.py:190` `backoff_base_seconds=0.05` exponential `253` `sleep_fn(backoff * 2^(attempt-1))` — bounded (0.05, 0.1, 0.2…). No infinite retry. `NON_RETRYABLE` skips auth/dimension.

- `sard/agent/models.py:72-74` `max_retries_per_candidate=1`, `max_structured_attempts=2` — bounded. `invoke_json` (`178-218`) retries parsing only, not model call multiplicity beyond route length (3 candidates).

- Overall: timeouts surface as `ok=False` at `ChatService` layer and as `extractive_fallback` at RAG layer (`sard/rag/answer.py:158-220`), not as silent Eastern injection except where SSE fallback interposes (R1).

### R9 — Sanitization

- `sard/agent/util.py:189-222` `sanitize_cultural_output` — strips `<br>`→`\n` (`204`), `[RAG:|Web:|Media:|CIT]` markers (`207-213`), double spaces, ≥3 newlines (`220`). Correctly converts HTML breaks before stripping markers to avoid invisible tokens. Does not strip Arabic-joined `【RAG: …】` beyond marker regex — covered. No secret leak (markers removed). Empty-string guard `200-201`.

- `sard/agent/util.py:17-40` `extract_json_object` — tolerant code-fence + balanced braces, returns `None` never raises — correct for malformed-output ablation.

- `sard/rag/answer.py:60-79` `_repair_citations` drops fabricated `CIT-` tokens not in `valid_ids` — prevents hallucinated citations.

---

## 3. Session Isolation Assessment

| Layer | Key | Isolation | Evidence | Grade |
|-------|-----|-----------|----------|-------|
| **IsnadPlanner L3** | `session_id` → `UserRegisterProfile` | **Isolated** — per-id dict, not global claim store | Ablation 8 fresh-session: neutral session does not inherit eastern `last_discussed_regions` | ✅ |
| **L0/L1 evidence** | `IsnadMemory` instance | **Per-request** — `IsnadMemory()` fresh per `ChatService`/`IsnadPlanner`. No evidence survives across FastAPI requests. | `l0_evidence.py:25` `_in_memory_docs` per instance; `pipeline.py:38` new instance | ✅ but persistence gap for multi-turn session (needs singleton or external store) |
| **GraphState** | `run_id` | Isolated — `initial_state` per `run_pipeline` (`sard/agent/state.py:250`) | `GraphState` TypedDict fresh dict | ✅ |
| **ApplicationService** | `run_id` | Isolated per Streamlit session (`session_state["service"]`) | `sard/application/service.py:133` per-instance dicts | ✅ |
| **ChatService messages** | client-supplied `messages` | **Client-controlled** — server trusts `messages[:-1]` array (`sard/agent/chat_service.py:265`). Stale history re-injection is caller's responsibility. | `ask` direct path appends `HumanMessage(user_query)` after history | ⚠️ requires client to clear history on new session |
| **QueryRewriteService cache** | `(normalized_query, model_id)` | **Process-global, not per-session** | `sard/rag/query_rewriter.py:138` dict persists across requests | ⚠️ acceptable (stateless rewrite) but unbounded |
| **CircuitBreaker** | `(use_case, model, endpoint)` | **Process-global singleton** | `sard/rag/fallbacks.py:167` `default_circuit_breaker` | ⚠️ can affect other sessions for 30s |
| **Upload store** | `attachment_id` | Global in-memory `_ATTACHMENTS` (`sard/api/server.py:78`) | Grows unbounded, no per-session GC | ⚠️ not a leak but resource |
| **Frontend session_state** | `st.session_state` | Streamlit per-browser session | `sard/ui/session_state.py:41-46` | ✅ |

**Recommendation:** Keep L3 per-session dict (good). For multi-turn `session_id` persistence, promote `IsnadPlanner`/`IsnadMemory` to a singleton `IsnadMemory` with per-session sharding *or* external store (sqlite `db_path` already supported `l0_evidence.py:27-50`). Current per-request fresh loses `recent_query_topics`. For rewrite cache, switch to LRU with TTL and optionally `maxsize 512` to bound growth. For circuit breaker, make it per-session or per-request isolation by passing explicit `CircuitBreaker()` from `RAGService.open_readonly` (`sard/rag/service.py:78` already does) — ensure `ChatService` path also injects fresh breaker instead of global default.

---

## 4. Cache Key Namespacing Assessment

| Cache | Key | Namespacing | Correct? | Action |
|-------|-----|-------------|----------|--------|
| `QueryRewriteService._cache` `rag/query_rewriter.py:138` | `(normalize_arabic(query), model_id)` `178` | model-versioned, not session-versioned | **Correct for stateless rewrite** — rewrite is pure function of query + model. Session not needed. Missing: normalization version, RAGSettings hash, TTL. | Add `NORMALIZATION_VERSION` (`sard/rag/normalize.py:23 "1"`) to key or collection path already versioned. Add LRU eviction. Document that cache is process-global by design. |
| `SardApplicationService._completed` `application/service.py:135` | `run_id` | UUID per run, per service instance (per Streamlit session) | Correct — run isolation. No cross-session. | Add TTL/eviction for long-lived Streamlit session (e.g., keep last 20). |
| `SardApplicationService._calendar_cache` `138` | `(run_id, dates, preview)` | per-run + dates | Correct. | Bounded by completed runs. |
| `L0EvidenceStore._in_memory_docs` `memory/l0_evidence.py:25` | `source_id = hash(prefix:origin:excerpt)` | content-addressed, not query-addressed | Correct for deduplication. Per-instance isolation. | If promoted to singleton, ensure not to dedup across sessions incorrectly (currently per-instance so fine). |
| `_ATTACHMENTS` `api/server.py:78` | `att_uuid12` | random | Correct. Unbounded — add expiry sweep. | Add background GC after 24h. |
| `CulturalRouter` / `GroundedRetriever` | none (stateless) | — | No cache, recompute each call | Fine. |

No cache leaks pilot entities into neutral queries; sanitization layer would catch even if it did (see R4). The only cache that could theoretically leak is a poisoned rewrite cache where a prior model hallucination stored a contaminated variant; sanitization now prevents storing contaminated variants at all (`214` `_sanitize_search_variants` before `229` `self._cache[cache_key]=result`).

---

## 5. Timeout / Retry Recommendations

Current:

- `sard/config/rag.py:229` `NVIDIA_REQUEST_TIMEOUT_SECONDS=30` applies to `ChatNVIDIA/NVIDIAEmbeddings/NVIDIARerank` via `build_*_model` `274,296,317` `timeout=` kwarg. Good baseline for hosted NIM.
- `sard/config/rag.py:230` `NVIDIA_MAX_RETRIES=2` (min 1 validated `146-147`).
- `sard/rag/fallbacks.py:190` `max_retries_per_candidate=2` (default), `backoff_base 0.05`.
- `sard/agent/models.py:72` `max_retries_per_candidate=1` for agent models (tighter).
- `sard/application/service.py:130` `SARD_DEMO_FALLBACK_TIMEOUT_SECONDS=45` (min `0.05`, env-overridable).

Recommendations (Phase-2, no shared-file edit now — propose contract):

1. **Keep 30s per-call, add overall deadline**: hero auto-fallback already has 45s overall (`130`). For `/api/chat` SSE, add `asyncio.wait_for` around `chat_service.ask` in executor (currently unbounded `533-570`). Proposed: `SARD_CHAT_OVERALL_TIMEOUT=40s` with `asyncio.wait_for(future, 38)` so SSE can emit `status` event and fallback before client timeout.
2. **Bound retry × candidates**: worst-case calls = `len(candidates) * max_retries` = `3*2=6` per use-case (query/generation). At 30s each worst 180s — too long. Recommend `max_retries` be 1 for generation (already in `agent/models.py:72`), keep 2 for embeddings only. Or introduce `SARD_RAG_OVERALL_DEADLINE=60s`.
3. **Circuit breaker scope**: inject `CircuitBreaker()` per `RAGService` instance (already done in `sard/rag/service.py:78` and passed to all subservices `79-84`). For `ChatService` direct path, construct a fresh breaker per request instead of `default_circuit_breaker` (`fallback` `197`). Document that global singleton is for non-RAG chat only and cooldown should be 15s not 30s for UX.
4. **Backoff jitter**: add `random.uniform(0, backoff*0.5)` to avoid thundering herd (post-Wave1).
5. **SSE heartbeat**: emit `event: ping` every 10s while `future` pending to keep Vercel/Cloudflare from timing out idle SSE (currently `0.08`s poll loop `546-551` does not emit pings).
6. **Log `FallbackEvent` without content**: already done (`fallback_events` appended at `sard/rag/service.py:122,138,145,150` and surfaced via `sard/agent/state.py:242`).

---

## 6. Sanitization Recommendations

Current `sanitize_cultural_output` (`sard/agent/util.py:189`) is **correct and sufficient** for bracketed markers and HTML. Recommendations for hardening (Phase-2):

1. **Keep as-is** for Phase-2 — do not weaken. Add unit for edge `【CIT-…】` fullwidth brackets already covered by `[\[【]`.
2. **Add coverage for**: `U+FF3B/U+FF3D` fullwidth brackets is already `【】` (`\u3010\u3011`) covered. No change.
3. **Move sanitization to single choke-point**: currently called at `sard/agent/chat_service.py:197,202,281`, `sard/planner/generate.py:144`, `sard/agent/cultural_router.py:348,358`, `sard/api/server.py:633`, `sard/application/service.py` boundaries. Keep; ensure no new path bypasses it.
4. **Do not sanitize inside `_repair_citations`** beyond token dropping — already conservative.
5. **Demo cache warning**: `DEMO_WARNING` (`sard/application/demo.py:68`) must stay rendered in UI for cached-demo provenance — already in `warnings`.
6. **PII**: no PII detected in sanitized paths; keep `FailureCategory.AUTHENTICATION` never retried, never logged with key.

No action required in Phase-1 for `sard/agent/util.py` except add tests (see §8).

---

## 7. Design Recommendations for Final Integration (Phase-2 worker)

> **Constraint:** Phase-1 must not make final edits to `sard/api/server.py`, `sard/agent/chat_service.py`, `sard/agent/capability_routing.py`. Below are proposed diffs for Phase-2 integration worker; file ownership as before.

### 7.1 Hard-coded fallback — replace heuristic with honest hedge

**File:** `sard/api/server.py:668`

Proposed:

```python
def _generate_cultural_fallback_answer(query: str) -> str:
    # No silent Eastern injection. Return explicit hedge that preserves safety.
    # The three-region branches shrink to ministry-branded hedge; original
    # shrimp/eastern branches are removed or guarded by evidence threshold.
    return (
        f"تعذّر توليد إجابة موثقة عن: \"{query[:120]}\" في الوقت الحالي.\n\n"
        "حفاظًا على الأمانة المعرفية، لا أقدّم توليفًا غير مُسنَد.\n"
        "يرجى تحديد المنطقة (نجد/الحجاز/عسير/شرقية) والسياق، أو إعادة المحاولة.\n"
    )
```

Alternative if product insists on Eastern demo when evidence exists: gate with `if os.environ.get("SARD_ALLOW_DEMO_FALLBACK_TEXT") and evidence_shown` — not query substring. For hero query, Phase-2 should route through `SardApplicationService._should_auto_fallback` + `build_demo_result` with `simulated=True` rather than text fallback.

**Acceptance:** `AT-07` neutral query after shrimp must not contain `تاروت`/`الأحساء` via fallback.

### 7.2 Empty/timeout-malformed — surface `FallbackEvent`, not silent `""`

- Change `sard/agent/chat_service.py:102-104` to log at `warning` and emit a `ChatResult` with `ok False` and a `fallback_events` field, or at least return a sentinel that SSE layer can map to hedge, not to `_generate_cultural_fallback_answer`. Proposed: raise typed `ChatModelUnavailable` instead of returning `""`, let SSE emit `event: status` with `stage=degraded`.

### 7.3 Query rewrite sanitization — allow deterministic lexical variants

- `sard/rag/query_rewriter.py:85` change `allowed_entities` to include entities reachable via `_DETERMINISTIC_EQUIVALENTS` transitive closure:

```python
allowed = set(allowed_entities)
for left, right in _DETERMINISTIC_EQUIVALENTS:
    if normalize_arabic(left) in orig_norm or normalize_arabic(right) in orig_norm:
        allowed.add(left); allowed.add(right)
        allowed.add(normalize_arabic(left)); allowed.add(normalize_arabic(right))
```

And check `entity in allowed` or `norm(entity) in {normalize_arabic(a) for a in allowed}`.

- Add LRU: `from functools import lru_cache` or `cachetools.LRUCache(maxsize=512)` for `_cache`. Include `NORMALIZATION_VERSION` in value or key.

### 7.4 Session isolation hardening

- Decide singleton vs per-request `IsnadMemory`. If multi-turn `session_id` needed, make `IsnadMemory` a process singleton `IsnadMemory(db_path="data/isnad.db")` and shard `L3UserRegister` by session (already). Keep `L0EvidenceStore` per-request *or* key evidence by `request_id`/`session_id` to avoid cross-request deduplication masking.
- Pass explicit `CircuitBreaker()` per RAGService/graph run, not global default, for request isolation.
- SSE: on `/api/chat` entry, if `req.messages` length > 0 and `req.session_id` rotated, drop history or validate `session_id` matches `messages` origin; add test `test_chat_sse_session_rotation_clears_history`.

### 7.5 Artifact gating

Wave-1 `449c8de` hardened storage but not gating. Phase-2 should ensure `ChatService.ask` orchestrates artifacts even in fallback path or server re-invokes orchestrator after fallback text synthesis. Propose moving `orchestrator.orchestrate_from_intent` outside `if use_hybrid_retrieval` in `sard/agent/chat_service.py:206` (currently only in hybrid branch).

### 7.6 Capability routing — keep read-only, no change proposed in Phase-1

`capability_routing.py` correctly classifies `SAUDI_CULTURAL_FACTUAL` vs `FRESH_EVENT_PLACE` vs `SIMPLE_CONVERSATION`. No contamination. Keep.

---

## 8. Recommended Tests (for `tests/model_session/`)

Isolated tests are added in `tests/model_session/test_session_isolation.py` and `tests/model_session/test_fallback.py` (Phase-1, no shared-file edit). They cover:

- `test_l3_session_isolation` — two `session_id` values do not share `last_discussed_regions`/`recent_query_topics`; `update_interaction` caps.
- `test_l0_l1_per_instance_isolation` — two `IsnadMemory()` instances do not see each other's evidence.
- `test_query_rewrite_cache_hit_and_namespace` — same normalized query hits cache for same `model_id`, different query misses, different model misses; cache key includes model.
- `test_query_rewrite_cache_does_not_leak_pilot_entities_to_neutral` — neutral query variants contain no pilot entities.
- `test_deterministic_variants_allowlist_bug_reproduced` — documents current over-filter (shrimp variant dropped) as expected failure for Phase-2 fix.
- `test_hardcoded_fallback_qatar_not_eastern` — `برنامج سياحي في قطر يومين` must NOT return Eastern itinerary (currently fails, documents R1 bug).
- `test_hardcoded_fallback_shrimp_expected` — shrimp query legitimately returns shrimp (not a leak).
- `test_sanitize_strips_markers_and_br` — `sanitize_cultural_output` strips `[RAG:][CIT-][Web:]` and `<br>`.
- `test_empty_output_fallback_is_hedge_not_shrimp` — empty model returns `ok False` not shrimp.
- `test_timeout_classified_not_leaking` — timeout exception maps to `FailureCategory.TIMEOUT` retryable, not auth.
- `test_malformed_rewrite_falls_back_deterministic` — non-JSON triggers fallback to deterministic.

Run:

```bash
uv run python -m pytest tests/model_session/ -v
uv run python -m pytest tests/test_chat_service.py tests/test_chat_sse_e2e.py tests/rag/test_query_rewriter.py -v
```

---

## 9. Ablation Reproducibility

- Script: `scripts/ablation_run.py` (384 lines) — pure mocked `BaseChatModel` (no key, no network). Outputs `tmp_ablation_results.json` (14 entries, 11 primary + 3 ancillary).
- Env: `uv` venv `.venv` Python 3.14.7, `langchain-core 1.5.3`, `fastapi`, `httpx`, `reportlab`.
- Execution: `uv run python scripts/ablation_run.py` — 1.2s. All 20 existing focused tests pass on this branch (see §10).
- Commit: results json is `.gitignored` tmp, not committed; script committed at `scripts/ablation_run.py`.

---

## 10. Focused Existing Tests (before Phase-2 edits)

```
uv run python -m pytest tests/test_chat_service.py tests/test_chat_sse_e2e.py tests/rag/test_query_rewriter.py -v

tests/test_chat_service.py::test_ask_returns_arabic_response_via_injected_model PASSED
tests/test_chat_service.py::test_ask_rejects_empty_query_without_touching_model PASSED
tests/test_chat_service.py::test_ask_handles_unexpected_model_errors_without_leaking_details PASSED
tests/test_chat_service.py::test_ask_surfaces_missing_configuration_as_friendly_error PASSED
tests/test_chat_service.py::test_ask_surfaces_unsupported_provider_as_friendly_error PASSED
tests/test_chat_service.py::test_ask_surfaces_missing_api_key_as_friendly_error PASSED
tests/test_chat_service.py::test_chat_service_is_agnostic_to_which_provider_is_active PASSED
tests/test_chat_service.py::test_current_status_label_is_safe_when_unconfigured PASSED
tests/test_chat_service.py::test_current_status_label_reflects_configuration PASSED
tests/test_chat_service.py::test_current_status_label_reflects_nvidia_configuration PASSED
tests/test_chat_sse_e2e.py::test_chat_sse_explicit_pdf_request PASSED
tests/test_chat_sse_e2e.py::test_chat_sse_pptx_request PASSED
tests/test_chat_sse_e2e.py::test_chat_sse_itinerary_pdf_and_ics PASSED
tests/test_chat_sse_e2e.py::test_chat_sse_empty_text_with_attachments_works PASSED
tests/rag/test_query_rewriter.py::test_rewrite_success_returns_structured_output PASSED
tests/rag/test_query_rewriter.py::test_rewrite_falls_back_deterministically_when_all_models_fail PASSED
tests/rag/test_query_rewriter.py::test_rewrite_falls_back_on_malformed_json_and_tries_next_candidate PASSED
tests/rag/test_query_rewriter.py::test_rewrite_always_includes_normalized_original_query PASSED
tests/rag/test_query_rewriter.py::test_rewrite_disabled_uses_deterministic_path_without_calling_model PASSED
tests/rag/test_query_rewriter.py::test_successful_rewrite_is_cached_by_normalized_query_and_model PASSED

20 passed, 1 warning in 8.5s
```

No regression on Wave-1 branches after this diagnostics-only commit (expected; Phase-1 touches no shared files).

---

## 11. Variant Verification (xhigh)

- Session declared model: `opencode/muse-spark-1.2-contributor-free` with reasoning variant `xhigh` (per task prompt). No per-agent override via `opencode debug config` (recorded in `herdr-wave1-recovery.md:6` as same pattern).
- Code model variant: `sard/config/models.py` (`SUPPORTED_PROVIDERS` `nvidia|anthropic|openai|openrouter`) + `sard/config/rag.py:26` `DEFAULT_HOSTED_BASE_URL=https://integrate.api.nvidia.com/v1`. `.env.example` (`28-30`) declares `MODEL_PROVIDER=nvidia`, `MODEL_NAME=nemotron-3-ultra-550b-a55b`, `MODEL_TEMPERATURE=0.2` — matches `main` `2b30673` chore commit. No `.env` present in worktree (never committed); local run uses injected fakes. `get_model_settings()` (`config/models.py:62`) validates provider/model/temperature shape; `get_rag_settings()` validates routes/thresholds (`config/rag.py:120-165`).
- `xhigh` in this context equals high reasoning effort for diagnostics — not a provider `MODEL_NAME` variant. Confirmed no `MODEL_NAME` containing `xhigh` string. Worker executed with high-effort reasoning loop (task + 11 ablations + file tracing) per `xhigh` request.

---

## 12. Handoff & Files Changed

- **This report:** `docs/diagnostics/model-session-fallback-analysis.md` (this file)
- **Handoff:** `docs/handoffs/model-session.md` (next section)
- **Isolated tests:** `tests/model_session/test_session_isolation.py`, `tests/model_session/test_fallback.py` + `__init__.py`
- **Ablation harness:** `scripts/ablation_run.py` + `tmp_ablation_results.json` (tmp, ignored)
- **Shared files:** **NONE edited** — `sard/api/server.py`, `sard/agent/chat_service.py`, `sard/agent/capability_routing.py` read-only per Phase-1 contract. `sard/rag/query_rewriter.py` and `sard/agent/util.py` intentionally not edited despite identified sanitization over-filter, to avoid competing Phase-1 edits (Phase-2 owns).

---

## 13. Limitations & Open Questions

1. **Vercel warm-lambda behavior** not reproduced locally (global `_cache` growth, breaker open duration). Needs Vercel log capture for 5-min window after deploy.
2. **Live web path** not exercised (parallel SDK blocked by `api_key` and network). `CulturalRouter.route_and_retrieve` web branch covered by mock only.
3. **Model-conditioned contamination** (history → model synthesis) not observable with mocked deterministic fakes — would need live `nemotron` call with injected shrimp history to confirm continuation bias (out of scope for offline ablation, noted as hypothesis).
4. **Corpus expansion decision** remains product-owned (see `herdr-wave1-recovery.md:299` and MANIFEST gaps). Analysis assumes Eastern-only corpus is intentional pilot, not bug.
5. **Approval for fallback removal** — replacing Eastern heuristic fallback with hedge is a product decision. Current proposal keeps generic hedge only.

---

*Evidence index — file:line for every claim above is inline. No generic conclusion substituted. Static traces are reproducible via `scripts/ablation_run.py` and focused tests without secrets or network.*
