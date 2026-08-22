/** Minimal AG-UI adapter for Sard without vendoring CopilotKit.
 *  Provides typed streaming state, tool events, and interruption handling
 *  while keeping provider neutrality and testability.
 *  References: docs.copilotkit.ai/ag-ui/introduction - verified at implementation time.
 */

export type AGUIEvent =
  | { type: "text_delta"; delta: string }
  | { type: "tool_call"; name: string; args: Record<string, unknown>; id: string }
  | { type: "tool_result"; id: string; result: unknown }
  | { type: "state_update"; state: Record<string, unknown> }
  | { type: "interrupt"; reason: string };

export interface SardEvent {
  event: "status" | "citations" | "delta" | "artifacts" | "done";
  data: string;
}

// Translate Sard SSE events to AG-UI typed events
export function toAGUI(sard: SardEvent): AGUIEvent | null {
  try {
    const data = JSON.parse(sard.data);
    switch (sard.event) {
      case "delta":
        return { type: "text_delta", delta: data.text || "" };
      case "status":
        return { type: "state_update", state: { stage: data.stage, message: data.message } };
      case "citations":
        return { type: "tool_result", id: "citations", result: data.citations };
      case "artifacts":
        return { type: "tool_result", id: "artifacts", result: data.artifacts };
      case "done":
        return { type: "state_update", state: { done: true, verified: data.verified, sources_count: data.sources_count } };
      default:
        return null;
    }
  } catch {
    return null;
  }
}

// Headless hook: consumes AGUI events and updates local state without UI coupling
export function createAGUIConsumer(handlers: {
  onDelta?: (d: string) => void;
  onState?: (s: Record<string, unknown>) => void;
  onToolResult?: (id: string, r: unknown) => void;
}) {
  return (ev: AGUIEvent) => {
    if (ev.type === "text_delta" && handlers.onDelta) handlers.onDelta(ev.delta);
    else if (ev.type === "state_update" && handlers.onState) handlers.onState(ev.state);
    else if (ev.type === "tool_result" && handlers.onToolResult) handlers.onToolResult(ev.id, ev.result);
  };
}
