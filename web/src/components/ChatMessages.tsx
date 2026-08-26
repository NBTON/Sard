"use client";
import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Citation, Lang, Message } from "@/types";
import { SardMiniMark, ThinkingWeave } from "./SardMark";
import { t } from "@/lib/copy";

function extractFacts(content: string): string[] | null {
  const lines = content
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.startsWith("-") || l.startsWith("•") || /^\d+\./.test(l));

  if (lines.length >= 2 && lines.length <= 8) {
    return lines.slice(0, 4).map((l) => l.replace(/^[-•\d.\s]+/, "").trim()).filter(Boolean);
  }
  return null;
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

function AgentCard({ m, lang }: { m: Message; lang: Lang }) {
  const isAr = lang === "ar";
  const factChips = !m.isThinking && !m.error ? extractFacts(m.content) : null;
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
            }}
          >
            {m.content}
          </ReactMarkdown>

          {/* Optional Fact Chips (2-col grid, paper wells) */}
          {factChips && (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: 10,
                marginTop: 16,
              }}
            >
              {factChips.map((f, i) => (
                <div
                  key={i}
                  style={{
                    background: "#F3EEE4",
                    border: "1px solid #E8E0D2",
                    borderRadius: 12,
                    padding: "10px 14px",
                    fontSize: 13,
                    lineHeight: 1.6,
                    color: "#3A342E",
                  }}
                >
                  {f}
                </div>
              ))}
            </div>
          )}

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
            <div style={{ marginTop: 14 }}>
              <div
                style={{
                  fontSize: 11.5,
                  fontWeight: 700,
                  color: "#8A8178",
                  marginBottom: 8,
                }}
              >
                {t("attachments", lang)}
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {m.artifacts.map((a, i) => (
                  <a
                    key={i}
                    href={a.url}
                    target="_blank"
                    rel="noreferrer"
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 6,
                      background: "#F3EEE4",
                      border: "1px solid #D4CBBD",
                      borderRadius: 999,
                      padding: "6px 12px",
                      fontSize: 12,
                      color: "#141210",
                      textDecoration: "none",
                    }}
                  >
                    <span>📄</span>
                    <span>{a.title || a.filename}</span>
                  </a>
                ))}
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
}: {
  messages: Message[];
  lang: Lang;
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
          <AgentCard key={m.id} m={m} lang={lang} />
        )
      )}
    </div>
  );
}
