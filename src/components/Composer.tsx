"use client";
import React, { useRef, useEffect, useState } from "react";
import { Attachment, Lang } from "@/types";
import { t } from "@/lib/copy";
import { uploadAttachment } from "@/lib/api";

export function Composer({
  lang,
  value,
  onChange,
  onSend,
  onStop,
  isStreaming,
  attachments = [],
  onAttachmentsChange,
}: {
  lang: Lang;
  value: string;
  onChange: (v: string) => void;
  onSend: (text: string, currentAttachments?: Attachment[]) => void;
  onStop: () => void;
  isStreaming: boolean;
  attachments?: Attachment[];
  onAttachmentsChange?: (attachments: Attachment[]) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const isAr = lang === "ar";
  const [isDragging, setIsDragging] = useState(false);
  const [localAttachments, setLocalAttachments] = useState<Attachment[]>(attachments);
  const [uploadError, setUploadError] = useState<string | null>(null);

  useEffect(() => {
    setLocalAttachments(attachments);
  }, [attachments]);

  const updateAttachments = (newAtts: Attachment[]) => {
    setLocalAttachments(newAtts);
    if (onAttachmentsChange) {
      onAttachmentsChange(newAtts);
    }
  };

  useEffect(() => {
    if (ref.current) {
      ref.current.style.height = "auto";
      ref.current.style.height = `${Math.min(ref.current.scrollHeight, 140)}px`;
    }
  }, [value]);

  const handleFilesSelected = async (files: FileList | File[]) => {
    setUploadError(null);
    const fileArray = Array.from(files);
    if (fileArray.length === 0) return;

    for (const file of fileArray) {
      const tempId = `temp_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
      const previewUrl = file.type.startsWith("image/") ? URL.createObjectURL(file) : undefined;

      const placeholder: Attachment = {
        id: tempId,
        attachment_id: tempId,
        filename: file.name,
        mime_type: file.type || "application/octet-stream",
        size_bytes: file.size,
        preview_url: previewUrl,
        uploading: true,
      };

      setLocalAttachments((prev) => {
        const next = [...prev, placeholder];
        if (onAttachmentsChange) onAttachmentsChange(next);
        return next;
      });

      try {
        const uploaded = await uploadAttachment(file);
        setLocalAttachments((prev) => {
          const next = prev.map((a) => (a.id === tempId ? { ...uploaded, preview_url: previewUrl, uploading: false } : a));
          if (onAttachmentsChange) onAttachmentsChange(next);
          return next;
        });
      } catch (err: any) {
        console.error("File upload error:", err);
        setUploadError(err.message || "Failed to upload file");
        setLocalAttachments((prev) => {
          const next = prev.map((a) => (a.id === tempId ? { ...a, uploading: false, error: err.message || "Upload failed" } : a));
          if (onAttachmentsChange) onAttachmentsChange(next);
          return next;
        });
      }
    }
  };

  const removeAttachment = (id: string) => {
    setLocalAttachments((prev) => {
      const next = prev.filter((a) => a.id !== id && a.attachment_id !== id);
      if (onAttachmentsChange) onAttachmentsChange(next);
      return next;
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      const hasContent = value.trim().length > 0 || localAttachments.length > 0;
      const isUploading = localAttachments.some((a) => a.uploading);
      if (!isStreaming && hasContent && !isUploading) {
        onSend(value, localAttachments);
      }
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleFilesSelected(e.dataTransfer.files);
    }
  };

  const formatFileSize = (bytes: number) => {
    if (!bytes || bytes === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
  };

  const hasSendableContent = value.trim().length > 0 || localAttachments.length > 0;
  const isUploadingAny = localAttachments.some((a) => a.uploading);

  return (
    <div
      style={{
        padding: "12px 18px 18px",
        background: "transparent",
      }}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
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
          { icon: "📄", labelAr: "تقرير PDF", labelEn: "PDF Report", prompt: "أنشئ لي ملف PDF توثيقي شامل عن " },
          { icon: "📊", labelAr: "عرض PPTX", labelEn: "PPTX Deck", prompt: "صمم عرض بوربوينت ثقافي متكامل عن " },
          { icon: "📝", labelAr: "مستند DOCX", labelEn: "DOCX Document", prompt: "أريد تقرير DOCX مفصل عن " },
          { icon: "🍲", labelAr: "بطاقة وصفة PDF", labelEn: "Recipe Card", prompt: "أعطني وصفة بطاقة PDF لـ " },
          { icon: "📅", labelAr: "مزامنة التقويم", labelEn: "Sync Calendar", prompt: "أضف مواسم التقويم التراثية وموسم سهيل إلى تقويم Google" },
          { icon: "🧭", labelAr: "محاكي الإتيكيت", labelEn: "Etiquette Simulator", prompt: "شغل محاكي إتيكيت المجلس وآداب صب القهوة مع مخطط تدفقي" },
          { icon: "💌", labelAr: "بطاقة تهنئة", labelEn: "Greeting Card", prompt: "صمم بطاقة تهنئة ومعايدة لليوم الوطني بأبيات فصحى ونبطية" },
          { icon: "📜", labelAr: "سالفة مثل", labelEn: "Proverb Lore", prompt: "فسر مثل واذكر سالفته وسياق استخدامه: " },
          { icon: "🏺", labelAr: "أصالة حرفة", labelEn: "Craft Authenticator", prompt: "استخرج دليل أصالة الحرفة ومعايير تمييز القطع الأصلية لـ " },
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

      {/* Main Composer Box */}
      <div
        data-dir-animate="composer-bar"
        data-dir-id="composer-input-bar"
        data-dir-stagger="40"
        style={{
          maxWidth: 860,
          margin: "0 auto",
          background: isDragging ? "#F3EEE4" : "#FAF7F1",
          border: isDragging ? "2px dashed #BE4A24" : "1px solid #D4CBBD",
          borderRadius: 18,
          boxShadow:
            "0 10px 36px -8px rgba(20, 18, 16, 0.10), 0 2px 8px -2px rgba(20, 18, 16, 0.05)",
          display: "flex",
          flexDirection: "column",
          padding: "8px 10px 10px 14px",
          transition: "all 0.18s ease",
        }}
      >
        {/* Uploaded Attachments Chips */}
        {localAttachments.length > 0 && (
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: 8,
              paddingBottom: 8,
              marginBottom: 4,
              borderBottom: "1px solid #E8E0D2",
            }}
          >
            {localAttachments.map((att) => {
              const isImage = att.mime_type.startsWith("image/") || (att.preview_url !== undefined);
              const isAudio = att.mime_type.startsWith("audio/") || att.filename.match(/\.(mp3|wav|m4a)$/i);
              const isDoc = att.mime_type.includes("pdf") || att.filename.match(/\.(pdf|docx|txt|md)$/i);

              return (
                <div
                  key={att.id}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 8,
                    background: att.error ? "rgba(190, 74, 36, 0.1)" : "#F3EEE4",
                    border: `1px solid ${att.error ? "#BE4A24" : "#D4CBBD"}`,
                    borderRadius: 12,
                    padding: "4px 10px 4px 6px",
                    fontSize: 12,
                    color: "#141210",
                    maxWidth: 240,
                  }}
                >
                  {isImage && att.preview_url ? (
                    <img
                      src={att.preview_url}
                      alt={att.filename}
                      style={{
                        width: 24,
                        height: 24,
                        objectFit: "cover",
                        borderRadius: 6,
                      }}
                    />
                  ) : (
                    <span style={{ fontSize: 14 }}>{isAudio ? "🎵" : isDoc ? "📄" : "📎"}</span>
                  )}
                  <div style={{ display: "flex", flexDirection: "column", minWidth: 0, flex: 1 }}>
                    <span
                      style={{
                        fontSize: 12,
                        fontWeight: 600,
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {att.filename}
                    </span>
                    <span style={{ fontSize: 10, color: att.error ? "#BE4A24" : "#8A8178" }}>
                      {att.uploading ? (isAr ? "جارٍ الرفع..." : "Uploading...") : att.error ? att.error : formatFileSize(att.size_bytes)}
                    </span>
                  </div>
                  <button
                    onClick={() => removeAttachment(att.id)}
                    aria-label={isAr ? "إزالة المرفق" : "Remove attachment"}
                    style={{
                      background: "transparent",
                      border: "none",
                      color: "#8A8178",
                      fontSize: 14,
                      fontWeight: 700,
                      cursor: "pointer",
                      padding: "0 2px",
                      lineHeight: 1,
                    }}
                  >
                    ×
                  </button>
                </div>
              );
            })}
          </div>
        )}

        {/* Upload Error Banner if any */}
        {uploadError && (
          <div style={{ color: "#BE4A24", fontSize: 12, paddingBottom: 6, fontWeight: 600 }}>
            {uploadError}
          </div>
        )}

        {/* Input Bar: Attachment Button + Textarea + Send/Stop */}
        <div style={{ display: "flex", alignItems: "flex-end", gap: 10 }}>
          {/* Attachment button */}
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            aria-label={isAr ? "إرفاق ملف أو مستند أو تسجيل" : "Attach file, document or audio"}
            title={isAr ? "إرفاق ملف (PDF, DOCX, صور, صوت)" : "Attach file (PDF, DOCX, image, audio)"}
            style={{
              background: "transparent",
              border: "none",
              color: "#8A8178",
              cursor: "pointer",
              padding: "6px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              borderRadius: 999,
              transition: "all 0.15s ease",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = "#BE4A24";
              e.currentTarget.style.background = "#E8E0D2";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = "#8A8178";
              e.currentTarget.style.background = "transparent";
            }}
          >
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" />
            </svg>
          </button>

          {/* Hidden File Input */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".pdf,.docx,.txt,.md,.csv,.json,.jpg,.jpeg,.png,.webp,.tiff,.bmp,.gif,.mp3,.wav,.m4a,.ogg,.flac,.ply,.obj,.stl,.glb,.gltf"
            style={{ display: "none" }}
            onChange={(e) => {
              if (e.target.files && e.target.files.length > 0) {
                handleFilesSelected(e.target.files);
                e.target.value = "";
              }
            }}
          />

          <textarea
            ref={ref}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            dir={isAr ? "rtl" : "ltr"}
            rows={1}
            placeholder={
              localAttachments.length > 0
                ? isAr
                  ? "اكتب سؤالاً أو تعليمات حول الملفات المرفقة..."
                  : "Ask a question or provide instructions about attached files..."
                : t("composerPlaceholder", lang)
            }
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
              onClick={() => onSend(value, localAttachments)}
              disabled={!hasSendableContent || isUploadingAny}
              aria-label={t("send", lang)}
              style={{
                background: hasSendableContent && !isUploadingAny ? "#141210" : "#E8E0D2",
                color: hasSendableContent && !isUploadingAny ? "#FAF7F1" : "#8A8178",
                border: `1px solid ${hasSendableContent && !isUploadingAny ? "#141210" : "#D4CBBD"}`,
                borderRadius: 999,
                padding: "8px 20px",
                fontSize: 13.5,
                fontWeight: 700,
                cursor: hasSendableContent && !isUploadingAny ? "pointer" : "not-allowed",
                flexShrink: 0,
                opacity: hasSendableContent && !isUploadingAny ? 1 : 0.9,
                transition: "background 0.15s ease, opacity 0.15s ease",
              }}
            >
              {t("send", lang)}
            </button>
          )}
        </div>
      </div>

      <div
        style={{
          maxWidth: 860,
          margin: "8px auto 0",
          textAlign: "center",
          fontSize: 11.5,
          color: "#8A8178",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 12,
        }}
      >
        <span>
          {isAr
            ? "⇧ + Enter لسطر جديد — Enter للإرسال"
            : "Shift + Enter for newline — Enter to send"}
        </span>
        <span>•</span>
        <span>
          {isAr
            ? "اسحب الملفات هنا للرفع (PDF, DOCX, صور, صوت)"
            : "Drag & drop files to attach (PDF, DOCX, images, audio)"}
        </span>
      </div>
    </div>
  );
}
