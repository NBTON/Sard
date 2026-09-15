import assert from "node:assert";
import test from "node:test";

// Frontend workstream H: alias normalization + versions accumulation.
// Imports the real src/types/index.ts (Node type-stripping; module is import-free).
import {
  artifactHtml,
  canonicalPreview,
  mergeArtifactVersions,
  normalizeArtifact,
  normalizeArtifactsList,
} from "../src/types/index.ts";

test("normalizeArtifact - legacy aliases (artifact_id/type/url/data.card_data)", () => {
  const raw = {
    artifact_id: "art-legacy-1",
    type: "PDF",
    url: "/api/artifacts/sard-report.pdf",
    title: "تقرير ثقافي",
    filename: "sard-report.pdf",
    mime_type: "application/pdf",
    size_bytes: 1234,
    status: "created",
    data: { card_data: { name: "card", ingredients_or_materials: ["a"] } },
  };
  const a = normalizeArtifact(raw);
  assert.strictEqual(a.id, "art-legacy-1");
  assert.strictEqual(a.format, "pdf");
  assert.strictEqual(a.type, "pdf");
  assert.strictEqual(a.download_url, "/api/artifacts/sard-report.pdf");
  assert.strictEqual(a.url, "/api/artifacts/sard-report.pdf");
  assert.deepStrictEqual(a.preview.card_data, { name: "card", ingredients_or_materials: ["a"] });
  assert.ok(Array.isArray(a.versions) && a.versions.length >= 1);
  assert.strictEqual(a.activeVersion, a.versions.length);
});

test("normalizeArtifact - preview/to_preview/html aliases resolve to html", () => {
  const fromPreview = normalizeArtifact({ id: "x1", preview: { html: "<h1>hi</h1>" } });
  assert.strictEqual(fromPreview.html, "<h1>hi</h1>");
  assert.strictEqual(artifactHtml(fromPreview.preview), "<h1>hi</h1>");

  const fromToPreview = normalizeArtifact({ id: "x2", preview: { to_preview: "<p>tp</p>" } });
  assert.strictEqual(fromToPreview.html, "<p>tp</p>");

  const fromRoot = normalizeArtifact({ id: "x3", to_preview: "<p>root</p>" });
  assert.strictEqual(fromRoot.html, "<p>root</p>");

  assert.strictEqual(canonicalPreview(null), undefined);
  assert.strictEqual(artifactHtml(undefined), null);
});

test("normalizeArtifact - failed artifacts never carry a download_url", () => {
  const a = normalizeArtifact({
    id: "art-f1",
    format: "pdf",
    status: "failed",
    download_url: "/api/artifacts/x.pdf",
    url: "/api/artifacts/x.pdf",
    error: "boom",
    error_category: "renderer_exception",
  });
  assert.strictEqual(a.status, "failed");
  assert.strictEqual(a.download_url, null);
  assert.strictEqual(a.error_category, "renderer_exception");
});

test("normalizeArtifact - malformed input yields failed placeholder, never throws", () => {
  for (const bad of [null, undefined, 42, "nope", []]) {
    const a = normalizeArtifact(bad);
    assert.strictEqual(a.status, "failed");
    assert.strictEqual(a.download_url, null);
  }
  assert.deepStrictEqual(normalizeArtifactsList("not-an-array"), []);
  assert.deepStrictEqual(normalizeArtifactsList(null), []);
});

test("mergeArtifactVersions - accumulates snapshots, never overwrites", () => {
  const v1 = normalizeArtifact({ id: "a1", format: "pdf", status: "pending", title: "T" });
  let list = mergeArtifactVersions([], [v1]);
  assert.strictEqual(list.length, 1);
  assert.strictEqual(list[0].versions.length, 1);

  // Same id, new content (created + URL) -> new version appended, live fields refreshed.
  const v2 = normalizeArtifact({
    id: "a1", format: "pdf", status: "created",
    download_url: "/api/artifacts/t.pdf", title: "T",
  });
  list = mergeArtifactVersions(list, [v2]);
  assert.strictEqual(list.length, 1);
  assert.strictEqual(list[0].versions.length, 2);
  assert.strictEqual(list[0].status, "created");
  assert.strictEqual(list[0].download_url, "/api/artifacts/t.pdf");
  assert.strictEqual(list[0].activeVersion, 2);

  // Identical re-delivery -> no version spam.
  list = mergeArtifactVersions(list, [v2]);
  assert.strictEqual(list[0].versions.length, 2);

  // New id -> appended alongside.
  const other = normalizeArtifact({ id: "a2", format: "ics", status: "created", download_url: "/api/artifacts/e.ics" });
  list = mergeArtifactVersions(list, [other]);
  assert.strictEqual(list.length, 2);
  assert.strictEqual(list[1].id, "a2");
});
