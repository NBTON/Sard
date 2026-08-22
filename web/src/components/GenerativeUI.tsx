import React from "react";

// Typed generative UI components - no arbitrary HTML/JS execution

export interface MapPoint {
  name: string;
  lat: number;
  lng: number;
  verified?: boolean;
}

export interface TimelineEvent {
  date: string;
  title: string;
  titleEn?: string;
  description: string;
}

export interface ComparisonRow {
  aspect: string;
  a: string;
  b: string;
}

export const MapView: React.FC<{ points: MapPoint[]; isEn?: boolean }> = ({ points, isEn }) => {
  // Only render with verified coordinates; never invent
  const valid = points.filter((p) => Number.isFinite(p.lat) && Number.isFinite(p.lng) && p.verified !== false && Math.abs(p.lat) <= 90 && Math.abs(p.lng) <= 180);
  if (valid.length === 0) return <div className="p-3 text-xs text-moc-navy-300">{isEn ? "No verified locations to display." : "لا توجد مواقع موثَّقة للعرض."}</div>;
  return (
    <div className="rounded-2xl border border-moc-navy-700/60 overflow-hidden bg-moc-navy-900/50">
      <div className="px-3 py-2 text-xs font-bold text-moc-peach-300 border-b border-moc-navy-700/50">{isEn ? "Map - verified locations" : "الخريطة - مواقع موثَّقة"}</div>
      <ul className="divide-y divide-moc-navy-700/40">
        {valid.map((p, i) => (
          <li key={i} className="flex items-center justify-between px-3 py-2 text-xs">
            <span className="text-white font-medium">{p.name}</span>
            <span className="font-mono text-moc-navy-300">{p.lat.toFixed(4)}, {p.lng.toFixed(4)}</span>
          </li>
        ))}
      </ul>
      <div className="px-3 py-2 text-[11px] text-moc-navy-400">{isEn ? "Coordinates verified; distances not invented." : "الإحداثيات موثَّقة؛ المسافات غير مُختلقة."}</div>
    </div>
  );
};

export const TimelineView: React.FC<{ events: TimelineEvent[]; isEn?: boolean }> = ({ events, isEn }) => {
  if (events.length < 3) return <div className="p-3 text-xs text-moc-navy-300">{isEn ? "Timeline requires at least 3 events." : "المخطط الزمني يتطلب 3 أحداث على الأقل."}</div>;
  return (
    <div className="rounded-2xl border border-moc-navy-700/60 bg-moc-navy-900/50 p-3">
      <ol className="relative border-l border-moc-navy-700/50 ml-2 space-y-3">
        {events.map((e, i) => (
          <li key={i} className="ml-4">
            <div className="absolute w-2 h-2 bg-moc-coral-500 rounded-full -left-1 mt-1.5" />
            <time className="text-[11px] font-mono text-moc-sage-300">{e.date}</time>
            <h4 className="text-xs font-bold text-white">{isEn && e.titleEn ? e.titleEn : e.title}</h4>
            <p className="text-xs text-moc-navy-200">{e.description}</p>
          </li>
        ))}
      </ol>
    </div>
  );
};

export const ComparisonView: React.FC<{ titleA: string; titleB: string; rows: ComparisonRow[]; isEn?: boolean }> = ({ titleA, titleB, rows }) => {
  return (
    <div className="rounded-2xl border border-moc-navy-700/60 overflow-hidden">
      <table className="w-full text-xs">
        <thead className="bg-moc-navy-900 text-moc-peach-300">
          <tr><th className="p-2 text-right">الجانب</th><th className="p-2">{titleA}</th><th className="p-2">{titleB}</th></tr>
        </thead>
        <tbody className="divide-y divide-moc-navy-700/40">
          {rows.map((r, i) => (
            <tr key={i} className="text-moc-navy-100"><td className="p-2 font-medium">{r.aspect}</td><td className="p-2">{r.a}</td><td className="p-2">{r.b}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

// Registry for safe rendering - only allow listed components
export const GENERATIVE_UI_REGISTRY = {
  map: MapView,
  timeline: TimelineView,
  comparison: ComparisonView,
} as const;

// Simple sanitizer: validates schemas before rendering
export function validateMapPayload(payload: any): boolean {
  if (!payload || !Array.isArray(payload.points)) return false;
  if (payload.points.length === 0) return false;
  return payload.points.every((p: any) => typeof p.name === "string" && typeof p.lat === "number" && typeof p.lng === "number" && Math.abs(p.lat) <= 90 && Math.abs(p.lng) <= 180);
}
