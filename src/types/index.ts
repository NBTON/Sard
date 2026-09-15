export interface Citation {
  citation_id: string;
  title: string;
  source_name: string;
  source_url: string;
  chunk_id?: string;
  snippet?: string;
  topic?: string;
}

export type ArtifactKind = "document" | "presentation" | "calendar" | "image" | "diagram" | "interactive";
export type ArtifactFormat = "pdf" | "docx" | "pptx" | "ics" | "svg" | "png" | "json" | string;
export type ArtifactStatus = "pending" | "created" | "failed" | "skipped";

/** One immutable snapshot of an artifact. New backend `artifacts` events append here. */
export interface ArtifactVersion {
  version: number;
  id?: string;
  download_url: string | null;
  preview?: ArtifactPreview;
  status?: ArtifactStatus;
  created_at?: number;
  checksum?: string | null;
  title?: string;
  filename?: string;
  format?: string;
  html?: string | null;
  size_bytes?: number;
}

/** Canonical preview payload. Accepts `to_preview` / `html` / `card_data` legacy aliases. */
export interface ArtifactDocument {
  html?: string;
  to_preview?: string;
  status?: string;
  revisionHint?: string;
  revision_hint?: string;
  card_data?: any;
  [key: string]: any;
}

export type ArtifactPreview = ArtifactDocument | any;

export interface Artifact {
  id: string;
  kind: ArtifactKind;
  format: ArtifactFormat;
  title: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  status: ArtifactStatus;
  download_url: string | null;
  preview?: ArtifactPreview;
  /** Accumulated snapshots across streaming `artifacts` events (never overwritten). */
  versions?: ArtifactVersion[];
  /** 1-based index into `versions` of the currently displayed snapshot. */
  activeVersion?: number;
  html?: string;
  revisionHint?: string;
  warnings?: string[];
  error?: string | null;
  error_category?: string | null;
  checksum?: string | null;
  degraded?: boolean;
  // Compatibility fields (legacy aliases, kept for old backends/clients)
  type?: string;
  url?: string;
  data?: any;
  card_data?: any;
}

export interface Attachment {
  id: string;
  attachment_id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  url?: string;
  preview_url?: string;
  uploading?: boolean;
  error?: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  citations?: Citation[];
  artifacts?: Artifact[];
  attachments?: Attachment[];
  isThinking?: boolean;
  statusStage?: string;
  /** 0-1 streaming progress derived from backend stage (null = unknown). */
  statusProgress?: number | null;
  isStreaming?: boolean;
  error?: string;
  /** User-stopped mid-stream: partial content kept, resume/retry offered. */
  stopped?: boolean;
  runId?: string | null;
}

export interface ChatSession {
  id: string;
  title: string;
  messages: Message[];
  createdAt: number;
  updatedAt: number;
  /** Persisted across reloads: which artifact the workspace panel had open. */
  activeArtifactId?: string | null;
  /** Last backend run_id seen on this session (for retry/resume). */
  lastRunId?: string | null;
}

export interface SystemStatus {
  status_label: string;
  verified: boolean;
  sources_count?: number;
  updated_at?: string;
  sources: { verified: boolean };
  moc_branding?: string;
}

export type Lang = "ar" | "en";
export type View = "landing" | "chat" | "explore";

export interface Sector {
  id: string;
  ar: string;
  en: string;
  color: string;
  promptAr: string;
  promptEn: string;
}

// ---------------------------------------------------------------------------
// Canonical artifact normalization (parse boundary).
// Accepts legacy aliases: type/format (+artifact_type), url/download_url,
// data/card_data/preview (+to_preview/html). Single canonical Artifact out.
// ---------------------------------------------------------------------------

function asNonEmptyString(v: unknown): string | null {
  if (typeof v !== "string") return null;
  const t = v.trim();
  return t ? t : null;
}

function pickFirstString(...vals: unknown[]): string | null {
  for (const v of vals) {
    const s = asNonEmptyString(v);
    if (s) return s;
  }
  return null;
}

/** Resolve the canonical preview object from preview/data/card_data/to_preview aliases. */
export function canonicalPreview(raw: any): ArtifactPreview | undefined {
  if (raw == null) return undefined;
  const preview = raw.preview ?? raw.data ?? undefined;
  const cardData =
    (preview && typeof preview === "object" ? preview.card_data : undefined) ??
    raw.card_data ??
    undefined;
  // Primitive preview payloads (string HTML etc.) become { html }.
  if (typeof preview === "string") {
    return cardData !== undefined
      ? { html: preview, card_data: cardData }
      : { html: preview };
  }
  if (preview && typeof preview === "object") {
    if (cardData !== undefined && (preview as any).card_data === undefined) {
      return { ...(preview as object), card_data: cardData };
    }
    return preview as ArtifactPreview;
  }
  // No preview/data object, but a bare card_data exists.
  if (cardData !== undefined) return { card_data: cardData };
  // Bare to_preview/html on the artifact root.
  const bareHtml = raw.to_preview ?? raw.html;
  if (typeof bareHtml === "string" && bareHtml) return { html: bareHtml };
  return undefined;
}

/** Best-effort HTML string for <iframe srcDoc> from any preview shape. */
export function artifactHtml(preview: ArtifactPreview | undefined): string | null {
  if (!preview) return null;
  if (typeof preview === "string") return preview;
  const html =
    asNonEmptyString((preview as any).html) ??
    asNonEmptyString((preview as any).to_preview) ??
    asNonEmptyString((preview as any).raw_html) ??
    asNonEmptyString((preview as any).content);
  if (html) return html;
  const text = asNonEmptyString((preview as any).text);
  if (text && (/<(!DOCTYPE|html|head|body|div|article|section)[\s>]/i.test(text))) {
    return text;
  }
  return null;
}

/** Shared ArtifactDocument CSS tokens — also injected into the preview iframe. */
export const ARTIFACT_DOC_CSS = `
:root{--sard-paper:#FAF7F1;--sard-ink:#141210;--sard-gold:#C4A46A;--sard-clay:#BE4A24;--sard-sage:#4A513C;}
body{background:var(--sard-paper);color:var(--sard-ink);font-family:'Noto Naskh Arabic','IBM Plex Sans Arabic',serif;line-height:1.85;margin:0;padding:24px;}
h1,h2,h3{color:var(--sard-ink);}a{color:var(--sard-clay);}
.sard-card{background:#fff;border:1px solid #E0D8C8;border-radius:14px;padding:18px;margin:12px 0;}
.sard-badge{display:inline-block;background:var(--sard-ink);color:var(--sard-paper);border-radius:6px;padding:2px 8px;font-size:12px;}
table{border-collapse:collapse;width:100%;}th,td{border:1px solid #D4CBBD;padding:8px 12px;text-align:start;}
blockquote{border-inline-start:3px solid var(--sard-gold);background:#F3EEE4;border-radius:8px;padding:8px 14px;margin:12px 0;}
`;

/** Construct iframe srcDoc safely without nested <html> / <!DOCTYPE> tags. */
export function buildArtifactSrcDoc(html: string, dir: "rtl" | "ltr" = "rtl"): string {
  if (!html) return "";
  const isFullDoc = /<!DOCTYPE\s+html/i.test(html) || /<html[\s>]/i.test(html);
  if (isFullDoc) {
    let doc = html;
    if (!/<html[^>]*\sdir=/i.test(doc)) {
      doc = doc.replace(/<html([^>]*)>/i, `<html$1 dir="${dir}">`);
    }
    const styleTag = `<style>${ARTIFACT_DOC_CSS}</style>`;
    if (/<head[^>]*>/i.test(doc)) {
      return doc.replace(/<head([^>]*)>/i, `<head$1>${styleTag}`);
    } else if (/<body[^>]*>/i.test(doc)) {
      return doc.replace(/<body([^>]*)>/i, `<head>${styleTag}</head><body$1>`);
    }
    return `${styleTag}${doc}`;
  }
  return `<!DOCTYPE html><html dir="${dir}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${ARTIFACT_DOC_CSS}</style></head><body>${html}</body></html>`;
}

export interface CalendarEventFields {
  title: string;
  start: string;
  end: string;
  location: string;
  description: string;
  googleCalendarUrl?: string;
}

export function resolveCalendarEvent(ev: any, fallbackTitle = "مخرج ثقافي"): CalendarEventFields {
  if (!ev || typeof ev !== "object") {
    return { title: fallbackTitle, start: "", end: "", location: "", description: "" };
  }
  const title =
    pickFirstString(ev.title_ar, ev.title_en, ev.summary, ev.title, ev.name) || fallbackTitle;
  const start = pickFirstString(ev.start_date, ev.start, ev.datetime, ev.date) || "";
  const end = pickFirstString(ev.end_date, ev.end) || "";
  const location = pickFirstString(ev.location_name, ev.location, ev.venue, ev.place) || "";
  const description = pickFirstString(ev.description_ar, ev.description_en, ev.description, ev.details) || "";
  const googleCalendarUrl = pickFirstString(ev.google_calendar_url, ev.google_cal_url) || undefined;
  return { title, start, end, location, description, googleCalendarUrl };
}

export function buildGoogleCalendarUrl(ev: any, fallbackTitle = "مخرج ثقافي"): string {
  const resolved = resolveCalendarEvent(ev, fallbackTitle);
  if (resolved.googleCalendarUrl) {
    return resolved.googleCalendarUrl;
  }
  const text = encodeURIComponent(resolved.title);
  const details = encodeURIComponent(resolved.description);
  const loc = encodeURIComponent(resolved.location);
  let dates = "";
  const s = resolved.start.replace(/[-:]/g, "").split(".")[0].replace(" ", "T");
  const e = resolved.end ? resolved.end.replace(/[-:]/g, "").split(".")[0].replace(" ", "T") : s;
  if (s) {
    dates = `&dates=${encodeURIComponent(s + (e ? "/" + e : "/" + s))}`;
  }
  return `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${text}${dates}&details=${details}&location=${loc}`;
}

export function buildWebcalUrl(url: string, origin?: string): string {
  if (!url) return "";
  let full = url;
  if (full.startsWith("/")) {
    const base = origin || "";
    full = base ? `${base.replace(/\/+$/, "")}${full}` : full;
  }
  return full.replace(/^https?:/i, "webcal:");
}

export function qualifyDownloadUrl(url: string | null | undefined, apiBase?: string): string {
  if (!url) return "";
  if (url.startsWith("/") && apiBase) {
    return `${apiBase.replace(/\/+$/, "")}${url}`;
  }
  return url;
}

/** Human revision hint, if the backend supplied one. */
export function artifactRevisionHint(raw: any): string | null {
  if (!raw || typeof raw !== "object") return null;
  const p = raw.preview ?? raw.data;
  return (
    pickFirstString(
      raw.revisionHint,
      raw.revision_hint,
      p && typeof p === "object" ? p.revisionHint : null,
      p && typeof p === "object" ? p.revision_hint : null
    )
  );
}

/** Normalize one single version object. */
export function normalizeArtifactVersion(raw: any, fallbackIndex = 0): ArtifactVersion | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const versionNum =
    typeof raw.version === "number" && Number.isFinite(raw.version) && raw.version > 0
      ? Math.floor(raw.version)
      : fallbackIndex + 1;
  const rawUrl = pickFirstString(raw.download_url, raw.url);
  const download_url = rawUrl && rawUrl !== "#" ? rawUrl : null;
  const preview = canonicalPreview(raw);
  const status = (asNonEmptyString(raw.status)?.toLowerCase() as ArtifactStatus) ?? undefined;
  return {
    version: versionNum,
    id: pickFirstString(raw.id, raw.artifact_id) ?? undefined,
    download_url: status === "failed" ? null : download_url,
    preview,
    status,
    created_at: typeof raw.created_at === "number" ? raw.created_at : undefined,
    checksum: asNonEmptyString(raw.checksum) ?? undefined,
    title: pickFirstString(raw.title, raw.display_label) ?? undefined,
    filename: pickFirstString(raw.filename) ?? undefined,
    format: pickFirstString(raw.format, raw.type, raw.artifact_type)?.toLowerCase() ?? undefined,
    html: artifactHtml(preview) ?? asNonEmptyString(raw.html) ?? undefined,
    size_bytes:
      typeof raw.size_bytes === "number" && Number.isFinite(raw.size_bytes) && raw.size_bytes >= 0
        ? Math.floor(raw.size_bytes)
        : undefined,
  };
}

/** Normalize raw versions list (unwrapped array or wrapped { versions: [...] }). */
export function normalizeArtifactVersions(input: unknown): ArtifactVersion[] {
  let list: unknown[] = [];
  if (Array.isArray(input)) {
    list = input;
  } else if (input && typeof input === "object") {
    const obj = input as any;
    if (Array.isArray(obj.versions)) list = obj.versions;
    else if (Array.isArray(obj.data?.versions)) list = obj.data.versions;
    else if (Array.isArray(obj.data)) list = obj.data;
    else if (Array.isArray(obj.result)) list = obj.result;
    else if (obj.version !== undefined) list = [obj];
  }
  const result: ArtifactVersion[] = [];
  for (let i = 0; i < list.length; i++) {
    const norm = normalizeArtifactVersion(list[i], i);
    if (norm) result.push(norm);
  }
  return result.sort((a, b) => a.version - b.version);
}

/**
 * Normalize one raw backend artifact dict into the canonical Artifact.
 * Never throws on malformed input — returns a failed-status placeholder.
 */
export function normalizeArtifact(raw: any, fallbackIndex = 0): Artifact {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return {
      id: `art-invalid-${Date.now()}-${fallbackIndex}`,
      kind: "document",
      format: "unknown",
      title: "مخرج ثقافي",
      filename: "file",
      mime_type: "application/octet-stream",
      size_bytes: 0,
      status: "failed",
      download_url: null,
      error: "Invalid artifact payload",
      error_category: "validation",
    };
  }
  const id =
    pickFirstString(raw.id, raw.artifact_id) ?? `art-${Date.now()}-${fallbackIndex}`;
  const format =
    pickFirstString(raw.format, raw.type, raw.artifact_type)?.toLowerCase() ?? "unknown";
  const kind = (pickFirstString(raw.kind)?.toLowerCase() ?? "document") as Artifact["kind"];
  const status = (pickFirstString(raw.status, raw.creation_status)?.toLowerCase() ??
    "created") as ArtifactStatus;
  const rawUrl = pickFirstString(raw.download_url, raw.url);
  // Backend uses "" / "#" sentinels for missing URLs — canonicalize to null.
  const download_url = rawUrl && rawUrl !== "#" ? rawUrl : null;
  const preview = canonicalPreview(raw);
  const sizeRaw = raw.size_bytes;
  const size_bytes =
    typeof sizeRaw === "number" && Number.isFinite(sizeRaw) && sizeRaw >= 0
      ? Math.floor(sizeRaw)
      : 0;
  const rawVersions = Array.isArray(raw.versions)
    ? raw.versions
    : raw.data?.versions || raw.versions || [];
  const versions: ArtifactVersion[] = normalizeArtifactVersions(rawVersions);

  // Seed versions[] from the artifact itself so every artifact has ≥1 snapshot.
  if (versions.length === 0) {
    const rootVersionNum =
      typeof raw.version === "number" && Number.isFinite(raw.version) && raw.version > 0
        ? Math.floor(raw.version)
        : 1;
    versions.push({
      version: rootVersionNum,
      id,
      download_url,
      preview,
      status,
      created_at: typeof raw.created_at === "number" ? raw.created_at : Date.now(),
      checksum: asNonEmptyString(raw.checksum) ?? undefined,
      title: pickFirstString(raw.title, raw.display_label) ?? undefined,
      filename: pickFirstString(raw.filename) ?? undefined,
      format,
      html: artifactHtml(preview) ?? asNonEmptyString(raw.html) ?? undefined,
      size_bytes,
    });
  } else if (typeof raw.version === "number" && !versions.some((v) => v.version === raw.version)) {
    versions.push({
      version: Math.floor(raw.version),
      id,
      download_url,
      preview,
      status,
      created_at: typeof raw.created_at === "number" ? raw.created_at : Date.now(),
      checksum: asNonEmptyString(raw.checksum) ?? undefined,
      title: pickFirstString(raw.title, raw.display_label) ?? undefined,
      filename: pickFirstString(raw.filename) ?? undefined,
      format,
      html: artifactHtml(preview) ?? asNonEmptyString(raw.html) ?? undefined,
      size_bytes,
    });
    versions.sort((a, b) => a.version - b.version);
  }

  const activeVersion =
    typeof raw.version === "number" && Number.isFinite(raw.version)
      ? Math.floor(raw.version)
      : versions.length;

  return {
    id,
    kind,
    format,
    title: pickFirstString(raw.title, raw.display_label) ?? "مخرج ثقافي",
    filename: pickFirstString(raw.filename) ?? "file",
    mime_type: pickFirstString(raw.mime_type) ?? "application/octet-stream",
    size_bytes,
    status,
    download_url: status === "failed" ? null : download_url,
    preview,
    versions,
    activeVersion,
    html: artifactHtml(preview) ?? asNonEmptyString(raw.html) ?? asNonEmptyString(raw.to_preview) ?? undefined,
    revisionHint: artifactRevisionHint(raw) ?? undefined,
    warnings: Array.isArray(raw.warnings)
      ? raw.warnings.filter((w: unknown): w is string => typeof w === "string")
      : undefined,
    error: asNonEmptyString(raw.error),
    error_category: asNonEmptyString(raw.error_category ?? raw.errorCategory),
    checksum: asNonEmptyString(raw.checksum),
    degraded:
      raw.degraded === true || (raw.status as string) === "degraded" || undefined,
    type: format,
    url: download_url ?? "",
  };
}

/** Normalize a whole `artifacts` SSE payload. */
export function normalizeArtifactsList(input: unknown): Artifact[] {
  if (!Array.isArray(input)) return [];
  return input.map((a, i) => normalizeArtifact(a, i));
}

/**
 * Accumulate incoming artifacts into the existing list WITHOUT overwriting.
 * Same id → append a new version snapshot (cap 20) and refresh live fields.
 * Preserves old versions on failure.
 * New id → append. Returns a new array.
 */
export function mergeArtifactVersions(
  existing: Artifact[],
  incoming: Artifact[]
): Artifact[] {
  const next = existing.map((a) => ({
    ...a,
    versions: [...(a.versions ?? [])],
  }));
  const byId = new Map(next.map((a) => [a.id, a]));
  for (const inc of incoming) {
    const cur = byId.get(inc.id);
    if (!cur) {
      next.push({ ...inc, versions: [...(inc.versions ?? [])] });
      byId.set(inc.id, next[next.length - 1]);
      continue;
    }

    // Merge version snapshots by version number to preserve historical snapshots
    const versionsByNum = new Map<number, ArtifactVersion>();
    const initialCount = (cur.versions ?? []).length;
    for (const v of cur.versions ?? []) {
      versionsByNum.set(v.version, v);
    }
    if (inc.status !== "failed") {
      for (const v of inc.versions ?? []) {
        versionsByNum.set(v.version, v);
      }
    }

    const latest = cur.versions?.[cur.versions.length - 1];
    const changed =
      !latest ||
      latest.download_url !== inc.download_url ||
      latest.status !== inc.status ||
      JSON.stringify(latest.preview ?? null) !== JSON.stringify(inc.preview ?? null);

    // If incoming changed, is not failed, and no new version was introduced by inc.versions, append new snapshot
    if (changed && inc.status !== "failed" && versionsByNum.size <= initialCount) {
      const nextVerNum = Math.max(0, ...Array.from(versionsByNum.keys())) + 1;
      versionsByNum.set(nextVerNum, {
        version: nextVerNum,
        id: inc.id,
        download_url: inc.download_url,
        preview: inc.preview,
        status: inc.status,
        created_at: Date.now(),
        checksum: inc.checksum,
        title: inc.title,
        filename: inc.filename,
        format: inc.format,
        html: inc.html,
      });
    }


    cur.versions = Array.from(versionsByNum.values())
      .sort((a, b) => a.version - b.version)
      .slice(-20);
    cur.activeVersion = cur.versions.length;

    // Refresh live (non-versioned) fields from newest event, but preserve old versions on failure.
    if (inc.status === "failed") {
      cur.status = "failed";
      cur.error = inc.error || cur.error;
      cur.error_category = inc.error_category || cur.error_category;
      // Do NOT erase cur.download_url if old version was successful
      if (!cur.download_url && cur.versions.length > 0) {
        const lastOk = [...cur.versions].reverse().find((v) => v.status === "created" && v.download_url);
        if (lastOk) cur.download_url = lastOk.download_url;
      }
    } else {
      cur.status = inc.status;
      cur.download_url = inc.download_url;
      cur.preview = inc.preview;
      cur.html = inc.html || cur.html;
      cur.title = inc.title || cur.title;
      cur.filename = inc.filename || cur.filename;
      cur.error = inc.error;
      cur.error_category = inc.error_category;
    }
  }
  return next;
}

/** Core revision submission logic with normalized wrapped/unwrapped response handling. */
export async function postArtifactRevisionCore(
  fetchFn: (input: string, init?: any) => Promise<Response>,
  apiBase: string,
  artifactId: string,
  instruction: string,
  format?: string
): Promise<Artifact> {
  const safeId = encodeURIComponent(artifactId.trim());
  const body = JSON.stringify({
    instruction,
    ...(format ? { format } : {}),
  });

  const base = apiBase ? apiBase.replace(/\/+$/, "") : "";
  const endpoints = [
    `${base}/api/artifacts/${safeId}/revisions`,
    `${base}/artifacts/${safeId}/revisions`,
  ];

  let lastResponse: Response | null = null;
  let successData: any = null;

  for (const endpoint of endpoints) {
    try {
      const res = await fetchFn(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body,
      });
      lastResponse = res;
      if (res.status === 404 && endpoint !== endpoints[endpoints.length - 1]) {
        continue;
      }
      if (!res.ok) {
        let detail = `Revision failed (${res.status})`;
        let category: string | undefined;
        try {
          const errJson = await res.json();
          detail = errJson.detail || errJson.error || detail;
          category = errJson.error_category || errJson.category;
        } catch {
          // ignore
        }
        const err = new Error(detail) as any;
        err.status = res.status;
        err.category = category || (res.status === 422 ? "validation" : "http_error");
        err.code = res.status === 422 ? "validation" : "http_error";
        throw err;
      }
      successData = await res.json();
      break;
    } catch (err: any) {
      if (err.status || err.code) throw err;
      if (endpoint === endpoints[endpoints.length - 1]) {
        const wrap = new Error(err?.message || "Failed to submit artifact revision") as any;
        wrap.code = "http_error";
        throw wrap;
      }
    }
  }

  if (!successData && lastResponse && !lastResponse.ok) {
    let detail = `Revision failed (${lastResponse.status})`;
    let category: string | undefined;
    try {
      const errJson = await lastResponse.json();
      detail = errJson.detail || errJson.error || detail;
      category = errJson.error_category || errJson.category;
    } catch {}
    const err = new Error(detail) as any;
    err.status = lastResponse.status;
    err.category = category || (lastResponse.status === 422 ? "validation" : "http_error");
    err.code = lastResponse.status === 422 ? "validation" : "http_error";
    throw err;
  }

  const raw =
    (successData &&
      typeof successData === "object" &&
      (successData.artifact || successData.data || successData.result)) ||
    successData;

  return normalizeArtifact(raw);
}

/** Core version retrieval logic with normalized wrapped/unwrapped response handling. */
export async function fetchArtifactVersionsCore(
  fetchFn: (input: string, init?: any) => Promise<Response>,
  apiBase: string,
  artifactId: string
): Promise<ArtifactVersion[]> {
  if (!artifactId) return [];
  const safeId = encodeURIComponent(artifactId.trim());
  const base = apiBase ? apiBase.replace(/\/+$/, "") : "";
  const endpoints = [
    `${base}/api/artifacts/${safeId}/versions`,
    `${base}/artifacts/${safeId}/versions`,
  ];

  for (const endpoint of endpoints) {
    try {
      const res = await fetchFn(endpoint, {
        headers: { Accept: "application/json" },
      });
      if (res.status === 404) {
        if (endpoint !== endpoints[endpoints.length - 1]) continue;
        return [];
      }
      if (!res.ok) return [];
      const data = await res.json();
      return normalizeArtifactVersions(data);
    } catch {
      if (endpoint !== endpoints[endpoints.length - 1]) continue;
      return [];
    }
  }
  return [];
}


