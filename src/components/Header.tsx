"use client";
import React from "react";
import { SardMark } from "./SardMark";
import { Lang } from "@/types";
import { t } from "@/lib/copy";

export function Header({
  lang,
  onToggleLang,
  onGoHome,
  onGoExplore,
  view,
}: {
  lang: Lang;
  onToggleLang: () => void;
  onGoHome: () => void;
  onGoExplore?: () => void;
  view: "landing" | "chat" | "explore";
}) {
  return (
    <header
      style={{
        height: 68,
        background: "rgba(243, 238, 228, 0.88)",
        backdropFilter: "blur(12px)",
        WebkitBackdropFilter: "blur(12px)",
        borderBottom: "1px solid #D4CBBD",
        position: "sticky",
        top: 0,
        zIndex: 30,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "0 16px",
        gap: 12,
        flexShrink: 0,
        flexWrap: "wrap",
        minHeight: 68,
      }}
    >
      {/* Start: 13-thread mark + سرد + tagline */}
      <div
        data-dir-animate="header-brand"
        data-dir-id="header-brand"
        data-dir-stagger="10"
        onClick={onGoHome}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          minWidth: 0,
          cursor: "pointer",
        }}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") onGoHome();
        }}
      >
        <SardMark size={34} />
        <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.1 }}>
          <span
            style={{
              fontFamily: "'Noto Naskh Arabic', serif",
              fontSize: 22,
              fontWeight: 700,
              color: "#141210",
              letterSpacing: -0.2,
            }}
          >
            سرد
          </span>
          <span
            style={{
              fontSize: 11.5,
              color: "#8A8178",
              marginTop: 1,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              maxWidth: "38vw",
            }}
          >
            {t("tagline", lang)}
          </span>
        </div>
      </div>

      {/* End: AR | EN pills + Home control */}
      <div
        data-dir-animate="header-actions"
        data-dir-id="header-actions"
        data-dir-stagger="30"
        style={{ display: "flex", alignItems: "center", gap: 10 }}
      >
        {/* عربي | EN pills */}
        <div
          role="group"
          aria-label="Language selection"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 2,
            background: "#E8E0D2",
            border: "1px solid #D4CBBD",
            borderRadius: 999,
            padding: 3,
          }}
        >
          <button
            onClick={() => lang !== "ar" && onToggleLang()}
            aria-pressed={lang === "ar"}
            style={{
              padding: "6px 14px",
              borderRadius: 999,
              fontSize: 13,
              fontWeight: 600,
              border: "none",
              cursor: "pointer",
              background: lang === "ar" ? "#141210" : "transparent",
              color: lang === "ar" ? "#F3EEE4" : "#3A342E",
              transition: "all 0.22s ease",
            }}
          >
            عربي
          </button>
          <button
            onClick={() => lang !== "en" && onToggleLang()}
            aria-pressed={lang === "en"}
            style={{
              padding: "6px 14px",
              borderRadius: 999,
              fontSize: 13,
              fontWeight: 600,
              border: "none",
              cursor: "pointer",
              background: lang === "en" ? "#141210" : "transparent",
              color: lang === "en" ? "#F3EEE4" : "#3A342E",
              transition: "all 0.22s ease",
            }}
          >
            EN
          </button>
        </div>

        {/* الواجهة / Home + Explore */}
        <button
          onClick={onGoHome}
          style={{
            padding: "8px 16px",
            borderRadius: 999,
            fontSize: 13.5,
            fontWeight: 600,
            background: view === "chat" ? "#141210" : "#FAF7F1",
            color: view === "chat" ? "#FAF7F1" : "#141210",
            border: "1px solid #D4CBBD",
            cursor: "pointer",
            transition: "all 0.22s ease",
          }}
          aria-label={t("home", lang)}
        >
          {lang === "ar" ? "المحادثة" : "Chat"}
        </button>
        {onGoExplore && (
          <button
            onClick={onGoExplore}
            style={{
              padding: "8px 16px",
              borderRadius: 999,
              fontSize: 13.5,
              fontWeight: 600,
              background: view === "explore" ? "#141210" : "#FAF7F1",
              color: view === "explore" ? "#FAF7F1" : "#141210",
              border: "1px solid #D4CBBD",
              cursor: "pointer",
              transition: "all 0.22s ease",
            }}
            aria-label={lang === "ar" ? "استكشف القدرات" : "Explore capabilities"}
          >
            {lang === "ar" ? "استكشف" : "Explore"}
          </button>
        )}
      </div>
    </header>
  );
}
