import { ChatSession } from "@/types";

const SESSIONS_KEY = "sard_chat_sessions_v2";
const ACTIVE_SESSION_KEY = "sard_active_session_id_v2";
const THEME_KEY = "sard_theme_mode_v2";
const LANG_KEY = "sard_language_v2";

export function loadSessions(): ChatSession[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(SESSIONS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (e) {
    console.error("Error loading chat sessions from localStorage:", e);
    return [];
  }
}

export function saveSessions(sessions: ChatSession[]): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions));
  } catch (e) {
    console.error("Error saving chat sessions to localStorage:", e);
  }
}

export function getActiveSessionId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACTIVE_SESSION_KEY);
}

export function setActiveSessionId(id: string | null): void {
  if (typeof window === "undefined") return;
  if (id) {
    localStorage.setItem(ACTIVE_SESSION_KEY, id);
  } else {
    localStorage.removeItem(ACTIVE_SESSION_KEY);
  }
}

export function getStoredTheme(): "dark" | "light" | "moc" {
  if (typeof window === "undefined") return "dark";
  const t = localStorage.getItem(THEME_KEY);
  return (t === "light" || t === "dark" || t === "moc") ? t : "dark";
}

export function setStoredTheme(theme: "dark" | "light" | "moc"): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(THEME_KEY, theme);
}

export function getStoredLang(): "ar" | "en" {
  if (typeof window === "undefined") return "ar";
  const l = localStorage.getItem(LANG_KEY);
  return l === "en" ? "en" : "ar";
}

export function setStoredLang(lang: "ar" | "en"): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(LANG_KEY, lang);
}
