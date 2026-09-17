import assert from "node:assert";
import test from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import vm from "node:vm";

// F-4: exercise the shipped parser (src/lib/sseParser.ts), never a copy.
// The TypeScript source is transpiled with the repo's own compiler so the
// committed cases fail if the shipped implementation drifts.
const require = createRequire(import.meta.url);
const ts = require("typescript");
const here = path.dirname(fileURLToPath(import.meta.url));
const shipped = fs.readFileSync(
  path.join(here, "..", "src", "lib", "sseParser.ts"),
  "utf8"
);
const { outputText } = ts.transpileModule(shipped, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
  },
});
const shim = { exports: {} };
vm.runInNewContext(outputText, {
  module: shim,
  exports: shim.exports,
  console,
});
const { PersistentSSEParser } = shim.exports;
assert.strictEqual(
  typeof PersistentSSEParser,
  "function",
  "shipped sseParser.ts must export PersistentSSEParser"
);

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
