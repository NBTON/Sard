import { Citation, Artifact, Attachment, SystemStatus, normalizeArtifactsList } from "@/types";
import { PersistentSSEParser, SSEEvent } from "./sseParser";

const API_BASE =
  (typeof process !== "undefined" &&
    (process.env.NEXT_PUBLIC_API_BASE || process.env.SARD_BACKEND_ORIGIN || "")) ||
  "";

export type SardErrorCode =
  | "http_error"
  | "timeout"
  | "aborted"
  | "no_body"
  | "stream_interrupted"
  | "validation"
  | "partial"
  | "unknown";

export class SardApiError extends Error {
  code: SardErrorCode;
  status?: number;
  category?: string;
  constructor(message: string, opts?: { code?: SardErrorCode; status?: number; category?: string }) {
    super(message);
    this.name = "SardApiError";
    this.code = opts?.code || "http_error";
    this.status = opts?.status;
    this.category = opts?.category;
  }
}

/** Read an error response body exactly once (text), then try JSON parse. */
async function readErrorDetail(response: Response, fallback: string): Promise<{ detail: string; category?: string }> {
  let raw = "";
  try {
    raw = await response.text();
  } catch {
    return { detail: fallback };
  }
  if (!raw) return { detail: fallback };
  try {
    const parsed = JSON.parse(raw);
    if (typeof parsed?.detail === "string" && parsed.detail) {
      return { detail: parsed.detail, category: parsed?.error_category };
    }
    if (typeof parsed?.error === "string" && parsed.error) {
      return { detail: parsed.error, category: parsed?.error_category };
    }
  } catch {
    // raw is plain text
  }
  return { detail: raw.slice(0, 500) || fallback };
}

export async function fetchSystemStatus(): Promise<SystemStatus | null> {
  try {
    const res = await fetch(`${API_BASE}/api/status`);
    if (!res.ok) throw new SardApiError(`Status HTTP ${res.status}`, { code: "http_error", status: res.status });
    return await res.json();
  } catch (err) {
    console.warn("Could not fetch backend system status:", err);
    return null;
  }
}

export async function uploadAttachment(file: File): Promise<Attachment> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`${API_BASE}/api/upload`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const { detail, category } = await readErrorDetail(response, `Upload failed (${response.status})`);
    throw new SardApiError(detail, { code: "http_error", status: response.status, category });
  }

  const data = await response.json();
  return {
    id: data.attachment_id,
    attachment_id: data.attachment_id,
    filename: data.filename,
    mime_type: data.mime_type,
    size_bytes: data.size_bytes,
    url: data.url,
  };
}

/** Structured progress detail for a backend `status` event. */
export interface StatusDetail {
  stage: string;
  /** 0-1 progress. Backend may omit it — then derived from the stage name. */
  progress: number;
  message: string;
  artifactStatus?: string | null;
  runId?: string | null;
  raw?: unknown;
}

export interface DoneMeta {
  verified?: boolean;
  sources_count?: number;
  timings_ms?: { total_ms?: number };
  updated_at?: string;
  artifacts_count?: number;
  run_id?: string | null;
  session_id?: string | null;
  versions?: unknown;
}

export interface StreamChatOptions {
  messages: Array<{
    role: string;
    content: string;
    attachments?: Array<{ attachment_id: string; filename: string; mime_type?: string; size_bytes?: number }>;
  }>;
  query?: string;
  attachments?: Attachment[];
  sessionId?: string;
  itineraryMode?: boolean;
  lang?: string;
  signal?: AbortSignal;
  onStatus?: (statusText: string, detail?: StatusDetail) => void;
  onCitations?: (citations: Citation[]) => void;
  onDelta?: (deltaText: string) => void;
  onArtifacts?: (artifacts: Artifact[]) => void;
  onDone?: (meta: DoneMeta) => void;
  onError?: (error: Error) => void;
}

/**
 * Backend stage → progress estimate (0-1). Used when the backend omits
 * `progress` (current contract sends {stage, message} only — forward
 * compatible with a future {stage, progress, artifact_status, run_id} shape).
 */
export function stageProgress(stage: string): number {
  const s = (stage || "").toLowerCase().trim();
  if (!s) return 0.05;
  if (s === "init" || s === "start" || s === "started") return 0.05;
  if (s.includes("understand")) return 0.15;
  if (s.includes("plan")) return 0.25;
  if (s.includes("retriev") || s.includes("search") || s.includes("isnad")) return 0.35;
  if (s.includes("compose") || s.includes("draft")) return 0.5;
  if (s.includes("verif")) return 0.62;
  if (s.includes("render") || s.includes("artifact")) return 0.74;
  if (s.includes("generat") || s.includes("compos") || s.includes("writ")) return 0.85;
  if (s.includes("fallback") || s.includes("direct")) return 0.88;
  if (s.includes("done") || s.includes("complet") || s.includes("finish")) return 1;
  return 0.4;
}

/** Parse a raw `status` payload tolerantly (old {message} and new {stage,progress,...}). */
export function parseStatusDetail(data: any, fallbackMessage: string): StatusDetail {
  const stage =
    (typeof data?.stage === "string" && data.stage) ||
    (typeof data?.status === "string" && data.status) ||
    "working";
  const message =
    (typeof data?.message === "string" && data.message) || fallbackMessage;
  const rawProgress = data?.progress;
  const progress =
    typeof rawProgress === "number" && Number.isFinite(rawProgress)
      ? Math.min(1, Math.max(0, rawProgress))
      : stageProgress(stage);
  const artifactStatus =
    (typeof data?.artifact_status === "string" && data.artifact_status) || null;
  const runId =
    (typeof data?.run_id === "string" && data.run_id) ||
    (typeof data?.runId === "string" && data.runId) ||
    null;
  return { stage, progress, message, artifactStatus, runId, raw: data };
}

/**
 * Normalize one raw `artifacts` payload into canonical Artifacts.
 * Enforces: failed ⇒ download_url null; created ⇒ warn when URL missing.
 */
export function normalizeIncomingArtifacts(input: unknown): Artifact[] {
  const list = normalizeArtifactsList(Array.isArray(input) ? input : []);
  for (const art of list) {
    if (art.status === "failed" && art.download_url) {
      console.warn("[SSE] Invalid artifact: failed artifact has download_url", art);
      art.download_url = null;
    }
    if (art.status === "created" && !art.download_url) {
      console.warn("[SSE] Invalid artifact: created artifact missing download_url", art);
    }
  }
  return list;
}

/**
 * Best-effort poll of a run (retry/resume). Returns null when the
 * backend has no GET /api/runs/:id endpoint (404) or is unreachable —
 * callers must fall back to re-sending the prompt.
 */
export async function fetchRunStatus(runId: string): Promise<{
  run_id: string;
  status?: string;
  artifacts?: Artifact[];
  [key: string]: unknown;
} | null> {
  if (!runId) return null;
  try {
    const res = await fetch(`${API_BASE}/api/runs/${encodeURIComponent(runId)}`, {
      headers: { Accept: "application/json" },
    });
    if (res.status === 404 || res.status === 405) return null;
    if (!res.ok) return null;
    const data = await res.json();
    if (data && Array.isArray((data as any).artifacts)) {
      (data as any).artifacts = normalizeIncomingArtifacts((data as any).artifacts);
    }
    return { run_id: runId, ...(data as object) };
  } catch {
    return null;
  }
}

/**
 * Controlled download: fetch → blob → object URL → a[download].
 * Never uses a bare cross-origin <a download target=_blank> (unreliable).
 * Throws SardApiError("expired" category on 404/410, "http_error" otherwise).
 */
export async function downloadArtifactFile(
  url: string,
  filename: string
): Promise<{ objectUrl: string; blob: Blob }> {
  let response: Response;
  try {
    response = await fetch(url, { credentials: "same-origin" });
  } catch (err: any) {
    throw new SardApiError(err?.message || "Network error during download", {
      code: "http_error",
    });
  }
  if (response.status === 404 || response.status === 410) {
    throw new SardApiError(
      "انتهت صلاحية رابط التحميل. أعد توليد المخرج ثم حاول مجدداً.",
      { code: "http_error", status: response.status, category: "expired" }
    );
  }
  if (!response.ok) {
    const { detail, category } = await readErrorDetail(response, `Download failed (${response.status})`);
    throw new SardApiError(detail, { code: "http_error", status: response.status, category });
  }
  let blob: Blob;
  try {
    blob = await response.blob();
  } catch {
    throw new SardApiError("تعذّر قراءة الملف المحمّل.", { code: "http_error", category: "blob_error" });
  }
  if (!blob || blob.size === 0) {
    throw new SardApiError("الملف المحمّل فارغ.", { code: "http_error", category: "blob_error" });
  }
  const objectUrl = URL.createObjectURL(blob);
  // Trigger via a temporary anchor so the filename is honored same-origin.
  if (typeof document !== "undefined") {
    const a = document.createElement("a");
    a.href = objectUrl;
    a.download = filename || "sard-artifact";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
  return { objectUrl, blob };
}

export const SSE_ORDER = ["status", "citations", "artifacts", "delta", "done"] as const;
export type SSEOrderPhase = (typeof SSE_ORDER)[number];

export function isValidSSEOrder(events: string[]): { valid: boolean; violation?: string } {
  const orderIndex = new Map<string, number>(SSE_ORDER.map((e, i) => [e, i]));
  let lastIdx = -1;
  for (const ev of events) {
    // Allow multiple status/delta/citations in any order before done, but enforce artifacts before done, status before citations/artifacts
    // The strict contract is: status (0) -> citations (1) -> artifacts (2) -> delta (3) -> done (4)
    // Frontend should tolerate interleaved status/delta but must ensure done is last and artifacts before done.
    const idx = orderIndex.get(ev) ?? -1;
    if (idx === -1) continue;
    // done must be last; anything after done is violation
    if (lastIdx === 4 && idx !== 4) {
      return { valid: false, violation: `event ${ev} after done` };
    }
    // artifacts must precede done/delta completion - but we relax to only ensure artifacts before done if both present
    // So we only flag if done appears before artifacts when artifacts expected
    if (ev === "done" && events.includes("artifacts") && !events.slice(0, events.indexOf("done")).includes("artifacts")) {
      return { valid: false, violation: "done before artifacts" };
    }
    lastIdx = Math.max(lastIdx, idx);
  }
  // If both artifacts and done present, ensure ordering
  if (events.includes("artifacts") && events.includes("done")) {
    if (events.indexOf("artifacts") > events.indexOf("done")) {
      return { valid: false, violation: "artifacts after done" };
    }
  }
  return { valid: true };
}

export function deduplicateArtifacts(artifacts: Artifact[]): Artifact[] {
  if (!Array.isArray(artifacts)) return [];
  const seen = new Map<string, Artifact>();
  for (const art of artifacts) {
    const key = art.id || `${art.filename}__${art.format}`;
    if (!seen.has(key)) seen.set(key, art);
  }
  return Array.from(seen.values());
}

export function getUniqueDisplayNames(artifacts: Artifact[]): Map<string, string> {
  const counts = new Map<string, number>();
  const result = new Map<string, string>();
  for (const art of artifacts) {
    const base = art.filename || art.title || "artifact";
    const cnt = (counts.get(base) || 0) + 1;
    counts.set(base, cnt);
    if (cnt === 1) {
      result.set(art.id, base);
    } else {
      // Append counter before extension
      const dotIdx = base.lastIndexOf(".");
      if (dotIdx > 0) {
        const name = base.slice(0, dotIdx);
        const ext = base.slice(dotIdx);
        result.set(art.id, `${name} (${cnt})${ext}`);
      } else {
        result.set(art.id, `${base} (${cnt})`);
      }
    }
  }
  // Second pass to fix first duplicate naming (e.g., two files named same -> first should be "name (1)")
  // For strict uniqueness, if any duplicate exists, rename even the first occurrence.
  const duplicates = new Set<string>();
  const freq = new Map<string, number>();
  for (const art of artifacts) {
    const base = art.filename || art.title || "artifact";
    freq.set(base, (freq.get(base) || 0) + 1);
  }
  for (const [base, f] of freq) if (f > 1) duplicates.add(base);
  if (duplicates.size > 0) {
    const dupCounters = new Map<string, number>();
    for (const art of artifacts) {
      const base = art.filename || art.title || "artifact";
      if (duplicates.has(base)) {
        const c = (dupCounters.get(base) || 0) + 1;
        dupCounters.set(base, c);
        const dotIdx = base.lastIndexOf(".");
        if (dotIdx > 0) {
          const name = base.slice(0, dotIdx);
          const ext = base.slice(dotIdx);
          result.set(art.id, `${name} (${c})${ext}`);
        } else {
          result.set(art.id, `${base} (${c})`);
        }
      }
    }
  }
  return result;
}

export async function streamChat(options: StreamChatOptions): Promise<void> {
  const {
    messages,
    query,
    attachments,
    sessionId,
    itineraryMode = false,
    lang,
    signal,
    onStatus,
    onCitations,
    onDelta,
    onArtifacts,
    onDone,
    onError,
  } = options;

  // Order tracking + run_id capture (first status event wins for resume).
  const seenRunIds = new Set<string>();
  const eventOrder: string[] = [];

  const fetchWithRetry = async (attempt = 0): Promise<Response> => {
    const formattedAttachments = attachments?.map((a) => ({
      attachment_id: a.attachment_id,
      filename: a.filename,
      mime_type: a.mime_type,
      size_bytes: a.size_bytes,
    }));

    try {
      const response = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        },
        body: JSON.stringify({
          messages,
          query,
          session_id: sessionId,
          itinerary_mode: itineraryMode,
          attachments: formattedAttachments,
          lang: lang || undefined,
          locale: lang || undefined,
        }),
        signal,
      });
      return response;
    } catch (err: any) {
      // Network error could be backend restart; retry once with backoff if not aborted
      if (attempt < 1 && err.name !== "AbortError" && signal?.aborted !== true) {
        if (onStatus) {
          const msg = "جاري إعادة الاتصال بالخادم...";
          onStatus(msg, { stage: "reconnect", progress: 0.02, message: msg });
        }
        await new Promise((r) => setTimeout(r, 800));
        return fetchWithRetry(attempt + 1);
      }
      throw err;
    }
  };

  try {
    const response = await fetchWithRetry(0);

    if (!response.ok) {
      // Retry once for 502/503/504 (backend restart) unless aborted
      if ([502, 503, 504].includes(response.status) && signal?.aborted !== true) {
        if (onStatus) {
          const msg = "الخادم يعيد التشغيل، جارٍ إعادة المحاولة...";
          onStatus(msg, { stage: "reconnect", progress: 0.02, message: msg });
        }
        await new Promise((r) => setTimeout(r, 900));
        const retryResp = await fetchWithRetry(1);
        if (!retryResp.ok) {
          const { detail, category } = await readErrorDetail(retryResp, `Server error (${retryResp.status})`);
          throw new SardApiError(detail, { code: "http_error", status: retryResp.status, category });
        }
        // Use retry response if successful (fallthrough by reassigning)
        // We need to handle retryResp stream instead
        if (!retryResp.body) throw new SardApiError("No response body received from server", { code: "no_body" });
        // Continue to streaming with retryResp (duplicate code path handled below via goto-like)
        // To avoid duplication, throw to outer catch if retry needed more logic; instead we handle streaming from retryResp
        // We do streaming for retryResp here
        const retryReader = retryResp.body.getReader();
        const retryDecoder = new TextDecoder("utf-8");
        const retryParser = new PersistentSSEParser();
        const handleRetryEvent = (ev: SSEEvent) => {
          const { event, data: rawData } = ev;
          if (!rawData) return;
          eventOrder.push(event);
          if (event === "delta") {
            try {
              const parsed = JSON.parse(rawData);
              if (parsed.text !== undefined && onDelta) onDelta(parsed.text);
            } catch {
              if (onDelta) onDelta(rawData);
            }
            return;
          }
          try {
            const data = JSON.parse(rawData);
            if (event === "status" && onStatus) {
              const detail = parseStatusDetail(data, data.message);
              if (detail.runId) seenRunIds.add(detail.runId);
              onStatus(detail.message, detail);
            } else if (event === "citations" && data.citations && onCitations) onCitations(data.citations);
            else if (event === "artifacts" && data.artifacts && onArtifacts) {
              // Canonical normalization (legacy aliases resolved here).
              // Version accumulation happens in the consumer via mergeArtifactVersions.
              onArtifacts(normalizeIncomingArtifacts(data.artifacts));
            } else if (event === "done" && onDone) onDone(data);
          } catch (parseErr) {
            console.warn(`[SSE] Failed to parse JSON for event "${event}":`, parseErr, rawData);
          }
        };
        while (true) {
          if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
          const { done, value } = await retryReader.read();
          if (done) break;
          const chunk = retryDecoder.decode(value, { stream: true });
          retryParser.feed(chunk, handleRetryEvent);
        }
        retryParser.flush(handleRetryEvent);
        const ordCheck = isValidSSEOrder(eventOrder);
        if (!ordCheck.valid) console.warn(`[SSE] Order violation: ${ordCheck.violation}`, eventOrder);
        return;
      }
      const { detail, category } = await readErrorDetail(response, `Server error (${response.status})`);
      throw new SardApiError(detail, { code: "http_error", status: response.status, category });
    }

    if (!response.body) {
      throw new SardApiError("No response body received from server", { code: "no_body" });
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    const sseParser = new PersistentSSEParser();

    const handleEvent = (ev: SSEEvent) => {
      const { event, data: rawData } = ev;
      if (!rawData) return;
      eventOrder.push(event);

      if (event === "delta") {
        try {
          const parsed = JSON.parse(rawData);
          if (parsed.text !== undefined && onDelta) {
            onDelta(parsed.text);
          }
        } catch {
          // If raw delta is unquoted string
          if (onDelta) onDelta(rawData);
        }
        return;
      }

      // Metadata events: status, citations, artifacts, done, error
      try {
        const data = JSON.parse(rawData);

        if (event === "status") {
          if (onStatus) {
            const detail = parseStatusDetail(
              data,
              data.message || (lang === "en" ? "Working..." : "جارٍ العمل...")
            );
            if (detail.runId) seenRunIds.add(detail.runId);
            if (detail.message) onStatus(detail.message, detail);
          }
        } else if (event === "citations") {
          if (data.citations && onCitations) {
            // Deduplicate citations by citation_id/source_url before emitting
            const incoming: Citation[] = Array.isArray(data.citations) ? data.citations : [];
            const seenC = new Set<string>();
            const deduped = incoming.filter((c) => {
              const key = c.citation_id || c.source_url || c.title || "";
              if (seenC.has(key)) return false;
              seenC.add(key);
              return true;
            });
            onCitations(deduped);
          }
        } else if (event === "artifacts") {
          if (data.artifacts && onArtifacts) {
            // Canonical normalization at the parse boundary (legacy aliases
            // resolved here). The consumer ACCUMULATES via mergeArtifactVersions
            // — same-id re-delivery becomes a new version snapshot, never a drop.
            onArtifacts(normalizeIncomingArtifacts(data.artifacts));
          }
        } else if (event === "done") {
          if (onDone) {
            onDone(data);
          }
        } else if (event === "error") {
          // Backend signalled error/cancellation - surface via onError but still allow done in finally
          console.warn("[SSE] error event:", data);
          if (data.cancelled) {
            // Cancellation is not an error to surface to user; just stop
            return;
          }
          if (onError) onError(new Error(data.error || data.detail || "Stream error"));
        }
      } catch (parseErr) {
        console.warn(`[SSE] Failed to parse JSON for event "${event}":`, parseErr, rawData);
      }
    };

    let streamInterrupted = false;
    try {
      while (true) {
        if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });
        sseParser.feed(chunk, handleEvent);
      }
    } catch (readErr: any) {
      if (readErr.name === "AbortError") throw readErr;
      console.warn("[SSE] Stream interrupted:", readErr);
      streamInterrupted = true;
      // Attempt to recover by flushing what we have, then surface error
      try { sseParser.flush(handleEvent); } catch {}
      if (onError) onError(new Error(`Stream interrupted: ${readErr.message || readErr}`));
    }

    // Flush any pending trailing data
    sseParser.flush(handleEvent);

    // Final order validation
    const ordCheck = isValidSSEOrder(eventOrder);
    if (!ordCheck.valid) {
      console.warn(`[SSE] Order violation: ${ordCheck.violation}`, eventOrder);
    }

    // If stream was interrupted and no done was seen, ensure consumer knows it's incomplete
    if (streamInterrupted && !eventOrder.includes("done")) {
      console.warn("[SSE] Stream ended without done event (interrupted)");
    }

  } catch (err: any) {
    if (err.name === "AbortError") {
      console.log("Chat stream aborted by user.");
      return;
    }
    console.error("Stream chat error:", err);
    if (onError) onError(err);
  }
}
