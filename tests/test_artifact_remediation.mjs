import assert from "node:assert";
import test from "node:test";

import {
  artifactHtml,
  buildArtifactSrcDoc,
  buildGoogleCalendarUrl,
  buildWebcalUrl,
  fetchArtifactVersionsCore,
  mergeArtifactVersions,
  normalizeArtifact,
  normalizeArtifactVersion,
  normalizeArtifactVersions,
  postArtifactRevisionCore,
  qualifyDownloadUrl,
  resolveCalendarEvent,
} from "../src/types/index.ts";

test("POST /api/artifacts/{id}/revisions - unwrapped artifact response", async () => {
  const fakeFetch = async (url, init) => {
    assert.ok(url.includes("/api/artifacts/art-123/revisions"));
    assert.strictEqual(init.method, "POST");
    const body = JSON.parse(init.body);
    assert.strictEqual(body.instruction, "Make it concise");
    return {
      ok: true,
      status: 200,
      json: async () => ({
        id: "art-123",
        artifact_id: "art-123",
        version: 2,
        format: "pdf",
        status: "created",
        title: "تقرير الدرعية (منقح)",
        download_url: "/api/artifacts/version/art-123/2",
        preview: { html: "<p>موجز منقح</p>" },
      }),
    };
  };

  const res = await postArtifactRevisionCore(
    fakeFetch,
    "https://api.sard.ai",
    "art-123",
    "Make it concise"
  );
  assert.strictEqual(res.id, "art-123");
  assert.strictEqual(res.activeVersion, 2);
  assert.strictEqual(res.download_url, "/api/artifacts/version/art-123/2");
  assert.strictEqual(res.title, "تقرير الدرعية (منقح)");
  assert.strictEqual(res.html, "<p>موجز منقح</p>");
});

test("POST /api/artifacts/{id}/revisions - wrapped response shapes ({ artifact } and { data })", async () => {
  // Wrapped in { artifact: ... }
  const fakeFetch1 = async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      artifact: {
        id: "art-wrapped",
        version: 3,
        format: "docx",
        status: "created",
        title: "وثيقة تراثية",
        download_url: "/api/artifacts/doc-v3.docx",
      },
    }),
  });

  const res1 = await postArtifactRevisionCore(fakeFetch1, "", "art-wrapped", "Add glossary");
  assert.strictEqual(res1.id, "art-wrapped");
  assert.strictEqual(res1.activeVersion, 3);
  assert.strictEqual(res1.download_url, "/api/artifacts/doc-v3.docx");

  // Wrapped in { data: ... }
  const fakeFetch2 = async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      data: {
        id: "art-data",
        version: 2,
        format: "ics",
        status: "created",
        title: "جدول الفعاليات",
        download_url: "/api/artifacts/cal-v2.ics",
      },
    }),
  });

  const res2 = await postArtifactRevisionCore(fakeFetch2, "", "art-data", "Update dates");
  assert.strictEqual(res2.id, "art-data");
  assert.strictEqual(res2.activeVersion, 2);
});

test("POST /api/artifacts/{id}/revisions - error handling and validation", async () => {
  const fakeFetchErr = async () => ({
    ok: false,
    status: 422,
    json: async () => ({
      detail: "لا يمكن تطبيق التنقيح على الصيغة الحالية.",
      error_category: "validation",
    }),
  });

  await assert.rejects(
    async () => {
      await postArtifactRevisionCore(fakeFetchErr, "", "art-1", "Invalid instruction");
    },
    (err) => {
      assert.strictEqual(err.status, 422);
      assert.strictEqual(err.code, "validation");
      assert.ok(err.message.includes("لا يمكن تطبيق التنقيح"));
      return true;
    }
  );
});

test("GET /api/artifacts/{id}/versions - unwrapped and wrapped history views", async () => {
  // Wrapped in { artifact_id, versions: [...], count }
  const fakeFetch1 = async (url) => {
    assert.ok(url.includes("/api/artifacts/art-history/versions"));
    return {
      ok: true,
      status: 200,
      json: async () => ({
        artifact_id: "art-history",
        count: 2,
        versions: [
          {
            version: 1,
            title: "المسودة الأولى",
            download_url: "/api/artifacts/version/art-history/1",
            status: "created",
            format: "pdf",
          },
          {
            version: 2,
            title: "المسودة الثانية",
            download_url: "/api/artifacts/version/art-history/2",
            status: "created",
            format: "pdf",
          },
        ],
      }),
    };
  };

  const vers = await fetchArtifactVersionsCore(fakeFetch1, "https://api.sard.ai", "art-history");
  assert.strictEqual(vers.length, 2);
  assert.strictEqual(vers[0].version, 1);
  assert.strictEqual(vers[0].title, "المسودة الأولى");
  assert.strictEqual(vers[1].version, 2);
  assert.strictEqual(vers[1].download_url, "/api/artifacts/version/art-history/2");

  // 404 endpoint returns empty list cleanly without throwing
  const fakeFetch404 = async () => ({
    ok: false,
    status: 404,
  });
  const emptyVers = await fetchArtifactVersionsCore(fakeFetch404, "", "non-existent");
  assert.deepStrictEqual(emptyVers, []);
});

test("Multi-turn revision lifecycle preserves historical snapshots on failure", () => {
  // Step 1: initial artifact generation
  const initial = normalizeArtifact({
    id: "art-life",
    title: "تقرير قصر المصمك",
    format: "pdf",
    status: "created",
    download_url: "/api/artifacts/life-v1.pdf",
    preview: { html: "<p>الإصدار الأول</p>" },
  });
  let activeList = mergeArtifactVersions([], [initial]);
  assert.strictEqual(activeList[0].versions.length, 1);
  assert.strictEqual(activeList[0].activeVersion, 1);

  // Step 2: successful revision
  const revised = normalizeArtifact({
    id: "art-life",
    version: 2,
    title: "تقرير قصر المصمك (منقح)",
    format: "pdf",
    status: "created",
    download_url: "/api/artifacts/life-v2.pdf",
    preview: { html: "<p>الإصدار الثاني المنقح</p>" },
  });
  activeList = mergeArtifactVersions(activeList, [revised]);
  assert.strictEqual(activeList[0].versions.length, 2);
  assert.strictEqual(activeList[0].versions[0].download_url, "/api/artifacts/life-v1.pdf");
  assert.strictEqual(activeList[0].versions[1].download_url, "/api/artifacts/life-v2.pdf");
  assert.strictEqual(activeList[0].title, "تقرير قصر المصمك (منقح)");

  // Step 3: revision fails (e.g. backend error)
  const failedRev = normalizeArtifact({
    id: "art-life",
    format: "pdf",
    status: "failed",
    error: "Synthesis timeout",
    error_category: "timeout",
  });
  activeList = mergeArtifactVersions(activeList, [failedRev]);

  // Old versions must remain accessible and undamaged
  assert.strictEqual(activeList[0].versions.length, 2);
  assert.strictEqual(activeList[0].versions[0].version, 1);
  assert.strictEqual(activeList[0].versions[0].download_url, "/api/artifacts/life-v1.pdf");
  assert.strictEqual(activeList[0].versions[1].version, 2);
  assert.strictEqual(activeList[0].versions[1].download_url, "/api/artifacts/life-v2.pdf");
  // F-2: retained created versions keep the tile readable as created.
  assert.strictEqual(activeList[0].status, "created");
  assert.strictEqual(activeList[0].error, "Synthesis timeout");
});

test("Secret safety - client artifact normalization excludes credentials", () => {
  const dirty = {
    id: "art-sec",
    format: "pdf",
    status: "created",
    download_url: "/api/artifacts/report.pdf",
    api_key: "sk-secret-1234",
    authorization: "Bearer secret-token",
    token: "priv_987654",
  };
  const clean = normalizeArtifact(dirty);
  assert.strictEqual(clean.api_key, undefined);
  assert.strictEqual(clean.authorization, undefined);
  assert.strictEqual(clean.token, undefined);
  assert.strictEqual(clean.download_url, "/api/artifacts/report.pdf");
});
