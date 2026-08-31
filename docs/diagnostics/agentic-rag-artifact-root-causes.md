# Sard Agent — Agentic / RAG / Artifact Root-Cause Diagnosis

Date: 2026-08-31  
Scope: read-only diagnostics, no broad fixes.  
Branch: `ao/sard_agent-3/root` → `2b30673`

---

## 0. Method

Read-only inspection of:
`sard/api/server.py`, `sard/agent/chat_service.py`, `sard/agent/cultural_router.py`,
`sard/agent/capability_routing.py`, `sard/rag/query_rewriter.py`, `sard/outputs/orchestrator.py`,
`sard/runtime_paths.py`, frontend SSE/artifact handling, `data/corpus` manifest.
Environment probed only for existence (`NVIDIA_API_KEY`, `OPENROUTER_API_KEY`, `BLOB_*`, `SARD_*`) — no secret values read.
No live LLM/RAG calls; traces are static code-flow simulations. Test runners unavailable in this worktree (`pytest` not installed on Windows Python 3.12) — timings recorded as `not-executed`.

---

## 1. End-to-end request traces

Legend: ✅ reliable · ⚠️ conditional · ❌ broken path · `fallback` = `_generate_cultural_fallback_answer`

### 1) «مرحبا، ما الذي تستطيع مساعدتي به؟»

- **Intent:** `SIMPLE_CONVERSATION`, formats=`("text",)`, `explicit_artifact_request=False` (`capability_routing.py:303`)
- **Greeting gate:** `server.py:514-516` normalizes via `re.sub(r"[^\w\s]","",…)` and exact-match against `greetings` list. `"مرحبا"` alone → `is_greeting=True` → hybrid branch skipped entirely.
- **Retrieval:** none. `use_hybrid_retrieval=False` in fallback path, or greeting short-circuit before `ChatService.ask`.
- **Rewritten queries:** none.
- **Local candidates:** none.
- **Model context:** `_SYSTEM_PROMPT = CULTURAL_SYSTEM_PROMPT`.
- **Model result:** direct `model.invoke(lc_messages)` (`chat_service.py:276`). If `ModelConfigError` → `ok=False` with Arabic error; if empty/ exception → `ok=False` or empty.
- **Fallback:** `server.py:628-630` → `_generate_cultural_fallback_answer` else-branch (line 708-717): generic Sard intro with echoed query. Not shrimp-contaminated by itself.
- **Renderer/storage/SSE:** text-only `delta` chunks + `done`.

**Risk:** Arabic greeting with punctuation (`"مرحبا!"`) stripped correctly, but `"مرحبا، ما الذي…"` contains comma → not exact match → not a greeting → falls into real pipeline where empty-model then hits generic fallback (still ok but inconsistent UX).

### 2) «أنشئ تقرير PDF عن زراعة النخيل في القصيم»

- **Intent:** `SAUDI_CULTURAL_FACTUAL` (matches `تاريخ/تراث/ثقافة` + `briefing`? Qasīm not in region list? Actually region extractor at `capability_routing.py:311` checks `["الرياض",…,"المنطقة الشرقية",…]` — `"القصيم"` not matched → region defaults to `المملكة العربية السعودية`). Formats: `_FORMAT_PDF_RE` matches `تقرير PDF` → `requested_formats=("pdf",)`, `explicit_artifact_request=True` (`158-164`). No specialized hint (`_PRESENTATION_HINT` etc.) so domain = `SAUDI_CULTURAL_FACTUAL`.
- **Hybrid branch:** `chat_service.py:208-244` — artifact gate checks `intent.explicit_artifact_request or domain in (PRESENTATION_DECK, RECIPE_CARD,…)` → true (explicit). So even though domain is factual, artifact path fires.
- **Retrieval:** `CulturalRouter.route_and_retrieve`: `is_in_corpus_topic=False` (Qasīm/نخيل not in `_CORPUS_KEYWORDS`), `top_score <0.8` likely → triggers web search (line 166). Local RAG corpus is Eastern Province only (`Al-Ahsa, Tarout, shrimp, springs`) — palm cultivation in Qasīm has zero coverage → RAG low score.
- **Rewritten queries:** deterministic variants via `deterministic_query_variants` — no palm/Qasīm equivalents in `_DETERMINISTIC_EQUIVALENTS` (only shrimp/springs). So variants = `[normalized]` only.
- **Model context:** context blocks contain whatever `rag_search` found (likely off-topic shrimp/spring chunks) + web excerpts.
- **Artifact:** `orchestrate_from_intent` for `pdf` with `kind="document"` → `render_pdf` → `render_cultural_pdf_report` with paragraphs from `raw_text` split on `\n\n`. If `text_resp` empty (model empty), paragraphs fall back to 2-line placeholder (355-358). In hybrid branch this is **executed inside the try that already captured `text_resp`** — but if planner threw, `text_resp` comes from `ask_cultural` which itself may be empty → placeholder document generated. Artifact `store_bytes` to `FilesystemArtifactStore` rooted at `output_root()` (local `output/` vs Vercel `/tmp` — see §4).
- **SSE:** `status` → `citations` → `artifacts` (597-603) only if `chat_res.artifacts` present; if empty `text_resp` then outer fallback re-runs `ChatService.ask(..., use_hybrid=False)` (618-623) which **never generates artifacts** → artifact lost.

**Confirmed failure mode:** hybrid success + empty `text_resp` still yields artifact; hybrid model-empty fallback to direct path yields **zero artifacts** — PDF request silently becomes text-only.

### 3) «Create a DOCX briefing about traditional architecture in Asir.»

- **Intent:** English `briefing` matches `SAUDI_CULTURAL_FACTUAL` (`:299` keywords include `briefing`, `heritage`), `Asir` not in Arabic region list → region default. Formats: `_FORMAT_DOCX_RE` does **not** match `DOCX` without Arabic anchor? Regex `\.docx\b` and `docx\b` with `re.I` does match `DOCX` → `("docx",)` detected.
- **Domain:** not presentation/recipe/etc., so `SAUDI_CULTURAL_FACTUAL` again but explicit artifact true.
- **Retrieval:** same as (2): Asir not in `_CORPUS_KEYWORDS` → `is_in_corpus_topic=False` → web search triggered. Deterministic equivalents irrelevant.
- **Extra bug:** `capability_routing.py:306` topic extractor strips `docx|pdf|…` but not `briefing about`? Leaves reasonable topic.
- **Artifact:** `orchestrator.render_docx` always succeeds locally; but hybrid→direct fallback gap same as (2).
- **Language bug:** `CulturalRouter._synthesize_grounded_answer` returns English only when `is_arabic==False` **and** web_res exists; otherwise Arabic placeholder — user gets Arabic DOCX for English prompt.

### 4) «ما خصائص العمارة التقليدية في رجال ألمع؟»

- **Intent:** `SAUDI_CULTURAL_FACTUAL` (matches `تراث/ثقافة/عسير` keywords). Formats text-only. Region: `عسير` detected? `رجال ألمع` substring not in list, but `عسير` not present either → default region. Yet `عسير` is part of `_CORPUS_KEYWORDS`? No — corpus keywords are Eastern Province only.
- **Retrieval:** `is_in_corpus_topic=False` (عسير/رجال ألمع absent from `_CORPUS_KEYWORDS`) + low RAG score → web search. Deterministic equivalents add nothing.
- **Model context:** RAG returns top shrimp/spring docs with low score but still injected as context blocks. Web search should provide Asir results, but code caps `max_search_calls=2`, `limit=3` and dedupes by URL.
- **Empty-model path:** `answer_query:340-346` — if `llm_invoke_fn is None` or returns empty, falls back to `_synthesize_grounded_answer` which for `rag_res and not web_res` returns raw `top["chunk"][:800]` — which will be the unrelated Eastern Province chunk → **shrimp/spring contamination for Asir query** (confirmed code path).
- **Else fallback:** `server.py:634` `sanitize_cultural_output` (no-op if empty) → empty check → `_generate_cultural_fallback_answer` which for this query hits the final `else` (generic intro) — not shrimp but also not an answer. Which one user sees depends on whether hybrid succeeded (synthesis) vs fallback (generic). Both are wrong.

### 5) «ما الفعاليات الثقافية في الرياض هذا الأسبوع؟»

- **Intent:** `FRESH_EVENT_PLACE` (`_FRESH_KEYWORDS` matches `فعالية`) or `CALENDAR_SYNC`? `_CALENDAR_HINT` requires `تقويم/روزنامة/مزامنة/calendar/سهيل/المربعانية` plus not itinerary — `"فعاليات"` alone does not trigger calendar sync (105-108), so stays `FRESH_EVENT_PLACE`.
- **Retrieval:** `_FRESHNESS_PATTERN` matches `فعاليات` and `هذا الأسبوع` → `is_time_sensitive=True` → always triggers web search (`158-160`). RAG threshold irrelevant.
- **Rewritten queries:** adds `base_query 2026 وزارة الثقافة` variant (381).
- **Failure:** if model empty and `web_res` empty (offline), `_synthesize_grounded_answer` at 465 returns `"تعذّر تكوين إجابة…"`. In `server.py` hybrid text may be that hedge; `ChatResult.ok=True` with hedge text is still considered `verified=False`? `server.py:574` sets `verified = bool(decision in ("generate","hedge") or citations)` — hedge counts as verified but no citations. In direct fallback, hedge lost.
- **Artifacts:** none requested.

### 6) «Explain photosynthesis.»

- **Intent:** `SIMPLE_CONVERSATION` (no Arabic cultural keyword, short). No formats.
- **Retrieval:** `is_in_corpus_topic=False`, `top_score<0.8` → web search triggered even for general knowledge (`cultural_router.py:166`). Objective generated as generic `Find accurate, verified cultural practices regarding: photosynthesis` — culturally biased query → poor web results or empty.
- **Branch E:** if both RAG and web empty and no multimodal → returns hedge asking for `الدولة أو المنطقة` (303-327) — unrelated to photosynthesis. This is **root cause of "unrelated responses"** for out-of-corpus, non-cultural prompts.
- **Server fallback:** same hedge eventually sanitized and streamed; user sees apology asking for region.

### 7) Fresh session after shrimp-drying conversation

- **Session memory:** `ChatService.ask` accepts `messages` history and `session_id` passed to `IsnadPlanner`; `server.py` hybrid path passes full `history_dicts` (531), direct fallback also passes it (621). No server-side session store, but **client sends full history** — contamination is history-bound.
- **Demo caches:** `query_rewriter._cache: dict[tuple[normalized_query, model_id], RewrittenQuery]` persists across requests in same process (Vercel warm lambda). Not session-isolated.
- **Model-bound context:** `_SYSTEM_PROMPT = CULTURAL_SYSTEM_PROMPT` is stateless, but `lc_messages` in direct path includes `messages[:-1]` history — previous shrimp answer becomes part of context for next turn. If previous answer was fallback shrimp text, next answer synthesis includes it as assistant history → model may continue shrimp theme.
- **Frontend SSE:** `frontend/src/app/page.tsx` (inspected) accumulates `artifacts` and renders artifact download tiles; no cache clear on new session except `session_id` UUID rotation. Sticky tiles can persist if `session_id` reused.
- **Verdict:** fresh session with **new `session_id` and empty `messages=[]`** should be clean; fresh tab reusing history array causes contamination. Vercel warm `query_rewriter` cache is query-specific, not session-specific — not a cross-session leak itself, but `_CORPUS_KEYWORDS` bias plus RAG recall is deterministic.

Summarized trace table

| # | intent | formats | retrieval trigger | rewritten variants | local top_score expectation | artifact? | SSE path |
|---|--------|---------|-------------------|--------------------|------------------------------|-----------|----------|
|1|SIMPLE_CONVERSATION|text|none|1|n/a|no|direct|
|2|SAUDI_CULTURAL_FACTUAL|pdf|web (out-of-corpus)|1|low (<0.65)|hybrid yes / fallback no|artifacts if hybrid|
|3|SAUDI_CULTURAL_FACTUAL|docx|web|1|low|same as 2|same|
|4|SAUDI_CULTURAL_FACTUAL|text|web|1|low|no|hedge or raw chunk|
|5|FRESH_EVENT_PLACE|text|web (freshness)|3 (2026)|low|no|citations+hedge|
|6|SIMPLE_CONVERSATION|text|web (out-of-corpus rule)|1|low|no|Branch-E hedge|
|7|depends|depends|depends|cache hit possible|depends|stale possible|history|

---

## 2. Ranked root causes (confidence: C=confirmed, H=hypothesis)

### R1 — Catch-all shrimp/hot-springs fallback contaminates all empty-model paths [C, High]

**File:line:** `sard/api/server.py:668-717`, `chat_service.py:102-104` (`_invoke_llm_str` returns `""` on exception), `server.py:623-635` (direct fallback → `_generate_cultural_fallback_answer`)

**Evidence:** `_generate_cultural_fallback_answer` is invoked in two places: (a) direct-path failure (629), (b) empty `full_response_text` (635). Its first branch `if "روبيان" in q_norm or "تاروت" in q_norm or "تجفيف" …` and second `elif "شرقية" in q_norm or …` are trivially triggered by prior history or by any model-empty response for Eastern Province queries, but **the `else` branch is the real bulk contamination**: when `query` is generic (photosynthesis, architecture general) the else still returns a culturally plausible but unrelated intro that masks the failure. More critically, `CulturalRouter._synthesize_grounded_answer` at `cultural_router.py:434-443` returns the top RAG chunk verbatim — which for Eastern Province corpus **is** shrimp/spring text — so even without `_generate_cultural_fallback_answer`, Asir/Qasīm queries return shrimp text.

**Explains:** shrimp/hot-springs fixation (symptom 5), unrelated responses (symptom 4), empty→shrimp transition.

**Repro:** set `enable_query_rewrite=False` and inject `chat_model` that returns `AIMessage(content="")`; call `ChatService(router with rag_search returning shrimp chunk).ask("ما خصائص العمارة في رجال ألمع؟", use_hybrid_retrieval=True)` → `answer_text` contains `روبيان`.

### R2 — Artifact generation lives only in hybrid branch; direct fallback is artifact-blind [C, High]

**File:line:** `sard/agent/chat_service.py:206-253` (orchestration gated by `if use_hybrid_retrieval:`), `sard/api/server.py:596-603` (artifacts only from `chat_res.artifacts`), `server.py:618-624` (fallback `use_hybrid_retrieval=False` never calls orchestrator)

**Evidence:** `ChatService.ask` early-returns at 246-253 only in `if use_hybrid_retrieval:`. The direct path (255-291) never touches `orchestrator`. Server's fallback at 618 re-invokes `ask(..., use_hybrid=False)` — artifact intent is dropped. Itinerary plan's separate `/api/itinerary` path does generate artifacts but `/api/chat` does not.

**Explains:** PDF/DOCX generation fails through real chat (symptom 1), empty artifact array in SSE, symptom 3 when user expects a file.

**Repro:** `intent=classify_intent("أنشئ تقرير PDF عن زراعة النخيل في القصيم")` → `explicit_artifact_request=True`; `ChatService(injected_model=empty).ask(q, use_hybrid_retrieval=False)` → `result.artifacts==[]`.

### R3 — Small Eastern Province corpus + `_CORPUS_KEYWORDS` hard filter biases routing [C, High]

**File:line:** `sard/agent/cultural_router.py:41-58`, `data/corpus/MANIFEST.md` (topics: `ينابيع`, `حرف_تقليدية`/shrimp), `cultural_router.py:142-168`

**Evidence:** `_CORPUS_KEYWORDS` enumerates 15 Eastern Province terms. `is_in_corpus_topic = any(kw in q_norm for kw in _CORPUS_KEYWORDS)` is used to trigger web search when `not is_corpus_topic and top_score<0.8`. Consequence: Qasīm, Asir, Najd, Hijaz, Hail etc. always force web search even if RAG could answer; Eastern queries suppress web search even when RAG is weak. Corpus itself contains ~2 guides; any non-Eastern query gets low RAG score + forced web path + low-quality fallback.

**Explains:** RAG/web-routing unreliable (symptom 6), shrimp fixation only for Eastern topics in `else` fallback is amplified.

### R4 — Deterministic query equivalents are shrimp/spring-ambiguous [C, Medium]

**File:line:** `sard/rag/query_rewriter.py:43-50`, `70-87`

**Evidence:** `_DETERMINISTIC_EQUIVALENTS` includes `("الينابيع الحارة","العيون الحارة")`, `("تجفيف الروبيان","الروبيان المجفف")`. `deterministic_query_variants` generates bidirectional replacements for any query containing those substrings — but also used for **all** queries when rewrite model unavailable. For Eastern queries, variants are shrimp/hot-spring synonyms that reinforce corpus bias; for non-Eastern queries they add zero value.

**Explains:** hot-springs contamination (symptom 5), low retrieval diversity.

### R5 — Hybrid-to-direct fallback swallows errors and loses citations/artifacts [C, Medium]

**File:line:** `chat_service.py:199-204` (planner exception → cultural router), `server.py:605-606` (broad `except Exception: logger.warning … falling back to direct`), `server.py:634-635` (empty sanitize → fallback)

**Evidence:** Two-level fallback: planner→router, hybrid→direct, each catching `Exception` and continuing with empty `text_resp`. No error surfaced to SSE except via generic text. `citations_sent` only populated if `chat_res.ok` in hybrid success; direct fallback sets `verified=False`.

**Explains:** empty output / stuck generating (symptom 3), unreliable agentic capabilities (symptom 2).

### R6 — Vercel `/tmp` vs local `output/` persistence + blob stub [H, Medium]

**File:line:** `sard/runtime_paths.py`, `sard/outputs/orchestrator.py:140-272`, `api/index.py` (Vercel handler)

**Evidence:** `FileSystemArtifactStore` defaults to `output_root(default=Path("output"))`; `runtime_paths.output_root` resolves to `/tmp/sard_output` on Vercel. `ConfigurableBlobArtifactStore` checks `BLOB_READ_WRITE_TOKEN`/`SARD_STORAGE_BUCKET`/`AWS_S3_BUCKET` but **always delegates to `fallback.store_bytes`** (260) — remote never used. `get_artifact_file` fallback glob `OUTPUT_DIR.glob("**/safe_name")` is fragile across serverless invocations (ephemeral `/tmp` cleared between cold starts). Download URL is always `/api/artifacts/{name}` which 404s if store was temp.

**Explains:** deployment-only artifact download failures (symptom 1 second half).

### R7 — Session/memory/demo caches not isolated [C, Low]

**File:line:** `sard/rag/query_rewriter.py:100` (`self._cache`), `sard/agent/chat_service.py:530-531` (history passed through), frontend `page.tsx` artifact state

**Evidence:** `QueryRewriteService._cache` is process-global, keyed by `(normalized_query, model_id)` — not session-keyed but query-keyed so not a direct leak, but warm lambda serves stale rewrite for same query across users. Conversation history inclusion means previous shrimp answer conditions next answer unless client clears `messages`.

**Explains:** symptom 7 (fresh session contamination when history not cleared), repeated fixation.

### R8 — Frontend SSE and artifact handling does not surface failures [H, Low]

**File:line:** `frontend/src/app/page.tsx`, `sard/api/server.py:596-603` (artifacts event only if non-empty)

**Evidence:** SSE emits `artifacts` only if `chat_res.artifacts` truthy; failed artifacts with `status="failed"` are still emitted but frontend may treat `artifacts_count==0` as no file. `delta` chunking splits on spaces with `await asyncio.sleep(0.015)` — frontend `EventSource` buffering may show "generating" indefinitely if connection drops before `done`.

**Explains:** "remain generating" (symptom 3).

### R9 — Itinerary vs general artifact paths divergence [H, Low]

**File:line:** `sard/agent/graph.py` + `sard/agent/nodes/render.py` (LangGraph itinerary renderer) vs `orchestrator.orchestrate_from_intent`

**Evidence:** Itinerary path renders via LangGraph `render` node with its own PDF/ICS generation; general artifact path via `ArtifactOrchestrator`. Code paths duplicate logic and diverge on error handling — itinerary has fallback orchestrator at `server.py:443-454`, chat does not.

---

## 3. Minimal reproductions (no secrets, no network required)

```python
# R1 — shrimp contamination
from sard.agent.cultural_router import CulturalRouter
def fake_rag(q, k=5): return [{"title":"تجفيف الروبيان - تاروت","chunk":"حرفة تجفيف الروبيان في جزيرة تاروت...","score":0.4,"metadata":{"region":"الشرقية","culture":"سعودي","topic":"حرف"}}]
router = CulturalRouter(rag_search_fn=fake_rag, parallel_search_fn=lambda **kw: [], parallel_extract_fn=lambda **kw: [], multimodal_extract_fn=lambda q, mock_files=None: [])
res = router.answer_query("ما خصائص العمارة التقليدية في رجال ألمع؟", llm_invoke_fn=lambda s,u: "")
assert "روبيان" in res.answer_text  # confirms R1
```

```python
# R2 — PDF lost in direct fallback
from sard.agent.capability_routing import classify_intent
from sard.agent.chat_service import ChatService
from langchain_core.messages import AIMessage
class EmptyModel:
    def invoke(self, msgs): return AIMessage(content="")
intent = classify_intent("أنشئ تقرير PDF عن زراعة النخيل في القصيم")
assert intent.explicit_artifact_request
svc = ChatService(chat_model=EmptyModel())  # type: ignore
r = svc.ask("أنشئ تقرير PDF عن زراعة النخيل في القصيم", use_hybrid_retrieval=False)
assert r.artifacts == []  # R2 confirmed: direct path drops artifact
```

```python
# R4 — deterministic variant reinforces shrimp
from sard.rag.query_rewriter import deterministic_query_variants
assert "العيون الحارة" in deterministic_query_variants("ما أنواع الينابيع الحارة؟")
```

```python
# Greeting edge
from sard.api.server import _generate_cultural_fallback_answer
assert "شرق" not in _generate_cultural_fallback_answer("Explain photosynthesis.")  # else branch, generic
assert "روبيان" in _generate_cultural_fallback_answer("ما هو تجفيف الروبيان؟")
```

---

## 4. Test-coverage gaps

| Area | Existing suites found | Gap |
|------|----------------------|-----|
| capability routing | `tests/test_capability_routing.py` (if present) — format extraction unit only | No test for `SAUDI_CULTURAL_FACTUAL + explicit pdf → artifact gate` |
| artifact pipeline | `tests/test_orchestrator*.py` unit | No integration test `ChatService.ask(..., use_hybrid=False)` with artifact intent |
| PDF/agentic outputs | `sard/outputs/*.py` renderers unit | No test that `/api/chat` SSE emits `artifacts` event for hybrid vs direct |
| RAG retrieval | `evals/test_cultural_search_rag.py` | No test for out-of-corpus (Asir/Qasīm) contamination (expects no shrimp) |
| query rewriting | `tests/test_query_rewriter.py` (deterministic variants) | No test asserting non-Eastern query does NOT produce shrimp variant |
| chat service | `tests/test_chat_service.py` | No test for empty-model fallback path retaining citations/artifacts |
| SSE | no suite | No contract test for `status→citations→artifacts→delta→done` ordering & 404 handling |
| frontend build | `frontend/package.json` typecheck | No E2E for artifact download polling / SSE reconnect |
| corpus bias | none | No manifest count assertion (expects ≥6 regions) |

Execution note: focused suites glob `tests/**/test_*.py`, `evals/**/*.py`, `frontend` build were **not executed** in this session — runner lacks `pytest`/`npm` in PATH (Windows). Recorded as `not-executed`. Manual collect via `python -m pytest --collect-only` failed (`No module named pytest`).

---

## 5. File-ownership plan for Tasks 2–4

| Task | Owner files | Reviewer files |
|------|-------------|----------------|
| **Task 2 — Cultural grounding & contamination** | `sard/agent/cultural_router.py` (`_CORPUS_KEYWORDS`, `_FRESHNESS_PATTERN`, `_synthesize_grounded_answer`), `sard/rag/query_rewriter.py` (`_DETERMINISTIC_EQUIVALENTS`), `data/corpus/**`, `sard/rag/normalize.py` | `sard/agent/util.py` (sanitize), `evals/test_cultural_search_rag.py` |
| **Task 3 — Chat orchestration & SSE contract** | `sard/api/server.py` (`/api/chat`, `_generate_cultural_fallback_answer`, SSE generator), `sard/agent/chat_service.py` (hybrid vs direct), `frontend/src/app/page.tsx` (SSE, artifact tiles) | `sard/agent/capability_routing.py` |
| **Task 4 — Artifact generation & persistence** | `sard/outputs/orchestrator.py`, `sard/outputs/pdf_report.py`, `sard/outputs/office_docx.py`, `sard/runtime_paths.py`, `api/index.py`, `frontend/src/lib/artifacts.ts` | `sard/api/server.py` (download endpoint) |

Cross-cutting: `sard/config/models.py`, `sard/config/rag.py` (env wiring) — owned by Task 3.

---

## 6. Proposed acceptance tests (must pass before merging Tasks 2–4)

1. **AT-01 Greeting isolation:** `POST /api/chat {messages:[{role:"user",content:"مرحبا، ما الذي تستطيع مساعدتي به؟"}]}` → SSE `done.verified==false`, text contains `سرد` and does not contain `روبيان`/`عين الحارة`.
2. **AT-02 Qasīm PDF:** `POST /api/chat {query:"أنشئ تقرير PDF عن زراعة النخيل في القصيم"}` with stubbed model returning non-empty Arabic synthesis → SSE includes `artifacts` event with one `format=pdf status=created size_bytes>1000`, `GET /api/artifacts/{filename}` returns `application/pdf` and Asir/Qasīm content, not shrimp.
3. **AT-03 Asir DOCX:** `POST /api/chat` English DOCX about Asir → DOCX artifact `status=created`, `mammoth` text contains `Asir`/`Rijal Almaa`, not shrimp.
4. **AT-04 Asir grounding (no shrimp):** `CulturalRouter` with injected RAG stub returning only Eastern docs + model-empty → `answer_query("ما خصائص العمارة في رجال ألمع؟")` must NOT contain `روبيان`/`تاروت`; must hedge or cite correctly.
5. **AT-05 Out-of-scope hedge:** `POST /api/chat {query:"Explain photosynthesis."}` → response is Branch-E hedge asking for region, not shrimp; `verified==false`.
6. **AT-06 Fresh events:** `POST /api/chat {query:"ما الفعاليات الثقافية في الرياض هذا الأسبوع؟"}` → `decision.web_search_triggered==true`, `is_time_sensitive==true`.
7. **AT-07 Session isolation:** Send shrimp query, then new `session_id` with empty history same question → second answer does not contain shrimp unless query is shrimp-related.
8. **AT-08 Hybrid→direct artifact retention:** Simulate planner exception (mock planner throws) with PDF intent → response still contains `artifacts` with `status=created` (or at minimum `failed` with Arabic error), never zero artifacts.
9. **AT-09 SSE contract:** Streaming response contains exactly one `done` event after `delta` chunks; `artifacts` (if any) arrives before `done`; client can download via `download_url`.
10. **AT-10 Vercel persistence:** Build artifact in `/tmp`, immediately `GET /api/artifacts/{name}` succeeds; after simulated cold start (fallback glob) still resolves — or else blob store is configured and used.

---

## 7. Local-only vs deployment-specific

| Issue | Local-only | Deployment-only | Both |
|-------|------------|-----------------|------|
| R1 shrimp fallback | ✅ (same code) | ✅ |  |
| R2 hybrid→direct artifact loss | ✅ | ✅ |  |
| R3 corpus bias / routing | ✅ | ✅ |  |
| R4 deterministic equivalents | ✅ | ✅ |  |
| R5 swallowed exceptions | ✅ | ✅ |  |
| R6 Vercel /tmp + blob stub |  | ✅ Vercel `/tmp` ephemerality, cold-start 404 |  |
| R7 session/cache | ✅ warm lambda | ✅ same |  |
| R8 frontend SSE buffering |  | ✅ (Vercel + EventSource reconnect) |  |
| Frontend build/typecheck | ✅ | ✅ |  |

---

## 8. Which root causes explain shrimp/hot-springs contamination

- **Primary:** R1 (fallback + raw RAG chunk echo), R3 (Eastern corpus is the only high-score content), R4 (equivalents reinforce shrimp/spring terms), R7 (history carries shrimp into next prompt).
- **Contributing:** R5 (empty model silently triggers fallback), R3 routing keeps web search off for Eastern queries, so Eastern queries fall back to RAG shrimp text.

---

## 9. Which root causes explain artifact failures

- **Primary:** R2 (artifact gate only in hybrid branch), R5 (hybrid failure silently becomes artifact-free direct path), R6 (blob stub + `/tmp` 404 on Vercel).
- **Contributing:** R1 (empty synthesis → placeholder paragraphs → small/broken PDF), capability_routing region/format edge for English DOCX (R2-adjacent).

---

## 10. Decisions requiring user input

1. **Corpus strategy:** Expand `data/corpus` to cover Najd/Hijaz/Asir/Qasīm/Tabuk etc. before fixing `_CORPUS_KEYWORDS`, or make routing corpus-agnostic (always use freshness+threshold only)? Tradeoff: content cost vs code risk.
2. **Fallback posture:** Should `/api/chat` ever return a culturally plausible but unverified synthesis when both RAG and web are empty, or must it always return the Branch-E hedge? User choice affects UX for general-knowledge prompts like photosynthesis.
3. **Artifact store on Vercel:** Approve wiring `BLOB_READ_WRITE_TOKEN`/`AWS_S3_BUCKET` to `ConfigurableBlobArtifactStore` (requires real credentials and SDK) vs accepting ephemeral `/tmp` + warning that downloads work only within same warm invocation. Cost/compliance decision.
4. **Language policy:** For English prompts requesting DOCX/PDF, should output be English or Arabic? Current system prompt forces Arabic — needs product decision.
5. **Session scoping:** Should server enforce session-scoped `QueryRewriteService._cache` and forbid client-supplied `messages` history cross-session, or is client history canonical? Affects contamination fix.
6. **Model/provider selection:** Confirm `MODEL_PROVIDER`/`MODEL_NAME` (currently `nemotron-3-ultra-...`) and whether `use_hybrid_retrieval` should be forced on for all artifact requests even when model is empty (affects R2 scope).

---

## 11. Evidence index (file:line)

- `sard/api/server.py:513-635` SSE generator + greeting gate + hybrid→direct fallback
- `sard/api/server.py:668-717` `_generate_cultural_fallback_answer`
- `sard/agent/chat_service.py:90-104` `_invoke_llm_str` empty on error
- `sard/agent/chat_service.py:174-253` hybrid branch + artifact orchestration gate
- `sard/agent/chat_service.py:255-291` direct branch (no artifacts)
- `sard/agent/cultural_router.py:41-58` `_CORPUS_KEYWORDS`
- `sard/agent/cultural_router.py:142-168` routing rules B/C
- `sard/agent/cultural_router.py:340-365` empty-LLM synthesis → raw RAG chunk
- `sard/rag/query_rewriter.py:43-50` `_DETERMINISTIC_EQUIVALENTS`
- `sard/outputs/orchestrator.py:140-272` `FileSystemArtifactStore` + `ConfigurableBlobArtifactStore` stub
- `sard/runtime_paths.py` output root resolution
- `sard/agent/capability_routing.py:73-96` format regexes, `298-304` SAUDI_CULTURAL_FACTUAL classification
- `sard/agent/nodes/render.py` + `sard/agent/graph.py` LangGraph itinerary render path

---

## 12. Environment

- Required vars probed (existence only): `NVIDIA_API_KEY` — exists? **not recorded (redacted)**; `OPENROUTER_API_KEY` — not probed; `BLOB_READ_WRITE_TOKEN`/`SARD_STORAGE_BUCKET`/`AWS_S3_BUCKET` — likely absent (blob stub never activates); `SARD_ENABLE_DEV_OBSERVABILITY` — opt-in.
- Frontend: `frontend/src/app/page.tsx` SSE handling inspected manually.
- Tests/build: not executed (no `pytest`/`npm` in PATH on host Python). Re-run on CI Linux runner before Tasks 2–4.

---

*Confirmed vs hypothesis:* R1–R5 marked **C** are supported by direct code reads and constructible repros. R6, R8 are **H** pending Vercel log/cold-start evidence. R7 is **C** for cache/history shape, **H** for user-visible impact. No generic conclusion substituted.
