"use client";
import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Artifact, Citation, Lang, Message } from "@/types";
import { SardMiniMark, ThinkingWeave } from "./SardMark";
import { t } from "@/lib/copy";

function cleanContent(text: string): string {
  if (!text) return "";
  // 1. Normalize HTML break tags to markdown newlines
  let cleaned = text.replace(/<\s*br\s*\/?>/gi, "\n");
  // 2. Strip any bracketed developer markers if leaked
  cleaned = cleaned.replace(/[\[【]\s*(?:RAG|Web|Media|CIT|cit)[\s:-][^\]】]*?[\]】]/gi, "");
  cleaned = cleaned.replace(/\[\s*CIT-[A-Za-z0-9_-]+\s*\]/gi, "");
  return cleaned.trim();
}

function UserBubble({ m, lang }: { m: Message; lang: Lang }) {
  const isAr = lang === "ar";
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "flex-end",
        marginBottom: 18,
        width: "100%",
      }}
    >
      <div
        data-dir-animate="user-bubble"
        data-dir-id={m.id}
        style={{
          maxWidth: "75%",
          background: "#141210",
          color: "#F3EEE4",
          borderRadius: 18,
          borderEndEndRadius: 4,
          borderEndStartRadius: 18,
          borderStartStartRadius: 18,
          borderStartEndRadius: 18,
          padding: "13px 20px",
          fontSize: 15,
          lineHeight: 1.75,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          boxShadow: "0 2px 10px -2px rgba(20,18,16,0.14)",
          animation: "fade-in 0.2s ease",
          textAlign: "start",
        }}
        dir={isAr ? "rtl" : "ltr"}
      >
        {m.content}
      </div>
    </div>
  );
}

function AgentCard({
  m,
  lang,
  onSelectArtifact,
}: {
  m: Message;
  lang: Lang;
  onSelectArtifact?: (artifact: Artifact) => void;
}) {
  const isAr = lang === "ar";
  // Deduplicate citations by unique source / citation_id
  const citations = React.useMemo(() => {
    if (!m.citations || m.citations.length === 0) return [];
    const seen = new Set<string>();
    return m.citations.filter((c) => {
      const key = c.citation_id || c.source_url || c.title || "";
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [m.citations]);

  const isVerified = Boolean(citations.length > 0);
  const formattedContent = React.useMemo(() => cleanContent(m.content), [m.content]);

  return (
    <div
      data-dir-animate="agent-card"
      data-dir-id={m.id}
      style={{
        background: "#FAF7F1",
        border: m.error ? "1px solid #BE4A24" : "1px solid #D4CBBD",
        borderRadius: 18,
        padding: "18px 20px",
        marginBottom: 20,
        boxShadow: "0 2px 18px -6px rgba(20,18,16,0.07)",
        animation: "fade-in 0.25s ease",
      }}
    >
      {/* Meta Row: tiny clay mark + سرد */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: m.isThinking ? 10 : 14,
        }}
      >
        <SardMiniMark />
        <span
          style={{
            fontFamily: "'Noto Naskh Arabic', serif",
            fontSize: 14,
            fontWeight: 700,
            color: "#BE4A24",
            letterSpacing: 0.2,
          }}
        >
          سرد
        </span>
        <span style={{ fontSize: 11, color: "#8A8178" }}>•</span>
        <span style={{ fontSize: 11.5, color: "#8A8178" }}>
          {new Date(m.timestamp).toLocaleTimeString(isAr ? "ar-SA" : "en-US", {
            hour: "2-digit",
            minute: "2-digit",
          })}
        </span>
        <span
          style={{
            marginInlineStart: "auto",
            fontSize: 11,
            fontWeight: 600,
            color: m.isThinking ? "#BE4A24" : isVerified ? "#4A513C" : "#8A8178",
            border: m.isThinking
              ? "1px solid rgba(190, 74, 36, 0.3)"
              : isVerified
              ? "1px solid rgba(74, 81, 60, 0.3)"
              : "1px solid #D4CBBD",
            borderRadius: 999,
            padding: "2.5px 10px",
            background: m.isThinking
              ? "rgba(190, 74, 36, 0.06)"
              : isVerified
              ? "rgba(74, 81, 60, 0.08)"
              : "#F3EEE4",
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            transition: "all 0.2s ease",
          }}
        >
          {m.isThinking && (
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: 999,
                background: "#BE4A24",
                display: "inline-block",
                animation: "pulse-dot 1.2s ease-in-out infinite",
              }}
            />
          )}
          {m.isThinking
            ? isAr
              ? "جارٍ التفكير والبحث..."
              : "Thinking & Searching..."
            : isVerified
            ? isAr
              ? "موثّق بمصادر"
              : "Verified Sources"
            : isAr
            ? "مستشار سرد"
            : "Sard Advisor"}
        </span>
      </div>

      {/* Thinking State: 13 stripes waving + live status */}
      {m.isThinking ? (
        <div style={{ transition: "opacity 0.28s ease" }}>
          <ThinkingWeave lang={lang} statusText={m.statusStage} />
        </div>
      ) : m.error ? (
        <div style={{ color: "#BE4A24", fontSize: 13.5, lineHeight: 1.75 }}>
          {m.error}
        </div>
      ) : (
        <div
          dir={isAr ? "rtl" : "ltr"}
          style={{
            fontSize: 15,
            lineHeight: 1.85,
            color: "#141210",
            animation: "fade-in 0.28s ease",
          }}
          className="sard-prose"
        >
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              p: ({ children }) => <p style={{ margin: "10px 0" }}>{children}</p>,
              strong: ({ children }) => (
                <strong style={{ color: "#141210", fontWeight: 700 }}>{children}</strong>
              ),
              h1: ({ children }) => (
                <h1
                  style={{
                    fontFamily: "'Noto Naskh Arabic', serif",
                    fontSize: 21,
                    fontWeight: 700,
                    margin: "16px 0 10px",
                    color: "#141210",
                  }}
                >
                  {children}
                </h1>
              ),
              h2: ({ children }) => (
                <h2
                  style={{
                    fontFamily: "'Noto Naskh Arabic', serif",
                    fontSize: 18,
                    fontWeight: 700,
                    margin: "14px 0 8px",
                    color: "#3A342E",
                  }}
                >
                  {children}
                </h2>
              ),
              h3: ({ children }) => (
                <h3
                  style={{
                    fontSize: 15.5,
                    fontWeight: 700,
                    margin: "12px 0 6px",
                    color: "#3A342E",
                  }}
                >
                  {children}
                </h3>
              ),
              ul: ({ children }) => (
                <ul style={{ paddingInlineStart: 20, margin: "10px 0" }}>{children}</ul>
              ),
              ol: ({ children }) => (
                <ol style={{ paddingInlineStart: 20, margin: "10px 0" }}>{children}</ol>
              ),
              li: ({ children }) => <li style={{ margin: "5px 0" }}>{children}</li>,
              blockquote: ({ children }) => (
                <blockquote
                  style={{
                    borderInlineStart: "3px solid #C4A46A",
                    paddingInlineStart: 14,
                    margin: "12px 0",
                    color: "#3A342E",
                    background: "#F3EEE4",
                    borderRadius: 8,
                    paddingTop: 8,
                    paddingBottom: 8,
                  }}
                >
                  {children}
                </blockquote>
              ),
              a: ({ children, href }) => (
                <a
                  href={href}
                  target="_blank"
                  rel="noreferrer"
                  style={{
                    color: "#BE4A24",
                    textDecoration: "underline",
                    textUnderlineOffset: 3,
                  }}
                >
                  {children}
                </a>
              ),
              code: ({ children }) => (
                <code
                  style={{
                    background: "#F3EEE4",
                    border: "1px solid #D4CBBD",
                    borderRadius: 6,
                    padding: "2px 6px",
                    fontSize: 13,
                    fontFamily: "'IBM Plex Sans Arabic', monospace",
                  }}
                >
                  {children}
                </code>
              ),
              table: ({ children }) => (
                <div
                  style={{
                    overflowX: "auto",
                    margin: "18px 0",
                    width: "100%",
                    borderRadius: 14,
                    border: "1px solid #D4CBBD",
                    background: "#FAF7F1",
                    boxShadow: "0 2px 10px -2px rgba(20,18,16,0.05)",
                  }}
                >
                  <table
                    style={{
                      width: "100%",
                      borderCollapse: "separate",
                      borderSpacing: 0,
                      fontSize: 14,
                      lineHeight: 1.75,
                      textAlign: "start",
                    }}
                  >
                    {children}
                  </table>
                </div>
              ),
              thead: ({ children }) => (
                <thead
                  style={{
                    background: "#EFE8DB",
                    borderBottom: "2px solid #D4CBBD",
                  }}
                >
                  {children}
                </thead>
              ),
              tbody: ({ children }) => (
                <tbody style={{ background: "#FAF7F1" }}>
                  {children}
                </tbody>
              ),
              tr: ({ children }) => (
                <tr
                  style={{
                    borderBottom: "1px solid #E8E0D2",
                    transition: "background 0.15s ease",
                  }}
                >
                  {children}
                </tr>
              ),
              th: ({ children }) => (
                <th
                  style={{
                    padding: "12px 16px",
                    fontWeight: 700,
                    color: "#141210",
                    textAlign: "start",
                    borderBottom: "1.5px solid #D4CBBD",
                    borderInlineEnd: "1px solid #D4CBBD",
                    whiteSpace: "nowrap",
                    fontFamily: "'IBM Plex Sans Arabic', 'IBM Plex Sans', sans-serif",
                  }}
                >
                  {children}
                </th>
              ),
              td: ({ children }) => (
                <td
                  style={{
                    padding: "12px 16px",
                    color: "#3A342E",
                    verticalAlign: "top",
                    borderBottom: "1px solid #E8E0D2",
                    borderInlineEnd: "1px solid #E8E0D2",
                    lineHeight: 1.75,
                    fontSize: 14,
                  }}
                >
                  {children}
                </td>
              ),
              hr: () => (
                <hr
                  style={{
                    border: "none",
                    borderTop: "1px solid #D4CBBD",
                    margin: "20px 0",
                  }}
                />
              ),
            }}
          >
            {formattedContent}
          </ReactMarkdown>

          {/* Source Pills (olive text, paper chip, 1px line) */}
          {citations.length > 0 && (
            <div style={{ marginTop: 16, paddingTop: 12, borderTop: "1px solid #E8E0D2" }}>
              <div
                style={{
                  fontSize: 11.5,
                  fontWeight: 700,
                  color: "#8A8178",
                  marginBottom: 8,
                }}
              >
                {t("sources", lang)} • {citations.length}
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {citations.map((c: Citation, idx: number) => (
                  <a
                    key={`${c.citation_id || c.source_url || "cit"}-${idx}`}
                    href={c.source_url || "#"}
                    target={c.source_url ? "_blank" : undefined}
                    rel="noreferrer"
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 6,
                      background: "#F3EEE4",
                      border: "1px solid #D4CBBD",
                      color: "#4A513C",
                      borderRadius: 999,
                      padding: "6px 12px",
                      fontSize: 12,
                      textDecoration: "none",
                      transition: "border-color 0.15s ease",
                    }}
                  >
                    <span
                      style={{
                        width: 6,
                        height: 6,
                        borderRadius: 999,
                        background: "#4A513C",
                        display: "inline-block",
                      }}
                    />
                    {c.title ? c.title.slice(0, 48) : c.source_name || c.citation_id}
                  </a>
                ))}
              </div>
            </div>
          )}

          {/* Attachments / Artifacts */}
          {m.artifacts && m.artifacts.length > 0 && (
            <div style={{ marginTop: 16, paddingTop: 12, borderTop: "1px solid #E8E0D2" }}>
              <div
                style={{
                  fontSize: 11.5,
                  fontWeight: 700,
                  color: "#BE4A24",
                  marginBottom: 8,
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <span>✦</span>
                <span>{isAr ? "مخرجات سرد التفاعلية والمستندات المعتمدة" : "Sard Cultural Outputs & Artifacts"} ({m.artifacts.length})</span>
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
                {m.artifacts.map((a, i) => {
                  const isPptx = a.type === "pptx" || a.type === "presentation_pptx";
                  const isRecipe = a.type === "recipe_craft_card";
                  const isCal = a.type === "ics" || a.type === "calendar_ics";
                  const isCard = a.type === "card" || a.type === "greeting_card";
                  const isEt = a.type === "etiquette_flow";
                  const isDl = a.type === "dialect_lore";
                  const isArt = a.type === "artisan_craft";
                  const isMem = a.type === "family_memoir_booklet";
                  const isRes = a.type === "verified_research";

                  const icon = isPptx ? "📊" : isRecipe ? "🍲" : isCal ? "📅" : isCard ? "💌" : isEt ? "🧭" : isDl ? "📜" : isArt ? "🏺" : isMem ? "📖" : isRes ? "🏛️" : "📄";

                  return (
                    <button
                      key={i}
                      onClick={() => onSelectArtifact?.(a)}
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 8,
                        background: "#F3EEE4",
                        border: "1.5px solid #C4A46A",
                        borderRadius: 12,
                        padding: "8px 14px",
                        fontSize: 13,
                        fontWeight: 600,
                        color: "#141210",
                        cursor: "pointer",
                        boxShadow: "0 2px 8px rgba(20,18,16,0.06)",
                        transition: "all 0.15s ease",
                      }}
                      onMouseEnter={(e) => {
                        e.currentTarget.style.background = "#E8E0D2";
                        e.currentTarget.style.borderColor = "#BE4A24";
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.background = "#F3EEE4";
                        e.currentTarget.style.borderColor = "#C4A46A";
                      }}
                    >
                      <span style={{ fontSize: 16 }}>{icon}</span>
                      <span style={{ fontFamily: "'Noto Naskh Arabic', serif", fontWeight: 700 }}>
                        {a.title || a.filename}
                      </span>
                      <span
                        style={{
                          fontSize: 10.5,
                          background: "#BE4A24",
                          color: "#FFFFFF",
                          padding: "2px 6px",
                          borderRadius: 4,
                          marginInlineStart: 4,
                        }}
                      >
                        {isAr ? "معاينة" : "Preview"}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
      <style>{`
        @keyframes fade-in { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes pulse-dot { 0%, 100% { opacity: 0.4; transform: scale(0.85); } 50% { opacity: 1; transform: scale(1.15); } }
        .sard-prose a:hover { color: #8F3518; }
      `}</style>
    </div>
  );
}

export function ChatMessages({
  messages,
  lang,
  onSelectArtifact,
}: {
  messages: Message[];
  lang: Lang;
  onSelectArtifact?: (artifact: Artifact) => void;
}) {
  if (messages.length === 0) {
    return (
      <div
        style={{
          flex: 1,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 32,
          color: "#8A8178",
          fontSize: 14,
          textAlign: "center",
        }}
      >
        {t("chatEmptyHint", lang)}
      </div>
    );
  }
  return (
    <div
      style={{
        maxWidth: 860,
        margin: "0 auto",
        width: "100%",
        padding: "20px 18px 0",
      }}
    >
      {messages.map((m) =>
        m.role === "user" ? (
          <UserBubble key={m.id} m={m} lang={lang} />
        ) : (
          <AgentCard key={m.id} m={m} lang={lang} onSelectArtifact={onSelectArtifact} />
        )
      )}
    </div>
  );
}
