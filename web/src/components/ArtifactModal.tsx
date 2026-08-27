"use client";

import React, { useState } from "react";
import { Artifact, Lang } from "@/types";

interface ArtifactModalProps {
  artifact: Artifact | null;
  onClose: () => void;
  lang: Lang;
}

export function ArtifactModal({ artifact, onClose, lang }: ArtifactModalProps) {
  if (!artifact) return null;

  const isAr = lang === "ar";
  const artData = artifact.data?.card_data || artifact.data || {};
  const [currentSlideIdx, setCurrentSlideIdx] = useState(0);
  const [cardTheme, setCardTheme] = useState<string>("dark_gold");

  // Determine modal view mode based on artifact type
  const isPresentation = artifact.type === "pptx" || artifact.type === "presentation_pptx";
  const isRecipeOrCraft = artifact.type === "recipe_craft_card" || (artifact.type === "pdf" && (artData.ingredients_or_materials || artData.prep_time_minutes));
  const isCalendar = artifact.type === "ics" || artifact.type === "calendar_ics";
  const isGreetingCard = artifact.type === "card" || artifact.type === "greeting_card";
  const isEtiquette = artifact.type === "etiquette_flow";
  const isDialect = artifact.type === "dialect_lore";
  const isArtisan = artifact.type === "artisan_craft";
  const isMemoir = artifact.type === "family_memoir_booklet" || (artifact.type === "pdf" && artData.chapters);
  const isResearch = artifact.type === "verified_research";

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
              <h2
                style={{
                  fontFamily: "'Noto Naskh Arabic', serif",
                  fontSize: 17,
                  fontWeight: 700,
                  color: "#F3EEE4",
                  margin: 0,
                }}
              >
                {artifact.title || artifact.filename}
              </h2>
              <span style={{ fontSize: 11.5, color: "#C4A46A" }}>
                {isAr ? "مخرجات سرد الثقافية المعتمدة" : "Sard Verified Cultural Output"}
              </span>
            </div>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {artifact.url && artifact.url !== "#" && (
              <a
                href={artifact.url}
                download
                target="_blank"
                rel="noreferrer"
                style={{
                  background: "#BE4A24",
                  color: "#FFFFFF",
                  padding: "6px 14px",
                  borderRadius: 999,
                  fontSize: 12.5,
                  fontWeight: 600,
                  textDecoration: "none",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  transition: "background 0.15s ease",
                }}
              >
                <span>⬇</span>
                <span>{isAr ? "تحميل الملف" : "Download File"}</span>
              </a>
            )}
            <button
              onClick={onClose}
              style={{
                background: "rgba(243, 238, 228, 0.15)",
                border: "none",
                color: "#F3EEE4",
                borderRadius: 999,
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
        </div>

        {/* Modal Scrollable Content Area */}
        <div style={{ flex: 1, overflowY: "auto", padding: "24px" }}>
          {/* 1. Presentation Deck Viewer */}
          {isPresentation && (
            <div>
              {slides.length > 0 && activeSlide ? (
                <div>
                  {/* 16:9 Slide Canvas */}
                  <div
                    style={{
                      aspectRatio: "16 / 9",
                      background: activeSlide.slide_type === "title" ? "#141210" : "#FAF7F1",
                      border: "1.5px solid #D4CBBD",
                      borderRadius: 16,
                      padding: "32px",
                      display: "flex",
                      flexDirection: "column",
                      justifyContent: "space-between",
                      color: activeSlide.slide_type === "title" ? "#F3EEE4" : "#141210",
                      boxShadow: "0 4px 20px rgba(20,18,16,0.08)",
                      position: "relative",
                    }}
                  >
                    {/* Top Gold Bar */}
                    <div
                      style={{
                        position: "absolute",
                        top: 0,
                        insetInline: 0,
                        height: 4,
                        background: "#C4A46A",
                        borderStartStartRadius: 16,
                        borderStartEndRadius: 16,
                      }}
                    />

                    {/* Header */}
                    <div>
                      <div
                        style={{
                          fontSize: 11.5,
                          fontWeight: 700,
                          color: "#BE4A24",
                          marginBottom: 6,
                        }}
                      >
                        ✦ {isAr ? "الإيجاز الثقافي السعودي" : "Saudi Cultural Briefing"}
                      </div>
                      <h1
                        style={{
                          fontFamily: "'Noto Naskh Arabic', serif",
                          fontSize: activeSlide.slide_type === "title" ? 28 : 22,
                          fontWeight: 700,
                          color: activeSlide.slide_type === "title" ? "#F3EEE4" : "#141210",
                          margin: "0 0 8px",
                        }}
                      >
                        {activeSlide.title}
                      </h1>
                      {activeSlide.subtitle && (
                        <div style={{ fontSize: 14, color: "#C4A46A", fontWeight: 600 }}>
                          {activeSlide.subtitle}
                        </div>
                      )}
                    </div>

                    {/* Slide Body / Cards */}
                    <div style={{ flex: 1, margin: "18px 0" }}>
                      {activeSlide.cards && activeSlide.cards.length > 0 ? (
                        <div
                          style={{
                            display: "grid",
                            gridTemplateColumns: `repeat(${Math.min(activeSlide.cards.length, 3)}, 1fr)`,
                            gap: 12,
                            height: "100%",
                          }}
                        >
                          {activeSlide.cards.map((c: any, i: number) => (
                            <div
                              key={i}
                              style={{
                                background: "#F3EEE4",
                                border: "1px solid #D4CBBD",
                                borderRadius: 12,
                                padding: "12px 14px",
                                borderTop: "3px solid #BE4A24",
                              }}
                            >
                              <div
                                style={{
                                  fontFamily: "'Noto Naskh Arabic', serif",
                                  fontWeight: 700,
                                  fontSize: 15,
                                  color: "#BE4A24",
                                  marginBottom: 6,
                                }}
                              >
                                {c.title}
                              </div>
                              <ul style={{ paddingInlineStart: 16, margin: 0, fontSize: 12, lineHeight: 1.6 }}>
                                {c.bullets?.map((b: string, bi: number) => (
                                  <li key={bi} style={{ marginBottom: 4 }}>
                                    {b}
                                  </li>
                                ))}
                              </ul>
                            </div>
                          ))}
                        </div>
                      ) : activeSlide.bullets && activeSlide.bullets.length > 0 ? (
                        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                          {activeSlide.bullets.map((b: string, bi: number) => (
                            <div
                              key={bi}
                              style={{
                                display: "flex",
                                alignItems: "flex-start",
                                gap: 8,
                                background: activeSlide.slide_type === "title" ? "rgba(255,255,255,0.06)" : "#F3EEE4",
                                padding: "10px 14px",
                                borderRadius: 10,
                                fontSize: 14,
                                lineHeight: 1.6,
                              }}
                            >
                              <span style={{ color: "#BE4A24", fontWeight: 700 }}>✦</span>
                              <span>{b}</span>
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </div>

                    {/* Slide Footer */}
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        fontSize: 11,
                        color: "#8A8178",
                        borderTop: "1px solid #D4CBBD",
                        paddingTop: 8,
                      }}
                    >
                      <span>سرد • المستشار الثقافي للمملكة العربية السعودية</span>
                      <span>
                        شريحة {currentSlideIdx + 1} من {slides.length}
                      </span>
                    </div>
                  </div>

                  {/* Navigation Controls */}
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      gap: 14,
                      marginTop: 16,
                    }}
                  >
                    <button
                      onClick={() => setCurrentSlideIdx((prev) => Math.max(0, prev - 1))}
                      disabled={currentSlideIdx === 0}
                      style={{
                        background: currentSlideIdx === 0 ? "#E8E0D2" : "#141210",
                        color: currentSlideIdx === 0 ? "#8A8178" : "#F3EEE4",
                        border: "none",
                        borderRadius: 999,
                        padding: "8px 18px",
                        cursor: currentSlideIdx === 0 ? "not-allowed" : "pointer",
                        fontWeight: 600,
                        fontSize: 13,
                      }}
                    >
                      {isAr ? "السابق" : "Previous"}
                    </button>

                    <div style={{ display: "flex", gap: 6 }}>
                      {slides.map((_: any, idx: number) => (
                        <button
                          key={idx}
                          onClick={() => setCurrentSlideIdx(idx)}
                          style={{
                            width: 10,
                            height: 10,
                            borderRadius: 999,
                            background: currentSlideIdx === idx ? "#BE4A24" : "#D4CBBD",
                            border: "none",
                            cursor: "pointer",
                            padding: 0,
                          }}
                        />
                      ))}
                    </div>

                    <button
                      onClick={() => setCurrentSlideIdx((prev) => Math.min(slides.length - 1, prev + 1))}
                      disabled={currentSlideIdx === slides.length - 1}
                      style={{
                        background: currentSlideIdx === slides.length - 1 ? "#E8E0D2" : "#141210",
                        color: currentSlideIdx === slides.length - 1 ? "#8A8178" : "#F3EEE4",
                        border: "none",
                        borderRadius: 999,
                        padding: "8px 18px",
                        cursor: currentSlideIdx === slides.length - 1 ? "not-allowed" : "pointer",
                        fontWeight: 600,
                        fontSize: 13,
                      }}
                    >
                      {isAr ? "التالي" : "Next"}
                    </button>
                  </div>
                </div>
              ) : (
                <div style={{ textAlign: "center", padding: "40px 20px" }}>
                  <p style={{ fontSize: 16, color: "#141210" }}>{artData.message_ar || "عرض تقديمي ثقافي جاهز للتحميل"}</p>
                </div>
              )}
            </div>
          )}

          {/* 2. Recipe & Craft Card Viewer */}
          {isRecipeOrCraft && (
            <div
              style={{
                background: "#FAF7F1",
                border: "1.5px solid #D4CBBD",
                borderRadius: 18,
                padding: "24px",
                boxShadow: "0 2px 14px rgba(20,18,16,0.06)",
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "flex-start",
                  borderBottom: "2px solid #E8E0D2",
                  paddingBottom: 14,
                  marginBottom: 16,
                }}
              >
                <div>
                  <span style={{ fontSize: 12, fontWeight: 700, color: "#BE4A24" }}>
                    ✦ {artData.region || "المملكة العربية السعودية"}
                  </span>
                  <h1
                    style={{
                      fontFamily: "'Noto Naskh Arabic', serif",
                      fontSize: 22,
                      fontWeight: 700,
                      color: "#141210",
                      margin: "4px 0 0",
                    }}
                  >
                    {artData.title || artData.item_name}
                  </h1>
                </div>
                <div
                  style={{
                    background: "#F3EEE4",
                    border: "1px solid #D4CBBD",
                    borderRadius: 10,
                    padding: "6px 12px",
                    fontSize: 12,
                    color: "#4A513C",
                    fontWeight: 600,
                  }}
                >
                  {artData.servings_or_yield || "٦ أشخاص"}
                </div>
              </div>

              {/* Cultural Lore Box */}
              {artData.cultural_story && (
                <div
                  style={{
                    background: "#F3EEE4",
                    borderInlineStart: "4px solid #C4A46A",
                    borderRadius: 10,
                    padding: "12px 16px",
                    marginBottom: 18,
                    fontSize: 13.5,
                    lineHeight: 1.7,
                    color: "#3A342E",
                  }}
                >
                  <strong style={{ color: "#6E1F1F" }}>📜 {isAr ? "سالفة الطبخة / الحرفة:" : "Cultural Lore:"} </strong>
                  {artData.cultural_story}
                </div>
              )}

              {/* Ingredients & Steps Two Columns */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
                {/* Ingredients */}
                <div>
                  <h3
                    style={{
                      fontFamily: "'Noto Naskh Arabic', serif",
                      fontSize: 16,
                      fontWeight: 700,
                      color: "#BE4A24",
                      marginBottom: 10,
                    }}
                  >
                    ✦ {isAr ? "المقادير والمكونات الأصيلة" : "Ingredients & Materials"}
                  </h3>
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    {artData.ingredients_or_materials?.map((ing: any, idx: number) => (
                      <div
                        key={idx}
                        style={{
                          background: "#F3EEE4",
                          border: "1px solid #E8E0D2",
                          borderRadius: 8,
                          padding: "8px 12px",
                          fontSize: 13,
                          display: "flex",
                          justifyContent: "space-between",
                        }}
                      >
                        <span style={{ fontWeight: 600, color: "#141210" }}>{ing.name}</span>
                        {ing.quantity && (
                          <span style={{ color: "#6E1F1F", fontSize: 12 }}>
                            {ing.quantity} {ing.traditional_unit || ""}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>

                {/* Steps */}
                <div>
                  <h3
                    style={{
                      fontFamily: "'Noto Naskh Arabic', serif",
                      fontSize: 16,
                      fontWeight: 700,
                      color: "#4A513C",
                      marginBottom: 10,
                    }}
                  >
                    ✦ {isAr ? "خطوات التحضير التراثية" : "Preparation Steps"}
                  </h3>
                  <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                    {artData.steps?.map((step: any, idx: number) => (
                      <div
                        key={idx}
                        style={{
                          background: "#F3EEE4",
                          border: "1px solid #E8E0D2",
                          borderRadius: 8,
                          padding: "10px 12px",
                          fontSize: 13,
                        }}
                      >
                        <div style={{ fontWeight: 700, color: "#BE4A24", marginBottom: 3 }}>
                          {step.step_number || idx + 1}. {step.title}
                        </div>
                        <div style={{ color: "#3A342E", lineHeight: 1.6 }}>{step.instruction}</div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* 3. Heritage Calendar Sync Panel */}
          {isCalendar && (
            <div>
              <div
                style={{
                  background: "#141210",
                  color: "#F3EEE4",
                  borderRadius: 16,
                  padding: "18px 22px",
                  marginBottom: 20,
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <div>
                  <span style={{ color: "#C4A46A", fontSize: 12, fontWeight: 700 }}>
                    ✦ {isAr ? "التقويم والمواسم الفلكية التراثية" : "Heritage Astronomical Calendar"}
                  </span>
                  <h2
                    style={{
                      fontFamily: "'Noto Naskh Arabic', serif",
                      fontSize: 19,
                      margin: "4px 0 0",
                    }}
                  >
                    {isAr ? "مزامنة المناسبات والمواسم بنقرة واحدة" : "1-Click Heritage Calendar Sync"}
                  </h2>
                </div>
                {artifact.url && (
                  <a
                    href={artifact.url}
                    download
                    style={{
                      background: "#C4A46A",
                      color: "#141210",
                      padding: "8px 18px",
                      borderRadius: 999,
                      fontWeight: 700,
                      fontSize: 13,
                      textDecoration: "none",
                    }}
                  >
                    {isAr ? "تحميل روزنامة (.ics)" : "Download (.ics)"}
                  </a>
                )}
              </div>

              {/* Events List */}
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                {artData.events?.map((ev: any, idx: number) => (
                  <div
                    key={idx}
                    style={{
                      background: "#FAF7F1",
                      border: "1.5px solid #D4CBBD",
                      borderRadius: 14,
                      padding: "16px 20px",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      gap: 16,
                    }}
                  >
                    <div style={{ flex: 1 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                        <span
                          style={{
                            background: "#E8E0D2",
                            color: "#BE4A24",
                            padding: "2px 8px",
                            borderRadius: 6,
                            fontSize: 11,
                            fontWeight: 700,
                          }}
                        >
                          {ev.category === "astronomical_season"
                            ? "موسم فلكي"
                            : ev.category === "national_holiday"
                            ? "مناسبة وطنية"
                            : "مهرجان ثقافي"}
                        </span>
                        <span style={{ fontSize: 12, color: "#8A8178" }}>
                          {ev.start_date} {ev.hijri_start ? `(${ev.hijri_start})` : ""}
                        </span>
                      </div>
                      <h3
                        style={{
                          fontFamily: "'Noto Naskh Arabic', serif",
                          fontSize: 16,
                          fontWeight: 700,
                          color: "#141210",
                          margin: "0 0 6px",
                        }}
                      >
                        {ev.title_ar}
                      </h3>
                      <p style={{ fontSize: 13, color: "#3A342E", margin: 0, lineHeight: 1.6 }}>
                        {ev.description_ar}
                      </p>
                    </div>

                    {ev.google_calendar_url && (
                      <a
                        href={ev.google_calendar_url}
                        target="_blank"
                        rel="noreferrer"
                        style={{
                          background: "#4A513C",
                          color: "#FFFFFF",
                          padding: "8px 16px",
                          borderRadius: 999,
                          fontSize: 12.5,
                          fontWeight: 600,
                          textDecoration: "none",
                          whiteSpace: "nowrap",
                          display: "inline-flex",
                          alignItems: "center",
                          gap: 6,
                        }}
                      >
                        <span>+ Google Calendar</span>
                      </a>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 4. Cultural Greeting Card Studio */}
          {isGreetingCard && (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
              {/* Theme Switcher Bar */}
              <div style={{ display: "flex", gap: 10, marginBottom: 18 }}>
                {[
                  { id: "dark_gold", label: "الذهب الملكي (أسود)" },
                  { id: "royal_green", label: "الأخضر الزيتي" },
                  { id: "warm_clay", label: "الطين العتيق" },
                ].map((t) => (
                  <button
                    key={t.id}
                    onClick={() => setCardTheme(t.id)}
                    style={{
                      background: cardTheme === t.id ? "#141210" : "#E8E0D2",
                      color: cardTheme === t.id ? "#C4A46A" : "#141210",
                      border: "none",
                      borderRadius: 999,
                      padding: "6px 14px",
                      fontSize: 12.5,
                      fontWeight: 600,
                      cursor: "pointer",
                    }}
                  >
                    {t.label}
                  </button>
                ))}
              </div>

              {/* Render SVG or Visual Card */}
              {artData.svg_markup ? (
                <div
                  style={{
                    maxWidth: 720,
                    width: "100%",
                    borderRadius: 18,
                    overflow: "hidden",
                    boxShadow: "0 10px 30px rgba(20,18,16,0.15)",
                  }}
                  dangerouslySetInnerHTML={{ __html: artData.svg_markup }}
                />
              ) : (
                <div
                  style={{
                    maxWidth: 680,
                    width: "100%",
                    background: cardTheme === "dark_gold" ? "#141210" : "#4A513C",
                    color: "#F3EEE4",
                    border: "2px solid #C4A46A",
                    borderRadius: 18,
                    padding: "36px 30px",
                    textAlign: "center",
                  }}
                >
                  <div style={{ color: "#C4A46A", fontSize: 14, fontWeight: 700, marginBottom: 12 }}>
                    ✦ {artData.title || "بطاقة تهنئة ومعايدة"} ✦
                  </div>
                  {artData.poetic_verse && (
                    <div
                      style={{
                        fontFamily: "'Noto Naskh Arabic', serif",
                        fontSize: 20,
                        fontWeight: 700,
                        color: "#C4A46A",
                        margin: "18px 0",
                        lineHeight: 1.8,
                      }}
                    >
                      « {artData.poetic_verse} »
                    </div>
                  )}
                  {artData.personal_message && (
                    <p style={{ fontSize: 14, lineHeight: 1.8, color: "#F3EEE4" }}>
                      {artData.personal_message}
                    </p>
                  )}
                </div>
              )}
            </div>
          )}

          {/* 5. Etiquette Simulator & Flowchart */}
          {isEtiquette && (
            <div>
              {artData.diagram_svg ? (
                <div
                  style={{
                    width: "100%",
                    borderRadius: 16,
                    overflow: "hidden",
                    boxShadow: "0 4px 20px rgba(20,18,16,0.06)",
                  }}
                  dangerouslySetInnerHTML={{ __html: artData.diagram_svg }}
                />
              ) : null}
            </div>
          )}

          {/* 6. Dialect & Proverb Lore Card */}
          {isDialect && (
            <div
              style={{
                background: "#FAF7F1",
                border: "1.5px solid #D4CBBD",
                borderRadius: 18,
                padding: "24px",
              }}
            >
              <span style={{ fontSize: 12, fontWeight: 700, color: "#BE4A24" }}>
                ✦ {artData.region_name || "لهجة سعودية أصيلة"}
              </span>
              <h1
                style={{
                  fontFamily: "'Noto Naskh Arabic', serif",
                  fontSize: 22,
                  fontWeight: 700,
                  color: "#141210",
                  margin: "6px 0 16px",
                }}
              >
                « {artData.proverb_title || artData.input_phrase} »
              </h1>

              <div
                style={{
                  background: "#F3EEE4",
                  borderRadius: 12,
                  padding: "16px 20px",
                  marginBottom: 16,
                  borderInlineStart: "4px solid #BE4A24",
                }}
              >
                <div style={{ fontWeight: 700, color: "#141210", marginBottom: 6 }}>
                  📖 {isAr ? "المعنى والدلالة:" : "Meaning & Interpretation:"}
                </div>
                <p style={{ margin: 0, fontSize: 14, lineHeight: 1.7, color: "#3A342E" }}>
                  {artData.meaning_ar}
                </p>
              </div>

              {artData.lore_story_ar && (
                <div
                  style={{
                    background: "#F3EEE4",
                    borderRadius: 12,
                    padding: "16px 20px",
                    marginBottom: 16,
                    borderInlineStart: "4px solid #C4A46A",
                  }}
                >
                  <div style={{ fontWeight: 700, color: "#6E1F1F", marginBottom: 6 }}>
                    📜 {isAr ? "سالفة المثل (القصة التاريخية):" : "Origin Story & Lore:"}
                  </div>
                  <p style={{ margin: 0, fontSize: 14, lineHeight: 1.7, color: "#3A342E" }}>
                    {artData.lore_story_ar}
                  </p>
                </div>
              )}

              {artData.situational_context_ar && (
                <div
                  style={{
                    background: "rgba(74, 81, 60, 0.08)",
                    border: "1px solid rgba(74, 81, 60, 0.2)",
                    borderRadius: 12,
                    padding: "14px 18px",
                    fontSize: 13.5,
                    color: "#4A513C",
                    fontWeight: 600,
                  }}
                >
                  💡 {isAr ? "سياق الاستخدام الأمثل:" : "When to use:"} {artData.situational_context_ar}
                </div>
              )}
            </div>
          )}

          {/* 7. Artisan Craft Concierge Guide */}
          {isArtisan && (
            <div
              style={{
                background: "#FAF7F1",
                border: "1.5px solid #D4CBBD",
                borderRadius: 18,
                padding: "24px",
              }}
            >
              <span style={{ fontSize: 12, fontWeight: 700, color: "#BE4A24" }}>
                ✦ {artData.region || "التراث الثقافي غير المادي"}
              </span>
              <h1
                style={{
                  fontFamily: "'Noto Naskh Arabic', serif",
                  fontSize: 22,
                  fontWeight: 700,
                  color: "#141210",
                  margin: "6px 0 12px",
                }}
              >
                {artData.craft_name}
              </h1>
              <p style={{ fontSize: 14, lineHeight: 1.7, color: "#3A342E", marginBottom: 20 }}>
                {artData.description}
              </p>

              {/* Authentication Checklist */}
              {artData.authentication_checklist && (
                <div style={{ marginBottom: 20 }}>
                  <h3
                    style={{
                      fontFamily: "'Noto Naskh Arabic', serif",
                      fontSize: 16,
                      fontWeight: 700,
                      color: "#6E1F1F",
                      marginBottom: 10,
                    }}
                  >
                    🛡️ {isAr ? "معايير تمييز القطعة الأصلية عن المقلدة:" : "Authentication Criteria:"}
                  </h3>
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {artData.authentication_checklist.map((item: string, idx: number) => (
                      <div
                        key={idx}
                        style={{
                          background: "#F3EEE4",
                          border: "1px solid #D4CBBD",
                          borderRadius: 10,
                          padding: "10px 14px",
                          fontSize: 13.5,
                          display: "flex",
                          gap: 10,
                          alignItems: "flex-start",
                        }}
                      >
                        <span style={{ color: "#BE4A24", fontWeight: 700 }}>✓</span>
                        <span style={{ color: "#141210", lineHeight: 1.6 }}>{item}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Care Instructions */}
              {artData.care_instructions && (
                <div
                  style={{
                    background: "rgba(196, 164, 106, 0.15)",
                    border: "1px solid #C4A46A",
                    borderRadius: 12,
                    padding: "14px 18px",
                    fontSize: 13,
                    color: "#141210",
                  }}
                >
                  <strong>🧺 {isAr ? "إرشادات الصيانة والحفظ:" : "Care & Preservation:"} </strong>
                  {artData.care_instructions}
                </div>
              )}
            </div>
          )}

          {/* 8. Oral History Memoir Reader */}
          {isMemoir && (
            <div>
              <div
                style={{
                  background: "#141210",
                  color: "#F3EEE4",
                  borderRadius: 16,
                  padding: "20px 24px",
                  marginBottom: 20,
                  borderBottom: "3px solid #C4A46A",
                }}
              >
                <span style={{ color: "#C4A46A", fontSize: 12, fontWeight: 700 }}>
                  ✦ {isAr ? "سفر الذكريات والتاريخ الشفوي" : "Oral History & Family Memoir"}
                </span>
                <h1
                  style={{
                    fontFamily: "'Noto Naskh Arabic', serif",
                    fontSize: 22,
                    fontWeight: 700,
                    margin: "4px 0",
                  }}
                >
                  {artData.title}
                </h1>
                <div style={{ fontSize: 13, color: "#E8E0D2" }}>
                  {isAr ? `الراوي: ${artData.narrator || ""}` : `Narrator: ${artData.narrator || ""}`}
                </div>
              </div>

              {/* Chapters */}
              <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                {artData.chapters?.map((ch: any, idx: number) => (
                  <div
                    key={idx}
                    style={{
                      background: "#FAF7F1",
                      border: "1px solid #D4CBBD",
                      borderRadius: 14,
                      padding: "16px 20px",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                      <span style={{ fontWeight: 700, color: "#BE4A24", fontSize: 12 }}>
                        الفصل {ch.chapter_number || idx + 1} • {ch.era || ""}
                      </span>
                      {ch.location && <span style={{ fontSize: 12, color: "#8A8178" }}>📍 {ch.location}</span>}
                    </div>
                    <h3
                      style={{
                        fontFamily: "'Noto Naskh Arabic', serif",
                        fontSize: 16,
                        fontWeight: 700,
                        color: "#141210",
                        margin: "0 0 8px",
                      }}
                    >
                      {ch.title}
                    </h3>
                    <p style={{ fontSize: 13.5, lineHeight: 1.75, color: "#3A342E", margin: 0 }}>
                      {ch.prose_preview || ch.narrative_prose}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 9. Verified Heritage Research Inspector */}
          {isResearch && (
            <div>
              {artData.timeline_svg && (
                <div
                  style={{
                    marginBottom: 20,
                    borderRadius: 16,
                    overflow: "hidden",
                    boxShadow: "0 4px 18px rgba(20,18,16,0.06)",
                  }}
                  dangerouslySetInnerHTML={{ __html: artData.timeline_svg }}
                />
              )}

              {/* Bibliography */}
              {artData.bibliography && (
                <div
                  style={{
                    background: "#FAF7F1",
                    border: "1.5px solid #D4CBBD",
                    borderRadius: 16,
                    padding: "20px",
                  }}
                >
                  <h3
                    style={{
                      fontFamily: "'Noto Naskh Arabic', serif",
                      fontSize: 16,
                      fontWeight: 700,
                      color: "#141210",
                      marginBottom: 12,
                    }}
                  >
                    📚 {isAr ? "سلسلة الإسناد والمراجع المعتمدة (دارة الملك عبد العزيز وهيئة التراث):" : "Verified Bibliography:"}
                  </h3>
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {artData.bibliography.map((b: any, idx: number) => (
                      <div
                        key={idx}
                        style={{
                          background: "#F3EEE4",
                          border: "1px solid #E8E0D2",
                          borderRadius: 8,
                          padding: "10px 14px",
                          fontSize: 13,
                          display: "flex",
                          justifyContent: "space-between",
                        }}
                      >
                        <div>
                          <strong style={{ color: "#6E1F1F" }}>{b.author}: </strong>
                          <span style={{ color: "#141210" }}>{b.title}</span>
                        </div>
                        <span
                          style={{
                            background: "#E8E0D2",
                            padding: "2px 8px",
                            borderRadius: 4,
                            fontSize: 11,
                            color: "#4A513C",
                            fontWeight: 600,
                          }}
                        >
                          {b.doc_type} ({b.year})
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
