import React from "react";
import { Search, Compass, MapPinned, Eye, PenLine, Clock } from "lucide-react";

export type StageKey = "understanding" | "consulting" | "planning" | "building" | "verifying" | "composing";

export interface Stage {
  key: StageKey;
  labelAr: string;
  labelEn: string;
  status: "waiting" | "running" | "completed" | "needs_attention";
  summary?: string;
  elapsedMs?: number;
}

const STAGES: Record<StageKey, { ar: string; en: string; icon: React.ReactNode }> = {
  understanding: { ar: "فهم السؤال", en: "Understanding", icon: <Search className="w-3.5 h-3.5" /> },
  consulting: { ar: "استكشاف المصادر", en: "Consulting sources", icon: <Compass className="w-3.5 h-3.5" /> },
  planning: { ar: "تنظيم الرحلة", en: "Planning", icon: <MapPinned className="w-3.5 h-3.5" /> },
  building: { ar: "رسم المشهد", en: "Building the visual", icon: <Eye className="w-3.5 h-3.5" /> },
  verifying: { ar: "التحقق من المعلومات", en: "Verifying", icon: <Clock className="w-3.5 h-3.5" /> },
  composing: { ar: "صياغة الإجابة", en: "Composing", icon: <PenLine className="w-3.5 h-3.5" /> },
};

export const ProgressStages: React.FC<{ stages: Stage[]; isEn?: boolean }> = ({ stages, isEn = false }) => {
  if (!stages || stages.length === 0) return null;
  return (
    <div className="flex flex-col gap-2 p-3 rounded-2xl bg-moc-navy-900/60 border border-moc-navy-700/50" role="status" aria-live="polite">
      {stages.map((s) => {
        const meta = STAGES[s.key];
        const color =
          s.status === "completed"
            ? "text-moc-sage-300 border-moc-sage-500/30 bg-moc-sage-500/10"
            : s.status === "running"
            ? "text-moc-coral-300 border-moc-coral-500/30 bg-moc-coral-500/10 animate-pulse"
            : s.status === "needs_attention"
            ? "text-amber-300 border-amber-500/30 bg-amber-500/10"
            : "text-moc-navy-300 border-moc-navy-700/40";
        return (
          <div key={s.key} className={`flex items-center justify-between px-2.5 py-1.5 rounded-xl border text-xs ${color}`}>
            <div className="flex items-center gap-2">
              <span className="p-1 rounded bg-white/5">{meta.icon}</span>
              <span className="font-arabic font-medium">{isEn ? meta.en : meta.ar}</span>
            </div>
            <div className="flex items-center gap-2 text-[11px]">
              {s.summary && <span className="opacity-80">{s.summary}</span>}
              {s.elapsedMs !== undefined && <span className="font-mono">{Math.round(s.elapsedMs)}ms</span>}
              <span className="w-2 h-2 rounded-full bg-current opacity-70" />
            </div>
          </div>
        );
      })}
    </div>
  );
};

// Derive stages from observable graph node events - never from chain-of-thought
export function deriveStagesFromEvents(events: Array<{ node: string; status: string; source_count?: number; duration_ms?: number }>): Stage[] {
  const mapping: Record<string, StageKey> = {
    understand: "understanding",
    plan: "planning",
    retrieve: "consulting",
    compose: "composing",
    verify: "verifying",
    render: "building",
  };
  const stages: Stage[] = [];
  for (const ev of events) {
    const key = mapping[ev.node];
    if (!key) continue;
    const status = ev.status === "completed" ? "completed" : ev.status === "failed" ? "needs_attention" : ev.status === "started" ? "running" : "waiting";
    stages.push({
      key,
      labelAr: STAGES[key].ar,
      labelEn: STAGES[key].en,
      status: status as Stage["status"],
      summary: ev.source_count ? `راجع ${ev.source_count} مصادر` : undefined,
      elapsedMs: ev.duration_ms,
    });
  }
  return stages;
}
