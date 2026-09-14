import assert from "node:assert";
import test from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");

function readJson(p) {
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

// Minimal glob match for the patterns we allow in vercel.json `functions`.
// Supports exact paths, `api/*.py` (top-level only), and `api/**/*.py`
// (any depth, including top-level).
function matchesPattern(pattern, file) {
  const norm = file.replace(/\\/g, "/");
  if (pattern === norm) return true;
  if (pattern === "api/*.py") return /^api\/[^/]+\.py$/.test(norm);
  if (pattern === "api/**/*.py") return /^api\/(.+\/)?[^/]+\.py$/.test(norm);
  return false;
}

function listApiPyFiles() {
  const apiDir = path.join(ROOT, "api");
  if (!fs.existsSync(apiDir)) return [];
  return fs
    .readdirSync(apiDir)
    .filter((f) => f.toLowerCase().endsWith(".py"))
    .map((f) => `api/${f}`);
}

test("vercel.json functions patterns each match at least one api file (no unmatched-function-pattern)", () => {
  const vercel = readJson(path.join(ROOT, "vercel.json"));
  assert.ok(vercel.functions, "vercel.json must define functions");
  const patterns = Object.keys(vercel.functions);
  assert.ok(patterns.length >= 1, "at least one functions pattern required");
  const files = listApiPyFiles();
  assert.ok(files.length >= 1, "expected at least one api/*.py file");
  for (const pattern of patterns) {
    const matched = files.filter((f) => matchesPattern(pattern, f));
    assert.ok(
      matched.length >= 1,
      `pattern "${pattern}" must match at least one Serverless Function (files: ${files.join(", ")})`
    );
  }
});

test("vercel.json functions has no redundant overlapping patterns", () => {
  const vercel = readJson(path.join(ROOT, "vercel.json"));
  const patterns = Object.keys(vercel.functions ?? {});
  const files = listApiPyFiles();
  // Every api file must be covered, and no two patterns may cover the same file.
  const coverage = new Map();
  for (const pattern of patterns) {
    for (const f of files.filter((x) => matchesPattern(pattern, x))) {
      assert.ok(!coverage.has(f), `overlapping functions patterns both match ${f}: ${coverage.get(f)} and ${pattern}`);
      coverage.set(f, pattern);
    }
  }
  for (const f of files) {
    assert.ok(coverage.has(f), `api file ${f} is not covered by any functions pattern`);
  }
});

test("vercel.json /api rewrite points at an existing function file", () => {
  const vercel = readJson(path.join(ROOT, "vercel.json"));
  const rewrites = vercel.rewrites ?? [];
  const apiRewrite = rewrites.find((r) => String(r.source ?? "").startsWith("/api/"));
  assert.ok(apiRewrite, "expected an /api rewrite in vercel.json");
  const dest = String(apiRewrite.destination ?? "").replace(/^\//, "");
  assert.ok(dest, "rewrite destination must not be empty");
  assert.ok(fs.existsSync(path.join(ROOT, dest)), `rewrite destination ${apiRewrite.destination} must exist`);
});

test("api/index.py exposes a top-level FastAPI app for Vercel detection", () => {
  const entry = path.join(ROOT, "api", "index.py");
  assert.ok(fs.existsSync(entry), "api/index.py must exist");
  const text = fs.readFileSync(entry, "utf8");
  // Must explicitly bind `app` at top level via assignment (not a bare
  // re-export): Vercel's static entrypoint detection looks for an assignment.
  const bindsApp = /^\s*app\s*:/m.test(text) || /^\s*app\s*=/m.test(text);
  assert.ok(bindsApp, "api/index.py must explicitly assign a top-level `app`");
  // Detector-friendly: reference FastAPI or the real backend module.
  assert.ok(
    text.includes("fastapi") || text.includes("sard.api.server"),
    "api/index.py must reference fastapi or sard.api.server"
  );
});

test("requirements.txt exists and is non-empty (Vercel pip install source)", () => {
  const req = path.join(ROOT, "requirements.txt");
  assert.ok(fs.existsSync(req), "requirements.txt must exist");
  const text = fs.readFileSync(req, "utf8").trim();
  assert.ok(text.length > 0, "requirements.txt must not be empty");
});

test("api paths are lowercase (Linux case-safety)", () => {
  const vercel = readJson(path.join(ROOT, "vercel.json"));
  for (const pattern of Object.keys(vercel.functions ?? {})) {
    assert.equal(pattern, pattern.toLowerCase(), `functions pattern must be lowercase: ${pattern}`);
  }
  for (const f of listApiPyFiles()) {
    assert.equal(f, f.toLowerCase(), `api file must be lowercase: ${f}`);
  }
});
