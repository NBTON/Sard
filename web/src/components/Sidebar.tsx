"use client";
import React from "react";
import { Lang } from "@/types";
import { t } from "@/lib/copy";
import { STARTERS } from "@/lib/sectors";

export function ChatSidebar({
  lang,
  onNewChat,
  onStarter,
  open,
}: {
  lang: Lang;
  onNewChat: () => void;
  onStarter: (prompt: string) => void;
  open: boolean;
}) {
  const isAr = lang === "ar";

  return (
    <aside
      style={{
        width: 300,
        minWidth: 300,
        background: "#EFE8DB",
        borderInlineEnd: "1px solid #D4CBBD",
        display: open ? "flex" : "none",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
        position: "relative",
        zIndex: 10,
      }}
      aria-label="Chat sidebar"
    >
      <div
        style={{
          padding: 18,
          display: "flex",
          flexDirection: "column",
          gap: 20,
          flex: 1,
          overflowY: "auto",
        }}
      >
        {/* Full-width ink button: حوار جديد */}
        <button
          onClick={onNewChat}
          style={{
            width: "100%",
            background: "#141210",
            color: "#FAF7F1",
            border: "1px solid #141210",
            borderRadius: 14,
            padding: "13px 18px",
            fontSize: 14.5,
            fontWeight: 700,
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            transition: "opacity 0.15s ease",
          }}
        >
          <span style={{ fontSize: 16 }}>+</span>
          <span>{t("newChat", lang)}</span>
        </button>

        {/* Starter prompts as paper chips */}
        <div>
          <div
            style={{
              fontSize: 12.5,
              fontWeight: 700,
              color: "#3A342E",
              marginBottom: 10,
              letterSpacing: 0.2,
            }}
          >
            {t("startersLabel", lang)}
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {STARTERS.map((s, i) => (
              <button
                key={i}
                onClick={() => onStarter(isAr ? s.ar : s.en)}
                style={{
                  background: "#F3EEE4",
                  border: "1px solid #D4CBBD",
                  color: "#3A342E",
                  borderRadius: 999,
                  padding: "8px 14px",
                  fontSize: 13,
                  cursor: "pointer",
                  textAlign: isAr ? "right" : "left",
                  lineHeight: 1.4,
                  transition: "border-color 0.15s ease, background 0.15s ease",
                }}
                onMouseEnter={(e) => {
                  const el = e.currentTarget;
                  el.style.borderColor = "#BE4A24";
                  el.style.background = "#FAF7F1";
                }}
                onMouseLeave={(e) => {
                  const el = e.currentTarget;
                  el.style.borderColor = "#D4CBBD";
                  el.style.background = "#F3EEE4";
                }}
              >
                {isAr ? s.ar : s.en}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Small disclaimer at the bottom */}
      <div
        style={{
          padding: "14px 16px",
          borderTop: "1px solid #D4CBBD",
          fontSize: 11.5,
          lineHeight: 1.6,
          color: "#8A8178",
          background: "rgba(243, 238, 228, 0.65)",
        }}
      >
        {t("disclaimer", lang)}
      </div>
    </aside>
  );
}
