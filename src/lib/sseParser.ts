/**
 * Standards-compliant, chunk-safe Persistent Server-Sent Events (SSE) Parser.
 *
 * Guarantees:
 * 1. Event names and incomplete data are preserved across arbitrary network chunks.
 * 2. Handles both CRLF (\r\n) and LF (\n) line breaks.
 * 3. Supports multiple data: lines (joined with \n per SSE specification).
 * 4. Dispatches events strictly at blank-line event boundaries.
 * 5. Flushes pending buffered events upon stream termination.
 */

export interface SSEEvent {
  event: string;
  data: string;
}

export class PersistentSSEParser {
  private buffer: string = "";
  private currentEvent: string = "message";
  private currentDataLines: string[] = [];

  /**
   * Feeds an incoming text chunk and dispatches completed SSE messages.
   */
  public feed(chunk: string, onEvent: (event: SSEEvent) => void): void {
    this.buffer += chunk;

    while (true) {
      const newlineIndex = this.buffer.indexOf("\n");
      if (newlineIndex === -1) {
        break;
      }

      let line = this.buffer.slice(0, newlineIndex);
      this.buffer = this.buffer.slice(newlineIndex + 1);

      if (line.endsWith("\r")) {
        line = line.slice(0, -1);
      }

      this.processLine(line, onEvent);
    }
  }

  private processLine(line: string, onEvent: (event: SSEEvent) => void): void {
    // Blank line indicates the end of an SSE message block
    if (line === "") {
      if (this.currentDataLines.length > 0) {
        const payload = this.currentDataLines.join("\n");
        onEvent({
          event: this.currentEvent || "message",
          data: payload,
        });
        this.currentEvent = "message";
        this.currentDataLines = [];
      }
      return;
    }

    // Comment line per SSE spec
    if (line.startsWith(":")) {
      return;
    }

    if (line.startsWith("event:")) {
      this.currentEvent = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      let dataVal = line.slice(5);
      // Remove optional single leading space per SSE spec
      if (dataVal.startsWith(" ")) {
        dataVal = dataVal.slice(1);
      }
      this.currentDataLines.push(dataVal);
    }
  }

  /**
   * Flushes buffered lines and pending events when stream closes.
   */
  public flush(onEvent: (event: SSEEvent) => void): void {
    if (this.buffer.length > 0) {
      let line = this.buffer;
      if (line.endsWith("\r")) {
        line = line.slice(0, -1);
      }
      this.buffer = "";
      this.processLine(line, onEvent);
    }

    if (this.currentDataLines.length > 0) {
      const payload = this.currentDataLines.join("\n");
      onEvent({
        event: this.currentEvent || "message",
        data: payload,
      });
      this.currentEvent = "message";
      this.currentDataLines = [];
    }
  }
}
