import React, { useState } from "react";
import { Citation } from "@/types";
import { BookOpen, ChevronDown, ChevronUp, ExternalLink, Sparkles, Check, Copy } from "lucide-react";

interface CitationsDrawerProps {
  citations: Citation[];
  isEn?: boolean;
}

export const CitationsDrawer: React.FC<CitationsDrawerProps> = ({ citations, isEn = false }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  if (!citations || citations.length === 0) return null;

  const handleCopyLink = (url: string, id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(url);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  return (
    <div className="mt-4 border border-moc-coral-500/25 rounded-2xl bg-gradient-to-br from-moc-navy-950/70 via-moc-plum-950/30 to-moc-navy-950/80 backdrop-blur-md overflow-hidden transition-all duration-300 shadow-sm">
      {/* Header Button */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center justify-between px-4 py-3 text-xs md:text-sm font-medium text-moc-peach-300 hover:text-white hover:bg-moc-navy-800/40 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500 transition-colors"
      >
        <div className="flex items-center gap-2">
          <div className="p-1 rounded-lg bg-moc-coral-500/20 text-moc-coral-400">
            <BookOpen className="w-4 h-4" />
          </div>
          <span className="font-semibold font-arabic">
            {isEn
              ? `Verified Cultural Sources (${citations.length})`
              : `المصادر المرجعية المعتمدة (${citations.length} مراجع)`}
          </span>
          <span className="hidden sm:inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-moc-sage-500/20 text-moc-sage-300 border border-moc-sage-500/30">
            <Sparkles className="w-3 h-3 text-moc-coral-500" />
            {isEn ? "Always-On RAG" : "مسترجع تلقائياً"}
          </span>
        </div>

        <div className="flex items-center gap-1 text-moc-coral-400">
          <span className="text-xs">{isOpen ? (isEn ? "Hide" : "إخفاء") : (isEn ? "Show" : "عرض")}</span>
          {isOpen ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </div>
      </button>

      {/* Collapsible Content */}
      {isOpen && (
        <div className="px-4 pb-4 pt-1 grid grid-cols-1 md:grid-cols-2 gap-3 border-t border-moc-navy-700/60 animate-fade-in">
          {citations.map((c, index) => {
            const displayUrl = c.source_url || "#";
            const isCopied = copiedId === c.citation_id;

            return (
              <div
                key={c.citation_id || index}
                className="group relative flex flex-col justify-between p-3.5 rounded-xl bg-moc-navy-900/90 border border-moc-navy-700/60 hover:border-moc-coral-500/50 hover:bg-moc-navy-800/80 transition-all duration-200 shadow-sm"
              >
                <div>
                  <div className="flex items-start justify-between gap-2 mb-1.5">
                    <div className="flex items-center gap-2">
                      <span className="flex items-center justify-center w-5 h-5 rounded-md bg-moc-coral-500/20 text-moc-coral-400 text-[11px] font-bold">
                        {index + 1}
                      </span>
                      <h4 className="text-xs font-semibold text-white font-arabic line-clamp-1 group-hover:text-moc-peach-300 transition-colors">
                        {c.title}
                      </h4>
                    </div>

                    {c.topic && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-moc-plum-900/50 text-moc-sage-300 border border-moc-plum-700/40 whitespace-nowrap">
                        {c.topic}
                      </span>
                    )}
                  </div>

                  {c.snippet && (
                    <p className="text-[11px] text-moc-navy-200/80 font-arabic line-clamp-2 leading-relaxed mb-2.5">
                      "{c.snippet}"
                    </p>
                  )}
                </div>

                <div className="flex items-center justify-between pt-2 border-t border-white/5 text-[11px]">
                  <span className="text-moc-navy-300 font-medium">
                    {c.source_name || "وزارة الثقافة"}
                  </span>

                  <div className="flex items-center gap-2">
                    {c.source_url && (
                      <button
                        onClick={(e) => handleCopyLink(c.source_url, c.citation_id, e)}
                        title={isEn ? "Copy URL" : "نسخ الرابط"}
                        className="p-1 rounded text-moc-navy-300 hover:text-white hover:bg-moc-navy-800 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500 transition-colors"
                      >
                        {isCopied ? <Check className="w-3.5 h-3.5 text-moc-sage-400" /> : <Copy className="w-3.5 h-3.5" />}
                      </button>
                    )}

                    {c.source_url ? (
                      <a
                        href={displayUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-moc-coral-400 hover:text-moc-coral-300 hover:underline font-medium cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500 rounded"
                      >
                        <span>{isEn ? "Open" : "زيارة المصدر"}</span>
                        <ExternalLink className="w-3 h-3" />
                      </a>
                    ) : (
                      <span className="text-moc-navy-400/70">{isEn ? "Internal" : "مرجع داخلي"}</span>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
