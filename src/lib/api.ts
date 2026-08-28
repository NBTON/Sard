import { Citation, Artifact, SystemStatus } from "@/types";

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

export interface StreamChatOptions {
  messages: Array<{ role: string; content: string }>;
  query?: string;
  sessionId?: string;
  itineraryMode?: boolean;
  signal?: AbortSignal;
  onStatus?: (statusText: string) => void;
  onCitations?: (citations: Citation[]) => void;
  onDelta?: (deltaText: string) => void;
  onArtifacts?: (artifacts: Artifact[]) => void;
  onDone?: (meta: { verified?: boolean; sources_count?: number; timings_ms?: { total_ms?: number }; updated_at?: string }) => void;
  onError?: (error: Error) => void;
}

export async function streamChat(options: StreamChatOptions): Promise<void> {
  const {
    messages,
    query,
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
      }),
      signal,
    });

    if (!response.ok) {
      const errText = await response.text();
      throw new Error(errText || `Server error (${response.status})`);
    }

    if (!response.body) {
      throw new Error("No response body received from server");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() || "";

      let currentEvent = "message";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;

        if (trimmed.startsWith("event:")) {
          currentEvent = trimmed.substring(6).trim();
          continue;
        }

        if (trimmed.startsWith("data:")) {
          const rawData = trimmed.substring(5).trim();
          if (!rawData) continue;

          try {
            const data = JSON.parse(rawData);

            if (currentEvent === "status") {
              if (data.message && onStatus) {
                onStatus(data.message);
              }
            } else if (currentEvent === "citations") {
              if (data.citations && onCitations) {
                onCitations(data.citations);
              }
            } else if (currentEvent === "delta") {
              if (data.text && onDelta) {
                onDelta(data.text);
              }
            } else if (currentEvent === "artifacts") {
              if (data.artifacts && onArtifacts) {
                onArtifacts(data.artifacts);
              }
            } else if (currentEvent === "done") {
              if (onDone) {
                onDone(data);
              }
            }
          } catch {
            // Raw text delta fallback
            if (currentEvent === "delta" && onDelta) {
              onDelta(rawData);
            }
          }
        }
      }
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
