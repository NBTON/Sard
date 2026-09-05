# Sard end-to-end recovery swarm prompt

Copy the prompt below into the coding agent that will coordinate the repair.

---

## PROMPT START

You are the principal engineer and swarm coordinator responsible for recovering the Sard application as a complete user-facing product. Work in the repository root. Read `AGENTS.md` and every applicable local instruction before editing. Use Ruflo to coordinate a specialist swarm: initialize/check it with the repository's installed Ruflo skill and `npx ruflo init` / `npx ruflo doctor`, then create bounded workstreams with explicit file ownership. If the environment exposes native subagents instead, use them with the same ownership model. The coordinator owns architecture, integration, conflict resolution, end-to-end verification, and the final report.

Do not treat this as a renderer-only task. The current PDF, DOCX, PPTX, and ICS renderers often work in isolation. The primary defect is that the normal chat journey does not reliably reach, await, retain, deliver, or truthfully report those outputs. Repair the system from the user's prompt through routing, model calls, artifact creation, persistence, SSE events, preview, and download.

### Mission

Make every advertised Sard capability work through the main product journey. A user must be able to ask for a document or specialized cultural task in natural Arabic or English, optionally attach real files, see accurate progress, receive a valid result, preview/download it, and find it again when appropriate. Failures must be explicit and actionable. Never replace a failed provider, OCR, ASR, research, or renderer operation with invented content presented as a successful result.

Preserve useful existing behavior and cultural quality, but simplify duplicated architecture where necessary. Remove false capability claims when a capability cannot be implemented with the available providers. A capability is complete only when it passes a real end-to-end test through the same HTTP/UI path used in production.

### Baseline evidence already reproduced

Use this evidence as the starting hypothesis, then reproduce it before changing behavior:

1. `npm run build` passes.
2. The full Python suite produced `613 passed, 1 failed, 1 skipped, 14 warnings` in about 8 minutes. The failure is `tests/model_session/test_fallback.py::test_timeout_classified_and_not_leaking`: a model timeout is converted to `ChatResult(ok=True, canned hedge)` while the contract expects `ok=False`.
3. A real local `POST /api/chat` request for `أنشئ لي ملف PDF عن تاريخ نجد` returned HTTP 200 as SSE but needed about `40031 ms`. It emitted a failed zero-byte artifact with `download_url: null`, `error_category: fallback_empty`, plus a canned hedge.
4. The frontend aborts the request at exactly 40 seconds. It therefore races and commonly aborts milliseconds before the backend's final `artifacts` and `done` events.
5. The backend's overall chat timeout defaults to 38 seconds. After exhausting that budget it starts a fallback with `remaining = max(2.0, deadline-now)`, so the nominal 38-second operation becomes about 40 seconds.
6. The executor future is cancelled, but Python cannot stop an already-running provider call in a worker thread. In the reproduced request, the failed response ended first; a valid 46,861-byte PDF was written about 22 seconds later and a second valid 33,631-byte orphan PDF appeared about 46 seconds after the response. The artifact engine is capable of rendering, but its results are detached from the request and duplicated.
7. `ChatService` wraps provider calls in `with ThreadPoolExecutor(max_workers=1)` and calls `future.result(timeout=6)`. On timeout, leaving the context invokes `shutdown(wait=True)`, so the six-second timeout can still block until the worker returns.
8. Direct local calls to `/api/tools/*` showed that several isolated engines are healthy and fast: presentation produced a downloadable 32,542-byte PPTX; recipe a 22,768-byte PDF; greeting a 16,246-byte PDF; memoir a 16,228-byte PDF. Other endpoints returned JSON/SVG only. These successes do not prove the chat feature works.
9. The main Next.js page sends prompts only to `/api/chat`. The functions in `sard/agent/tools/cultural_agentic_tools.py` are called by direct `/api/tools/*` endpoints and tests, but are not bound to or invoked by ordinary chat orchestration.
10. `ChatRequest.itinerary_mode` is declared and serialized by the API client, but the chat endpoint never reads it. The main page does not pass it. The full LangGraph/itinerary path lives at `/api/itinerary`, so the main UI cannot reach it.
11. `/api/upload` stores bytes and attachment metadata, but `/api/chat` passes only metadata into intent classification. It never resolves the uploaded attachment IDs back to their bytes/path for analysis. The router receives `mock_multimodal_files=None`. Existing tests pass nonexistent attachment metadata and assert only that an SSE response completes, so they do not catch this.
12. The multimodal fallbacks fabricate plausible generic results. With no DashScope key, any image can be described as a generic cultural artifact, any audio can receive the same Al-Ahsa two-speaker transcript, and scanned documents can receive generic heritage-manuscript text. Some configured-key branches also return placeholders without making a real OCR/ASR call. This is a severe truthfulness defect.
13. Intent routing recognizes common PDF/DOCX/PPTX/ICS phrases, but JSON, CSV, TXT, maps, attachment analysis, Excel, dashboards, and several natural creation verbs are absent or misrouted. `Write a report about Diriyah` returns text because `write` is not treated as a creation verb. `Create a TXT document` can default incorrectly to PDF. Several declared intent enum values are never assigned, and map intent has no executor.
14. `ChatService._maybe_orchestrate` covers a limited set of generic formats and a few special cases. It omits or bypasses dialect, artisan, verified research, map, translation, real vision/audio/document analysis, and other advertised capabilities.
15. The specialized direct tools contain silent defaults and canned data. Unknown calendar requests can return the first four static events; unknown dialect input can return the first proverb; unknown craft can become sadu; etiquette collapses most scenarios into majlis. The research endpoint returns hardcoded milestones/bibliography while labeling them verified official sources without performing retrieval.
16. Production artifact persistence is unsafe. Under Vercel, the local output root becomes `/tmp/sard-output`, which is ephemeral and not shared across invocations. The configurable blob adapter falls back to local storage when blob variables are absent. Existing environment files have no Blob configuration. A later `/api/artifacts/...` request can hit a different instance and return 404.
17. The custom Blob adapter should not be trusted as a Vercel Blob implementation. It hardcodes a generic endpoint, performs manual PUTs, ignores the provider's returned URL, fabricates a public URL, and maintains a custom index. Replace it with the supported Vercel Python SDK (`vercel`, `BlobClient`/`AsyncBlobClient`) and retain the provider-returned blob metadata/URL. Official reference: https://vercel.com/docs/vercel-blob/using-blob-sdk
18. Vercel function duration is configurable for Python routes through the `functions.*.maxDuration` entry in `vercel.json`; a function that exceeds its configured duration is terminated. Configure a value compatible with the project's plan and architecture, but do not use a larger timeout to hide blocking/cancellation bugs. Official reference: https://vercel.com/docs/functions/configuring-functions/duration
19. `/api/status` always reports the Arabic equivalent of “ready” because both branches are identical. Its `verified` concept mostly means a local/bundled retriever exists. The frontend defines `fetchSystemStatus` but does not call it.
20. `ArtifactModal` conditionally returns before declaring hooks. Opening it after an initial null render can violate React's Rules of Hooks. The build does not currently enforce a hook lint rule that catches this.
21. Chat sessions are held in an in-memory ref. Storage helpers exist but are unused, and the sidebar does not list saved sessions. Switching/reloading can make prior sessions inaccessible.
22. The SSE retry path duplicates the parser and includes dead/no-op deduplication variables. Artifact events replace the array rather than merging by stable artifact ID. `AbortError` returns without a normal `onDone`/`onError` contract.
23. There are three competing artifact paths: the older LangGraph `ArtifactManager` under run directories, the newer flat `ArtifactOrchestrator` store, and direct tools that write to a module-level output directory. They have incompatible persistence, metadata, and public URL behavior.
24. Tests called “E2E” use an in-process FastAPI `TestClient` with a temporary filesystem store. They cannot detect frontend timeout races, serverless cold starts, instance changes, real attachment consumption, or durable downloads. Test imports can load the real `.env` despite comments claiming offline isolation, which caused live model calls in the full suite. Fixture mutation of the global artifact store is not consistently restored.

### Non-negotiable engineering rules

- Never claim success for a zero-byte file, missing URL, missing blob, placeholder, mock, or post-response orphan.
- Never fabricate OCR, ASR, visual interpretation, citations, source verification, or cultural facts when a provider is unavailable.
- Never make a timed-out request look successful by returning a canned hedge with `ok=True`.
- A timeout must bound wall-clock time. Do not place a timed `future.result` inside a context manager that waits indefinitely during cleanup.
- Thread cancellation is not process cancellation. Use provider-level HTTP timeouts, async cancellation where supported, and a single propagated monotonic deadline. Do not assume `future.cancel()` stops an active call.
- Do not write work to an ephemeral serverless filesystem when another request must retrieve it.
- Do not use “verified,” “grounded,” “ready,” or “downloadable” unless the corresponding invariant was checked.
- Do not retain tests that prove only that a function returned a dictionary. Validate the actual user journey, binary signature, file size, meaningful content, URL lifetime, and failure semantics.
- Do not weaken or delete meaningful tests just to obtain green output. Update incorrect tests when the repaired contract is intentionally different and document that decision.
- Keep secrets out of logs, fixtures, generated reports, commits, and browser traces.
- Keep direct endpoints if useful, but make them thin adapters over the same capability service used by chat. There must be one source of truth for routing, execution, artifact records, and persistence.
- Use stable IDs and idempotency keys so retrying a client request cannot create duplicate chargeable model calls or duplicate artifacts.

### Swarm organization

Launch the following workstreams. Each agent must begin with a short written contract: owned files, interfaces consumed, interfaces produced, tests added, and risks. Agents must not edit another workstream's files without coordinator approval. Prefer separate branches/worktrees where supported. The integration owner rebases or cherry-picks in dependency order and resolves contracts centrally.

1. **Runtime and cancellation agent** — owns provider clients, model factories, deadline propagation, fallback behavior, thread/async execution, and timeout tests. Primary files: `sard/agent/chat_service.py`, `sard/config/models.py`, `sard/config/rag.py`, model-session code, and narrowly related tests.
2. **Unified orchestration agent** — owns the capability request/result contracts and a single execution path shared by chat and direct APIs. Primary files: `sard/api/server.py`, `sard/agent/intent_router.py`, `sard/outputs/orchestrator.py`, capability registry, and new service modules. Coordinate interfaces before editing artifact storage or specialist tools.
3. **Cultural tools agent** — converts specialized cultural tools from canned endpoint demos into validated capability handlers with structured schemas, honest unsupported cases, and content-quality tests. Own `sard/agent/tools/cultural_agentic_tools.py` and its tests.
4. **Attachments and multimodal agent** — owns upload records, attachment resolution, MIME validation, extraction, OCR/ASR/vision integrations, cleanup/retention, and content-canary tests. Own upload/multimodal modules and tests; coordinate the request contract with orchestration.
5. **Artifact and deployment agent** — owns the canonical artifact record, official Vercel Blob integration, local development store, signed/private or public delivery, download behavior, cleanup, `vercel.json`, and deployment documentation.
6. **Frontend reliability agent** — owns SSE parsing, request lifecycle, artifact merging, progress/failure UX, modal hook correctness, preview/download behavior, system status, and session persistence. Own `src/` and frontend tests.
7. **RAG and research truth agent** — owns retrieval availability, corpus packaging, citations, verification semantics, and real research behavior. It must remove hardcoded “verified source” claims and ensure production has an explicit usable corpus or reports degraded status.
8. **Test architecture agent** — owns isolated fixtures, environment safety, contract tests, browser tests, deterministic provider simulators, and regression coverage for late workers/duplicate artifacts. It may add tests around other files but should not change production behavior independently.
9. **Security and observability agent** — audits attachment access, path traversal, file type confusion, artifact authorization, raw exception leakage, secret logging, correlation IDs, structured timing events, and health/readiness semantics. It proposes narrow patches to the owning agent.
10. **Integration and release agent** — owns the end-to-end matrix, local production build, real HTTP smoke suite, deployed-preview validation when credentials are available, performance evidence, final capability matrix, and release report. This agent must challenge unsupported completion claims.

The coordinator should run discovery work in parallel, then freeze shared interface decisions before implementation branches diverge. Suggested merge order: core contracts; runtime/cancellation; canonical artifact store; unified orchestration; attachments and cultural handlers; frontend; test harness; security/observability; end-to-end release verification.

### Phase 0 — preserve and reproduce the failures

Before editing:

1. Record the current commit, Python/Node versions, dependency lock state, relevant environment-variable names without values, and deployment configuration.
2. Run `npm ci` if needed and `npm run build`.
3. Run the full Python suite with a clean environment that cannot accidentally consume real provider keys. Separately run explicitly marked live-provider tests only when authorized keys are present.
4. Start the API with Uvicorn and reproduce at least PDF, DOCX, PPTX, ICS, ordinary grounded chat, and one failure case through real HTTP SSE.
5. Record timestamps for status events, first token, artifact start, artifact complete, done, client abort, and any files created after the response.
6. Snapshot active worker/thread counts and output directory contents before the request, on timeout, and one minute later. Add a regression that fails if a late worker creates an orphan.
7. Exercise the actual Next.js UI with a browser test. Capture the 40-second race, modal behavior, retry behavior, and download result.
8. Upload three canary fixtures whose content is unmistakable: a text/PDF containing a random nonce, an image with a unique visible phrase/object, and an audio file containing a unique spoken phrase. Prove the current system does not actually consume them.
9. Test artifact retrieval from a second process or clean server instance to reproduce the local-filesystem durability defect.

Store this baseline as a machine-readable test/evidence report. Do not commit generated secrets or large transient artifacts.

### Phase 1 — choose and document the target architecture

Write a short architecture decision record before broad implementation. The target must contain these concepts:

#### One capability contract

Create a typed request that includes:

- request ID and idempotency key;
- session/user context as available;
- natural-language prompt and language;
- resolved attachments, each with stable ID, safe filename, detected MIME, size, checksum, and a storage reference;
- explicit requested output formats;
- parsed capability and structured arguments;
- one absolute monotonic deadline or a durable background-job mode;
- trace/correlation metadata.

Create a typed result that includes:

- status (`queued`, `running`, `succeeded`, `failed`, `cancelled`, or `partial`);
- truthful text response;
- zero or more canonical artifact records;
- citations/evidence when grounded or researched;
- machine-readable error code, safe user message, retryability, and provider stage;
- timings and capability version.

The main `/api/chat`, `/api/itinerary`, and `/api/tools/*` routes must invoke this service rather than independent implementations. It is acceptable for routes to request different response encodings, but their execution semantics must match.

#### One artifact contract

Define one canonical artifact record with stable ID, request/job ID, kind, format, filename with extension, MIME type, byte size, checksum, creation state, storage key, delivery URL or delivery route, timestamps, source capability, and a safe error object. Deprecate or adapt competing artifact managers behind this interface.

An artifact may be marked `succeeded` only after storage confirms the object and, where feasible, a read/head verification confirms the expected size/checksum. A `failed` artifact must never expose a misleading filename/MIME/URL. Multiple artifacts must be append/merge-safe and idempotent.

#### Request mode versus durable job mode

Use a bounded synchronous/streaming path for operations that reliably finish within the provider and deployment budget. Use a durable job model for long document/research/itinerary work that can exceed an HTTP request budget. A Vercel function must not spawn an untracked background thread and return; the work needs a durable queue/worker or must remain within an awaited invocation. Persist job state and artifacts outside `/tmp`.

The UI must understand both modes. For jobs, show stable progress, reconnect/poll by job ID, and allow later retrieval. For synchronous streams, ensure the server finishes before the client deadline with meaningful slack. Do not set client and server deadlines within milliseconds of each other.

If the repository lacks infrastructure for a durable queue, implement a reliable bounded synchronous path first and document the exact infrastructure blocker for job mode. Do not simulate durable background execution with in-memory tasks.

### Phase 2 — repair deadlines, providers, and fallback semantics

1. Replace the competing model-construction paths with one supported provider factory or a clearly documented adapter boundary. ChatService and LangGraph must honor the same route/model/timeout/retry settings.
2. Fix ChatNVIDIA initialization. Do not pass unsupported options such as `max_retries` through `model_kwargs`. Configure transport/request timeouts through parameters actually supported by the installed client version.
3. Propagate one deadline. Each stage computes remaining time and refuses to start if it cannot complete within a defined minimum budget. Remove `max(2.0, remaining)` behavior that extends work after deadline.
4. Eliminate timeout wrappers whose executor shutdown waits for the timed-out worker. Prefer native async APIs and provider-level timeouts. If a thread boundary is unavoidable, use a long-lived bounded executor plus explicit lifecycle/metrics, and understand that it still cannot kill a running call.
5. Make retries deadline-aware, capped, jittered where appropriate, and limited to retryable transport/provider failures. Do not retry authentication, validation, or deterministic unsupported-capability failures.
6. A provider timeout returns a failed or partial result with an honest error code. A safe explanatory sentence may accompany it, but it cannot turn the machine result into success.
7. Ensure a cancelled/disconnected request stops new stages and does not render or persist late artifacts. Add instrumentation and tests proving no new artifact appears after cancellation.
8. Separate model generation from deterministic rendering. Once validated document content exists, rendering should not trigger a second uncontrolled model call.

### Phase 3 — unify routing and make advertised capabilities reachable

Build a table-driven registry where every advertised capability has aliases, Arabic/English triggers, accepted attachments, required structured fields, output formats, handler, provider requirements, expected latency class, and readiness check. Generate or validate API/UI capability metadata from this registry so documentation cannot drift silently.

At minimum, repair and test:

- PDF, DOCX, PPTX, ICS, TXT, JSON, and CSV creation;
- generic report/document/presentation/calendar verbs in Arabic and English, including create, generate, make, write, prepare, export, save, and their Arabic equivalents;
- ordinary grounded cultural Q&A;
- itinerary planning through the main UI/chat path;
- recipe card, cultural calendar, etiquette, dialect/proverb, artisan/craft, greeting, oral-history/memoir, and research handlers;
- attachment-based document analysis;
- real image analysis/OCR and real audio transcription when configured;
- maps, spreadsheets/Excel, dashboards, translation, 3D, and any other capabilities shown in the registry/UI.

For the last group, either implement a functioning handler and end-to-end test or mark the capability unavailable with a clear reason and remove it from active UI claims. Do not route an unimplemented map/dashboard request to generic prose and call it complete.

Use structured parsing/validation for specialized tools. Do not infer all missing fields with silent canned defaults. Ask a concise follow-up when required information is missing, or apply an explicitly documented default and tell the user. Specific repairs include:

- recipe endpoints must accept or derive actual ingredients and steps for the requested recipe, not render empty sections for non-jareesh items;
- calendar requests with no matching event must say none were found or perform the configured retrieval path, not return the first static rows;
- etiquette must support its advertised scenarios or reject unsupported ones explicitly;
- dialect/proverb must distinguish “no match” from a valid first item;
- artisan lookup must not convert unknown crafts into sadu;
- presentation slide count and API claims must agree and should respond to user-requested length;
- research must retrieve and cite real sources or label itself as an offline curated summary without claiming live verification.

### Phase 4 — make real attachments flow end to end

1. Replace process-local attachment metadata as the source of truth with a storage-backed attachment record suitable for the deployment model. Local development may use a sandboxed directory; production requires durable storage.
2. Resolve attachment IDs in `/api/chat` and capability execution. Pass opened bytes/streams or safe storage references into extraction. Never trust a client-supplied path.
3. Enforce size/count limits, detected MIME allowlists, extension/MIME consistency, safe names, checksum, session ownership, and retention/cleanup. Reject archives or dangerous types unless deliberately supported.
4. Extract text from supported office/PDF/text files and include the extracted content in the model/retrieval context with token limits and provenance.
5. Implement OCR/vision and ASR through real configured providers. If no provider/key is available, return `capability_unavailable` or `provider_not_configured` and explain how to enable it. Delete fabricated generic descriptions/transcripts.
6. Protect against prompt injection in attached content by separating data from system instructions and labeling untrusted document text.
7. Add canary tests that require returning the unique nonce/phrase from each fixture. Metadata-only handling must fail these tests.
8. Include extraction status in SSE so the UI can distinguish upload complete from content analyzed.

### Phase 5 — make artifacts durable and downloadable

1. Implement local and production stores behind the canonical artifact-store interface.
2. For Vercel Blob, use the supported Python SDK and the `BLOB_READ_WRITE_TOKEN` convention. Preserve the SDK-returned URL and metadata. Choose public or private access deliberately. For private user content, proxy or issue an authorized delivery response according to the current SDK, rather than fabricating a public URL.
3. Remove `/tmp` as a cross-request artifact database. It may be used only for temporary rendering files that are uploaded before success is emitted and deleted afterward.
4. Store artifacts under collision-safe keys that include user/session/job ownership where applicable. Avoid guessing paths with recursive `glob` during download.
5. Make download authorization and content disposition explicit. Set correct MIME and filename. Prevent path traversal and cross-session access.
6. Configure Vercel Python function duration through `vercel.json` after measuring the repaired path. Confirm the path pattern actually matches `api/index.py` and the project's framework configuration.
7. Review the redundant production rewrite in `next.config.ts` and the catch-all rewrite in `vercel.json`. Prove the final routing with a deployed preview rather than assuming the build output is correct.
8. Verify persistence by creating an artifact, restarting the local server or using a second deployed invocation, then downloading and hashing the same bytes.
9. Add cleanup/retention behavior and document costs/limits. Never log storage tokens or signed URLs when they contain credentials.

### Phase 6 — repair frontend lifecycle and product behavior

1. Move all React hooks in `ArtifactModal` above conditional returns, reset modal state safely when the artifact changes, and add a component test that opens/closes multiple artifacts under Strict Mode.
2. Refactor SSE parsing into one parser used for initial and retry requests. Model the terminal states explicitly. Every request must end in exactly one `done`, `failed`, `cancelled`, or client-timeout state.
3. Give server timeout and frontend timeout sufficient measured slack, or use durable jobs. Surface stage-specific errors. Do not silently swallow `AbortError`.
4. Merge artifact events by stable ID and status/version. Support multiple artifacts and partial success without deleting earlier records.
5. Add idempotency keys to retries and make the UI distinguish reconnect from regenerate.
6. Wire real system/readiness status into the page. Show configured/degraded/unavailable capabilities accurately and provide a retry action.
7. Use the existing session storage helpers or replace them with a coherent store. Show saved sessions in the sidebar, restore messages/artifacts after reload, and define a retention policy.
8. Make itinerary and specialized capabilities reachable from natural chat. Extra UI shortcuts are optional; they may not be the only route.
9. Validate preview/download UX for PDF, DOCX, PPTX, ICS, TXT, JSON, and CSV. Unsupported inline preview should still provide a valid download and clear metadata.
10. Add accessibility checks for status announcements, focus trapping, keyboard close, progress labels, error messages, and RTL Arabic rendering.

### Phase 7 — repair RAG, research, and readiness truthfulness

1. Define exact meanings for `configured`, `ready`, `degraded`, `verified`, and `grounded`.
2. `/api/status` must test relevant dependencies without expensive model generation: provider configuration shape, corpus availability/version, artifact-store configuration, and optional multimodal providers. Return per-capability readiness and safe reasons.
3. Ensure the intended production corpus is packaged or available remotely. The ignored local `data/zvec/` index cannot be assumed to exist in deployment. If the bundled 14-document index is the production fallback, disclose its scope and do not imply comprehensive knowledge.
4. A grounded answer must carry citations linked to retrieved evidence. A research result must record source URLs/titles, retrieval time, and which claims they support.
5. Static curated facts may remain as a clearly named offline dataset, with provenance and version. They cannot masquerade as live verified research.
6. Add tests for no-corpus, stale-corpus, provider-down, storage-down, and partial-readiness states. The health endpoint must not say ready in every branch.

### Phase 8 — replace shallow tests with layered evidence

Keep fast unit tests, then add these layers:

#### Deterministic contract tests

- A fake provider that can succeed, stream slowly, exceed a deadline, reject auth, return malformed output, and ignore cancellation long enough to expose leaks.
- Deadline tests based on wall-clock bounds with generous CI tolerance and explicit proof that no file appears after terminal failure.
- Idempotent retry tests proving only one final artifact and one chargeable generation attempt per request key.
- Store contract tests run against local storage and, when credentials are available, a namespaced real Blob test store.
- Attachment canary tests proving bytes are read and content influences the answer.
- Binary tests using magic signatures and parsers: `%PDF`, ZIP/OpenXML structure for DOCX/PPTX, valid UTF-8/JSON/CSV, and valid ICS parsing.
- Semantic tests that inspect extracted text/slides/events to confirm the output addresses the request rather than merely opening successfully.

#### In-process API tests

- SSE event order and exactly one terminal event.
- Accurate HTTP/SSE failure codes and retryability.
- Multiple artifact merge behavior.
- Attachment ownership, invalid IDs, MIME mismatch, oversize uploads, and cleanup.
- Direct endpoints and chat invoke the same handler contracts.
- Restore global stores/settings after every test; eliminate test-order dependence.

#### Real-process integration tests

- Start Uvicorn as a subprocess and call it over TCP.
- Restart between create and download to validate durable storage.
- Disconnect a streaming client and verify no late artifacts or runaway workers.
- Run concurrent requests and verify session/artifact isolation.
- Run with all optional providers absent and confirm honest degraded behavior.

#### Browser tests

- Run the production-built Next.js frontend against the real-process API.
- Generate each core format through the main text box; wait for the terminal state; preview/download; parse the downloaded file.
- Test Arabic RTL and English prompts.
- Test upload canaries for document/image/audio.
- Test retry, reconnect, cancel, reload, session restoration, and opening/closing the modal under React Strict Mode.
- Confirm an unavailable capability is visibly unavailable rather than returning fake success.

#### Live-provider and deployment smoke tests

Mark these separately so normal CI never consumes secrets or money. With explicit credentials, test one grounded answer and one generated artifact against each configured provider. On a Vercel preview, test cold and warm invocations, creation followed by a separate download invocation, stream completion, request duration, and blob persistence. Save redacted evidence.

Fix test environment isolation. Importing the application in offline tests must not automatically load the developer's real `.env`. Load environment files only in entrypoints or behind an explicit setting. Direct tool endpoints must use injected stores instead of hidden module output directories.

### Required end-to-end acceptance matrix

Every row must be executed through the main chat UI and API, not only by calling a renderer directly. Use deterministic providers in CI and repeat representative rows with a real provider in the live suite.

| User request | Required outcome |
|---|---|
| `أنشئ لي ملف PDF عن تاريخ نجد` | Successful nonempty PDF about Najd; valid `%PDF`; stable download after server restart/second invocation; no late duplicate file |
| `Write a DOCX report about Diriyah` | Valid DOCX with title and substantive Diriyah content; creation verb recognized |
| `Create a 6-slide PPTX about AlUla` | Valid six-slide PPTX; requested length honored; no fixed/canned slide-count mismatch |
| Arabic calendar request with dates | Valid ICS whose parsed events match requested dates/time zone; no unrelated first-four fallback |
| `Create JSON/CSV/TXT files about Saudi heritage` | Correct requested formats and MIME/extensions; parseable content; no PDF substitution |
| Grounded cultural question | Text answer with evidence-backed citations; accurate grounded/verified status |
| Itinerary request in normal chat | Main UI reaches itinerary workflow and returns its expected artifacts/status |
| Recipe for a non-jareesh dish | Ingredients and steps are populated and relevant, or a follow-up asks for missing details |
| Unknown craft/proverb/event | Explicit no-match or clarification; never silently returns sadu/first proverb/first events |
| Uploaded PDF with unique nonce | Analysis quotes or reports the nonce with attachment provenance |
| Uploaded image with unique phrase/object | Real configured OCR/vision reports it; otherwise explicit provider-unavailable result |
| Uploaded audio with unique spoken phrase | Real ASR reports it; otherwise explicit provider-unavailable result; no Al-Ahsa canned transcript |
| Research request | Retrieved sources with claim-linked citations, or clearly labeled offline curated mode |
| Provider exceeds deadline | One terminal failure/partial result within budget; `ok=False`; no orphan artifact one minute later |
| Client retries same request | Idempotent continuation/result; no duplicate model charge or artifacts |
| Page reload after completion | Session and artifact metadata restore; valid artifact remains accessible according to retention policy |
| Artifact modal opened repeatedly | No hook-order exception; correct preview/download and keyboard behavior |
| New server instance downloads artifact | Same bytes/checksum are returned from durable storage |

Set measurable service objectives after observing provider behavior. At minimum, define hard maximums for simple chat, first progress event, cancellation recognition, deterministic rendering, and terminal response. The frontend deadline must exceed the backend's terminal deadline by deliberate slack, not two seconds consumed by a fallback.

### Security and privacy checks

- Validate all upload and artifact identifiers server-side and bind them to a session/user where authentication exists.
- Use random server-generated storage keys; sanitize only the displayed filename.
- Prevent traversal, overwrite, polyglot/type-confusion, decompression bombs, and oversized extraction.
- Do not send full unrelated attachment content to providers. Document which provider receives which content.
- Escape or safely render model-produced Markdown/HTML. Review SVG generation before embedding it.
- Replace raw `str(exc)` responses, including `/api/itinerary`, with safe public errors and correlation IDs; retain redacted structured server logs.
- Add bounded concurrency/backpressure so many slow generations cannot exhaust workers.
- Redact keys, tokens, signed URLs, private attachment text, and provider payloads from logs.

### Observability requirements

Emit structured events keyed by request/job ID: route decision, capability, provider/model, queue time, stage start/end, remaining deadline, retry reason, cancellation, renderer duration, artifact bytes/checksum, store confirmation, and terminal state. Do not log secrets or full private prompts/files by default.

Expose operational metrics for latency by stage, provider failures, deadline exhaustion, orphan prevention, artifact storage failures, SSE disconnects, and readiness by capability. Add a diagnostic mode or script that produces a redacted trace for one request. The final report must use these measurements to explain the original 40-second race and demonstrate its removal.

### Completion gates

Do not declare the work complete until all of the following are true:

1. `npm run build` passes with hook linting/type checks enabled.
2. The full offline Python suite passes without contacting live providers. The known model timeout test passes with truthful failure semantics. Investigate and remove the current ChatNVIDIA unsupported-parameter warning.
3. New deterministic integration and browser suites pass.
4. Repeating the original Arabic PDF request through the UI yields a valid, downloadable, durable PDF and no files appear after the terminal event.
5. Direct tools and normal chat use the same capability handlers and artifact contract.
6. Attachment canary tests prove real bytes are consumed.
7. Fake OCR/ASR/vision/research fallbacks are removed and unavailable-provider behavior is honest.
8. Status/readiness reflects actual configuration and dependency state.
9. A second process/instance can download the artifact from durable storage.
10. A deployed-preview smoke test passes when deployment credentials are available. If they are unavailable, provide the exact automated command and checklist the owner must run; do not claim deployed verification.
11. The capability matrix is regenerated from or checked against the registry and marks every capability as working, degraded, unavailable, or removed with test evidence.
12. No secrets, transient artifacts, test uploads, or local indexes are accidentally committed.

### Required final deliverables

Produce:

- an architecture decision record for the unified capability/job/artifact model;
- the implementation, migrations/adapters, and cleaned obsolete paths;
- unit, contract, process, browser, live-provider, and deployment test commands;
- a capability matrix mapping each UI claim to router trigger, handler, providers, output, readiness, and end-to-end test;
- environment/deployment documentation covering model providers, multimodal providers, Blob access mode/token, function duration, corpus availability, and retention;
- a redacted before/after timing trace for the original PDF request;
- a final report listing changes by user-visible behavior, test results with counts, measured timings, deployed-preview evidence, and any remaining explicit limitations.

In the final report, distinguish verified facts from unverified assumptions. Include exact commit hashes and commands. Link each unresolved limitation to an issue with an owner and acceptance criteria. Do not call Sard production-ready while any P0 path in the acceptance matrix still produces fake success, loses files, ignores attachments, or depends on process-local state.

Begin now: initialize the swarm, reproduce the baseline, freeze the shared contracts, assign file ownership, and continue through integration and evidence collection without stopping after an audit or plan.

## PROMPT END

