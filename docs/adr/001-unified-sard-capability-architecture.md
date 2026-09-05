# Architecture Decision Record (ADR) 001: Unified Sard Capability Architecture

**Status**: Accepted  
**Date**: September 4, 2026  
**Author**: Principal Engineer & Swarm Coordinator  

---

## 1. Context and Problem Statement

The Sard cultural AI application provides rich Saudi heritage research, grounded conversational answers, and interactive cultural artifacts (PDF research cards, DOCX documents, PowerPoint `.pptx` decks, iCalendar `.ics` files, SVG diagrams, and audio/visual multimodal inspection).

During baseline testing (Phase 0), critical end-to-end defects were identified:
1. **Timeout Race & Orphan Generation**: The frontend hardcodes a 40-second abort timeout, while the backend has a 38-second deadline that extends on fallback by `max(2.0, remaining)`. When timeouts occur, Python threads cannot be cancelled; workers run to completion in the background and write orphan files minutes after the connection drops.
2. **`ThreadPoolExecutor` Context Manager Trap**: Exiting `with ThreadPoolExecutor(max_workers=1)` calls `executor.shutdown(wait=True)`, blocking the thread until long model invocations finish, negating the `timeout=6.0` parameter.
3. **Dishonest Timeout Reporting**: When chat service invocation fails or times out, it returns `ChatResult(ok=True)` with a canned hedge, masking system failures as successes.
4. **Attachment Black Hole & Fabricated Multimodal Outputs**: Uploaded files were never resolved to disk or passed to models. When multimodal keys (`DASHSCOPE_API_KEY`) are missing, the system silently returned fabricated transcripts (e.g. Al-Ahsa two-speaker audio) or generic artifact descriptions.
5. **Competing & Ephemeral Storage Stores**: Three disconnected artifact store implementations coexisted. The custom HTTP PUT blob store did not use the official Vercel SDK, and serverless invocations depended on ephemeral local disk.
6. **Frontend Hooks Violation**: `ArtifactModal.tsx` contained an early return before `useState` calls, violating React Rules of Hooks.

---

## 2. Decision: Unified Capability & Artifact Architecture

### Decision 1: Single Capability Request & Result Contracts

Every capability (grounded chat, research, presentation deck, recipe card, calendar sync, etiquette flowchart, dialect lore, and oral history) will execute through a single typed contract:

```python
@dataclass(frozen=True)
class CapabilityRequest:
    intent_id: str
    capability_id: str
    locale: str  # 'ar' | 'en'
    query: str
    attachments: tuple[ResolvedAttachment, ...]
    deadline_monotonic: float
    stage_callback: Optional[Callable[[str, str], None]] = None
    target_formats: tuple[str, ...] = ("text",)
    session_id: Optional[str] = None
    parameters: dict[str, Any] = field(default_factory=dict)

@dataclass
class CapabilityResult:
    ok: bool
    status: str  # 'success' | 'partial' | 'failed' | 'timeout' | 'capability_unavailable'
    public_text: str
    citations: list[dict[str, Any]]
    artifacts: list[ArtifactRecord]
    structured_payload: dict[str, Any]
    timings: dict[str, float]
    warnings: list[str]
    error_message: str = ""
    error_category: Optional[str] = None
    provider_attribution: Optional[str] = None
```

### Decision 2: Canonical `ArtifactRecord` Specification

All renderers and tool handlers must emit the standard `ArtifactRecord`:

```python
@dataclass
class ArtifactRecord:
    artifact_id: str
    kind: str       # 'document' | 'presentation' | 'calendar' | 'diagram' | 'card' | 'audio' | 'image'
    format: str     # 'pdf' | 'docx' | 'pptx' | 'ics' | 'svg' | 'png' | 'json' | 'csv' | 'txt'
    filename: str   # sanitized, human-readable
    mime_type: str  # verified canonical MIME
    size_bytes: int # non-zero for created artifacts
    checksum: str   # SHA-256
    status: str     # 'created' | 'failed' | 'skipped'
    storage_key: str
    delivery_url: Optional[str]
    preview: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    error_category: Optional[str] = None
```

**Non-negotiable rule**: An artifact with `status="created"` MUST have `size_bytes > 0`, a valid `delivery_url`, a valid `checksum`, and existing bytes in the active store. Failed artifacts must report `status="failed"` with actionable `error_category`.

### Decision 3: Storage Layer Migration to Official Vercel SDK

1. Deprecate and remove custom manual PUT adapter `ConfigurableBlobArtifactStore`.
2. Implement `VercelBlobArtifactStore` using the official Vercel Python SDK:
   - `from vercel.blob import BlobClient, put, head, get, delete`
   - Read `BLOB_READ_WRITE_TOKEN`.
   - Store blobs using `put(pathname, body, options={"access": "public", "contentType": mime_type})`.
   - Retain the official provider-returned `downloadUrl` and metadata.
3. For local development, tests, and when `BLOB_READ_WRITE_TOKEN` is unset:
   - Use `FileSystemArtifactStore` rooted in `output/artifacts`.
   - Atomic writes with `.tmp` staging, `os.fsync`, and atomic rename.
   - Metadata persistence in `.artifact-metadata/` sidecars.
4. Serverless (`VERCEL=1`):
   - When `BLOB_READ_WRITE_TOKEN` is configured: use Vercel Blob store.
   - If unconfigured in production: log prominent warning and return truthful degraded status (`storage="ephemeral_unconfigured"`).

### Decision 4: Deadlines, Concurrency, and Cancellation

1. **Monotonic Budget**:
   - Backend SSE timeout default: 30.0s (maximum 45.0s).
   - Frontend abort controller timeout: 50.0s (guaranteeing at least 15s of grace period after backend deadline so terminal `artifacts` and `done` events always arrive).
   - Never use `max(2.0, remaining)` when `remaining <= 0`. If monotonic deadline is reached, immediately terminate execution and emit truthful timeout events.
2. **Eliminate `ThreadPoolExecutor` Context Manager Trap**:
   - Replace synchronous `with ThreadPoolExecutor` with non-blocking async invocation (`ainvoke`) or a shared background executor where `future.result(timeout=...)` does not trigger blocking shutdown upon timeout.
   - If timeout occurs:
     - Cancel the future.
     - Return `ok=False`, `error_category="timeout"`.
     - Reject/discard any subsequent worker output so orphan files are never stored or published.
3. **Single Model Factory**:
   - Clean up `_build_nvidia` in `sard/config/models.py`: remove invalid `max_retries` parameter from `ChatNVIDIA` kwargs to silence library warnings.

### Decision 5: Honest Attachment & Multimodal Semantics

1. **Attachment Resolution**:
   - `/api/chat` takes `attachments: [{"attachment_id": ...}]`.
   - Looks up `attachment_id` in attachment store.
   - Verifies file exists on disk, matches MIME type, and reads bytes.
   - Extracts text content (via PyMuPDF for PDF, utf-8 decoding for text/json).
   - Injects extracted text into the query context for retrieval and LLM synthesis.
2. **No Fabricated Fallbacks**:
   - If `DASHSCOPE_API_KEY` is missing or ASR/OCR fails:
     - Return honest status: `status="capability_unavailable"`.
     - Provide offline metadata (duration, format, channels for audio; dimensions for image).
     - NEVER return hardcoded transcripts (e.g. Al-Ahsa elder interviews) or fake OCR text.

### Decision 6: Frontend Hook and UI Hardening

1. In `src/components/ArtifactModal.tsx`:
   - Move all React hooks (`useState`, `useEffect`) to the top of the component before any conditional early return.
2. In `src/app/page.tsx`:
   - Increase client abort timer from 40s to 50s.
   - Handle timeout, partial artifacts, and retry states cleanly.
3. In `sard/api/server.py`:
   - `/api/status`: return honest `status_label` ("جاهز" if ready, "تدهور في الاسترجاع" if degraded, "غير مهيأ" if unconfigured).

---

## 3. Implementation Phases & Swarm Workstreams

- **Workstream 1 (Runtime & Cancellation)**: `sard/agent/chat_service.py`, `sard/config/models.py`, `tests/model_session/test_fallback.py`.
- **Workstream 2 (Artifact & Deployment)**: `sard/outputs/orchestrator.py`, Vercel Blob SDK integration, `vercel.json`.
- **Workstream 3 (Attachments & Multimodal)**: `sard/api/server.py`, `sard/agent/tools/multimodal_tools.py`, attachment resolution and canary tests.
- **Workstream 4 (Cultural Tools & RAG Truth)**: `sard/agent/tools/cultural_agentic_tools.py`, `/api/status`, removal of silent defaults.
- **Workstream 5 (Frontend Reliability)**: `src/components/ArtifactModal.tsx`, `src/app/page.tsx`, timeout coordination.
- **Workstream 6 (End-to-End Verification)**: Test matrix, full test suite execution, final report.
