import { Citation, Artifact, Attachment, SystemStatus } from "@/types";
import { PersistentSSEParser, SSEEvent } from "./sseParser";

const API_BASE = "";

export async function fetchSystemStatus(): Promise<SystemStatus | null> {
  try {
    const res = await fetch(`${API_BASE}/api/status`);
    if (!res.ok) throw new Error(`Status HTTP ${res.status}`);
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
    let errorDetail = `Upload failed (${response.status})`;
    try {
      const errJson = await response.json();
      if (errJson.detail) errorDetail = errJson.detail;
    } catch {
      const errText = await response.text();
      if (errText) errorDetail = errText;
    }
    throw new Error(errorDetail);
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
  signal?: AbortSignal;
  onStatus?: (statusText: string) => void;
  onCitations?: (citations: Citation[]) => void;
  onDelta?: (deltaText: string) => void;
  onArtifacts?: (artifacts: Artifact[]) => void;
  onDone?: (meta: { verified?: boolean; sources_count?: number; timings_ms?: { total_ms?: number }; updated_at?: string; artifacts_count?: number }) => void;
  onError?: (error: Error) => void;
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
    signal,
    onStatus,
    onCitations,
    onDelta,
    onArtifacts,
    onDone,
    onError,
  } = options;

  // Client-side dedup and order tracking
  const seenArtifactIds = new Set<string>();
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
        }),
        signal,
      });
      return response;
    } catch (err: any) {
      // Network error could be backend restart; retry once with backoff if not aborted
      if (attempt < 1 && err.name !== "AbortError" && signal?.aborted !== true) {
        if (onStatus) onStatus("جاري إعادة الاتصال بالخادم...");
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
        if (onStatus) onStatus("الخادم يعيد التشغيل، جارٍ إعادة المحاولة...");
        await new Promise((r) => setTimeout(r, 900));
        const retryResp = await fetchWithRetry(1);
        if (!retryResp.ok) {
          let errDetail = `Server error (${retryResp.status})`;
          try {
            const errJson = await retryResp.json();
            if (errJson.detail) errDetail = errJson.detail;
          } catch {
            const errText = await retryResp.text();
            if (errText) errDetail = errText;
          }
          throw new Error(errDetail);
        }
        // Use retry response if successful (fallthrough by reassigning)
        // We need to handle retryResp stream instead
        if (!retryResp.body) throw new Error("No response body received from server");
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
            if (event === "status" && data.message && onStatus) onStatus(data.message);
            else if (event === "citations" && data.citations && onCitations) onCitations(data.citations);
            else if (event === "artifacts" && data.artifacts && onArtifacts) {
              // Deduplicate artifacts by id to handle duplicate delivery
              const incoming: Artifact[] = Array.isArray(data.artifacts) ? data.artifacts : [];
              const deduped = incoming.filter((a) => {
                const key = a.id || a.filename;
                if (seenArtifactIds.has(key)) return false;
                seenArtifactIds.add(key);
                return true;
              });
              // We still pass full deduplicated list but accumulate seen ids
              const all = deduplicateArtifacts(incoming);
              onArtifacts(all);
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
      let errDetail = `Server error (${response.status})`;
      try {
        const errJson = await response.json();
        if (errJson.detail) errDetail = errJson.detail;
      } catch {
        const errText = await response.text();
        if (errText) errDetail = errText;
      }
      throw new Error(errDetail);
    }

    if (!response.body) {
      throw new Error("No response body received from server");
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
          if (data.message && onStatus) {
            onStatus(data.message);
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
            // Deduplicate and preserve failed artifacts (download_url null must remain)
            const incoming: Artifact[] = Array.isArray(data.artifacts) ? data.artifacts : [];
            // Track seen ids for duplicate delivery detection
            const filtered = incoming.filter((a) => {
              const key = a.id || `${a.filename}__${a.format}`;
              if (seenArtifactIds.has(key)) return false;
              seenArtifactIds.add(key);
              return true;
            });
            // If all were duplicates, still emit deduplicated full list to avoid empty emission on retry
            const toEmit = filtered.length > 0 ? deduplicateArtifacts(incoming) : deduplicateArtifacts(incoming);
            // Validate failed artifacts have no download_url
            for (const art of toEmit) {
              if (art.status === "failed" && art.download_url) {
                console.warn("[SSE] Invalid artifact: failed artifact has download_url", art);
                art.download_url = null;
              }
              if (art.status === "created" && !art.download_url) {
                console.warn("[SSE] Invalid artifact: created artifact missing download_url", art);
              }
            }
            onArtifacts(toEmit);
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
