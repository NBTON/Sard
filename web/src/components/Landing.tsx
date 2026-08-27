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
          <div
            data-dir-animate="hero-copy"
            data-dir-id="landing-hero-copy"
            data-dir-stagger="15"
            style={{ minWidth: 0, textAlign: "start" }}
          >
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
            data-dir-animate="card"
            data-dir-id="landing-weave-card"
            data-dir-stagger="35"
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

        {/* 8 Agentic Cultural Studio Tools */}
        <div style={{ marginTop: 36 }}>
          <div
            style={{
              fontSize: 13.5,
              fontWeight: 700,
              color: "#BE4A24",
              letterSpacing: 0.4,
              marginBottom: 14,
              textAlign: "start",
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            <span>✦</span>
            <span>{isAr ? "استوديو سرد التفاعلي والأدوات الثقافية المتقدمة" : "Sard Cultural Agentic Studio & Tools"}</span>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
              gap: 12,
            }}
            className="agentic-tools-grid"
          >
            {[
              {
                icon: "📊",
                titleAr: "عروض تقديمية PPTX",
                titleEn: "Cultural PPTX Decks",
                descAr: "توليد شرائح إيجاز ثقافي 16:9 بنسق جاهز",
                descEn: "Generate 16:9 cultural briefing slides",
                promptAr: "صمم عرض بوربوينت ثقافي متكامل عن يوم التأسيس السعودي مع مقارنات ومراحل زمنية",
                promptEn: "Generate a comprehensive cultural PowerPoint slide deck about Saudi Foundation Day",
                color: "#BE4A24",
              },
              {
                icon: "🍲",
                titleAr: "بطاقات الطهي والحرف (PDF)",
                titleEn: "Printable Recipe & Craft Cards",
                descAr: "بطاقات طباعة بالمقادير التراثية وسالفة الطبخة",
                descEn: "Printable cards with traditional lore and steps",
                promptAr: "صمم بطاقة وصفة الجريش النجدي التراثية مع المقادير بالوحدات التقليدية وسالفة الطبخة وخطوات التحضير",
                promptEn: "Create a printable traditional Najdi Jareesh recipe card with cultural lore and steps",
                color: "#6E1F1F",
              },
              {
                icon: "📅",
                titleAr: "مزامنة التقويم والمواسم",
                titleEn: "Calendar & Astronomical Sync",
                descAr: "مواسم سهيل والمربعانية وربط تقويم Google",
                descEn: "Suhail & astronomical seasons synced to Google",
                promptAr: "أضف مواسم التقويم التراثية وموسم سهيل وفعاليات العلا إلى تقويم Google وملف ics",
                promptEn: "Sync Saudi heritage astronomical seasons and AlUla festivals with Google Calendar (.ics)",
                color: "#4A513C",
              },
              {
                icon: "🧭",
                titleAr: "محاكي الإتيكيت والبروتوكول",
                titleEn: "Etiquette & Protocol Simulator",
                descAr: "مخططات تدفقية لآداب المجلس والضيافة",
                descEn: "Flowcharts for Majlis and hospitality etiquette",
                promptAr: "شغل محاكي إتيكيت المجلس وآداب صب القهوة السعودية وهز الفنجان مع مخطط تدفقي",
                promptEn: "Simulate Saudi Majlis etiquette protocol and coffee serving traditions with interactive flowchart",
                color: "#C4A46A",
              },
              {
                icon: "💌",
                titleAr: "ستوديو بطاقات التهنئة",
                titleEn: "Cultural Greeting Studio",
                descAr: "تصميم بطاقات معايدة بأبيات فصحى ونبطية",
                descEn: "Greeting cards with classical and Nabati poetry",
                promptAr: "صمم بطاقة تهنئة ومعايدة لليوم الوطني السعودي مع أبيات شعرية فصحى ونبطية وتصميم تراثي",
                promptEn: "Create a Saudi National Day greeting card with custom poetry and decorative calligraphy theme",
                color: "#BE4A24",
              },
              {
                icon: "📜",
                titleAr: "فك شفرة وسالفة الأمثال",
                titleEn: "Dialect & Proverb Decoder",
                descAr: "قصة نشأة المثل وسياق استخدامه باللهجات",
                descEn: "Origin stories and dialect context of proverbs",
                promptAr: "فسر مثل أبشر بسعدك واذكر سالفته وقصته التاريخية وسياق استخدامه في اللهجة النجدية",
                promptEn: "Decode the Saudi proverb 'Absher Bi Sa'adak' with its historical origin story and dialect usage",
                color: "#6E1F1F",
              },
              {
                icon: "🏺",
                titleAr: "دليل أصالة الحرف التراثية",
                titleEn: "Artisan Craft Authenticator",
                descAr: "معايير السدو والبشت الحساوي والورد الطائفي",
                descEn: "Authentication standards for Sadu and Bisht",
                promptAr: "استخرج دليل أصالة السدو ومعايير التمييز بين النسيج اليدوي والمصنع مع إرشادات الحفظ",
                promptEn: "Provide authentication criteria for genuine handmade Sadu weaving vs machine-made copies",
                color: "#4A513C",
              },
              {
                icon: "📖",
                titleAr: "توثيق الموروث والسيرة",
                titleEn: "Oral History Memoir Co-Pilot",
                descAr: "تحويل الذكريات الشفوية إلى كتيب سيرة فصيح",
                descEn: "Turn oral memories into chaptered memoir booklets",
                promptAr: "وثق تاريخ وسيرة عائلية في كتيب سيرة فصيح عن رحلات الغوص على اللؤلؤ في المنطقة الشرقية",
                promptEn: "Compile an oral history memoir booklet from family notes about pearl diving in Eastern Province",
                color: "#C4A46A",
              },
            ].map((tool, idx) => (
              <button
                key={idx}
                onClick={() => onSectorPrompt(isAr ? tool.promptAr : tool.promptEn)}
                style={{
                  textAlign: "start",
                  background: "#FAF7F1",
                  border: "1.5px solid #D4CBBD",
                  borderRadius: 16,
                  padding: "16px 14px",
                  cursor: "pointer",
                  display: "flex",
                  flexDirection: "column",
                  gap: 6,
                  transition: "all 0.18s ease",
                  boxShadow: "0 2px 8px rgba(20,18,16,0.04)",
                }}
                onMouseEnter={(e) => {
                  const el = e.currentTarget as HTMLButtonElement;
                  el.style.transform = "translateY(-3px)";
                  el.style.borderColor = tool.color;
                  el.style.boxShadow = `0 8px 20px -4px rgba(20,18,16,0.12)`;
                }}
                onMouseLeave={(e) => {
                  const el = e.currentTarget as HTMLButtonElement;
                  el.style.transform = "translateY(0)";
                  el.style.borderColor = "#D4CBBD";
                  el.style.boxShadow = "0 2px 8px rgba(20,18,16,0.04)";
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontSize: 20 }}>{tool.icon}</span>
                  <span
                    style={{
                      fontFamily: "'Noto Naskh Arabic', serif",
                      fontSize: 14.5,
                      fontWeight: 700,
                      color: "#141210",
                    }}
                  >
                    {isAr ? tool.titleAr : tool.titleEn}
                  </span>
                </div>
                <span style={{ fontSize: 11.5, color: "#6A6258", lineHeight: 1.45 }}>
                  {isAr ? tool.descAr : tool.descEn}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* Sectors: 11 tiles */}
        <div style={{ marginTop: 40 }}>
          <div
            style={{
              fontSize: 13.5,
              fontWeight: 700,
              color: "#3A342E",
              letterSpacing: 0.4,
              marginBottom: 14,
              textAlign: "start",
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
            {SECTORS.map((s, idx) => (
              <button
                key={s.id}
                data-dir-animate="card"
                data-dir-id={`sector_tile_${s.id}`}
                data-dir-stagger={String(40 + (idx % 4) * 20 + Math.floor(idx / 4) * 25)}
                onClick={() => onSectorPrompt(lang === "en" ? s.promptEn : s.promptAr)}
                style={{
                  textAlign: "start",
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
