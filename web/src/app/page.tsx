"use client";
import React, { useState, useEffect, useRef } from "react";
import { Header } from "@/components/Header";
import { Landing } from "@/components/Landing";
import { ChatSidebar } from "@/components/Sidebar";
import { ChatMessages } from "@/components/ChatMessages";
import { Composer } from "@/components/Composer";
import { DirectionProvider, StageTurnContainer, useDirection } from "@/lib/direction";
import { Message, View } from "@/types";
import { streamChat } from "@/lib/api";

function ChatAppContent() {
  const { lang, toggleDirection } = useDirection();
  const [view, setView] = useState<View>("landing");
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [sessionId, setSessionId] = useState<string>(
    () => `sard_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`
  );
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  function goHome() {
    setView("landing");
  }

  function handleNewChat() {
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

  async function doSend(text: string) {
    const trimmed = text.trim();
    if (!trimmed || isStreaming) return;

    const userMsg: Message = {
      id: `u_${Date.now()}`,
      role: "user",
      content: trimmed,
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
    }));
    const controller = new AbortController();
    abortRef.current = controller;

    let gotFirstToken = false;

    await streamChat({
      messages: history,
      query: trimmed,
      sessionId,
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
  }

  function handleComposerSend() {
    doSend(input);
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
              <ChatMessages messages={messages} lang={lang} />
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
              />
            </div>
          </div>
        </div>
      )}

      <style>{`
        @media (max-width: 860px) {
          .chat-shell aside { display: none !important; }
        }
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
