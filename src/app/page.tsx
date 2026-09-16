"use client";
import React, { useState, useEffect, useRef } from "react";
import { Header } from "@/components/Header";
import { Landing } from "@/components/Landing";
import { ChatSidebar } from "@/components/Sidebar";
import { ChatMessages } from "@/components/ChatMessages";
import { Composer } from "@/components/Composer";
import { DirectionProvider, StageTurnContainer, useDirection } from "@/lib/direction";
import { Artifact, Attachment, Message, View, mergeArtifactVersions } from "@/types";
import { fetchRunStatus, streamChat, postArtifactRevision, DoneMeta, StatusDetail } from "@/lib/api";
import { ArtifactPanel } from "@/components/ArtifactPanel";
import {
  getActiveArtifactId,
  getActiveId,
  getLastRunId,
  loadSessions,
  setActiveArtifactId,
  setActiveId,
  setLastRunId,
  upsertSession,
} from "@/lib/storage";

interface RunProgress {
  stage: string;
  progress: number;
  message: string;
}

function ChatAppContent() {
  const { lang, toggleDirection } = useDirection();
  // Chat-first workspace: composer visible immediately on first load.
  const [view, setView] = useState<View>("chat");
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [composerAttachments, setComposerAttachments] = useState<Attachment[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [runProgress, setRunProgress] = useState<RunProgress | null>(null);
  const [selectedArtifact, setSelectedArtifact] = useState<Artifact | null>(null);
  const [lastRunId, setLastRunIdState] = useState<string | null>(() =>
    typeof window !== "undefined" ? getLastRunId() : null
  );
  const [sessionId, setSessionId] = useState<string>(
    () => `sard_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`
  );
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const selectedArtifactRef = useRef<Artifact | null>(null);
  selectedArtifactRef.current = selectedArtifact;
  const lastPromptRef = useRef<{ text: string; attachments: Attachment[] }>({ text: "", attachments: [] });
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;

  function persistRunId(runId: string | null) {
    setLastRunIdState(runId);
    setLastRunId(runId);
    if (runId && sessionIdRef.current) {
      try {
        const all = loadSessions();
        const cur = all.find((s) => s.id === sessionIdRef.current);
        if (cur) upsertSession({ ...cur, lastRunId: runId });
      } catch {
        // non-fatal
      }
    }
  }

  // Restore active session (unified store; legacy keys migrated inside loadSessions).
  useEffect(() => {
    try {
      const sessions = loadSessions();
      const activeId = getActiveId();
      const active = (activeId && sessions.find((s) => s.id === activeId)) || sessions[sessions.length - 1];
      if (active && active.messages.length > 0) {
        setSessionId(active.id);
        setMessages(active.messages);
        setView("chat");
        if (active.lastRunId) {
          setLastRunIdState(active.lastRunId);
          setLastRunId(active.lastRunId);
        }
        const wantedArtifact = active.activeArtifactId || getActiveArtifactId();
        if (wantedArtifact) {
          for (const m of active.messages) {
            const found = m.artifacts?.find((a) => a.id === wantedArtifact);
            if (found) {
              setSelectedArtifact(found);
              break;
            }
          }
        }
      } else if (activeId) {
        setSessionId(activeId);
      }
    } catch {
      // Safe fallback if storage is unavailable
    }
  }, []);

  // Persist current session whenever it changes (previews stripped by storage layer).
  useEffect(() => {
    if (typeof window === "undefined" || messages.length === 0) return;
    try {
      const all = loadSessions();
      const existing = all.find((s) => s.id === sessionId);
      upsertSession({
        id: sessionId,
        title:
          existing?.title ||
          messages.find((m) => m.role === "user")?.content.slice(0, 60) ||
          sessionId,
        messages,
        createdAt: existing?.createdAt || Date.now(),
        updatedAt: Date.now(),
        activeArtifactId: selectedArtifact?.id ?? existing?.activeArtifactId ?? null,
        lastRunId: lastRunId || existing?.lastRunId || getLastRunId(),
      });
      setActiveId(sessionId);
    } catch {
      // quota or privacy mode guard
    }
  }, [sessionId, messages, selectedArtifact, lastRunId]);

  // Keep the global active-artifact pointer in sync for cross-reload restore.
  useEffect(() => {
    setActiveArtifactId(selectedArtifact?.id ?? null);
  }, [selectedArtifact]);

  function goHome() {
    setView("chat");
  }

  function goExplore() {
    setView("explore");
  }

  function handleNewChat() {
    const newId = `sard_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
    setSessionId(newId);
    setActiveId(newId);
    setMessages([]);
    setView("chat");
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setIsStreaming(false);
    setRunProgress(null);
    setSelectedArtifact(null);
    setActiveArtifactId(null);
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
    lastPromptRef.current = { text: trimmed, attachments: [...atts] };

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
      statusProgress: 0.02,
    };

    setMessages((prev) => [...prev, userMsg, assistantPlaceholder]);
    setInput("");
    setComposerAttachments([]);
    setIsStreaming(true);
    setRunProgress({ stage: "init", progress: 0.02, message: assistantPlaceholder.statusStage || "" });

    // Smooth scroll down
    setTimeout(() => {
      if (scrollRef.current) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      }
    }, 50);

    const history = [...messages, userMsg]
      // Stopped turns never reach the backend twice: a retried prompt drops
      // its stale placeholder, so exclude stopped messages from history.
      .filter((m) => !(m as any).stopped)
      .map((m) => ({
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
        setRunProgress(null);
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
      onStatus: (statusText: string, detail?: StatusDetail) => {
        if (detail?.runId) persistRunId(detail.runId);
        const newProgress = detail?.progress ?? 0.4;
        setRunProgress((prev) => ({
          stage: detail?.stage || "working",
          progress: Math.max(prev?.progress ?? 0, Math.min(1, newProgress)),
          message: statusText,
        }));
        setMessages((prev) =>
          prev.map((m) =>
            m.id === thinkId
              ? {
                  ...m,
                  statusStage: statusText,
                  statusProgress: detail?.progress
                    ? Math.max(m.statusProgress ?? 0, Math.min(1, detail.progress))
                    : m.statusProgress ?? null,
                  runId: detail?.runId || m.runId || lastRunId,
                }
              : m
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
                    title: s.title || (s as any).origin || "",
                    source_url: s.source_url || (s as any).url || "",
                    source_name: s.source_name || (s as any).origin || "",
                  })),
                }
              : m
          )
        );
      },
      onArtifacts: (arts) => {
        // ACCUMULATE versions[] — never overwrite the existing list.
        setMessages((prev) =>
          prev.map((m) => {
            if (m.id === thinkId) {
              const merged = mergeArtifactVersions(m.artifacts || [], arts);
              // If selectedArtifact is one of these, update to merged instance with all versions
              const curSelId = selectedArtifactRef.current?.id;
              if (curSelId) {
                const updated = merged.find((a) => a.id === curSelId);
                if (updated) setSelectedArtifact(updated);
              }
              return { ...m, artifacts: merged };
            }
            return m;
          })
        );
        // Auto-select newest ready artifact if no artifact was previously open
        const ready = [...arts].reverse().find((a) => a.status === "created") || arts[arts.length - 1];
        if (ready && !selectedArtifactRef.current) {
          setSelectedArtifact(ready);
        }
      },

      onDone: (meta: DoneMeta) => {
        clearTimeout(timeoutId);
        if (meta?.run_id) persistRunId(meta.run_id);
        setIsStreaming(false);
        setRunProgress(null);
        abortRef.current = null;
        setMessages((prev) =>
          prev.map((m) =>
            m.id === thinkId
              ? {
                  ...m,
                  isThinking: false,
                  isStreaming: false,
                  runId: meta?.run_id || m.runId || null,
                  // A late completion after the client timeout fired must
                  // clear the stale timeout error once content arrived.
                  ...(m.content ? { error: undefined } : {}),
                }
              : m
          )
        );
      },
      onError: (err) => {
        clearTimeout(timeoutId);
        setIsStreaming(false);
        setRunProgress(null);
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
    setRunProgress(null);
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
    // Surface partial output + resume/retry instead of silently swallowing.
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setIsStreaming(false);
    setRunProgress(null);
    setMessages((prev) =>
      prev.map((m) =>
        m.isStreaming || m.isThinking
          ? { ...m, isStreaming: false, isThinking: false, stopped: true, runId: m.runId || lastRunId }
          : m
      )
    );
  }

  function handleRetry() {
    const { text, attachments } = lastPromptRef.current;
    if (!text && attachments.length === 0) return;
    // Drop the stopped turn before resending: retry reuses one
    // user/assistant pair instead of duplicating the user bubble.
    setMessages((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (last && last.role === "assistant" && (last as any).stopped) {
        next.pop();
        const maybeUser = next[next.length - 1];
        if (maybeUser && maybeUser.role === "user" && maybeUser.content === text) {
          next.pop();
        }
      }
      return next.map((m) => ((m as any).stopped ? { ...m, stopped: false } : m));
    });
    doSend(text, attachments);
  }

  async function handleResume(thinkId: string) {
    const runId =
      messages.find((m) => m.id === thinkId)?.runId || lastRunId;
    if (!runId) {
      handleRetry();
      return;
    }
    // Graceful fallback: endpoint missing (404/405) → null → resend the prompt.
    const resumed = await fetchRunStatus(runId);
    if (resumed?.artifacts && resumed.artifacts.length > 0) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === thinkId
            ? { ...m, stopped: false, artifacts: mergeArtifactVersions(m.artifacts || [], resumed.artifacts || []) }
            : m
        )
      );
    } else {
      handleRetry();
    }
  }

  function handleDismissStopped(thinkId: string) {
    setMessages((prev) => prev.map((m) => (m.id === thinkId ? { ...m, stopped: false } : m)));
  }

  /** Real revision execution: POST /api/artifacts/{id}/revisions */
  async function handleRevise(artifact: Artifact, instruction: string, format?: string) {
    const revised = await postArtifactRevision(artifact.id, instruction, format);
    // Update message turn containing this artifact
    setMessages((prev) =>
      prev.map((m) => {
        if (m.artifacts?.some((a) => a.id === artifact.id)) {
          return {
            ...m,
            artifacts: mergeArtifactVersions(m.artifacts, [revised]),
          };
        }
        return m;
      })
    );
    // Merge-then-select: accumulate versions[] history into the open panel
    // instead of replacing the selection with the raw revision snapshot
    // (which would hide v1 until GET /versions refetches).
    setSelectedArtifact((prev) => {
      if (!prev || prev.id !== revised.id) return revised;
      const merged = mergeArtifactVersions([prev], [revised]);
      return merged[0] ?? revised;
    });
  }


  // Auto-scroll on update
  const lastMessageContent = messages[messages.length - 1]?.content;
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, lastMessageContent]);

  const stoppedMessage = [...messages].reverse().find((m) => m.stopped);
  const panelOpen = selectedArtifact !== null;

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
        onGoExplore={goExplore}
        view={view}
      />

      {view === "explore" ? (
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
          {/* Chat column: full width alone, ~420px scroll when the panel docks. */}
          <div
            className="chat-column"
            style={{
              flex: panelOpen ? "0 0 420px" : 1,
              width: panelOpen ? 420 : undefined,
              display: "flex",
              flexDirection: "column",
              minWidth: 0,
              background: "#F3EEE4",
              position: "relative",
            }}
          >
            {/* Streaming progress bar (replaces the static "Generating..." state). */}
            {isStreaming && runProgress && (
              <div style={{ padding: "10px 18px 0", width: "100%" }} role="status" aria-live="polite">
                <div style={{ fontSize: 12, color: "#8A8178", marginBottom: 6, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {runProgress.message} • {Math.round(runProgress.progress * 100)}%
                </div>
                <div style={{ height: 6, background: "#E8E0D2", borderRadius: 999, overflow: "hidden" }}>
                  <div
                    style={{
                      width: `${Math.round(runProgress.progress * 100)}%`,
                      height: "100%",
                      background: "#BE4A24",
                      borderRadius: 999,
                      transition: "width 0.3s ease",
                    }}
                  />
                </div>
              </div>
            )}
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
                onStarter={(p) => doSend(p)}
              />
            </div>
            {/* Stopped-partial banner: resume / retry / dismiss (never silent). */}
            {stoppedMessage && !isStreaming && (
              <div
                role="status"
                style={{
                  margin: "0 auto",
                  maxWidth: 860,
                  width: "calc(100% - 36px)",
                  background: "rgba(190,74,36,0.08)",
                  border: "1.5px solid #BE4A24",
                  borderRadius: 12,
                  padding: "10px 14px",
                  fontSize: 13,
                  color: "#3A342E",
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  flexWrap: "wrap",
                }}
              >
                <span>
                  {lang === "ar"
                    ? "⏸️ توقّف التوليد — المحتوى الجزئي محفوظ."
                    : "⏸️ Generation stopped — partial content kept."}
                </span>
                <span style={{ display: "inline-flex", gap: 6, marginInlineStart: "auto" }}>
                  <button
                    onClick={() => handleResume(stoppedMessage.id)}
                    style={{ background: "#141210", color: "#FAF7F1", border: "none", borderRadius: 8, padding: "6px 12px", fontSize: 12, fontWeight: 700, cursor: "pointer" }}
                  >
                    {lang === "ar" ? "استئناف" : "Resume"}
                  </button>
                  <button
                    onClick={handleRetry}
                    style={{ background: "#F3EEE4", color: "#141210", border: "1px solid #D4CBBD", borderRadius: 8, padding: "6px 12px", fontSize: 12, fontWeight: 700, cursor: "pointer" }}
                  >
                    {lang === "ar" ? "إعادة المحاولة" : "Retry"}
                  </button>
                  <button
                    onClick={() => handleDismissStopped(stoppedMessage.id)}
                    style={{ background: "transparent", color: "#8A8178", border: "none", borderRadius: 8, padding: "6px 8px", fontSize: 12, cursor: "pointer" }}
                  >
                    ✕
                  </button>
                </span>
              </div>
            )}
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
          {/* Persistent artifact dock: opening it never destroys chat. */}
          {panelOpen && selectedArtifact && (
            <div className="artifact-dock" style={{ flex: 1, minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column" }}>
              <ArtifactPanel
                artifact={selectedArtifact}
                lang={lang}
                mode="panel"
                onClose={() => setSelectedArtifact(null)}
                onRevise={handleRevise}
              />
            </div>
          )}
        </div>
      )}

      <style>{`
        @media (max-width: 860px) {
          .chat-shell aside { display: none !important; }
        }
        @media (max-width: 640px) {
          .chat-column { flex: 1 1 auto !important; width: auto !important; }
          .artifact-dock { position: fixed; inset: 0; z-index: 9000; background: #FAF7F1; }
        }
        /* Mobile viewport: ensure composer and artifact tiles adapt; pills stack. */
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
