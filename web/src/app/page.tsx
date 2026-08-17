"use client";

import React, { useState, useEffect, useRef } from "react";
import { Header } from "@/components/Header";
import { Sidebar } from "@/components/Sidebar";
import { ChatContainer } from "@/components/ChatContainer";
import { ChatInput } from "@/components/ChatInput";
import { SettingsModal } from "@/components/SettingsModal";
import { Message, ChatSession, SystemStatus } from "@/types";
import {
  loadSessions,
  saveSessions,
  getActiveSessionId,
  setActiveSessionId,
  getStoredTheme,
  setStoredTheme,
  getStoredLang,
  setStoredLang,
} from "@/lib/storage";
import { streamChat, fetchSystemStatus } from "@/lib/api";

export default function Home() {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionIdState] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const [theme, setTheme] = useState<"dark" | "light" | "moc">("dark");
  const [lang, setLang] = useState<"ar" | "en">("ar");
  const [isSidebarOpen, setIsSidebarOpen] = useState<boolean>(true);
  const [isSettingsOpen, setIsSettingsOpen] = useState<boolean>(false);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);

  const abortControllerRef = useRef<AbortController | null>(null);

  // Initialize from localStorage and fetch status
  useEffect(() => {
    const loadedSessions = loadSessions();
    setSessions(loadedSessions);

    const savedId = getActiveSessionId();
    if (savedId && loadedSessions.some((s) => s.id === savedId)) {
      setActiveSessionIdState(savedId);
    } else if (loadedSessions.length > 0) {
      setActiveSessionIdState(loadedSessions[0].id);
      setActiveSessionId(loadedSessions[0].id);
    }

    const savedTheme = getStoredTheme();
    setTheme(savedTheme);
    document.body.className = `theme-${savedTheme}`;

    const savedLang = getStoredLang();
    setLang(savedLang);
    document.documentElement.dir = savedLang === "en" ? "ltr" : "rtl";
    document.documentElement.lang = savedLang;

    // Fetch live system status
    fetchSystemStatus().then((status) => {
      if (status) setSystemStatus(status);
    });

    // Close sidebar on mobile screens initially
    if (typeof window !== "undefined" && window.innerWidth < 768) {
      setIsSidebarOpen(false);
    }
  }, []);

  // Update body class on theme change
  useEffect(() => {
    document.body.className = `theme-${theme}`;
    setStoredTheme(theme);
  }, [theme]);

  // Update HTML dir on language change
  useEffect(() => {
    document.documentElement.dir = lang === "en" ? "ltr" : "rtl";
    document.documentElement.lang = lang;
    setStoredLang(lang);
  }, [lang]);

  // Keyboard shortcut for New Chat (Ctrl+K / Cmd+K)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        handleNewChat();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [sessions]);

  const activeSession = sessions.find((s) => s.id === activeSessionId) || null;
  const messages = activeSession ? activeSession.messages : [];

  const handleNewChat = () => {
    if (isStreaming) {
      handleStopGeneration();
    }
    const newSession: ChatSession = {
      id: `session_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`,
      title: lang === "en" ? "New Cultural Conversation" : "محادثة ثقافية جديدة",
      messages: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };

    const updated = [newSession, ...sessions];
    setSessions(updated);
    saveSessions(updated);
    setActiveSessionIdState(newSession.id);
    setActiveSessionId(newSession.id);

    // On mobile, close sidebar when new chat is started
    if (typeof window !== "undefined" && window.innerWidth < 768) {
      setIsSidebarOpen(false);
    }
  };

  const handleSelectSession = (id: string) => {
    if (isStreaming) {
      handleStopGeneration();
    }
    setActiveSessionIdState(id);
    setActiveSessionId(id);
    if (typeof window !== "undefined" && window.innerWidth < 768) {
      setIsSidebarOpen(false);
    }
  };

  const handleDeleteSession = (id: string) => {
    const updated = sessions.filter((s) => s.id !== id);
    setSessions(updated);
    saveSessions(updated);

    if (activeSessionId === id) {
      const nextId = updated.length > 0 ? updated[0].id : null;
      setActiveSessionIdState(nextId);
      setActiveSessionId(nextId);
    }
  };

  const handleRenameSession = (id: string, newTitle: string) => {
    const updated = sessions.map((s) => (s.id === id ? { ...s, title: newTitle, updatedAt: Date.now() } : s));
    setSessions(updated);
    saveSessions(updated);
  };

  const handleClearSession = () => {
    if (!activeSessionId) return;
    const updated = sessions.map((s) => (s.id === activeSessionId ? { ...s, messages: [], updatedAt: Date.now() } : s));
    setSessions(updated);
    saveSessions(updated);
  };

  const handleClearAllSessions = () => {
    setSessions([]);
    saveSessions([]);
    setActiveSessionIdState(null);
    setActiveSessionId(null);
  };

  const handleToggleTheme = () => {
    const nextTheme = theme === "dark" ? "moc" : theme === "moc" ? "light" : "dark";
    setTheme(nextTheme);
  };

  const handleToggleLang = () => {
    const nextLang = lang === "ar" ? "en" : "ar";
    setLang(nextLang);
  };

  const handleStopGeneration = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setIsStreaming(false);
  };

  const handleSendMessage = async (text: string) => {
    if (!text.trim() || isStreaming) return;

    let targetSessionId = activeSessionId;
    let currentSessions = [...sessions];

    // Auto-create session if none active
    if (!targetSessionId || !currentSessions.some((s) => s.id === targetSessionId)) {
      const newSession: ChatSession = {
        id: `session_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`,
        title: text.substring(0, 36) + (text.length > 36 ? "..." : ""),
        messages: [],
        createdAt: Date.now(),
        updatedAt: Date.now(),
      };
      currentSessions = [newSession, ...currentSessions];
      targetSessionId = newSession.id;
      setActiveSessionIdState(targetSessionId);
      setActiveSessionId(targetSessionId);
    }

    const userMsg: Message = {
      id: `msg_${Date.now()}_user`,
      role: "user",
      content: text,
      timestamp: Date.now(),
    };

    const assistantMsgId = `msg_${Date.now()}_agent`;
    const assistantMsg: Message = {
      id: assistantMsgId,
      role: "assistant",
      content: "",
      timestamp: Date.now(),
      isStreaming: true,
      statusText: lang === "en" ? "Analyzing inquiry & retrieving cultural archives..." : "جارٍ تحليل الاستفسار واسترجاع المعارف الثقافية...",
    };

    // Update active session with user and placeholder assistant message
    const updatedWithUser = currentSessions.map((s) => {
      if (s.id === targetSessionId) {
        const isFirst = s.messages.length === 0;
        const newTitle = isFirst ? text.substring(0, 36) + (text.length > 36 ? "..." : "") : s.title;
        return {
          ...s,
          title: newTitle,
          messages: [...s.messages, userMsg, assistantMsg],
          updatedAt: Date.now(),
        };
      }
      return s;
    });

    setSessions(updatedWithUser);
    saveSessions(updatedWithUser);
    setIsStreaming(true);

    const controller = new AbortController();
    abortControllerRef.current = controller;

    // Stream the response
    await streamChat({
      messages: [...(activeSession?.messages || []), userMsg].map((m) => ({
        role: m.role,
        content: m.content,
      })),
      query: text,
      sessionId: targetSessionId,
      signal: controller.signal,
      onStatus: (statusText) => {
        setSessions((prev) =>
          prev.map((s) =>
            s.id === targetSessionId
              ? {
                  ...s,
                  messages: s.messages.map((m) => (m.id === assistantMsgId ? { ...m, statusText } : m)),
                }
              : s
          )
        );
      },
      onCitations: (citations) => {
        setSessions((prev) =>
          prev.map((s) =>
            s.id === targetSessionId
              ? {
                  ...s,
                  messages: s.messages.map((m) => (m.id === assistantMsgId ? { ...m, citations } : m)),
                }
              : s
          )
        );
      },
      onDelta: (deltaText) => {
        setSessions((prev) =>
          prev.map((s) =>
            s.id === targetSessionId
              ? {
                  ...s,
                  messages: s.messages.map((m) =>
                    m.id === assistantMsgId ? { ...m, content: m.content + deltaText } : m
                  ),
                }
              : s
          )
        );
      },
      onArtifacts: (artifacts) => {
        setSessions((prev) =>
          prev.map((s) =>
            s.id === targetSessionId
              ? {
                  ...s,
                  messages: s.messages.map((m) => (m.id === assistantMsgId ? { ...m, artifacts } : m)),
                }
              : s
          )
        );
      },
      onDone: (meta) => {
        setIsStreaming(false);
        abortControllerRef.current = null;

        setSessions((prev) => {
          const finalSessions = prev.map((s) =>
            s.id === targetSessionId
              ? {
                  ...s,
                  messages: s.messages.map((m) =>
                    m.id === assistantMsgId
                      ? {
                          ...m,
                          isStreaming: false,
                          statusText: undefined,
                          timings: meta.timings_ms,
                          modelUsed: meta.model,
                          retrievalMode: meta.retrieval_mode,
                        }
                      : m
                  ),
                }
              : s
          );
          saveSessions(finalSessions);
          return finalSessions;
        });
      },
      onError: (err) => {
        setIsStreaming(false);
        abortControllerRef.current = null;

        setSessions((prev) =>
          prev.map((s) =>
            s.id === targetSessionId
              ? {
                  ...s,
                  messages: s.messages.map((m) =>
                    m.id === assistantMsgId
                      ? {
                          ...m,
                          isStreaming: false,
                          statusText: undefined,
                          content:
                            m.content ||
                            (lang === "en"
                              ? "An error occurred while connecting to the Sard assistant. Please try again."
                              : "حدث خطأ أثناء الاتصال بالمساعد الثقافي. يُرجى المحاولة مرة أخرى."),
                        }
                      : m
                  ),
                }
              : s
          )
        );
      },
    });
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-transparent">
      {/* Sidebar */}
      <Sidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        systemStatus={systemStatus}
        onSelectSession={handleSelectSession}
        onNewChat={handleNewChat}
        onDeleteSession={handleDeleteSession}
        onRenameSession={handleRenameSession}
        onOpenSettings={() => setIsSettingsOpen(true)}
        isOpen={isSidebarOpen}
        onToggle={() => setIsSidebarOpen(!isSidebarOpen)}
        isEn={lang === "en"}
      />

      {/* Main Chat View */}
      <div className="flex-1 flex flex-col h-full min-w-0 overflow-hidden relative">
        {/* Header */}
        <Header
          activeSessionTitle={activeSession?.title}
          systemStatus={systemStatus}
          theme={theme}
          isEn={lang === "en"}
          onToggleSidebar={() => setIsSidebarOpen(!isSidebarOpen)}
          onToggleTheme={handleToggleTheme}
          onToggleLang={handleToggleLang}
          onClearSession={messages.length > 0 ? handleClearSession : undefined}
        />

        {/* Message Container */}
        <ChatContainer
          messages={messages}
          onSelectPrompt={handleSendMessage}
          isEn={lang === "en"}
        />

        {/* Floating Chat Input */}
        <ChatInput
          onSendMessage={handleSendMessage}
          onStopGeneration={handleStopGeneration}
          isStreaming={isStreaming}
          isEn={lang === "en"}
        />
      </div>

      {/* Settings Modal */}
      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        systemStatus={systemStatus}
        theme={theme}
        isEn={lang === "en"}
        sessions={sessions}
        onSelectTheme={setTheme}
        onToggleLang={handleToggleLang}
        onClearAllSessions={handleClearAllSessions}
      />
    </div>
  );
}
