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

  try {
    const formattedAttachments = attachments?.map((a) => ({
      attachment_id: a.attachment_id,
      filename: a.filename,
      mime_type: a.mime_type,
      size_bytes: a.size_bytes,
    }));

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

    if (!response.ok) {
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

      // Metadata events: status, citations, artifacts, done
      try {
        const data = JSON.parse(rawData);

        if (event === "status") {
          if (data.message && onStatus) {
            onStatus(data.message);
          }
        } else if (event === "citations") {
          if (data.citations && onCitations) {
            onCitations(data.citations);
          }
        } else if (event === "artifacts") {
          if (data.artifacts && onArtifacts) {
            onArtifacts(data.artifacts);
          }
        } else if (event === "done") {
          if (onDone) {
            onDone(data);
          }
        }
      } catch (parseErr) {
        console.warn(`[SSE] Failed to parse JSON for event "${event}":`, parseErr, rawData);
      }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value, { stream: true });
      sseParser.feed(chunk, handleEvent);
    }

    // Flush any pending trailing data
    sseParser.flush(handleEvent);

  } catch (err: any) {
    if (err.name === "AbortError") {
      console.log("Chat stream aborted by user.");
      return;
    }
    console.error("Stream chat error:", err);
    if (onError) onError(err);
  }
}
