import { Artifact, ArtifactVersion, ChatSession, Lang, Message } from "@/types";

// Canonical keys. Legacy page.tsx keys ("sard_active_session",
// "sard_session_<id>") are migrated on read — see migrateLegacySessions().
const SESSIONS_KEY = "sard_sessions_v3";
const ACTIVE_KEY = "sard_active_v3";
const LANG_KEY = "sard_lang_v3";
const LAST_RUN_KEY = "sard_last_run_v3";
const ACTIVE_ARTIFACT_KEY = "sard_active_artifact_v3";

const LEGACY_ACTIVE_KEY = "sard_active_session";
const LEGACY_SESSION_PREFIX = "sard_session_";

// Previews larger than this (serialized chars) are NOT persisted:
// we store metadata + download_url and re-fetch preview lazily.
const PREVIEW_BLOB_BUDGET = 4096;
const MAX_SESSIONS = 20;

function safeGet(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

/** Strip preview blobs over budget; keep metadata + URL for lazy reload. */
export function stripPreviewForStorage(preview: unknown): unknown {
  if (preview == null) return preview;
  if (typeof preview === "string") {
    return preview.length <= PREVIEW_BLOB_BUDGET
      ? preview
      : { _lazyPreview: true, _truncatedChars: preview.length };
  }
  if (typeof preview !== "object") return preview;
  let serialized = "";
  try {
    serialized = JSON.stringify(preview);
  } catch {
    return { _lazyPreview: true, _unserializable: true };
  }
  if (serialized.length <= PREVIEW_BLOB_BUDGET) return preview;
  // Keep scalar summary fields + drop known blob carriers (html/to_preview/slides bodies).
  const out: Record<string, unknown> = { _lazyPreview: true };
  for (const [k, v] of Object.entries(preview as Record<string, unknown>)) {
    if (k === "html" || k === "to_preview") {
      out[k] =
        typeof v === "string"
          ? { _lazyPreview: true, _truncatedChars: v.length }
          : { _lazyPreview: true };
      continue;
    }
    if (v == null || typeof v === "string" || typeof v === "number" || typeof v === "boolean") {
      const s = typeof v === "string" ? v : JSON.stringify(v);
      if (s.length <= 512) out[k] = v;
      continue;
    }
    if (Array.isArray(v)) {
      out[k] = { _lazyPreview: true, _count: v.length };
      continue;
    }
  }
  return out;
}

/** Storage-safe artifact: versions[] metadata persisted, preview blobs dropped. */
export function sanitizeArtifactForStorage(a: Artifact): Artifact {
  const versions: ArtifactVersion[] | undefined = Array.isArray(a.versions)
    ? a.versions.slice(-10).map((v) => ({
        version: v.version,
        id: v.id,
        download_url: v.download_url,
        status: v.status,
        created_at: v.created_at,
        checksum: v.checksum,
        preview: stripPreviewForStorage(v.preview) as ArtifactVersion["preview"],
      }))
    : undefined;
  return {
    ...a,
    preview: stripPreviewForStorage(a.preview) as Artifact["preview"],
    html: typeof a.html === "string" && a.html.length > PREVIEW_BLOB_BUDGET ? undefined : a.html,
    versions,
  };
}

export function sanitizeMessageForStorage(m: Message): Message {
  if (!m.artifacts || m.artifacts.length === 0) return m;
  return { ...m, artifacts: m.artifacts.map(sanitizeArtifactForStorage) };
}

function sanitizeSession(s: ChatSession): ChatSession {
  return {
    ...s,
    messages: Array.isArray(s.messages) ? s.messages.map(sanitizeMessageForStorage) : [],
  };
}

function isValidSession(s: unknown): s is ChatSession {
  return (
    !!s &&
    typeof s === "object" &&
    typeof (s as ChatSession).id === "string" &&
    Array.isArray((s as ChatSession).messages)
  );
}

/** One-way migration from legacy per-session keys into the canonical store. */
export function migrateLegacySessions(): ChatSession[] {
  if (typeof window === "undefined") return [];
  const migrated: ChatSession[] = [];
  try {
    const legacyActive = localStorage.getItem(LEGACY_ACTIVE_KEY);
    const ids = new Set<string>();
    if (legacyActive) ids.add(legacyActive);
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith(LEGACY_SESSION_PREFIX)) {
        ids.add(k.slice(LEGACY_SESSION_PREFIX.length));
      }
    }
    for (const id of ids) {
      try {
        const raw = localStorage.getItem(`${LEGACY_SESSION_PREFIX}${id}`);
        if (!raw) continue;
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) continue;
        migrated.push({
          id,
          title: id,
          messages: parsed,
          createdAt: Date.now(),
          updatedAt: Date.now(),
        });
      } catch {
        // skip corrupt legacy entries
      }
    }
  } catch {
    return [];
  }
  return migrated.filter(isValidSession).map(sanitizeSession);
}

export function loadSessions(): ChatSession[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = safeGet(SESSIONS_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return parsed.filter(isValidSession);
    }
  } catch {
    // fall through to legacy migration
  }
  const migrated = migrateLegacySessions();
  if (migrated.length > 0) {
    saveSessions(migrated);
    const legacyActive = safeGet(LEGACY_ACTIVE_KEY);
    if (legacyActive && migrated.some((s) => s.id === legacyActive)) {
      setActiveId(legacyActive);
    }
  }
  return migrated;
}

export function saveSessions(s: ChatSession[]) {
  if (typeof window === "undefined") return;
  const trimmed = s.slice(-MAX_SESSIONS).map(sanitizeSession);
  try {
    safeSet(SESSIONS_KEY, JSON.stringify(trimmed));
  } catch {
    // Quota pressure: retry with ALL previews dropped (metadata + URL only).
    try {
      const aggressive = trimmed.map((sess) => ({
        ...sess,
        messages: sess.messages.map((m) =>
          m.artifacts?.length
            ? {
                ...m,
                artifacts: m.artifacts.map((a) => ({
                  ...a,
                  preview: undefined,
                  html: undefined,
                  card_data: undefined,
                  data: undefined,
                  versions: a.versions?.map((v) => ({
                    version: v.version,
                    id: v.id,
                    download_url: v.download_url,
                    status: v.status,
                    created_at: v.created_at,
                  })),
                })),
              }
            : m
        ),
      }));
      safeSet(SESSIONS_KEY, JSON.stringify(aggressive));
    } catch {
      // storage unavailable (privacy mode) — chat still works in-memory
    }
  }
}

export function upsertSession(session: ChatSession) {
  const all = loadSessions();
  const idx = all.findIndex((s) => s.id === session.id);
  const clean = sanitizeSession(session);
  if (idx >= 0) all[idx] = { ...all[idx], ...clean };
  else all.push(clean);
  saveSessions(all);
}

export function getActiveId(): string | null {
  if (typeof window === "undefined") return null;
  return safeGet(ACTIVE_KEY) ?? safeGet(LEGACY_ACTIVE_KEY);
}

export function setActiveId(id: string | null) {
  if (typeof window === "undefined") return;
  try {
    if (id) localStorage.setItem(ACTIVE_KEY, id);
    else localStorage.removeItem(ACTIVE_KEY);
  } catch {
    // ignore
  }
}

export function getStoredLang(): Lang {
  if (typeof window === "undefined") return "ar";
  return safeGet(LANG_KEY) === "en" ? "en" : "ar";
}

export function setStoredLang(l: Lang) {
  if (typeof window === "undefined") return;
  safeSet(LANG_KEY, l);
}

export function getLastRunId(): string | null {
  if (typeof window === "undefined") return null;
  return safeGet(LAST_RUN_KEY);
}

export function setLastRunId(runId: string | null) {
  if (typeof window === "undefined") return;
  try {
    if (runId) localStorage.setItem(LAST_RUN_KEY, runId);
    else localStorage.removeItem(LAST_RUN_KEY);
  } catch {
    // ignore
  }
}

export function getActiveArtifactId(): string | null {
  if (typeof window === "undefined") return null;
  return safeGet(ACTIVE_ARTIFACT_KEY);
}

export function setActiveArtifactId(artifactId: string | null) {
  if (typeof window === "undefined") return;
  try {
    if (artifactId) localStorage.setItem(ACTIVE_ARTIFACT_KEY, artifactId);
    else localStorage.removeItem(ACTIVE_ARTIFACT_KEY);
  } catch {
    // ignore
  }
}
