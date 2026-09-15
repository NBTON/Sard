"use client";

import React, { useMemo, useState } from "react";
import { Artifact, ArtifactVersion, Lang, artifactHtml } from "@/types";
import { downloadArtifactFile } from "@/lib/api";

/** Shared ArtifactDocument CSS tokens — also injected into the preview iframe. */
export const ARTIFACT_DOC_CSS = `
:root{--sard-paper:#FAF7F1;--sard-ink:#141210;--sard-gold:#C4A46A;--sard-clay:#BE4A24;--sard-sage:#4A513C;}
body{background:var(--sard-paper);color:var(--sard-ink);font-family:'Noto Naskh Arabic','IBM Plex Sans Arabic',serif;line-height:1.85;margin:0;padding:24px;}
h1,h2,h3{color:var(--sard-ink);}a{color:var(--sard-clay);}
.sard-card{background:#fff;border:1px solid #E0D8C8;border-radius:14px;padding:18px;margin:12px 0;}
.sard-badge{display:inline-block;background:var(--sard-ink);color:var(--sard-paper);border-radius:6px;padding:2px 8px;font-size:12px;}
table{border-collapse:collapse;width:100%;}th,td{border:1px solid #D4CBBD;padding:8px 12px;text-align:start;}
blockquote{border-inline-start:3px solid var(--sard-gold);background:#F3EEE4;border-radius:8px;padding:8px 14px;margin:12px 0;}
`;

export function buildArtifactSrcDoc(html: string, dir: "rtl" | "ltr"): string {
  return `<!DOCTYPE html><html dir="${dir}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${ARTIFACT_DOC_CSS}</style></head><body>${html}</body></html>`;
}

export interface ArtifactPanelProps {
  artifact: Artifact | null;
  lang: Lang;
  /** "panel" docks beside chat; "modal" floats (backward-compat wrapper). */
  mode?: "panel" | "modal";
  onClose: () => void;
  onRevise?: (artifact: Artifact, instruction: string) => void;
}

function kindIcon(a: Artifact): string {
  const fmt = (a.format || "").toLowerCase();
  const kind = (a.kind || "").toLowerCase();
  if (fmt === "pptx" || kind === "presentation") return "📊";
  if (fmt === "ics" || kind === "calendar") return "📅";
  if (fmt === "pdf") return "📄";
  if (fmt === "docx") return "📝";
  if (fmt === "svg" || fmt === "png" || kind === "image") return "🖼️";
  if (kind === "card") return "💌";
  if (kind === "diagram") return "🧭";
  return "🏛️";
}

function statusLabel(status: string, isAr: boolean): string {
  const s = (status || "").toLowerCase();
  if (s === "created") return isAr ? "جاهز" : "Ready";
  if (s === "pending") return isAr ? "جارٍ التوليد..." : "Generating...";
  if (s === "failed") return isAr ? "تعذّر التوليد" : "Failed";
  if (s === "skipped") return isAr ? "تم التخطي" : "Skipped";
  return status;
}

function previewOf(a: Artifact): any {
  return a.preview ?? (a as any).data ?? (a as any).card_data ?? null;
}

function googleCalendarUrl(ev: any, title: string): string {
  const text = encodeURIComponent(ev.summary || ev.title || title);
  const details = encodeURIComponent(ev.description || "");
  const loc = encodeURIComponent(ev.location || "");
  let dates = "";
  const start = typeof ev.start === "string" ? ev.start.replace(/[-:]/g, "").split(".")[0] : "";
  const end = typeof ev.end === "string" ? ev.end.replace(/[-:]/g, "").split(".")[0] : "";
  if (start) dates = `&dates=${encodeURIComponent(start + (end ? "/" + end : ""))}`;
  return `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${text}${dates}&details=${details}&location=${loc}`;
}

export function ArtifactPanel({ artifact, lang, mode = "panel", onClose, onRevise }: ArtifactPanelProps) {
  const [slideIdx, setSlideIdx] = useState(0);
  const [tab, setTab] = useState<"preview" | "file">("preview");
  const [reviseText, setReviseText] = useState("");
  const [dlState, setDlState] = useState<{ kind: "idle" | "busy" | "error"; msg?: string }>({ kind: "idle" });
  const [touchY, setTouchY] = useState<number | null>(null);
  const versions: ArtifactVersion[] = useMemo(
    () => (artifact && Array.isArray(artifact.versions) ? artifact.versions : []),
    [artifact]
  );
  const [versionIdx, setVersionIdx] = useState<number>(-1); // -1 = latest/live
  const activeVersion: ArtifactVersion | null =
    versionIdx >= 0 && versionIdx < versions.length ? versions[versionIdx] : null;
  const isAr = lang === "ar";
  const dir = isAr ? "rtl" : "ltr";

  const liveUrl = artifact?.download_url || (artifact as any)?.url || null;
  const url = (activeVersion?.download_url || liveUrl || null) as string | null;
  const versionPreview = activeVersion?.preview ?? (artifact ? previewOf(artifact) : null);
  const html = artifact ? artifactHtml(versionPreview) || artifact.html || null : null;
  const p = versionPreview && typeof versionPreview === "object" ? versionPreview : {};
  const fmt = (artifact?.format || (artifact as any)?.type || "").toLowerCase();
  const kind = (artifact?.kind || "").toLowerCase();

  const isCalendar = fmt === "ics" || kind === "calendar";
  const slides: any[] = Array.isArray(p.slides) ? p.slides : [];
  const isPresentation = (fmt === "pptx" || kind === "presentation") && slides.length > 0;
  const events: any[] = Array.isArray(p.events) ? p.events : [];
  const steps: any[] = Array.isArray(p.steps)
    ? p.steps
    : Array.isArray(p.flow)
      ? p.flow
      : [];
  const isRecipe =
    kind === "recipe" || p.ingredients_or_materials || p.instructions_or_steps;
  const isEtiquette = !isCalendar && !isPresentation && !isRecipe && steps.length > 0;

  async function handleDownload() {
    if (!artifact || !url || url === "#") {
      setDlState({ kind: "error", msg: isAr ? "رابط التحميل غير متوفر." : "Download URL unavailable." });
      return;
    }
    setDlState({ kind: "busy" });
    try {
      const { objectUrl } = await downloadArtifactFile(url, artifact.filename || "sard-artifact");
      setDlState({ kind: "idle" });
      setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
    } catch (err: any) {
      setDlState({ kind: "error", msg: err?.message || (isAr ? "فشل التحميل." : "Download failed.") });
    }
  }

  async function copyWebcal() {
    if (!url) return;
    const webcal = url.replace(/^https?:/, "webcal:");
    try {
      await navigator.clipboard.writeText(webcal);
      setDlState({ kind: "idle", msg: undefined });
    } catch {
      setDlState({ kind: "error", msg: webcal });
    }
  }

  if (!artifact) return null;
  const shownVersionLabel = `v${activeVersion ? activeVersion.version : versions.length || 1}`;

  const shell: React.CSSProperties =
    mode === "modal"
      ? {
          position: "fixed", inset: 0, zIndex: 9999, background: "rgba(20,18,16,0.72)",
          backdropFilter: "blur(6px)", display: "flex", alignItems: "center",
          justifyContent: "center", padding: 16,
        }
      : { display: "flex", flexDirection: "column", height: "100%", minHeight: 0 };

  const card: React.CSSProperties =
    mode === "modal"
      ? {
          background: "#F3EEE4", border: "1.5px solid #D4CBBD", borderRadius: 22,
          maxWidth: 960, width: "100%", maxHeight: "90vh", display: "flex",
          flexDirection: "column", overflow: "hidden",
          boxShadow: "0 20px 50px rgba(20,18,16,0.35)",
        }
      : {
          background: "#FAF7F1", borderInlineStart: "1.5px solid #D4CBBD", display: "flex",
          flexDirection: "column", height: "100%", minHeight: 0, overflow: "hidden",
        };

  return (
    <div
      className="artifact-panel-shell"
      style={shell}
      onClick={mode === "modal" ? onClose : undefined}
      onTouchStart={(e) => setTouchY(e.touches[0]?.clientY ?? null)}
      onTouchEnd={(e) => {
        if (touchY == null) return;
        const dy = (e.changedTouches[0]?.clientY ?? touchY) - touchY;
        setTouchY(null);
        if (dy > 90) onClose(); // bottom-sheet swipe-close on mobile
      }}
    >
      <div className="artifact-panel-card" style={card} onClick={(e) => e.stopPropagation()} dir={dir}>
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "14px 20px", background: "#141210", color: "#F3EEE4", borderBottom: "2px solid #C4A46A" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
            <span style={{ fontSize: 20 }}>{kindIcon(artifact)}</span>
            <div style={{ minWidth: 0 }}>
              <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {artifact.title || (isAr ? "معاينة المخرج الثقافي" : "Cultural Artifact Preview")}
              </h3>
              <span style={{ fontSize: 11, color: "#C4A46A", textTransform: "uppercase", letterSpacing: 0.5 }}>
                {fmt.toUpperCase()} {kind ? `• ${kind}` : ""}
                {versions.length > 1 ? (
                  <select
                    aria-label={isAr ? "إصدارات المخرج" : "Artifact versions"}
                    value={versionIdx}
                    onChange={(e) => setVersionIdx(Number(e.target.value))}
                    onClick={(e) => e.stopPropagation()}
                    style={{ marginInlineStart: 8, background: "#2D2620", color: "#C4A46A", border: "1px solid #C4A46A", borderRadius: 6, fontSize: 11, padding: "1px 4px" }}
                  >
                    <option value={-1}>{isAr ? `الأحدث (v${versions.length})` : `Latest (v${versions.length})`}</option>
                    {versions.map((v, i) => (
                      <option key={i} value={i}>v{v.version}</option>
                    ))}
                  </select>
                ) : (
                  <span style={{ marginInlineStart: 8 }}>[{shownVersionLabel}▾]</span>
                )}
                {" • "}
                {statusLabel(activeVersion?.status || artifact.status, isAr)}
              </span>
            </div>
          </div>
          <button onClick={onClose} aria-label={isAr ? "إغلاق" : "Close"}
            style={{ background: "rgba(255,255,255,0.08)", border: "1px solid rgba(255,255,255,0.15)", color: "#F3EEE4", borderRadius: 8, width: 32, height: 32, cursor: "pointer", fontSize: 16 }}>
            ✕
          </button>
        </div>

        {/* Tab switch: Preview (inline) vs File (attachment/download) */}
        <div style={{ display: "flex", gap: 8, padding: "10px 20px 0" }}>
          {(["preview", "file"] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)}
              style={{ padding: "6px 14px", borderRadius: 999, fontSize: 12, fontWeight: 700, cursor: "pointer",
                background: tab === t ? "#141210" : "#F3EEE4", color: tab === t ? "#F3EEE4" : "#3A342E",
                border: "1px solid #D4CBBD" }}>
              {t === "preview" ? (isAr ? "معاينة" : "Preview") : (isAr ? "الملف" : "File")}
            </button>
          ))}
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflowY: "auto", padding: 20, minHeight: 0 }}>
          {artifact.status === "failed" ? (
            <div style={{ background: "rgba(190,74,36,0.08)", border: "1.5px solid #BE4A24", borderRadius: 12, padding: 16, color: "#BE4A24", fontSize: 13 }}>
              ⚠️ {artifact.error || (isAr ? "تعذّر توليد هذا المخرج." : "This artifact could not be generated.")}
              {artifact.error_category && <div style={{ fontSize: 11, marginTop: 6 }}>({artifact.error_category})</div>}
            </div>
          ) : tab === "preview" && html ? (
            <iframe title={artifact.title} sandbox="allow-same-origin"
              srcDoc={buildArtifactSrcDoc(html, dir)}
              style={{ width: "100%", minHeight: 420, height: "100%", border: "1px solid #E0D8C8", borderRadius: 12, background: "#FAF7F1" }} />
          ) : tab === "preview" && isPresentation ? (
            <div>
              <div style={{ background: "#1E1B18", borderRadius: 16, padding: "32px 28px", color: "#F3EEE4", minHeight: 240, border: "1px solid #3D352E", marginBottom: 12 }}>
                <div style={{ fontSize: 12, color: "#C4A46A", fontWeight: 700, marginBottom: 8 }}>
                  {isAr ? `شريحة ${slideIdx + 1} من ${slides.length}` : `Slide ${slideIdx + 1} of ${slides.length}`}
                </div>
                <h2 style={{ fontSize: 20, margin: "0 0 12px 0" }}>{slides[slideIdx]?.title || ""}</h2>
                <ul style={{ lineHeight: 1.8, fontSize: 14, color: "#D4CBBD" }}>
                  {(slides[slideIdx]?.bullets || slides[slideIdx]?.content || []).map((b: string, i: number) => <li key={i}>{b}</li>)}
                </ul>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <button disabled={slideIdx === 0} onClick={() => setSlideIdx((v) => Math.max(0, v - 1))}
                  style={{ padding: "8px 16px", borderRadius: 8, border: "none", cursor: "pointer", background: "#2D2620", color: "#F3EEE4", fontSize: 13 }}>{isAr ? "← السابقة" : "← Previous"}</button>
                <button disabled={slideIdx === slides.length - 1} onClick={() => setSlideIdx((v) => Math.min(slides.length - 1, v + 1))}
                  style={{ padding: "8px 16px", borderRadius: 8, border: "none", cursor: "pointer", background: "#2D2620", color: "#F3EEE4", fontSize: 13 }}>{isAr ? "التالية →" : "Next →"}</button>
              </div>
            </div>
          ) : tab === "preview" && isRecipe ? (
            <div style={{ background: "#FFF", borderRadius: 16, padding: 24, border: "1px solid #E0D8C8" }}>
              <h2 style={{ margin: "0 0 12px 0", fontSize: 18 }}>{p.name || p.title || artifact.title}</h2>
              {p.ingredients_or_materials && (
                <><h4 style={{ fontSize: 14 }}>{isAr ? "المقادير / المواد:" : "Ingredients:"}</h4>
                <ul style={{ fontSize: 13, lineHeight: 1.7 }}>{p.ingredients_or_materials.map((x: any, i: number) => <li key={i}>{typeof x === "string" ? x : x.name || x.title}</li>)}</ul></>
              )}
              {p.instructions_or_steps && (
                <><h4 style={{ fontSize: 14 }}>{isAr ? "الخطوات:" : "Steps:"}</h4>
                <ol style={{ fontSize: 13, lineHeight: 1.7 }}>{p.instructions_or_steps.map((x: any, i: number) => <li key={i}>{typeof x === "string" ? x : x.instruction || x.title}</li>)}</ol></>
              )}
              {p.cultural_significance && <p style={{ fontSize: 12, color: "#63584E" }}>{p.cultural_significance}</p>}
            </div>
          ) : tab === "preview" && (isCalendar || events.length > 0) ? (
            <div style={{ background: "#FFF", borderRadius: 16, padding: 24, border: "1px solid #E0D8C8" }}>
              <h3 style={{ margin: "0 0 12px 0", fontSize: 15 }}>{isAr ? "المناسبات المجدولة (.ics)" : "Scheduled Events (.ics)"} ({events.length || 1})</h3>
              {(events.length > 0 ? events : [p]).map((ev: any, i: number) => (
                <div key={i} style={{ padding: 12, background: "#F8F5EE", borderRadius: 10, border: "1px solid #EAE3D5", marginBottom: 8 }}>
                  <div style={{ fontWeight: 700, fontSize: 13 }}>{ev.summary || ev.title || artifact.title}</div>
                  <div style={{ fontSize: 12, color: "#7D6E5D" }}>{ev.start ? `⏱️ ${ev.start} ` : ""}{ev.location ? `📍 ${ev.location}` : ""}</div>
                  {tab === "preview" && (
                    <a href={googleCalendarUrl(ev, artifact.title)} target="_blank" rel="noopener noreferrer"
                      style={{ fontSize: 12, color: "#BE4A24", fontWeight: 700 }}>
                      {isAr ? "＋ إضافة إلى تقويم Google" : "＋ Add to Google Calendar"}
                    </a>
                  )}
                </div>
              ))}
            </div>
          ) : tab === "preview" && isEtiquette ? (
            <div style={{ background: "#FFF", borderRadius: 16, padding: 24, border: "1px solid #E0D8C8" }}>
              {steps.map((s: any, i: number) => (
                <div key={i} style={{ display: "flex", gap: 10, padding: 10, background: "#F8F5EE", borderRadius: 8, marginBottom: 8 }}>
                  <div style={{ width: 24, height: 24, borderRadius: "50%", background: "#C4A46A", color: "#FFF", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 700, flexShrink: 0 }}>{i + 1}</div>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{typeof s === "string" ? s : s.action || s.title}</div>
                </div>
              ))}
            </div>
          ) : (
            /* Generic fallback: human-readable summary — never raw JSON internals. */
            <div style={{ background: "#FFF", borderRadius: 16, padding: 24, border: "1px solid #E0D8C8" }}>
              <div style={{ fontSize: 40, marginBottom: 8 }}>{kindIcon(artifact)}</div>
              <h2 style={{ margin: "0 0 8px 0", fontSize: 17 }}>{artifact.title}</h2>
              <div style={{ fontSize: 13, color: "#63584E", lineHeight: 2 }}>
                <div>{isAr ? "الصيغة: " : "Format: "}<strong>{fmt.toUpperCase()}</strong>{kind ? ` • ${kind}` : ""}</div>
                {artifact.filename && <div>{isAr ? "الملف: " : "File: "}{artifact.filename}</div>}
                {artifact.size_bytes > 0 && <div>{isAr ? "الحجم: " : "Size: "}{Math.max(1, Math.round(artifact.size_bytes / 1024))} KB</div>}
                <div>{isAr ? "الحالة: " : "Status: "}{statusLabel(activeVersion?.status || artifact.status, isAr)}</div>
                {versions.length > 1 && <div>{isAr ? "الإصدارات: " : "Versions: "}{versions.length}</div>}
                {slides.length > 0 && <div>{isAr ? "الشرائح: " : "Slides: "}{slides.length}</div>}
                {events.length > 0 && <div>{isAr ? "الفعاليات: " : "Events: "}{events.length}</div>}
              </div>
              {artifact.revisionHint && (
                <p style={{ fontSize: 12, color: "#8A8178", borderTop: "1px solid #EAE3D5", paddingTop: 10, marginTop: 10 }}>💡 {artifact.revisionHint}</p>
              )}
              {!url && (
                <p style={{ fontSize: 12, color: "#9E3A2F" }}>{isAr ? "الملف غير متوفر للتحميل بعد." : "File download is not available yet."}</p>
              )}
            </div>
          )}
        </div>

        {/* Footer: download + calendar + revise */}
        <div style={{ padding: "12px 20px", background: "#EAE3D5", borderTop: "1px solid #D4CBBD" }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            {url ? (
              <button onClick={handleDownload} disabled={dlState.kind === "busy"}
                style={{ padding: "8px 18px", borderRadius: 10, background: "#141210", color: "#C4A46A", fontWeight: 700, fontSize: 13, border: "none", cursor: "pointer" }}>
                {dlState.kind === "busy" ? (isAr ? "جارٍ التحميل..." : "Downloading...") : `⬇️ ${isAr ? "تحميل الملف" : "Download"}`}
              </button>
            ) : (
              <span style={{ fontSize: 12, color: "#9E3A2F" }}>{isAr ? "التحميل غير متوفر" : "Download unavailable"}</span>
            )}
            {isCalendar && url && (
              <button onClick={copyWebcal}
                style={{ padding: "8px 14px", borderRadius: 10, background: "#4A513C", color: "#FFF", fontWeight: 700, fontSize: 12, border: "none", cursor: "pointer" }}>
                {isAr ? "نسخ رابط webcal" : "Copy webcal link"}
              </button>
            )}
            {onRevise && artifact.status === "created" && (
              <span style={{ display: "inline-flex", gap: 6, flex: 1, minWidth: 180 }}>
                <input value={reviseText} onChange={(e) => setReviseText(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && reviseText.trim()) { onRevise(artifact, reviseText.trim()); setReviseText(""); } }}
                  placeholder={isAr ? "اطلب تنقيحاً… (مثال: اجعله أقصر)" : "Request a revision… (e.g. make it shorter)"}
                  aria-label={isAr ? "تعليمات التنقيح" : "Revision instructions"}
                  style={{ flex: 1, borderRadius: 10, border: "1px solid #D4CBBD", padding: "8px 12px", fontSize: 12, background: "#FFF" }} />
                <button onClick={() => { if (reviseText.trim()) { onRevise(artifact, reviseText.trim()); setReviseText(""); } }}
                  style={{ padding: "8px 14px", borderRadius: 10, background: "#BE4A24", color: "#FFF", fontWeight: 700, fontSize: 12, border: "none", cursor: "pointer" }}>
                  {isAr ? "تنقيح" : "Revise"}
                </button>
              </span>
            )}
          </div>
          {dlState.kind === "error" && dlState.msg && (
            <div role="status" style={{ marginTop: 8, fontSize: 12, color: "#9E3A2F" }}>⚠️ {dlState.msg}</div>
          )}
          <div style={{ marginTop: 6, fontSize: 11, color: "#7D6E5D" }}>
            {artifact.filename ? `📄 ${artifact.filename}` : ""}{artifact.size_bytes > 0 ? ` (${Math.max(1, Math.round(artifact.size_bytes / 1024))} KB)` : ""}
          </div>
        </div>
      </div>
      <style>{`
        @media (max-width: 640px) {
          .artifact-panel-shell { padding: 0 !important; align-items: flex-end !important; }
          .artifact-panel-card { max-width: 100% !important; width: 100% !important; max-height: 92vh !important; height: 92vh !important; border-radius: 18px 18px 0 0 !important; border-inline-start: none !important; border-top: 1.5px solid #D4CBBD !important; animation: sheet-up 0.25s ease; }
        }
        @keyframes sheet-up { from { transform: translateY(40px); opacity: 0.6; } to { transform: translateY(0); opacity: 1; } }
      `}</style>
    </div>
  );
}
