// Repository-policy deployment contract for the Vercel configuration.
//
// SCOPE WARNING: these tests do NOT exercise Vercel. They check local
// repository invariants only (files exist, JSON parses, globs line up).
// Real proof of deployability is `vercel build` emitting a Python function
// artifact plus a preview/production deployment serving /api. That proof has
// NOT been obtained: `vercel build` is blocked on credentials in this
// environment (see docs/handoffs/20260914-vercel.md for the exact blocker).
// Do not cite this file as evidence that Vercel accepts the configuration.

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

// Minimal glob match for the patterns used in vercel.json `functions`.
// Supports exact paths, `api/*.py` (top-level only), and `api/**/*.py`
// (any depth, including top-level). This is a repository-policy helper, not
// Vercel's matcher.
function matchesPattern(pattern, file) {
  const norm = file.replace(/\\/g, "/");
  if (pattern === norm) return true;
  if (pattern === "api/*.py") return /^api\/[^/]+\.py$/.test(norm);
  if (pattern === "api/**/*.py") return /^api\/(.+\/)?[^/]+\.py$/.test(norm);
  return false;
}

// Recursively enumerate Python files under api/ (nested helpers must be
// visible to policy checks, e.g. future api/_utils/*.py).
function listApiPyFiles() {
  const apiDir = path.join(ROOT, "api");
  const out = [];
  const walk = (dir, prefix) => {
    if (!fs.existsSync(dir)) return;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
      if (entry.isDirectory()) {
        walk(path.join(dir, entry.name), rel);
      } else if (entry.name.toLowerCase().endsWith(".py")) {
        out.push(`api/${rel}`);
      }
    }
  };
  walk(apiDir, "");
  return out.sort();
}

test("policy: every vercel.json functions pattern matches at least one api file", () => {
  // Mirrors the *shape* of Vercel's documented unmatched-function-pattern
  // validation, evaluated locally. Passing here does not prove Vercel
  // discovers the function (only `vercel build` proves that).
  const vercel = readJson(path.join(ROOT, "vercel.json"));
  assert.ok(vercel.functions, "vercel.json must define functions");
  const patterns = Object.keys(vercel.functions);
  assert.ok(patterns.length >= 1, "at least one functions pattern required");
  const files = listApiPyFiles();
  assert.ok(files.length >= 1, "expected at least one api/**/*.py file");
  for (const pattern of patterns) {
    const matched = files.filter((f) => matchesPattern(pattern, f));
    assert.ok(
      matched.length >= 1,
      `pattern "${pattern}" matches no local api file (files: ${files.join(", ")})`
    );
  }
});

test("policy: single-function repo keeps exactly one functions pattern", () => {
  // REPO POLICY, not a Vercel rule: Vercel permits overlapping patterns.
  // This repo has one Python function (api/index.py), so one exact pattern
  // keeps the config minimal and reviewable. Revisit if a second function
  // is added.
  const vercel = readJson(path.join(ROOT, "vercel.json"));
  const patterns = Object.keys(vercel.functions ?? {});
  const files = listApiPyFiles().filter((f) => !f.includes("/_"));
  assert.deepEqual(
    patterns,
    ["api/index.py"],
    `expected exactly ["api/index.py"], got ${JSON.stringify(patterns)}`
  );
  assert.ok(files.includes("api/index.py"), "api/index.py must exist");
});

test("policy: /api rewrite points at an existing file", () => {
  const vercel = readJson(path.join(ROOT, "vercel.json"));
  const rewrites = vercel.rewrites ?? [];
  const apiRewrite = rewrites.find((r) => String(r.source ?? "").startsWith("/api/"));
  assert.ok(apiRewrite, "expected an /api rewrite in vercel.json");
  const dest = String(apiRewrite.destination ?? "").replace(/^\//, "");
  assert.ok(dest, "rewrite destination must not be empty");
  assert.ok(fs.existsSync(path.join(ROOT, dest)), `rewrite destination ${apiRewrite.destination} must exist`);
});

test("policy: api/index.py explicitly assigns top-level app (compatibility hardening)", () => {
  // HARDENING, not proven causality: the evidenced failure is only that
  // Vercel did not discover api/index.py. An explicit assignment plus a
  // direct fastapi reference is kept as compatibility hardening for static
  // entrypoint detectors; it is not claimed to be the proven fix.
  const entry = path.join(ROOT, "api", "index.py");
  assert.ok(fs.existsSync(entry), "api/index.py must exist");
  const text = fs.readFileSync(entry, "utf8");
  const bindsApp = /^\s*app\s*:/m.test(text) || /^\s*app\s*=/m.test(text);
  assert.ok(bindsApp, "api/index.py must explicitly assign a top-level `app`");
  assert.ok(
    text.includes("fastapi") || text.includes("sard.api.server"),
    "api/index.py must reference fastapi or sard.api.server"
  );
});

test("policy: requirements.txt exists and is non-empty", () => {
  const req = path.join(ROOT, "requirements.txt");
  assert.ok(fs.existsSync(req), "requirements.txt must exist");
  const text = fs.readFileSync(req, "utf8").trim();
  assert.ok(text.length > 0, "requirements.txt must not be empty");
});

test("policy: api paths are lowercase (Linux case-safety)", () => {
  const vercel = readJson(path.join(ROOT, "vercel.json"));
  for (const pattern of Object.keys(vercel.functions ?? {})) {
    assert.equal(pattern, pattern.toLowerCase(), `functions pattern must be lowercase: ${pattern}`);
  }
  for (const f of listApiPyFiles()) {
    assert.equal(f, f.toLowerCase(), `api file must be lowercase: ${f}`);
  }
});
