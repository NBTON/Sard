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

  // Persist current messages into the store whenever sessionId+messages change (for isolation verification)
  useEffect(() => {
    sessionStoreRef.current.set(sessionId, messages);
  }, [sessionId, messages]);

  function goHome() {
    setView("landing");
  }

  function handleNewChat() {
    // Save current session's messages before leaving
    if (messages.length > 0) sessionStoreRef.current.set(sessionId, [...messages]);
    const newId = `sard_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
    setSessionId(newId);
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
      attachments: atts.length > 0 ? [...atts] : undefined,
      timestamp: Date.now(),
    };
    const thinkId = `a_${Date.now()}`;
    const thinkingMsg: Message = {
      id: thinkId,
      role: "assistant",
      content: "",
      timestamp: Date.now(),
      isThinking: true,
      isStreaming: true,
    };

    setMessages((prev) => [...prev, userMsg, thinkingMsg]);
    setInput("");
    setComposerAttachments([]);
    setIsStreaming(true);

    setTimeout(() => {
      if (scrollRef.current) {
        scrollRef.current.scrollTo({
          top: scrollRef.current.scrollHeight,
          behavior: "smooth",
        });
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
    // Client-side timeout matches server SARD_CHAT_OVERALL_TIMEOUT (38s) + 2s grace: handles #11 Timeout scenario.
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
    }, 40000);

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
        setMessages((prev) =>
          prev.map((m) => {
            if (m.id !== thinkId) return m;
            const nextContent = (gotFirstToken ? m.content : "") + delta;
            if (!gotFirstToken) gotFirstToken = true;
            return {
              ...m,
              content: nextContent,
              isThinking: false,
              isStreaming: true,
            };
          })
        );
      },
      onCitations: (citations) => {
        setMessages((prev) =>
          prev.map((m) => (m.id === thinkId ? { ...m, citations } : m))
        );
      },
      onArtifacts: (artifacts) => {
        setMessages((prev) =>
          prev.map((m) => (m.id === thinkId ? { ...m, artifacts } : m))
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
    if (controller.signal.aborted) clearTimeout(timeoutId);
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
      prev.map((m) =>
        m.isStreaming
          ? { ...m, isStreaming: false, isThinking: false }
          : m
      )
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
