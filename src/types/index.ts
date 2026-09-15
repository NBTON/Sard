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
    asNonEmptyString((preview as any).to_preview);
  return html;
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
  const versionsRaw = Array.isArray(raw.versions) ? raw.versions : [];
  const versions: ArtifactVersion[] = versionsRaw
    .map((v: any, i: number) => {
      if (!v || typeof v !== "object") return null;
      const vUrl = pickFirstString(v.download_url, v.url);
      return {
        version: typeof v.version === "number" ? v.version : i + 1,
        id: asNonEmptyString(v.id) ?? undefined,
        download_url: vUrl && vUrl !== "#" ? vUrl : null,
        preview: canonicalPreview(v),
        status: (asNonEmptyString(v.status)?.toLowerCase() as ArtifactStatus) ?? undefined,
        created_at: typeof v.created_at === "number" ? v.created_at : undefined,
        checksum: asNonEmptyString(v.checksum) ?? undefined,
      } as ArtifactVersion;
    })
    .filter((v: ArtifactVersion | null): v is ArtifactVersion => v !== null);
  // Seed versions[] from the artifact itself so every artifact has ≥1 snapshot.
  if (versions.length === 0) {
    versions.push({
      version: 1,
      id,
      download_url,
      preview,
      status,
      created_at: Date.now(),
      checksum: asNonEmptyString(raw.checksum) ?? undefined,
    });
  }
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
    activeVersion: versions.length,
    html: artifactHtml(preview) ?? undefined,
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
 * Same id → append a new version snapshot (cap 10) and refresh live fields.
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
    const latest = cur.versions?.[cur.versions.length - 1];
    const changed =
      !latest ||
      latest.download_url !== inc.download_url ||
      latest.status !== inc.status ||
      JSON.stringify(latest.preview ?? null) !== JSON.stringify(inc.preview ?? null);
    if (changed) {
      cur.versions = [
        ...(cur.versions ?? []),
        {
          version: (cur.versions?.length ?? 0) + 1,
          id: inc.id,
          download_url: inc.download_url,
          preview: inc.preview,
          status: inc.status,
          created_at: Date.now(),
          checksum: inc.checksum,
        },
      ].slice(-10);
      cur.activeVersion = cur.versions.length;
    }
    // Refresh live (non-versioned) fields from the newest event.
    cur.status = inc.status;
    cur.download_url = inc.download_url;
    cur.preview = inc.preview;
    cur.html = inc.html;
    cur.title = inc.title || cur.title;
    cur.filename = inc.filename || cur.filename;
    cur.error = inc.error;
    cur.error_category = inc.error_category;
  }
  return next;
}
