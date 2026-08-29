import assert from "node:assert";
import test from "node:test";

// Simple compilation of PersistentSSEParser logic for Node testing
class PersistentSSEParser {
  constructor() {
    this.buffer = "";
    this.currentEvent = "message";
    this.currentDataLines = [];
  }

  feed(chunk, onEvent) {
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

  processLine(line, onEvent) {
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

    if (line.startsWith(":")) {
      return;
    }

    if (line.startsWith("event:")) {
      this.currentEvent = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      let dataVal = line.slice(5);
      if (dataVal.startsWith(" ")) {
        dataVal = dataVal.slice(1);
      }
      this.currentDataLines.push(dataVal);
    }
  }

  flush(onEvent) {
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

test("PersistentSSEParser - fragmented event and data chunks", () => {
  const parser = new PersistentSSEParser();
  const received = [];

  // Chunk 1: partial event line
  parser.feed("even", (e) => received.push(e));
  assert.strictEqual(received.length, 0);

  // Chunk 2: finish event line + partial data
  parser.feed("t: artifacts\ndat", (e) => received.push(e));
  assert.strictEqual(received.length, 0);

  // Chunk 3: finish data + double newline
  parser.feed('a: {"count":1}\n\n', (e) => received.push(e));
  assert.strictEqual(received.length, 1);
  assert.strictEqual(received[0].event, "artifacts");
  assert.strictEqual(received[0].data, '{"count":1}');
});

test("PersistentSSEParser - CRLF split across chunks", () => {
  const parser = new PersistentSSEParser();
  const received = [];

  parser.feed("event: delta\r\ndata: Hello\r", (e) => received.push(e));
  assert.strictEqual(received.length, 0);

  parser.feed("\n\r\n", (e) => received.push(e));
  assert.strictEqual(received.length, 1);
  assert.strictEqual(received[0].event, "delta");
  assert.strictEqual(received[0].data, "Hello");
});

test("PersistentSSEParser - multiple events in single chunk", () => {
  const parser = new PersistentSSEParser();
  const received = [];

  const multiChunk = "event: status\ndata: {\"msg\":\"init\"}\n\nevent: delta\ndata: line 1\n\n";
  parser.feed(multiChunk, (e) => received.push(e));

  assert.strictEqual(received.length, 2);
  assert.strictEqual(received[0].event, "status");
  assert.strictEqual(received[1].event, "delta");
});

test("PersistentSSEParser - multi-line data support", () => {
  const parser = new PersistentSSEParser();
  const received = [];

  parser.feed("event: doc\ndata: line1\ndata: line2\n\n", (e) => received.push(e));
  assert.strictEqual(received.length, 1);
  assert.strictEqual(received[0].event, "doc");
  assert.strictEqual(received[0].data, "line1\nline2");
});

test("PersistentSSEParser - flush on stream end without trailing newline", () => {
  const parser = new PersistentSSEParser();
  const received = [];

  parser.feed("event: done\ndata: {\"done\":true}", (e) => received.push(e));
  assert.strictEqual(received.length, 0);

  parser.flush((e) => received.push(e));
  assert.strictEqual(received.length, 1);
  assert.strictEqual(received[0].event, "done");
  assert.strictEqual(received[0].data, '{"done":true}');
});
