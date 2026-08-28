"use client";
import React from "react";

export interface SaudiRegionStripe {
  regionAr: string;
  regionEn: string;
  height: string;
  background: string;
}

export const SAUDI_13_REGIONS: SaudiRegionStripe[] = [
  { regionAr: "الرياض", regionEn: "Riyadh", height: "76%", background: "#BE4A24" }, // طين نجد
  { regionAr: "مكة المكرمة", regionEn: "Makkah", height: "92%", background: "#141210" }, // كسوة وحرير
  { regionAr: "المدينة المنورة", regionEn: "Madinah", height: "64%", background: "#43503B" }, // نخيل وواحات
  { regionAr: "المنطقة الشرقية", regionEn: "Eastern Province", height: "82%", background: "#294F50" }, // بحر ولؤلؤ
  { regionAr: "عسير", regionEn: "Asir", height: "88%", background: "#741E1D" }, // قط عسيري ورمان
  { regionAr: "تبوك", regionEn: "Tabuk", height: "56%", background: "#C4A46A" }, // رمال حسمي وصخور
  { regionAr: "حائل", regionEn: "Hail", height: "70%", background: "#943919" }, // رمال النفود وجبال أجا
  { regionAr: "القصيم", regionEn: "Qassim", height: "50%", background: "#505640" }, // بساتين ونخيل
  { regionAr: "جازان", regionEn: "Jazan", height: "68%", background: "#2E684F" }, // فل وتلال خضراء
  { regionAr: "نجران", regionEn: "Najran", height: "78%", background: "#A44C28" }, // عمارة طينية أخدودية
  { regionAr: "الباحة", regionEn: "Al Baha", height: "54%", background: "#5B5B4E" }, // حصون حجرية وشجر العرعر
  { regionAr: "الجوف", regionEn: "Al Jouf", height: "46%", background: "#827B32" }, // زيتون الجوف الذهبي
  { regionAr: "الحدود الشمالية", regionEn: "Northern Borders", height: "62%", background: "#88385A" }, // زهر الخزامى والوديان
];

export const CULTURAL_STRIPES = SAUDI_13_REGIONS;

// Header mark: 34×34, rounded 9px square, fill ink #141210, inside: vertical rounded bars with irregular heights
export function SardMark({ size = 34 }: { size?: number }) {
  const pad = 5;
  const gap = 1.1;
  const barW = (size - pad * 2 - gap * (CULTURAL_STRIPES.length - 1)) / CULTURAL_STRIPES.length;

  return (
    <div
      aria-hidden="true"
      style={{
        width: size,
        height: size,
        background: "#141210",
        borderRadius: 9,
        display: "flex",
        alignItems: "flex-end",
        justifyContent: "center",
        padding: pad,
        gap,
        flexShrink: 0,
        overflow: "hidden",
      }}
    >
      {CULTURAL_STRIPES.map((s, i) => (
        <div
          key={i}
          style={{
            width: barW,
            height: s.height,
            background: "#F3EEE4",
            borderRadius: 9999,
            flexShrink: 0,
          }}
        />
      ))}
    </div>
  );
}

// Tiny clay mark for agent meta row (18×18, clay background, paper threads)
export function SardMiniMark() {
  const bars = ["82%", "58%", "88%", "49%", "74%"];

  return (
    <div
      aria-hidden="true"
      style={{
        width: 18,
        height: 18,
        background: "#BE4A24",
        borderRadius: 5,
        display: "flex",
        alignItems: "flex-end",
        justifyContent: "center",
        padding: "2.5px 2px",
        gap: 1.2,
        flexShrink: 0,
      }}
    >
      {bars.map((h, i) => (
        <div
          key={i}
          style={{
            width: 1.6,
            height: h,
            background: "#F3EEE4",
            borderRadius: 999,
          }}
        />
      ))}
    </div>
  );
}

// Vertical cultural stripes with controlled irregular heights
export function WeaveBars({
  variant = "weave-card",
  animated = false,
  lang = "ar",
}: {
  variant?: "weave-card" | "thinking";
  animated?: boolean;
  lang?: "ar" | "en";
}) {
  const isAr = lang === "ar";

  if (variant === "weave-card") {
    return (
      <div className="stripes" aria-hidden="true">
        {SAUDI_13_REGIONS.map((s, i) => (
          <span
            key={i}
            title={`${s.regionAr} • ${s.regionEn}`}
            style={{
              height: s.height,
              background: s.background,
            }}
          />
        ))}
      </div>
    );
  }

  // Thinking State variant: 13 cultural stripes representing the 13 regions of Saudi Arabia
  return (
    <div
      aria-label={
        isAr
          ? "١٣ خيطًا تمثل مناطق المملكة الـ ١٣"
          : "13 cultural threads representing the 13 regions of Saudi Arabia"
      }
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 2.5,
        height: 28,
        padding: "2px 6px",
        background: "rgba(20, 18, 16, 0.03)",
        border: "1px solid rgba(212, 203, 189, 0.5)",
        borderRadius: 8,
        flexShrink: 0,
      }}
    >
      {SAUDI_13_REGIONS.map((region, i) => (
        <div
          key={region.regionEn}
          title={`${region.regionAr} • ${region.regionEn}`}
          style={{
            width: 3,
            height: 20,
            background: region.background,
            borderRadius: 9999,
            flexShrink: 0,
            animation: animated ? "sard-thread-wave 1.4s ease-in-out infinite" : undefined,
            animationDelay: `${(i * 1.4) / SAUDI_13_REGIONS.length}s`,
            transformOrigin: "center center",
          }}
        />
      ))}
    </div>
  );
}

// Premium thinking component for agent cards
export function ThinkingWeave({
  lang,
  statusText,
}: {
  lang: "ar" | "en";
  statusText?: string;
}) {
  const isAr = lang === "ar";

  // Classify current stage from backend status text
  let activeIndex = 0;
  if (statusText) {
    const lower = statusText.toLowerCase();
    if (
      statusText.includes("استرجاع") ||
      statusText.includes("وثائق") ||
      statusText.includes("تراثية") ||
      statusText.includes("مصادر") ||
      statusText.includes("بحث") ||
      lower.includes("retriev") ||
      lower.includes("rag")
    ) {
      activeIndex = 1;
    } else if (
      statusText.includes("صياغة") ||
      statusText.includes("إعداد") ||
      statusText.includes("تدقيق") ||
      lower.includes("synthesiz") ||
      lower.includes("generat")
    ) {
      activeIndex = 2;
    }
  }

  const stages = isAr
    ? [
        { label: "تحليل السؤال والبحث", icon: "✦" },
        { label: "استرجاع المصادر التراثية", icon: "📜" },
        { label: "صياغة الرواية المعتمدة", icon: "✍️" },
      ]
    : [
        { label: "Context Analysis", icon: "✦" },
        { label: "Heritage Archives", icon: "📜" },
        { label: "Narrative Synthesis", icon: "✍️" },
      ];

  const defaultStatus = isAr
    ? "سرد يبحث في المعارف والوثائق الثقافية المعتمدة..."
    : "Sard is exploring verified cultural archives & knowledge...";

  const displayMessage = statusText || defaultStatus;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 13,
        padding: "16px 18px",
        background: "linear-gradient(135deg, #FAF7F1 0%, #F5EFE6 100%)",
        border: "1px solid #E2D7C5",
        borderRadius: 14,
        position: "relative",
        overflow: "hidden",
      }}
      dir={isAr ? "rtl" : "ltr"}
    >
      {/* Top row: 13-Region Cultural Rhythm Wave + Dynamic Status Text */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 14,
        }}
      >
        <WeaveBars variant="thinking" animated lang={lang} />

        <div style={{ display: "flex", flexDirection: "column", gap: 3, flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontFamily: "'IBM Plex Sans Arabic', 'IBM Plex Sans', sans-serif",
              fontSize: 14,
              fontWeight: 600,
              color: "#141210",
              lineHeight: 1.5,
              display: "flex",
              alignItems: "center",
              gap: 8,
              wordBreak: "break-word",
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: 999,
                background: "#BE4A24",
                flexShrink: 0,
                animation: "pulse-dot 1.2s ease-in-out infinite",
              }}
            />
            <span>{displayMessage}</span>
          </div>
          <div
            style={{
              fontSize: 12,
              color: "#8A8178",
              fontWeight: 500,
            }}
          >
            {isAr
              ? "١٣ خيطًا تمثل مناطق المملكة الـ ١٣ • استرجاع وتوثيق ثقافي مستمر"
              : "13 threads weaving knowledge across the 13 Saudi regions"}
          </div>
        </div>
      </div>

      {/* Progress Stage Breadcrumbs */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
          paddingTop: 10,
          borderTop: "1px solid rgba(212, 203, 189, 0.4)",
        }}
      >
        {stages.map((stage, idx) => {
          const isActive = idx === activeIndex;
          const isCompleted = idx < activeIndex;

          return (
            <div
              key={idx}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "3.5px 11px",
                borderRadius: 999,
                fontSize: 12,
                fontWeight: isActive ? 700 : 500,
                background: isActive
                  ? "rgba(190, 74, 36, 0.1)"
                  : isCompleted
                  ? "rgba(74, 81, 60, 0.08)"
                  : "rgba(232, 224, 210, 0.5)",
                color: isActive ? "#BE4A24" : isCompleted ? "#4A513C" : "#8A8178",
                border: isActive
                  ? "1px solid rgba(190, 74, 36, 0.35)"
                  : isCompleted
                  ? "1px solid rgba(74, 81, 60, 0.25)"
                  : "1px solid transparent",
                transition: "all 0.25s ease",
              }}
            >
              {isActive && (
                <span
                  style={{
                    width: 5,
                    height: 5,
                    borderRadius: 999,
                    background: "#BE4A24",
                    display: "inline-block",
                  }}
                />
              )}
              {isCompleted && (
                <span style={{ fontSize: 11, fontWeight: 700 }}>✓</span>
              )}
              <span>{stage.label}</span>
            </div>
          );
        })}
      </div>

      {/* Elegant Shimmering Skeleton Lines */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 8,
          marginTop: 2,
        }}
      >
        <div
          className="sard-shimmer-line"
          style={{
            height: 8,
            width: "86%",
            borderRadius: 999,
          }}
        />
        <div
          className="sard-shimmer-line"
          style={{
            height: 8,
            width: "62%",
            borderRadius: 999,
          }}
        />
      </div>

      <style>{`
        @keyframes sard-thread-wave {
          0%, 100% {
            transform: scaleY(0.35);
            opacity: 0.5;
          }
          50% {
            transform: scaleY(1.0);
            opacity: 1;
          }
        }
        @keyframes sard-shimmer-pass {
          0% {
            background-position: -200% 0;
          }
          100% {
            background-position: 200% 0;
          }
        }
        .sard-shimmer-line {
          background: linear-gradient(90deg, #E2D7C5 0%, #FAF7F1 50%, #E2D7C5 100%);
          background-size: 200% 100%;
          animation: sard-shimmer-pass 2s ease-in-out infinite;
        }
      `}</style>
    </div>
  );
}

