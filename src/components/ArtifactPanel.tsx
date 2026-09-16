"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Artifact,
  ArtifactVersion,
  Lang,
  artifactHtml,
  buildArtifactSrcDoc,
  buildGoogleCalendarUrl,
  buildWebcalUrl,
  resolveCalendarEvent,
  ARTIFACT_DOC_CSS,
} from "@/types";
import { downloadArtifactFile, fetchArtifactVersions, postArtifactRevision } from "@/lib/api";

export { buildArtifactSrcDoc, ARTIFACT_DOC_CSS };

export interface ArtifactPanelProps {
  artifact: Artifact | null;
  lang: Lang;
  /** "panel" docks beside chat; "modal" floats (backward-compat wrapper). */
  mode?: "panel" | "modal";
  onClose: () => void;
  onRevise?: (artifact: Artifact, instruction: string, format?: string) => Promise<void> | void;
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

export function ArtifactPanel({
  artifact,
  lang,
  mode = "panel",
  onClose,
  onRevise,
}: ArtifactPanelProps) {
  const [slideIdx, setSlideIdx] = useState(0);
  const [tab, setTab] = useState<"preview" | "file">("preview");
  const [reviseText, setReviseText] = useState("");
  const [reviseState, setReviseState] = useState<"idle" | "busy" | "error">("idle");
  const [reviseError, setReviseError] = useState<string | null>(null);
  const [dlState, setDlState] = useState<{ kind: "idle" | "busy" | "error"; msg?: string }>({
    kind: "idle",
  });
  const [touchY, setTouchY] = useState<number | null>(null);
  const dragOriginRef = useRef<"handle" | "header" | "content" | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);

  // Fetch all retained versions from GET /api/artifacts/{id}/versions
  const [remoteVersions, setRemoteVersions] = useState<ArtifactVersion[]>([]);
  useEffect(() => {
    if (!artifact?.id) return;
    let cancelled = false;
    fetchArtifactVersions(artifact.id)
      .then((vers) => {
        if (!cancelled && Array.isArray(vers) && vers.length > 0) {
          setRemoteVersions(vers);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [artifact?.id]);

  // Combine inline artifact.versions with remote versions (deduplicated by version number)
  const versions: ArtifactVersion[] = useMemo(() => {
    const map = new Map<number, ArtifactVersion>();
    for (const v of artifact?.versions ?? []) {
      map.set(v.version, v);
    }
    for (const v of remoteVersions) {
      map.set(v.version, v);
    }
    return Array.from(map.values()).sort((a, b) => a.version - b.version);
  }, [artifact?.versions, remoteVersions]);

  const [versionIdx, setVersionIdx] = useState<number>(-1); // -1 = latest/live
  const activeVersion: ArtifactVersion | null =
    versionIdx >= 0 && versionIdx < versions.length ? versions[versionIdx] : null;

  const isAr = lang === "ar";
  const dir = isAr ? "rtl" : "ltr";

  const liveUrl = artifact?.download_url || (artifact as any)?.url || null;
  const url = (activeVersion?.download_url ?? liveUrl ?? null) as string | null;
  const versionPreview = activeVersion?.preview ?? (artifact ? previewOf(artifact) : null);
  const fmt = (activeVersion?.format || artifact?.format || (artifact as any)?.type || "").toLowerCase();
  const kind = (artifact?.kind || "").toLowerCase();
  const activeTitle = activeVersion?.title || artifact?.title;
  const activeFilename = activeVersion?.filename || artifact?.filename;

  const inlineHtml =
    activeVersion?.html || (artifact ? artifactHtml(versionPreview) || artifact.html || null : null);

  // Lazy fetch HTML for HTML artifacts if not in preview payload
  const [fetchedHtml, setFetchedHtml] = useState<string | null>(null);
  const [htmlFetchFailed, setHtmlFetchFailed] = useState(false);
  const [htmlFetchNonce, setHtmlFetchNonce] = useState(0);
  useEffect(() => {
    setFetchedHtml(null);
    setHtmlFetchFailed(false);
    if (!inlineHtml && url && (fmt === "html" || fmt === "htm") && (artifact?.status === "created" || activeVersion?.status === "created")) {
      let cancelled = false;
      fetch(url, { credentials: "same-origin" })
        .then((r) => (r.ok ? r.text() : null))
        .then((text) => {
          if (cancelled) return;
          if (text) setFetchedHtml(text);
          else setHtmlFetchFailed(true);
        })
        .catch(() => {
          if (!cancelled) setHtmlFetchFailed(true);
        });
      return () => {
        cancelled = true;
      };
    }
  }, [inlineHtml, url, fmt, artifact?.status, activeVersion?.status, htmlFetchNonce]);

  const html = inlineHtml || fetchedHtml;
  const p = versionPreview && typeof versionPreview === "object" ? versionPreview : {};

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

  const isViewingPrevious =
    activeVersion !== null &&
    versions.length > 1 &&
    activeVersion.version !== versions[versions.length - 1]?.version;

  async function handleDownload() {
    if (!artifact || !url || url === "#") {
      setDlState({
        kind: "error",
        msg: isAr ? "رابط التحميل غير متوفر." : "Download URL unavailable.",
      });
      return;
    }
    setDlState({ kind: "busy" });
    try {
      const targetFilename = activeFilename || artifact.filename || "sard-artifact";
      const { objectUrl } = await downloadArtifactFile(url, targetFilename);
      setDlState({ kind: "idle" });
      setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
    } catch (err: any) {
      setDlState({
        kind: "error",
        msg: err?.message || (isAr ? "فشل التحميل." : "Download failed."),
      });
    }
  }

  async function copyWebcal() {
    if (!url) return;
    const origin = typeof window !== "undefined" ? window.location.origin : "";
    const webcal = buildWebcalUrl(url, origin);
    try {
      await navigator.clipboard.writeText(webcal);
      setDlState({
        kind: "idle",
        msg: isAr ? "تم نسخ رابط webcal." : "Copied webcal link.",
      });
    } catch {
      // Clipboard denied: show a neutral notice, not the raw URL as an error.
      setDlState({
        kind: "idle",
        msg: isAr
          ? `تعذّر النسخ التلقائي — انسخ الرابط يدويًا: ${webcal}`
          : `Auto-copy failed — copy manually: ${webcal}`,
      });
    }
  }

  async function handleReviseSubmit() {
    const instruction = reviseText.trim();
    if (!instruction || !artifact || reviseState === "busy") return;
    setReviseState("busy");
    setReviseError(null);
    try {
      if (onRevise) {
        await onRevise(artifact, instruction);
      } else {
        const revised = await postArtifactRevision(artifact.id, instruction);
        if (revised.versions) {
          setRemoteVersions((prev) => [...prev, ...(revised.versions || [])]);
        }
      }
      setReviseText("");
      setReviseState("idle");
      setVersionIdx(-1); // Switch to latest version view
    } catch (err: any) {
      // Preserve old versions on failure
      setReviseState("error");
      setReviseError(err?.message || (isAr ? "تعذّر تطبيق التنقيح." : "Revision failed."));
    }
  }

  if (!artifact) return null;
  const currentVersionNumber = activeVersion ? activeVersion.version : versions.length || 1;
  const shownVersionLabel = `v${currentVersionNumber}`;

  const shell: React.CSSProperties =
    mode === "modal"
      ? {
          position: "fixed",
          inset: 0,
          zIndex: 9999,
          background: "rgba(20,18,16,0.72)",
          backdropFilter: "blur(6px)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 16,
        }
      : { display: "flex", flexDirection: "column", height: "100%", minHeight: 0 };

  const card: React.CSSProperties =
    mode === "modal"
      ? {
          background: "#F3EEE4",
          border: "1.5px solid #D4CBBD",
          borderRadius: 22,
          maxWidth: 960,
          width: "100%",
          maxHeight: "90vh",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          boxShadow: "0 20px 50px rgba(20,18,16,0.35)",
        }
      : {
          background: "#FAF7F1",
          borderInlineStart: "1.5px solid #D4CBBD",
          display: "flex",
          flexDirection: "column",
          height: "100%",
          minHeight: 0,
          overflow: "hidden",
        };

  return (
    <div
      className="artifact-panel-shell"
      style={shell}
      onClick={mode === "modal" ? onClose : undefined}
      onTouchStart={(e) => {
        const target = e.target as HTMLElement | null;
        const isHandle = Boolean(target?.closest(".artifact-drag-handle"));
        const isHeader = Boolean(target?.closest(".artifact-panel-header"));
        const scrollTop = scrollContainerRef.current?.scrollTop ?? 0;
        if (isHandle) {
          dragOriginRef.current = "handle";
          setTouchY(e.touches[0]?.clientY ?? null);
        } else if (isHeader) {
          dragOriginRef.current = "header";
          setTouchY(e.touches[0]?.clientY ?? null);
        } else if (scrollTop <= 0) {
          dragOriginRef.current = "content";
          setTouchY(e.touches[0]?.clientY ?? null);
        } else {
          dragOriginRef.current = null;
          setTouchY(null);
        }
      }}
      onTouchEnd={(e) => {
        if (touchY == null) return;
        const dy = (e.changedTouches[0]?.clientY ?? touchY) - touchY;
        const origin = dragOriginRef.current;
        const scrollTop = scrollContainerRef.current?.scrollTop ?? 0;
        setTouchY(null);
        dragOriginRef.current = null;
        // Guarded swipe-to-close: only trigger if dragging from handle/header or at top of content without scrolling
        if (dy > 90 && (origin === "handle" || origin === "header")) {
          onClose();
        } else if (dy > 130 && origin === "content" && scrollTop <= 0) {
          onClose();
        }
      }}
    >
      <div
        className="artifact-panel-card"
        style={card}
        onClick={(e) => e.stopPropagation()}
        dir={dir}
      >
        {/* Mobile drag handle visible only on mobile viewports */}
        <div
          className="artifact-drag-handle"
          style={{
            width: 44,
            height: 5,
            borderRadius: 999,
            background: "#C4A46A",
            margin: "8px auto 6px",
            cursor: "grab",
          }}
          aria-hidden="true"
        />

        {/* Header */}
        <div
          className="artifact-panel-header"
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "14px 20px",
            background: "#141210",
            color: "#F3EEE4",
            borderBottom: "2px solid #C4A46A",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
            <span style={{ fontSize: 20 }}>{kindIcon(artifact)}</span>
            <div style={{ minWidth: 0 }}>
              <h3
                style={{
                  margin: 0,
                  fontSize: 15,
                  fontWeight: 700,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {activeTitle || (isAr ? "معاينة المخرج الثقافي" : "Cultural Artifact Preview")}
              </h3>
              <span
                style={{
                  fontSize: 11,
                  color: "#C4A46A",
                  textTransform: "uppercase",
                  letterSpacing: 0.5,
                }}
              >
                {fmt.toUpperCase()} {kind ? `• ${kind}` : ""}
                {versions.length > 1 ? (
                  <select
                    aria-label={isAr ? "إصدارات المخرج" : "Artifact versions"}
                    value={versionIdx}
                    onChange={(e) => setVersionIdx(Number(e.target.value))}
                    onClick={(e) => e.stopPropagation()}
                    style={{
                      marginInlineStart: 8,
                      background: "#2D2620",
                      color: "#C4A46A",
                      border: "1px solid #C4A46A",
                      borderRadius: 6,
                      fontSize: 11,
                      padding: "2px 6px",
                      cursor: "pointer",
                    }}
                  >
                    <option value={-1}>
                      {isAr
                        ? `الأحدث (v${versions[versions.length - 1]?.version ?? versions.length})`
                        : `Latest (v${versions[versions.length - 1]?.version ?? versions.length})`}
                    </option>
                    {versions.map((v, i) => (
                      <option key={i} value={i}>
                        v{v.version} {v.title ? `• ${v.title.slice(0, 20)}` : ""}
                      </option>
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
          <button
            onClick={onClose}
            aria-label={isAr ? "إغلاق" : "Close"}
            style={{
              background: "rgba(255,255,255,0.08)",
              border: "1px solid rgba(255,255,255,0.15)",
              color: "#F3EEE4",
              borderRadius: 8,
              width: 32,
              height: 32,
              cursor: "pointer",
              fontSize: 16,
            }}
          >
            ✕
          </button>
        </div>

        {/* Tab switch: Preview (inline) vs File (attachment/download) */}
        <div style={{ display: "flex", gap: 8, padding: "10px 20px 0" }}>
          {(["preview", "file"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              style={{
                padding: "6px 14px",
                borderRadius: 999,
                fontSize: 12,
                fontWeight: 700,
                cursor: "pointer",
                background: tab === t ? "#141210" : "#F3EEE4",
                color: tab === t ? "#F3EEE4" : "#3A342E",
                border: "1px solid #D4CBBD",
              }}
            >
              {t === "preview" ? (isAr ? "معاينة" : "Preview") : isAr ? "الملف" : "File"}
            </button>
          ))}
        </div>

        {/* Body with ref for touch/scroll collision detection */}
        <div
          ref={scrollContainerRef}
          style={{ flex: 1, overflowY: "auto", padding: 20, minHeight: 0 }}
        >
          {/* Banner when viewing historical version */}
          {isViewingPrevious && activeVersion && (
            <div
              style={{
                background: "#EFE8DB",
                border: "1px solid #C4A46A",
                borderRadius: 10,
                padding: "8px 14px",
                marginBottom: 12,
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                fontSize: 12,
                color: "#3A342E",
              }}
            >
              <span>
                ℹ️{" "}
                {isAr
                  ? `أنت تستعرض الإصدار السابق (v${activeVersion.version}).`
                  : `Viewing older snapshot (v${activeVersion.version}).`}
              </span>
              <button
                onClick={() => setVersionIdx(-1)}
                style={{
                  background: "#141210",
                  color: "#FAF7F1",
                  border: "none",
                  borderRadius: 6,
                  padding: "4px 10px",
                  fontSize: 11,
                  fontWeight: 700,
                  cursor: "pointer",
                }}
              >
                {isAr ? "عرض الأحدث" : "View Latest"}
              </button>
            </div>
          )}

          {artifact.status === "failed" && !activeVersion ? (
            <div
              style={{
                background: "rgba(190,74,36,0.08)",
                border: "1.5px solid #BE4A24",
                borderRadius: 12,
                padding: 16,
                color: "#BE4A24",
                fontSize: 13,
              }}
            >
              ⚠️ {artifact.error || (isAr ? "تعذّر توليد هذا المخرج." : "This artifact could not be generated.")}
              {artifact.error_category && (
                <div style={{ fontSize: 11, marginTop: 6 }}>({artifact.error_category})</div>
              )}
            </div>
          ) : tab === "preview" && html ? (
            <iframe
              title={activeTitle || artifact.title}
              sandbox="allow-same-origin allow-popups"
              srcDoc={buildArtifactSrcDoc(html, dir)}
              style={{
                width: "100%",
                minHeight: 420,
                height: "100%",
                border: "1px solid #E0D8C8",
                borderRadius: 12,
                background: "#FAF7F1",
              }}
            />
          ) : tab === "preview" && fmt === "pdf" && url && url !== "#" ? (
            <div>
              <object
                data={url}
                type="application/pdf"
                style={{
                  width: "100%",
                  minHeight: 520,
                  height: "70vh",
                  border: "1px solid #E0D8C8",
                  borderRadius: 12,
                  background: "#FAF7F1",
                }}
              >
                <embed src={url} type="application/pdf" style={{ width: "100%", minHeight: 520 }} />
              </object>
              <div style={{ fontSize: 12, color: "#7D6E5D", marginTop: 8 }}>
                {isAr
                  ? "إن لم تظهر المعاينة، استخدم زر التحميل أدناه."
                  : "If the preview does not load, use the download button below."}
              </div>
            </div>
          ) : tab === "preview" &&
            (fmt === "png" || fmt === "jpg" || fmt === "jpeg" || fmt === "svg" || fmt === "webp") &&
            url &&
            url !== "#" ? (
            <div style={{ textAlign: "center" }}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={url}
                alt={activeTitle || artifact.title}
                style={{ maxWidth: "100%", borderRadius: 12, border: "1px solid #E0D8C8" }}
              />
            </div>
          ) : tab === "preview" && isPresentation ? (
            <div>
              <div
                style={{
                  background: "#1E1B18",
                  borderRadius: 16,
                  padding: "32px 28px",
                  color: "#F3EEE4",
                  minHeight: 240,
                  border: "1px solid #3D352E",
                  marginBottom: 12,
                }}
              >
                <div style={{ fontSize: 12, color: "#C4A46A", fontWeight: 700, marginBottom: 8 }}>
                  {isAr
                    ? `شريحة ${slideIdx + 1} من ${slides.length}`
                    : `Slide ${slideIdx + 1} of ${slides.length}`}
                </div>
                <h2 style={{ fontSize: 20, margin: "0 0 12px 0" }}>{slides[slideIdx]?.title || ""}</h2>
                <ul style={{ lineHeight: 1.8, fontSize: 14, color: "#D4CBBD" }}>
                  {(slides[slideIdx]?.bullets || slides[slideIdx]?.content || []).map(
                    (b: string, i: number) => (
                      <li key={i}>{b}</li>
                    )
                  )}
                </ul>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <button
                  disabled={slideIdx === 0}
                  onClick={() => setSlideIdx((v) => Math.max(0, v - 1))}
                  style={{
                    padding: "8px 16px",
                    borderRadius: 8,
                    border: "none",
                    cursor: "pointer",
                    background: "#2D2620",
                    color: "#F3EEE4",
                    fontSize: 13,
                  }}
                >
                  {isAr ? "← السابقة" : "← Previous"}
                </button>
                <button
                  disabled={slideIdx === slides.length - 1}
                  onClick={() => setSlideIdx((v) => Math.min(slides.length - 1, v + 1))}
                  style={{
                    padding: "8px 16px",
                    borderRadius: 8,
                    border: "none",
                    cursor: "pointer",
                    background: "#2D2620",
                    color: "#F3EEE4",
                    fontSize: 13,
                  }}
                >
                  {isAr ? "التالية →" : "Next →"}
                </button>
              </div>
            </div>
          ) : tab === "preview" && isRecipe ? (
            <div
              style={{
                background: "#FFF",
                borderRadius: 16,
                padding: 24,
                border: "1px solid #E0D8C8",
              }}
            >
              <h2 style={{ margin: "0 0 12px 0", fontSize: 18 }}>
                {p.name || p.title || activeTitle || artifact.title}
              </h2>
              {p.ingredients_or_materials && (
                <>
                  <h4 style={{ fontSize: 14 }}>{isAr ? "المقادير / المواد:" : "Ingredients:"}</h4>
                  <ul style={{ fontSize: 13, lineHeight: 1.7 }}>
                    {p.ingredients_or_materials.map((x: any, i: number) => (
                      <li key={i}>{typeof x === "string" ? x : x.name || x.title}</li>
                    ))}
                  </ul>
                </>
              )}
              {p.instructions_or_steps && (
                <>
                  <h4 style={{ fontSize: 14 }}>{isAr ? "الخطوات:" : "Steps:"}</h4>
                  <ol style={{ fontSize: 13, lineHeight: 1.7 }}>
                    {p.instructions_or_steps.map((x: any, i: number) => (
                      <li key={i}>{typeof x === "string" ? x : x.instruction || x.title}</li>
                    ))}
                  </ol>
                </>
              )}
              {p.cultural_significance && (
                <p style={{ fontSize: 12, color: "#63584E" }}>{p.cultural_significance}</p>
              )}
            </div>
          ) : tab === "preview" && (isCalendar || events.length > 0) ? (
            <div
              style={{
                background: "#FFF",
                borderRadius: 16,
                padding: 24,
                border: "1px solid #E0D8C8",
              }}
            >
              <h3 style={{ margin: "0 0 12px 0", fontSize: 15 }}>
                {isAr ? "المناسبات المجدولة (.ics)" : "Scheduled Events (.ics)"} ({events.length || 1})
              </h3>
              {(events.length > 0 ? events : [p]).map((ev: any, i: number) => {
                const res = resolveCalendarEvent(ev, activeTitle || artifact.title);
                return (
                  <div
                    key={i}
                    style={{
                      padding: 12,
                      background: "#F8F5EE",
                      borderRadius: 10,
                      border: "1px solid #EAE3D5",
                      marginBottom: 8,
                    }}
                  >
                    <div style={{ fontWeight: 700, fontSize: 13 }}>{res.title}</div>
                    <div style={{ fontSize: 12, color: "#7D6E5D", margin: "4px 0" }}>
                      {res.start ? `⏱️ ${res.start}${res.end ? ` - ${res.end}` : ""} ` : ""}
                      {res.location ? `📍 ${res.location}` : ""}
                    </div>
                    {tab === "preview" && (
                      <a
                        href={buildGoogleCalendarUrl(ev, activeTitle || artifact.title)}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{
                          fontSize: 12,
                          color: "#BE4A24",
                          fontWeight: 700,
                          display: "inline-block",
                          marginTop: 4,
                        }}
                      >
                        {isAr ? "＋ إضافة إلى تقويم Google" : "＋ Add to Google Calendar"}
                      </a>
                    )}
                  </div>
                );
              })}
            </div>
          ) : tab === "preview" && isEtiquette ? (
            <div
              style={{
                background: "#FFF",
                borderRadius: 16,
                padding: 24,
                border: "1px solid #E0D8C8",
              }}
            >
              {steps.map((s: any, i: number) => (
                <div
                  key={i}
                  style={{
                    display: "flex",
                    gap: 10,
                    padding: 10,
                    background: "#F8F5EE",
                    borderRadius: 8,
                    marginBottom: 8,
                  }}
                >
                  <div
                    style={{
                      width: 24,
                      height: 24,
                      borderRadius: "50%",
                      background: "#C4A46A",
                      color: "#FFF",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 12,
                      fontWeight: 700,
                      flexShrink: 0,
                    }}
                  >
                    {i + 1}
                  </div>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>
                    {typeof s === "string" ? s : s.action || s.title}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            /* Generic fallback: human-readable summary — never raw JSON internals. */
            <div
              style={{
                background: "#FFF",
                borderRadius: 16,
                padding: 24,
                border: "1px solid #E0D8C8",
              }}
            >
              {tab === "preview" && htmlFetchFailed && (
                <div
                  role="status"
                  style={{
                    fontSize: 12,
                    color: "#9E3A2F",
                    background: "#FBF1EC",
                    border: "1px solid #E8C9BC",
                    borderRadius: 8,
                    padding: "8px 12px",
                    marginBottom: 12,
                  }}
                >
                  {isAr ? "تعذّر تحميل المعاينة." : "Preview failed to load."}{" "}
                  <button
                    onClick={() => setHtmlFetchNonce((n) => n + 1)}
                    style={{
                      fontSize: 12,
                      fontWeight: 700,
                      color: "#BE4A24",
                      background: "none",
                      border: "none",
                      cursor: "pointer",
                      textDecoration: "underline",
                    }}
                  >
                    {isAr ? "إعادة المحاولة" : "Retry"}
                  </button>
                </div>
              )}
              <div style={{ fontSize: 40, marginBottom: 8 }}>{kindIcon(artifact)}</div>
              <h2 style={{ margin: "0 0 8px 0", fontSize: 17 }}>{activeTitle || artifact.title}</h2>
              <div style={{ fontSize: 13, color: "#63584E", lineHeight: 2 }}>
                <div>
                  {isAr ? "الصيغة: " : "Format: "}
                  <strong>{fmt.toUpperCase()}</strong>
                  {kind ? ` • ${kind}` : ""}
                </div>
                {activeFilename && (
                  <div>
                    {isAr ? "الملف: " : "File: "}
                    {activeFilename}
                  </div>
                )}
                {(activeVersion?.size_bytes || artifact.size_bytes > 0) && (
                  <div>
                    {isAr ? "الحجم: " : "Size: "}
                    {Math.max(
                      1,
                      Math.round((activeVersion?.size_bytes || artifact.size_bytes) / 1024)
                    )}{" "}
                    KB
                  </div>
                )}
                <div>
                  {isAr ? "الحالة: " : "Status: "}
                  {statusLabel(activeVersion?.status || artifact.status, isAr)}
                </div>
                {versions.length > 1 && (
                  <div>
                    {isAr ? "الإصدارات المحفوظة: " : "Saved Versions: "}
                    {versions.length} ({shownVersionLabel})
                  </div>
                )}
                {slides.length > 0 && (
                  <div>
                    {isAr ? "الشرائح: " : "Slides: "}
                    {slides.length}
                  </div>
                )}
                {events.length > 0 && (
                  <div>
                    {isAr ? "الفعاليات: " : "Events: "}
                    {events.length}
                  </div>
                )}
              </div>
              {artifact.revisionHint && (
                <p
                  style={{
                    fontSize: 12,
                    color: "#8A8178",
                    borderTop: "1px solid #EAE3D5",
                    paddingTop: 10,
                    marginTop: 10,
                  }}
                >
                  💡 {artifact.revisionHint}
                </p>
              )}
              {!url && (
                <p style={{ fontSize: 12, color: "#9E3A2F" }}>
                  {isAr ? "الملف غير متوفر للتحميل بعد." : "File download is not available yet."}
                </p>
              )}
            </div>
          )}
        </div>

        {/* Footer: download + calendar + revision UI */}
        <div
          style={{
            padding: "12px 20px",
            background: "#EAE3D5",
            borderTop: "1px solid #D4CBBD",
          }}
        >
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            {url ? (
              <button
                onClick={handleDownload}
                disabled={dlState.kind === "busy"}
                style={{
                  padding: "8px 18px",
                  borderRadius: 10,
                  background: "#141210",
                  color: "#C4A46A",
                  fontWeight: 700,
                  fontSize: 13,
                  border: "none",
                  cursor: "pointer",
                }}
              >
                {dlState.kind === "busy"
                  ? isAr
                    ? "جارٍ التحميل..."
                    : "Downloading..."
                  : `⬇️ ${isAr ? "تحميل الملف" : "Download"}`}
              </button>
            ) : (
              <span style={{ fontSize: 12, color: "#9E3A2F" }}>
                {isAr ? "التحميل غير متوفر" : "Download unavailable"}
              </span>
            )}
            {isCalendar && url && (
              <button
                onClick={copyWebcal}
                style={{
                  padding: "8px 14px",
                  borderRadius: 10,
                  background: "#4A513C",
                  color: "#FFF",
                  fontWeight: 700,
                  fontSize: 12,
                  border: "none",
                  cursor: "pointer",
                }}
              >
                {isAr ? "نسخ رابط webcal" : "Copy webcal link"}
              </button>
            )}

            {/* Revision UI: direct revision execution + version update */}
            {artifact.status === "created" && (
              <div style={{ flex: 1, minWidth: 220 }}>
                <div style={{ display: "flex", gap: 6 }}>
                  <input
                    value={reviseText}
                    onChange={(e) => setReviseText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleReviseSubmit();
                    }}
                    disabled={reviseState === "busy"}
                    placeholder={
                      isAr
                        ? "اطلب تنقيحاً… (مثال: أضف تفاصيل أكثر)"
                        : "Request a revision… (e.g. add more details)"
                    }
                    aria-label={isAr ? "تعليمات التنقيح" : "Revision instructions"}
                    style={{
                      flex: 1,
                      borderRadius: 10,
                      border: "1px solid #D4CBBD",
                      padding: "8px 12px",
                      fontSize: 12,
                      background: "#FFF",
                    }}
                  />
                  <button
                    onClick={handleReviseSubmit}
                    disabled={reviseState === "busy" || !reviseText.trim()}
                    style={{
                      padding: "8px 16px",
                      borderRadius: 10,
                      background: reviseState === "busy" ? "#8A8178" : "#BE4A24",
                      color: "#FFF",
                      fontWeight: 700,
                      fontSize: 12,
                      border: "none",
                      cursor: reviseState === "busy" ? "not-allowed" : "pointer",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {reviseState === "busy"
                      ? isAr
                        ? "جارٍ التنقيح..."
                        : "Revising..."
                      : isAr
                        ? "تنقيح"
                        : "Revise"}
                  </button>
                </div>
                {reviseError && (
                  <div role="status" style={{ fontSize: 11, color: "#9E3A2F", marginTop: 4 }}>
                    ⚠️ {reviseError}
                  </div>
                )}
              </div>
            )}
          </div>

          {dlState.msg && (
            <div
              role="status"
              style={{
                marginTop: 8,
                fontSize: 12,
                color: dlState.kind === "error" ? "#9E3A2F" : "#5C6B4C",
              }}
            >
              {dlState.kind === "error" ? "⚠️ " : "✓ "}
              {dlState.msg}
            </div>
          )}
          <div style={{ marginTop: 6, fontSize: 11, color: "#7D6E5D" }}>
            {activeFilename || artifact.filename ? `📄 ${activeFilename || artifact.filename}` : ""}
            {(activeVersion?.size_bytes || artifact.size_bytes > 0) &&
              ` (${Math.max(1, Math.round((activeVersion?.size_bytes || artifact.size_bytes) / 1024))} KB)`}
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
