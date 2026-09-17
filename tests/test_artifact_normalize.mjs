import assert from "node:assert";
import test from "node:test";

// Frontend workstream H: alias normalization + versions accumulation.
// Imports the real src/types/index.ts (Node type-stripping; module is import-free).
import {
  artifactHtml,
  buildArtifactSrcDoc,
  buildGoogleCalendarUrl,
  buildWebcalUrl,
  canonicalPreview,
  mergeArtifactVersions,
  normalizeArtifact,
  normalizeArtifactVersion,
  normalizeArtifactVersions,
  normalizeArtifactsList,
  qualifyDownloadUrl,
  resolveCalendarEvent,
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

test("mergeArtifactVersions - preserves old versions on failure", () => {
  const v1 = normalizeArtifact({
    id: "art-rev-1",
    format: "pdf",
    status: "created",
    download_url: "/api/artifacts/v1.pdf",
    title: "Version 1",
  });
  let list = mergeArtifactVersions([], [v1]);
  assert.strictEqual(list[0].versions.length, 1);
  assert.strictEqual(list[0].status, "created");
  assert.strictEqual(list[0].download_url, "/api/artifacts/v1.pdf");

  // Revision attempt fails
  const failedRev = normalizeArtifact({
    id: "art-rev-1",
    format: "pdf",
    status: "failed",
    error: "Revision synthesis timed out",
    error_category: "timeout",
  });
  list = mergeArtifactVersions(list, [failedRev]);

  // Existing version 1 snapshot must NOT be lost
  assert.strictEqual(list.length, 1);
  assert.strictEqual(list[0].versions.length, 1);
  assert.strictEqual(list[0].versions[0].download_url, "/api/artifacts/v1.pdf");
  assert.strictEqual(list[0].versions[0].status, "created");
  // F-2: a same-id failure must not flip the tile to failed while the
  // created version with a working URL is retained; the failure detail is
  // recorded but the downloadable version still reads as created.
  assert.strictEqual(list[0].status, "created");
  assert.strictEqual(list[0].error, "Revision synthesis timed out");
  // download_url on the artifact retains fallback to previous valid download URL
  assert.strictEqual(list[0].download_url, "/api/artifacts/v1.pdf");
});

test("normalizeArtifactVersions - unwrapped and wrapped payloads", () => {
  // Unwrapped array
  const rawArray = [
    { version: 1, id: "a1", download_url: "/api/artifacts/v1.pdf", status: "created", title: "V1" },
    { version: 2, id: "a1", download_url: "/api/artifacts/v2.pdf", status: "created", title: "V2" },
  ];
  const norm1 = normalizeArtifactVersions(rawArray);
  assert.strictEqual(norm1.length, 2);
  assert.strictEqual(norm1[0].version, 1);
  assert.strictEqual(norm1[1].version, 2);
  assert.strictEqual(norm1[1].download_url, "/api/artifacts/v2.pdf");

  // Wrapped { versions: [...] }
  const wrapped1 = {
    artifact_id: "a1",
    versions: rawArray,
    count: 2,
  };
  const norm2 = normalizeArtifactVersions(wrapped1);
  assert.strictEqual(norm2.length, 2);
  assert.strictEqual(norm2[0].title, "V1");

  // Wrapped { data: { versions: [...] } }
  const wrapped2 = { data: { versions: rawArray } };
  const norm3 = normalizeArtifactVersions(wrapped2);
  assert.strictEqual(norm3.length, 2);

  // Wrapped { data: [...] }
  const wrapped3 = { data: rawArray };
  const norm4 = normalizeArtifactVersions(wrapped3);
  assert.strictEqual(norm4.length, 2);

  // Single version object
  const single = { version: 3, download_url: "/api/artifacts/v3.pdf", status: "created" };
  const norm5 = normalizeArtifactVersions(single);
  assert.strictEqual(norm5.length, 1);
  assert.strictEqual(norm5[0].version, 3);

  // Malformed input
  assert.deepStrictEqual(normalizeArtifactVersions(null), []);
  assert.deepStrictEqual(normalizeArtifactVersions("invalid"), []);
});

test("buildArtifactSrcDoc - prevents nested <html> and <!DOCTYPE>", () => {
  // Full HTML document
  const fullHtml = `<!DOCTYPE html><html dir="rtl"><head><title>Test</title></head><body><h1>عنوان</h1></body></html>`;
  const doc = buildArtifactSrcDoc(fullHtml, "rtl");
  const doctypeCount = (doc.match(/<!DOCTYPE/gi) || []).length;
  const htmlTagCount = (doc.match(/<html/gi) || []).length;
  assert.strictEqual(doctypeCount, 1, "Full HTML must not have nested <!DOCTYPE>");
  assert.strictEqual(htmlTagCount, 1, "Full HTML must not have nested <html tags");
  assert.ok(doc.includes(":root{--sard-paper"), "Injected CSS should be present");

  // Fragment
  const fragment = `<h2>محتوى جزئي</h2><p>نص تجريبي</p>`;
  const fragDoc = buildArtifactSrcDoc(fragment, "rtl");
  assert.ok(fragDoc.startsWith("<!DOCTYPE html>"));
  assert.ok(fragDoc.includes("<html dir=\"rtl\">"));
  assert.ok(fragDoc.includes("<h2>محتوى جزئي</h2>"));
});

test("Calendar utilities - Google Calendar and webcal links", () => {
  // Event with precomputed google_calendar_url
  const evWithDirectUrl = {
    title_ar: "فعالية يوم التأسيس",
    start_date: "2026-02-22",
    location_name: "الدرعية",
    google_calendar_url: "https://calendar.google.com/calendar/render?custom=1",
  };
  const url1 = buildGoogleCalendarUrl(evWithDirectUrl);
  assert.strictEqual(url1, "https://calendar.google.com/calendar/render?custom=1");

  // Event without precomputed URL (start_date + location_name keys)
  const evWithoutUrl = {
    title_ar: "مهرجان التمور",
    start_date: "2026-09-20T10:00:00",
    end_date: "2026-09-20T18:00:00",
    location_name: "بريدة",
    description_ar: "مهرجان سنوي",
  };
  const resolved = resolveCalendarEvent(evWithoutUrl);
  assert.strictEqual(resolved.title, "مهرجان التمور");
  assert.strictEqual(resolved.start, "2026-09-20T10:00:00");
  assert.strictEqual(resolved.location, "بريدة");
  assert.strictEqual(resolved.description, "مهرجان سنوي");

  const url2 = buildGoogleCalendarUrl(evWithoutUrl);
  assert.ok(url2.includes("calendar.google.com"));
  assert.ok(url2.includes("dates="));
  assert.ok(url2.includes("location=%D8%A8%D8%B1%D9%8A%D8%AF%D8%A9"));

  // Webcal relative URL conversion
  const webcal1 = buildWebcalUrl("/api/artifacts/calendar.ics", "https://sard.ai");
  assert.strictEqual(webcal1, "webcal://sard.ai/api/artifacts/calendar.ics");

  // Webcal absolute URL conversion
  const webcal2 = buildWebcalUrl("https://sard.ai/api/artifacts/calendar.ics");
  assert.strictEqual(webcal2, "webcal://sard.ai/api/artifacts/calendar.ics");
});

test("qualifyDownloadUrl - cross origin qualification", () => {
  assert.strictEqual(qualifyDownloadUrl("/api/artifacts/file.pdf", "https://api.sard.ai"), "https://api.sard.ai/api/artifacts/file.pdf");
  assert.strictEqual(qualifyDownloadUrl("/api/artifacts/file.pdf", ""), "/api/artifacts/file.pdf");
  assert.strictEqual(qualifyDownloadUrl("https://other.com/file.pdf", "https://api.sard.ai"), "https://other.com/file.pdf");
  assert.strictEqual(qualifyDownloadUrl(null), "");
});

