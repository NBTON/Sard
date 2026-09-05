"use client";
import React, { useState, useEffect, useRef } from "react";
import { Header } from "@/components/Header";
import { Landing } from "@/components/Landing";
import { ChatSidebar } from "@/components/Sidebar";
import { ChatMessages } from "@/components/ChatMessages";
import { Composer } from "@/components/Composer";
import { DirectionProvider, StageTurnContainer, useDirection } from "@/lib/direction";
import { Artifact, Attachment, Message, View } from "@/types";
import { streamChat } from "@/lib/api";
import { ArtifactModal } from "@/components/ArtifactModal";

function ChatAppContent() {
  const { lang, toggleDirection } = useDirection();
  const [view, setView] = useState<View>("landing");
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [composerAttachments, setComposerAttachments] = useState<Attachment[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [selectedArtifact, setSelectedArtifact] = useState<Artifact | null>(null);
  const [sessionId, setSessionId] = useState<string>(
    () => `sard_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`
  );
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  // Session isolation: keep per-session message history so two sessions never intermix.
  // This satisfies Browser Test #13 (Two sessions) and guards against stale-history contamination.
  const sessionStoreRef = useRef<Map<string, Message[]>>(new Map());

  // Restore active session on mount
  useEffect(() => {
    if (typeof window !== "undefined") {
      try {
        const lastSession = localStorage.getItem("sard_active_session");
        if (lastSession) {
          const stored = localStorage.getItem(`sard_session_${lastSession}`);
          if (stored) {
            const parsed = JSON.parse(stored);
            if (Array.isArray(parsed) && parsed.length > 0) {
              setSessionId(lastSession);
              setMessages(parsed);
              sessionStoreRef.current.set(lastSession, parsed);
              setView("chat");
            }
          }
        }
      } catch (e) {
        // Safe fallback if localStorage is unavailable
      }
    }
  }, []);

  // Persist current messages into the store and localStorage whenever sessionId+messages change
  useEffect(() => {
    sessionStoreRef.current.set(sessionId, messages);
    if (typeof window !== "undefined" && messages.length > 0) {
      try {
        localStorage.setItem(`sard_session_${sessionId}`, JSON.stringify(messages));
        localStorage.setItem("sard_active_session", sessionId);
      } catch (e) {
        // quota or privacy mode guard
      }
    }
  }, [sessionId, messages]);

  function goHome() {
    setView("landing");
  }

  function handleNewChat() {
    // Save current session's messages before leaving
    if (messages.length > 0) {
      sessionStoreRef.current.set(sessionId, [...messages]);
      if (typeof window !== "undefined") {
        try {
          localStorage.setItem(`sard_session_${sessionId}`, JSON.stringify(messages));
        } catch (e) {}
      }
    }
    const newId = `sard_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
    setSessionId(newId);
    if (typeof window !== "undefined") {
      try {
        localStorage.setItem("sard_active_session", newId);
      } catch (e) {}
    }
    setMessages([]);
    setView("chat");
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setIsStreaming(false);
    setInput("");
    setComposerAttachments([]);
  }

  function openChat() {
    setView("chat");
  }

  async function sendPrompt(prompt: string) {
    const trimmed = prompt.trim();
    if (!trimmed || isStreaming) return;
    setView("chat");
    await doSend(trimmed);
  }

  async function doSend(text: string, currentAttachments?: Attachment[]) {
    const trimmed = text.trim();
    const atts = currentAttachments || composerAttachments;
    if ((!trimmed && atts.length === 0) || isStreaming) return;

    const userMsg: Message = {
      id: `u_${Date.now()}`,
      role: "user",
      content: trimmed,
      timestamp: Date.now(),
      attachments: atts.length > 0 ? [...atts] : undefined,
    };
    const thinkId = `a_${Date.now()}`;
    const assistantPlaceholder: Message = {
      id: thinkId,
      role: "assistant",
      content: "",
      timestamp: Date.now(),
      isThinking: true,
      statusStage: lang === "en" ? "Analyzing request..." : "جارٍ تحليل الطلب واستكشاف التراث المعتمد...",
    };

    setMessages((prev) => [...prev, userMsg, assistantPlaceholder]);
    setInput("");
    setComposerAttachments([]);
    setIsStreaming(true);

    // Smooth scroll down
    setTimeout(() => {
      if (scrollRef.current) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      }
    }, 50);

    const history = [...messages, userMsg].map((m) => ({
      role: m.role,
      content: m.content,
      attachments: m.attachments?.map((a) => ({
        attachment_id: a.attachment_id,
        filename: a.filename,
        mime_type: a.mime_type,
        size_bytes: a.size_bytes,
      })),
    }));
    const controller = new AbortController();
    abortRef.current = controller;
    // Client-side timeout provides 15s slack over backend overall deadline (35s)
    const timeoutId = setTimeout(() => {
      if (!controller.signal.aborted) {
        controller.abort();
        // onError will surface timeout hedge; ensure UI exits streaming even if stream never emits done
        setIsStreaming(false);
        abortRef.current = null;
        setMessages((prev) =>
          prev.map((m) =>
            m.id === thinkId
              ? {
                  ...m,
                  isThinking: false,
                  isStreaming: false,
                  error:
                    lang === "en"
                      ? "Request timed out. Please try again."
                      : "انتهت مهلة الطلب. حاول مرة أخرى.",
                }
              : m
          )
        );
      }
    }, 50000);

    let gotFirstToken = false;

    await streamChat({
      messages: history,
      query: trimmed,
      attachments: atts,
      sessionId,
      lang,
      signal: controller.signal,
      onStatus: (statusText) => {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === thinkId ? { ...m, statusStage: statusText } : m
          )
        );
      },
      onDelta: (delta) => {
        if (!gotFirstToken) {
          gotFirstToken = true;
          setMessages((prev) =>
            prev.map((m) =>
              m.id === thinkId
                ? { ...m, isThinking: false, isStreaming: true, content: delta }
                : m
            )
          );
        } else {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === thinkId ? { ...m, content: m.content + delta } : m
            )
          );
        }
      },
      onCitations: (citations) => {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === thinkId
              ? {
                  ...m,
                  citations: citations.map((s, idx) => ({
                    citation_id: s.citation_id || (s as any).id || `src-${idx}`,
                    title: s.title || (s as any).origin || "مرجع تراثي",
                    source_url: s.source_url || (s as any).url || "",
                    source_name: s.source_name || (s as any).origin || "وزارة الثقافة",
                  })),
                }
              : m
          )
        );
      },
      onArtifacts: (arts) => {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === thinkId
              ? {
                  ...m,
                  artifacts: arts.map((a) => ({
                    id: a.id || (a as any).artifact_id || `art-${Date.now()}`,
                    title: a.title || "مخرج ثقافي",
                    format: (a.format || (a as any).type || "unknown") as any,
                    download_url: a.download_url ?? (a as any).url ?? null,
                    filename: a.filename || "file",
                    mime_type: a.mime_type || "application/octet-stream",
                    size_bytes: a.size_bytes || 0,
                    status: (a.status as any) || "created",
                    preview: a.preview || (a as any).data,
                    kind: (a.kind as any) || "document",
                  })),
                }
              : m
          )
        );
      },
      onDone: () => {
        clearTimeout(timeoutId);
        setIsStreaming(false);
        abortRef.current = null;
        setMessages((prev) =>
          prev.map((m) =>
            m.id === thinkId
              ? { ...m, isThinking: false, isStreaming: false }
              : m
          )
        );
      },
      onError: (err) => {
        clearTimeout(timeoutId);
        setIsStreaming(false);
        abortRef.current = null;
        // Ignore AbortError (user cancelled) - already handled via handleStop/timeout
        if ((err as any)?.name === "AbortError") return;
        setMessages((prev) =>
          prev.map((m) =>
            m.id === thinkId
              ? {
                  ...m,
                  isThinking: false,
                  isStreaming: false,
                  error:
                    lang === "en"
                      ? "Could not complete the answer. Please try again."
                      : "تعذّر إتمام الإجابة. حاول مرة أخرى.",
                  content: m.content || "",
                }
              : m
          )
        );
        console.error("Chat error:", err);
      },
    });
    // Ensure timeout cleared if stream exits via abort before done/error
    // (streamChat returns early on AbortError without calling onDone/onError)
    clearTimeout(timeoutId);
    setIsStreaming(false);
    abortRef.current = null;
    // P1-2: guarantee per-message streaming flags never stick true.
    setMessages((prev) =>
      prev.map((m) => (m.isStreaming ? { ...m, isStreaming: false, isThinking: false } : m))
    );
  }

  function handleComposerSend(text: string, currentAttachments?: Attachment[]) {
    doSend(text, currentAttachments);
  }

  function handleStop() {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setIsStreaming(false);
    setMessages((prev) =>
      prev.map((m) => (m.isStreaming ? { ...m, isStreaming: false, isThinking: false } : m))
    );
  }

  // Auto-scroll on update
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, messages[messages.length - 1]?.content]);

  return (
    <StageTurnContainer
      style={{
        height: "100vh",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        background: "#F3EEE4",
        position: "relative",
      }}
    >
      <Header
        lang={lang}
        onToggleLang={() => toggleDirection()}
        onGoHome={goHome}
        view={view}
      />

      {view === "landing" ? (
        <Landing
          lang={lang}
          onStartChat={openChat}
          onSectorPrompt={(prompt) => sendPrompt(prompt)}
          onSeedPrompt={(prompt) => sendPrompt(prompt)}
        />
      ) : (
        <div
          style={{ flex: 1, display: "flex", minHeight: 0, overflow: "hidden" }}
          className="chat-shell"
        >
          <ChatSidebar
            lang={lang}
            onNewChat={handleNewChat}
            onStarter={(p) => doSend(p)}
            open={true}
          />
          <div
            style={{
              flex: 1,
              display: "flex",
              flexDirection: "column",
              minWidth: 0,
              background: "#F3EEE4",
              position: "relative",
            }}
          >
            <div
              ref={scrollRef}
              style={{
                flex: 1,
                overflowY: "auto",
                minHeight: 0,
                position: "relative",
                zIndex: 1,
              }}
            >
              <ChatMessages
                messages={messages}
                lang={lang}
                onSelectArtifact={(art) => setSelectedArtifact(art)}
              />
            </div>
            <div
              style={{
                position: "relative",
                zIndex: 1,
                borderTop: "1px solid transparent",
              }}
            >
              <Composer
                lang={lang}
                value={input}
                onChange={setInput}
                onSend={handleComposerSend}
                onStop={handleStop}
                isStreaming={isStreaming}
                attachments={composerAttachments}
                onAttachmentsChange={setComposerAttachments}
              />
            </div>
          </div>
        </div>
      )}

      {/* Artifact Interactive Modal Preview Suite */}
      <ArtifactModal
        artifact={selectedArtifact}
        onClose={() => setSelectedArtifact(null)}
        lang={lang}
      />

      <style>{`
        @media (max-width: 860px) {
          .chat-shell aside { display: none !important; }
        }
        /* Mobile viewport: ensure composer and artifact tiles adapt */
        @media (max-width: 640px) {
          .chat-shell { flex-direction: column; }
          [data-testid^="artifact-"] { flex-basis: 100%; min-width: 0; }
        }
        /* RTL layout: ensure dir=rtl for Arabic content verified via data-dir attributes */
        [dir="rtl"] .sard-prose { direction: rtl; text-align: right; }
        [dir="ltr"] .sard-prose { direction: ltr; text-align: left; }
      `}</style>
    </StageTurnContainer>
  );
}

export default function Home() {
  return (
    <DirectionProvider>
      <ChatAppContent />
    </DirectionProvider>
  );
}
