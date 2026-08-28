"use client";
import React, { useRef, useEffect } from "react";
import { Lang } from "@/types";
import { t } from "@/lib/copy";

export function Composer({
  lang,
  value,
  onChange,
  onSend,
  onStop,
  isStreaming,
}: {
  lang: Lang;
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  onStop: () => void;
  isStreaming: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const isAr = lang === "ar";

  useEffect(() => {
    if (ref.current) {
      ref.current.style.height = "auto";
      ref.current.style.height = `${Math.min(ref.current.scrollHeight, 140)}px`;
    }
  }, [value]);

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!isStreaming && value.trim()) {
        onSend();
      }
    }
  }

  return (
    <div
      style={{
        padding: "12px 18px 18px",
        background: "transparent",
      }}
    >
      {/* Quick Agentic Tools Launcher Bar */}
      <div
        style={{
          maxWidth: 860,
          margin: "0 auto 8px",
          display: "flex",
          gap: 6,
          overflowX: "auto",
          paddingBottom: 4,
          scrollbarWidth: "none",
        }}
      >
        {[
          { icon: "📊", labelAr: "عرض PPTX", labelEn: "PPTX Deck", prompt: "صمم عرض بوربوينت ثقافي متكامل عن " },
          { icon: "🍲", labelAr: "بطاقة وصفة PDF", labelEn: "Recipe Card", prompt: "صمم بطاقة وصفة تراثية مطبوعة لـ " },
          { icon: "📅", labelAr: "مزامنة التقويم", labelEn: "Sync Calendar", prompt: "أضف مواسم التقويم التراثية وموسم سهيل إلى تقويم Google" },
          { icon: "🧭", labelAr: "محاكي الإتيكيت", labelEn: "Etiquette Simulator", prompt: "شغل محاكي إتيكيت المجلس وآداب صب القهوة مع مخطط تدفقي" },
          { icon: "💌", labelAr: "بطاقة تهنئة", labelEn: "Greeting Card", prompt: "صمم بطاقة تهنئة ومعايدة لليوم الوطني بأبيات فصحى ونبطية" },
          { icon: "📜", labelAr: "سالفة مثل", labelEn: "Proverb Lore", prompt: "فسر مثل واذكر سالفته وسياق استخدامه: " },
          { icon: "🏺", labelAr: "أصالة حرفة", labelEn: "Craft Authenticator", prompt: "استخرج دليل أصالة الحرفة ومعايير تمييز القطع الأصلية لـ " },
          { icon: "📖", labelAr: "كتيب سيرة", labelEn: "Memoir Co-Pilot", prompt: "وثق تاريخ وسيرة عائلية في كتيب سيرة فصيح عن " },
        ].map((chip, idx) => (
          <button
            key={idx}
            onClick={() => {
              onChange(chip.prompt);
              ref.current?.focus();
            }}
            style={{
              background: "#FAF7F1",
              border: "1px solid #D4CBBD",
              borderRadius: 999,
              padding: "4px 10px",
              fontSize: 12,
              fontWeight: 600,
              color: "#3A342E",
              whiteSpace: "nowrap",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: 5,
              transition: "all 0.15s ease",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = "#BE4A24";
              e.currentTarget.style.color = "#BE4A24";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = "#D4CBBD";
              e.currentTarget.style.color = "#3A342E";
            }}
          >
            <span>{chip.icon}</span>
            <span>{isAr ? chip.labelAr : chip.labelEn}</span>
          </button>
        ))}
      </div>

      <div
        data-dir-animate="composer-bar"
        data-dir-id="composer-input-bar"
        data-dir-stagger="40"
        style={{
          maxWidth: 860,
          margin: "0 auto",
          background: "#FAF7F1",
          border: "1px solid #D4CBBD",
          borderRadius: 18,
          boxShadow:
            "0 10px 36px -8px rgba(20, 18, 16, 0.10), 0 2px 8px -2px rgba(20, 18, 16, 0.05)",
          display: "flex",
          alignItems: "flex-end",
          gap: 10,
          padding: "10px 10px 10px 16px",
        }}
      >
        <textarea
          ref={ref}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          dir={isAr ? "rtl" : "ltr"}
          rows={1}
          placeholder={t("composerPlaceholder", lang)}
          aria-label={t("composerPlaceholder", lang)}
          style={{
            flex: 1,
            background: "transparent",
            border: "none",
            outline: "none",
            resize: "none",
            fontSize: 15,
            lineHeight: 1.6,
            color: "#141210",
            maxHeight: 140,
            minHeight: 26,
            fontFamily: "inherit",
          }}
        />
        {isStreaming ? (
          <button
            onClick={onStop}
            aria-label="Stop generation"
            style={{
              background: "#8F3518",
              color: "#FAF7F1",
              border: "1px solid #8F3518",
              borderRadius: 999,
              padding: "8px 18px",
              fontSize: 13.5,
              fontWeight: 700,
              cursor: "pointer",
              flexShrink: 0,
            }}
          >
            {isAr ? "إيقاف" : "Stop"}
          </button>
        ) : (
          <button
            onClick={onSend}
            disabled={!value.trim()}
            aria-label={t("send", lang)}
            style={{
              background: value.trim() ? "#141210" : "#E8E0D2",
              color: value.trim() ? "#FAF7F1" : "#8A8178",
              border: `1px solid ${value.trim() ? "#141210" : "#D4CBBD"}`,
              borderRadius: 999,
              padding: "8px 20px",
              fontSize: 13.5,
              fontWeight: 700,
              cursor: value.trim() ? "pointer" : "not-allowed",
              flexShrink: 0,
              opacity: value.trim() ? 1 : 0.9,
              transition: "background 0.15s ease, opacity 0.15s ease",
            }}
          >
            {t("send", lang)}
          </button>
        )}
      </div>
      <div
        style={{
          maxWidth: 860,
          margin: "8px auto 0",
          textAlign: "center",
          fontSize: 11.5,
          color: "#8A8178",
        }}
      >
        {isAr
          ? "⇧ + Enter لسطر جديد — Enter للإرسال"
          : "Shift + Enter for newline — Enter to send"}
      </div>
    </div>
  );
}
