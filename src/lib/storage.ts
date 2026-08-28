import { ChatSession, Lang } from "@/types";

const SESSIONS_KEY = "sard_sessions_v3";
const ACTIVE_KEY = "sard_active_v3";
const LANG_KEY = "sard_lang_v3";

export function loadSessions(): ChatSession[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(SESSIONS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch { return []; }
}
export function saveSessions(s: ChatSession[]) {
  if (typeof window === "undefined") return;
  try { localStorage.setItem(SESSIONS_KEY, JSON.stringify(s)); } catch {}
}
export function getActiveId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACTIVE_KEY);
}
export function setActiveId(id: string | null) {
  if (typeof window === "undefined") return;
  if (id) localStorage.setItem(ACTIVE_KEY, id); else localStorage.removeItem(ACTIVE_KEY);
}
export function getStoredLang(): Lang {
  if (typeof window === "undefined") return "ar";
  return (localStorage.getItem(LANG_KEY) as Lang) === "en" ? "en" : "ar";
}
export function setStoredLang(l: Lang) {
  if (typeof window === "undefined") return;
  localStorage.setItem(LANG_KEY, l);
}
