"use client";
import React from "react";
import { Lang } from "@/types";
import { t } from "@/lib/copy";
import { SECTORS } from "@/lib/sectors";
import { WeaveBars } from "./SardMark";

export function Landing({
  lang,
  onStartChat,
  onSectorPrompt,
  onSeedPrompt,
}: {
  lang: Lang;
  onStartChat: () => void;
  onSectorPrompt: (prompt: string) => void;
  onSeedPrompt: (prompt: string) => void;
}) {
  const isAr = lang === "ar";
  const seedQuery = isAr
    ? "حدثني عن السدو وألوانه ورموزه في التراث السعودي"
    : "Tell me about Sadu weaving, its colors and symbols in Saudi heritage";

  return (
    <div
      style={{
        flex: 1,
        overflowY: "auto",
        position: "relative",
        background: "#F3EEE4",
      }}
    >
      <div className="watermark-lines" aria-hidden />
      <div
        style={{
          maxWidth: 1160,
          margin: "0 auto",
          padding: "40px 24px 48px",
          position: "relative",
          zIndex: 1,
        }}
      >
        {/* Two columns on desktop */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1.08fr 0.92fr",
            gap: 36,
            alignItems: "center",
          }}
          className="landing-grid"
        >
          {/* Copy column */}
          <div style={{ minWidth: 0 }}>
            <div
              style={{
                fontSize: 12,
                letterSpacing: 1.4,
                textTransform: "uppercase",
                color: "#BE4A24",
                fontWeight: 700,
                marginBottom: 16,
              }}
            >
              {t("kicker", lang)}
            </div>
            <h1
              style={{
                fontFamily: "'Noto Naskh Arabic', serif",
                fontSize: "clamp(36px, 4.4vw, 58px)",
                lineHeight: 1.18,
                color: "#141210",
                fontWeight: 700,
                margin: 0,
              }}
            >
              {t("hero", lang)}
            </h1>
            <p
              style={{
                marginTop: 18,
                fontSize: 16,
                lineHeight: 1.85,
                color: "#3A342E",
                maxWidth: 560,
              }}
            >
              {t("heroSupport", lang)}
            </p>
            <div style={{ display: "flex", gap: 12, marginTop: 26, flexWrap: "wrap" }}>
              <button
                onClick={onStartChat}
                style={{
                  background: "#141210",
                  color: "#FAF7F1",
                  border: "1px solid #141210",
                  padding: "13px 26px",
                  borderRadius: 999,
                  fontSize: 14.5,
                  fontWeight: 700,
                  cursor: "pointer",
                  transition: "transform 0.15s ease, background 0.15s ease",
                }}
              >
                {t("startChat", lang)}
              </button>
              <button
                onClick={() => onSeedPrompt(seedQuery)}
                style={{
                  background: "transparent",
                  color: "#141210",
                  border: "1px solid #D4CBBD",
                  padding: "13px 22px",
                  borderRadius: 999,
                  fontSize: 14.5,
                  fontWeight: 600,
                  cursor: "pointer",
                  transition: "border-color 0.15s ease",
                }}
              >
                {t("tryExample", lang)}
              </button>
            </div>
          </div>

          {/* Weave card: rounded ivory / cream card, thin warm-beige border, soft shadow */}
          <div
            style={{
              background: "#FAF7F1",
              borderRadius: 28,
              border: "1px solid #D4CBBD",
              boxShadow: "0 14px 44px -12px rgba(20,18,16,0.12), 0 4px 16px -4px rgba(20,18,16,0.06)",
              padding: "28px 24px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              minHeight: 250,
              overflow: "hidden",
            }}
          >
            <WeaveBars variant="weave-card" />
          </div>
        </div>

        {/* Sectors: 11 tiles */}
        <div style={{ marginTop: 44 }}>
          <div
            style={{
              fontSize: 13.5,
              fontWeight: 700,
              color: "#3A342E",
              letterSpacing: 0.4,
              marginBottom: 14,
            }}
          >
            {t("sectorsTitle", lang)}
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
              gap: 14,
            }}
            className="sectors-grid"
          >
            {SECTORS.map((s) => (
              <button
                key={s.id}
                onClick={() => onSectorPrompt(lang === "en" ? s.promptEn : s.promptAr)}
                style={{
                  textAlign: isAr ? "right" : "left",
                  background: "#FAF7F1",
                  border: "1px solid #D4CBBD",
                  borderRadius: 16,
                  padding: "16px 16px",
                  cursor: "pointer",
                  display: "flex",
                  flexDirection: "column",
                  gap: 6,
                  transition: "transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease",
                  boxShadow: "0 2px 10px -2px rgba(20,18,16,0.05)",
                }}
                onMouseEnter={(e) => {
                  const el = e.currentTarget as HTMLButtonElement;
                  el.style.transform = "translateY(-2px)";
                  el.style.borderColor = "#BE4A24";
                  el.style.boxShadow = "0 8px 22px -6px rgba(190,74,36,0.14)";
                }}
                onMouseLeave={(e) => {
                  const el = e.currentTarget as HTMLButtonElement;
                  el.style.transform = "translateY(0)";
                  el.style.borderColor = "#D4CBBD";
                  el.style.boxShadow = "0 2px 10px -2px rgba(20,18,16,0.05)";
                }}
              >
                <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span
                    aria-hidden
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: 999,
                      background: s.color,
                      display: "inline-block",
                      flexShrink: 0,
                    }}
                  />
                  <span
                    style={{
                      fontSize: 14.5,
                      fontWeight: 700,
                      color: "#141210",
                      lineHeight: 1.2,
                    }}
                  >
                    {isAr ? s.ar : s.en}
                  </span>
                </span>
                <span style={{ fontSize: 12, color: "#8A8178", lineHeight: 1.4 }}>
                  {isAr ? s.en : s.ar}
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>

      <style>{`
        @media (max-width: 860px) {
          .landing-grid { grid-template-columns: 1fr !important; gap: 24px !important; }
          .sectors-grid { grid-template-columns: repeat(2, minmax(0, 1fr)) !important; }
        }
        @media (max-width: 520px) {
          .sectors-grid { grid-template-columns: 1fr !important; }
        }
      `}</style>
    </div>
  );
}
