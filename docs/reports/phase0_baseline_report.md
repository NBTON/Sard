# Phase 0 Baseline Reproduction & Defect Evidence Report

**Project**: Sard Cultural Agent  
**Commit**: `da11050bd955ff6146da2669334774ab0baa79d1`  
**Date**: September 4, 2026  
**Environment**: Windows 11, Node `v24.19.0`, Python `3.11.15`, uv `0.12.7`  

---

## 1. System Environment & Lock State

- **Node Version**: `v24.19.0`
- **npm Version**: `11.5.0`
- **Python Version**: `3.11.15` (MSC v.1944 64 bit)
- **Frontend Build**: `npm run build` completed successfully (Next.js 15.1.6 App Router).
- **Environment Variables Detected**:
  - `MODEL_PROVIDER=nvidia`
  - `NVIDIA_API_KEY` (present in local `.env`)
  - `NVIDIA_CHAT_MODEL_PRIMARY=nvidia/nemotron-3-ultra-550b-a55b`
  - `PARALLEL_API_KEY` (present in local `.env`)
  - `BLOB_READ_WRITE_TOKEN` (missing / unconfigured)
  - `DASHSCOPE_API_KEY` (missing / unconfigured)

---

## 2. Key Defects Reproduced

### Defect A: React Rules of Hooks Violation
- **Location**: `src/components/ArtifactModal.tsx:12`
- **Evidence**:
  ```typescript
  export function ArtifactModal({ artifact, onClose, lang }: ArtifactModalProps) {
    if (!artifact) return null; // Early return violates Rules of Hooks
    ...
    const [currentSlideIdx, setCurrentSlideIdx] = useState(0);
    const [cardTheme, setCardTheme] = useState<string>("dark_gold");
  ```
  Calling `useState` after a conditional return triggers React runtime error when `artifact` toggles between null and object.

### Defect B: Backend/Frontend Timeout Race & Orphan Generation
- **Location**: `src/app/page.tsx:114` (Client 40s abort) vs `sard/api/server.py:748` (Backend 38s + `max(2.0, remaining)`)
- **Evidence**:
  - Client has a hard abort timer: `setTimeout(() => controller.abort(), 40000)`.
  - Backend has `overall_timeout = 38.0s`, and on fallback calculates:
    `remaining = max(2.0, (t_start + overall_timeout) - time.monotonic())`.
    When `remaining` is depleted at 38.0s, backend adds 2.0s more (reaching 40.0s), racing the exact millisecond the frontend aborts the HTTP connection.
  - Furthermore, Python threads cannot be cancelled by `future.cancel()`. A running worker continues executing after the HTTP connection drops and outputs orphan files (e.g. 46KB PDF) to disk minutes later.

### Defect C: `ThreadPoolExecutor` Context Manager Timeout Trap
- **Location**: `sard/agent/chat_service.py:431`
- **Evidence**:
  ```python
  with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
      future = executor.submit(model.invoke, lc_messages)
      response = future.result(timeout=6.0)
  ```
  In Python, exiting `with ThreadPoolExecutor` calls `executor.shutdown(wait=True)`.
  - **Reproduction Result**: When worker ran for 1.50s and timeout was set to 0.1s, the `with` block took **1.50s** to return instead of 0.1s.

### Defect D: Model Timeout Classified as `ok=True`
- **Location**: `sard/agent/chat_service.py:441`
- **Evidence**:
  - `pytest tests/model_session/test_fallback.py::test_timeout_classified_and_not_leaking` **FAILED**:
    `assert True is False where True = ChatResult(ok=True, text='تعذّر توليد إجابة موثقة عن: ...', ...).ok`.
    A timed-out request is falsely marked `ok=True`.

### Defect E: Attachment Data Black Hole
- **Location**: `/api/chat` and `sard/agent/chat_service.py:169`
- **Evidence**:
  - Canary file with nonce `SARD_CANARY_NONCE_ABC987654321` uploaded to `/api/upload` (200 OK, stored in `output/uploads/att_fab3b641adf7_canary.pdf`).
  - Sent `/api/chat` with attachment ID reference.
  - Result: Canary text was completely missing from the chat model's input and SSE response (`present in response: False`). Attachments were only used for modality tag classification and never read from disk.

### Defect F: Fabricated Fallbacks on Missing Keys
- **Location**: `sard/agent/tools/multimodal_tools.py:250-450`
- **Evidence**:
  - Calling `qwen_audio_transcribe` without `DASHSCOPE_API_KEY` returns a completely fabricated 2-speaker Al-Ahsa transcript mentioning "عين الحارة" and "واحة الأحساء" with `status: "offline_extracted"`.
  - Calling `qwen_vl_vision_analyze` without `DASHSCOPE_API_KEY` returns a synthetic "cultural_artifact" description.

### Defect G: Multi-Process Ephemeral Storage Non-Durability
- **Location**: `sard/outputs/orchestrator.py:FileSystemArtifactStore`
- **Evidence**:
  - Storing an artifact in serverless instance 1 makes it completely inaccessible to instance 2 (`exists_in_store2 == False`).
  - The custom `ConfigurableBlobArtifactStore` uses manual PUT requests and custom indexing rather than the official Vercel Blob Python SDK (`vercel.blob`).

### Defect H: False System Status
- **Location**: `sard/api/server.py:241`
- **Evidence**:
  - `GET /api/status` returns `"status_label": "جاهز"` unconditionally, masking degraded or unconfigured RAG states.

### Defect I: ChatNVIDIA max_retries Warning
- **Location**: `sard/config/models.py:194`
- **Evidence**:
  - Emits `UserWarning: WARNING! max_retries is not default parameter. max_retries was transferred to model_kwargs.`

---

## 3. Baseline Metrics Summary Table

| Format / Path | Query / Action | Elapsed (s) | Events Observed | Artifact Stored | Status |
|---|---|---|---|---|---|
| **Status API** | `GET /api/status` | 0.05s | JSON response | N/A | Hardcoded "جاهز" |
| **Canary PDF** | `POST /api/upload` + `/api/chat` | 8.27s | `status`, `done` | N/A | Content Ignored |
| **PDF** | "أنشئ لي ملف PDF عن تاريخ نجد" | 30.30s | `status`, `artifacts`, `done` | 46,861 B PDF | Success (Slow) |
| **DOCX** | "أنشئ لي مستند DOCX عن العمارة العسيرية" | 3.06s | `status`, `artifacts`, `done` | 5,447 B DOCX | Success |
| **PPTX** | "أعد عرضاً تقديمياً PPTX عن يوم التأسيس" | 34.84s | `status`, `artifacts`, `done` | 33,347 B PPTX | Success (Near-Timeout) |
| **ICS** | "زامن فعاليات يوم التأسيس في تقويم ics" | 30.76s | `status`, `artifacts`, `done` | 3,894 B ICS | Success (Near-Timeout) |
| **Grounded Chat** | "ما هي أبرز مواقع التراث في العلا؟" | 2.59s | `status`, `done` | N/A | Success |
