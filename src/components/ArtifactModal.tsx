"use client";

import React, { useState } from "react";
import { Artifact, Lang } from "@/types";

interface ArtifactModalProps {
  artifact: Artifact | null;
  onClose: () => void;
  lang: Lang;
}

export function ArtifactModal({ artifact, onClose, lang }: ArtifactModalProps) {
  const [currentSlideIdx, setCurrentSlideIdx] = useState(0);
  const [cardTheme, setCardTheme] = useState<string>("dark_gold");

  if (!artifact) return null;

  const isAr = lang === "ar";
  const artData = artifact.preview?.card_data || artifact.preview || artifact.data?.card_data || artifact.data || {};

  const downloadUrl = artifact.download_url || artifact.url;
  const fmt = (artifact.format || artifact.type || "").toLowerCase();
  const kind = (artifact.kind || "").toLowerCase();

  // Determine modal view mode based on artifact type/kind
  const isPresentation = fmt === "pptx" || kind === "presentation";
  const isRecipeOrCraft = kind === "recipe" || fmt === "recipe_craft_card" || (fmt === "pdf" && (artData.ingredients_or_materials || artData.prep_time_minutes));
  const isCalendar = fmt === "ics" || kind === "calendar";
  const isGreetingCard = kind === "card" || fmt === "card" || fmt === "greeting_card";
  const isEtiquette = kind === "diagram" || fmt === "etiquette_flow" || fmt === "svg";
  const isDialect = fmt === "dialect_lore";
  const isArtisan = fmt === "artisan_craft";
  const isMemoir = kind === "memoir" || fmt === "family_memoir_booklet" || (fmt === "pdf" && artData.chapters);
  const isResearch = kind === "verified_research" || fmt === "verified_research";

  const slides = artData.slides || [];
  const activeSlide = slides[currentSlideIdx] || null;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 9999,
        background: "rgba(20, 18, 16, 0.72)",
        backdropFilter: "blur(6px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "16px",
        animation: "fade-in 0.2s ease",
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: "#F3EEE4",
          border: "1.5px solid #D4CBBD",
          borderRadius: 22,
          maxWidth: 960,
          width: "100%",
          maxHeight: "90vh",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          boxShadow: "0 20px 50px rgba(20, 18, 16, 0.35)",
        }}
        onClick={(e) => e.stopPropagation()}
        dir={isAr ? "rtl" : "ltr"}
      >
        {/* Modal Top Bar */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "16px 24px",
            background: "#141210",
            color: "#F3EEE4",
            borderBottom: "2px solid #C4A46A",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ fontSize: 20 }}>
              {isPresentation
                ? "📊"
                : isRecipeOrCraft
                ? "🍲"
                : isCalendar
                ? "📅"
                : isGreetingCard
                ? "💌"
                : isEtiquette
                ? "🧭"
                : isDialect
                ? "📜"
                : isArtisan
                ? "🏺"
                : isMemoir
                ? "📖"
                : "🏛️"}
            </span>
            <div>
              <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: "#F3EEE4" }}>
                {artifact.title || (isAr ? "معاينة المخرج الثقافي" : "Cultural Artifact Preview")}
              </h3>
              <span style={{ fontSize: 11, color: "#C4A46A", textTransform: "uppercase", letterSpacing: 0.5 }}>
                {fmt.toUpperCase()} {kind ? `• ${kind}` : ""}
              </span>
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: "rgba(255,255,255,0.08)",
              border: "1px solid rgba(255,255,255,0.15)",
              color: "#F3EEE4",
              borderRadius: 8,
              width: 32,
              height: 32,
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 16,
            }}
          >
            ✕
          </button>
        </div>

        {/* Modal Content Scroll Area */}
        <div style={{ flex: 1, overflowY: "auto", padding: 24 }}>
          {/* 1. Presentation Mode (Slide Carousel) */}
          {isPresentation && slides.length > 0 && (
            <div>
              <div
                style={{
                  background: "#1E1B18",
                  borderRadius: 16,
                  padding: "36px 32px",
                  color: "#F3EEE4",
                  minHeight: 280,
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "space-between",
                  border: "1px solid #3D352E",
                  boxShadow: "0 8px 24px rgba(0,0,0,0.2)",
                  marginBottom: 16,
                }}
              >
                <div>
                  <div style={{ fontSize: 12, color: "#C4A46A", fontWeight: 700, marginBottom: 8 }}>
                    {isAr ? `شريحة ${currentSlideIdx + 1} من ${slides.length}` : `Slide ${currentSlideIdx + 1} of ${slides.length}`}
                  </div>
                  <h2 style={{ fontSize: 22, fontWeight: 700, margin: "0 0 16px 0", color: "#F3EEE4" }}>
                    {activeSlide?.title || ""}
                  </h2>
                  <ul style={{ margin: 0, paddingInlineStart: 20, lineHeight: 1.8, fontSize: 15, color: "#D4CBBD" }}>
                    {(activeSlide?.content || []).map((point: string, idx: number) => (
                      <li key={idx} style={{ marginBottom: 6 }}>{point}</li>
                    ))}
                  </ul>
                </div>
                {activeSlide?.speaker_notes && (
                  <div style={{ marginTop: 20, padding: "10px 14px", background: "rgba(255,255,255,0.04)", borderRadius: 8, fontSize: 12, color: "#A89F91" }}>
                    <span style={{ fontWeight: 700, color: "#C4A46A" }}>{isAr ? "ملاحظات المتحدث: " : "Speaker Notes: "}</span>
                    {activeSlide.speaker_notes}
                  </div>
                )}
              </div>

              {/* Slide Navigation Controls */}
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <button
                  disabled={currentSlideIdx === 0}
                  onClick={() => setCurrentSlideIdx((prev) => Math.max(0, prev - 1))}
                  style={{
                    padding: "8px 16px",
                    borderRadius: 8,
                    background: currentSlideIdx === 0 ? "#E0D8C8" : "#2D2620",
                    color: currentSlideIdx === 0 ? "#A89F91" : "#F3EEE4",
                    border: "none",
                    cursor: currentSlideIdx === 0 ? "not-allowed" : "pointer",
                    fontSize: 13,
                    fontWeight: 600,
                  }}
                >
                  {isAr ? "← الشريحة السابقة" : "← Previous"}
                </button>
                <div style={{ display: "flex", gap: 6 }}>
                  {slides.map((_: any, idx: number) => (
                    <div
                      key={idx}
                      onClick={() => setCurrentSlideIdx(idx)}
                      style={{
                        width: 10,
                        height: 10,
                        borderRadius: "50%",
                        background: idx === currentSlideIdx ? "#C4A46A" : "#D4CBBD",
                        cursor: "pointer",
                      }}
                    />
                  ))}
                </div>
                <button
                  disabled={currentSlideIdx === slides.length - 1}
                  onClick={() => setCurrentSlideIdx((prev) => Math.min(slides.length - 1, prev + 1))}
                  style={{
                    padding: "8px 16px",
                    borderRadius: 8,
                    background: currentSlideIdx === slides.length - 1 ? "#E0D8C8" : "#2D2620",
                    color: currentSlideIdx === slides.length - 1 ? "#A89F91" : "#F3EEE4",
                    border: "none",
                    cursor: currentSlideIdx === slides.length - 1 ? "not-allowed" : "pointer",
                    fontSize: 13,
                    fontWeight: 600,
                  }}
                >
                  {isAr ? "الشريحة التالية →" : "Next →"}
                </button>
              </div>
            </div>
          )}

          {/* 2. Recipe or Craft Card Mode */}
          {isRecipeOrCraft && (
            <div style={{ background: "#FFF", borderRadius: 16, padding: 28, border: "1px solid #E0D8C8" }}>
              <div style={{ borderBottom: "2px solid #C4A46A", paddingBottom: 12, marginBottom: 16 }}>
                <h2 style={{ margin: 0, fontSize: 20, color: "#141210" }}>{artData.name || artifact.title}</h2>
                {artData.origin_region && (
                  <span style={{ fontSize: 12, color: "#7D6E5D" }}>📍 {artData.origin_region}</span>
                )}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, marginBottom: 20 }}>
                {artData.ingredients_or_materials && (
                  <div style={{ background: "#F8F5EE", padding: 16, borderRadius: 10 }}>
                    <h4 style={{ margin: "0 0 10px 0", fontSize: 14, color: "#141210" }}>
                      {isAr ? "المقادير / المواد:" : "Ingredients / Materials:"}
                    </h4>
                    <ul style={{ margin: 0, paddingInlineStart: 18, fontSize: 13, lineHeight: 1.6, color: "#4A4036" }}>
                      {artData.ingredients_or_materials.map((it: string, i: number) => (
                        <li key={i}>{it}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {artData.instructions_or_steps && (
                  <div style={{ background: "#F8F5EE", padding: 16, borderRadius: 10 }}>
                    <h4 style={{ margin: "0 0 10px 0", fontSize: 14, color: "#141210" }}>
                      {isAr ? "طريقة الإعداد / الخطوات:" : "Steps / Instructions:"}
                    </h4>
                    <ol style={{ margin: 0, paddingInlineStart: 18, fontSize: 13, lineHeight: 1.6, color: "#4A4036" }}>
                      {artData.instructions_or_steps.map((st: string, i: number) => (
                        <li key={i}>{st}</li>
                      ))}
                    </ol>
                  </div>
                )}
              </div>
              {artData.cultural_significance && (
                <div style={{ padding: 12, background: "#FAF7F2", borderRadius: 8, borderLeft: isAr ? "none" : "3px solid #C4A46A", borderRight: isAr ? "3px solid #C4A46A" : "none", fontSize: 12, color: "#63584E" }}>
                  <strong>{isAr ? "الأهمية الثقافية: " : "Cultural Significance: "}</strong>
                  {artData.cultural_significance}
                </div>
              )}
            </div>
          )}

          {/* 3. Calendar Sync Mode */}
          {isCalendar && (
            <div style={{ background: "#FFF", borderRadius: 16, padding: 28, border: "1px solid #E0D8C8" }}>
              <h3 style={{ margin: "0 0 16px 0", fontSize: 16, color: "#141210" }}>
                {isAr ? "📅 المناسبات والمواسم المجدولة (.ics)" : "📅 Scheduled Events & Seasons (.ics)"}
              </h3>
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                {(artData.events || [artData]).map((ev: any, i: number) => (
                  <div key={i} style={{ padding: 14, background: "#F8F5EE", borderRadius: 10, border: "1px solid #EAE3D5" }}>
                    <div style={{ fontWeight: 700, fontSize: 14, color: "#141210", marginBottom: 4 }}>
                      {ev.summary || ev.title || artifact.title}
                    </div>
                    <div style={{ fontSize: 12, color: "#7D6E5D", display: "flex", gap: 16 }}>
                      {ev.start && <span>⏱️ {ev.start}</span>}
                      {ev.location && <span>📍 {ev.location}</span>}
                    </div>
                    {ev.description && (
                      <p style={{ margin: "8px 0 0 0", fontSize: 12, color: "#4A4036", lineHeight: 1.5 }}>
                        {ev.description}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 4. Etiquette Flow Mode */}
          {isEtiquette && (
            <div style={{ background: "#FFF", borderRadius: 16, padding: 28, border: "1px solid #E0D8C8" }}>
              <h3 style={{ margin: "0 0 16px 0", fontSize: 16, color: "#141210" }}>
                {isAr ? "🧭 بروتوكول وآداب المجلس التراثي" : "🧭 Traditional Majlis Protocol"}
              </h3>
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                {(artData.steps || artData.flow || []).map((step: any, i: number) => (
                  <div key={i} style={{ display: "flex", gap: 12, alignItems: "flex-start", padding: 12, background: "#F8F5EE", borderRadius: 8 }}>
                    <div style={{ width: 24, height: 24, borderRadius: "50%", background: "#C4A46A", color: "#FFF", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 700, flexShrink: 0 }}>
                      {i + 1}
                    </div>
                    <div>
                      <div style={{ fontWeight: 700, fontSize: 13, color: "#141210" }}>{step.action || step.title || step}</div>
                      {step.do_not && (
                        <div style={{ fontSize: 12, color: "#9E3A2F", marginTop: 4 }}>
                          ⚠️ {isAr ? "تجنب: " : "Avoid: "}{step.do_not}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 5. Generic / Default Preview */}
          {!isPresentation && !isRecipeOrCraft && !isCalendar && !isEtiquette && (
            <div style={{ background: "#FFF", borderRadius: 16, padding: 24, border: "1px solid #E0D8C8" }}>
              <div style={{ whiteSpace: "pre-wrap", fontSize: 14, lineHeight: 1.7, color: "#2D2620" }}>
                {artData.text || artData.summary || (artifact as any).text_content || (
                  <pre style={{ fontFamily: "monospace", fontSize: 12, background: "#F8F5EE", padding: 14, borderRadius: 8, overflowX: "auto" }}>
                    {JSON.stringify(artData, null, 2)}
                  </pre>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Modal Bottom Bar */}
        <div
          style={{
            padding: "16px 24px",
            background: "#EAE3D5",
            borderTop: "1px solid #D4CBBD",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <div style={{ fontSize: 12, color: "#7D6E5D" }}>
            {artifact.filename ? `📄 ${artifact.filename}` : ""}
            {artifact.size_bytes ? ` (${Math.round(artifact.size_bytes / 1024)} KB)` : ""}
          </div>
          <div style={{ display: "flex", gap: 10 }}>
            {downloadUrl ? (
              <a
                href={downloadUrl}
                download={artifact.filename || `sard-${fmt}`}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  padding: "8px 18px",
                  borderRadius: 10,
                  background: "#141210",
                  color: "#C4A46A",
                  fontWeight: 700,
                  fontSize: 13,
                  textDecoration: "none",
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  boxShadow: "0 2px 6px rgba(0,0,0,0.15)",
                }}
              >
                <span>⬇️</span>
                <span>{isAr ? "تحميل الملف" : "Download File"}</span>
              </a>
            ) : (
              <span style={{ fontSize: 12, color: "#9E3A2F" }}>
                {isAr ? "التحميل غير متوفر" : "Download unavailable"}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
