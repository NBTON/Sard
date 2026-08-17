import React from "react";
import { Artifact } from "@/types";
import { FileText, Calendar, Download, CheckCircle2 } from "lucide-react";

interface ArtifactCardProps {
  artifacts: Artifact[];
  isEn?: boolean;
}

export const ArtifactCard: React.FC<ArtifactCardProps> = ({ artifacts, isEn = false }) => {
  if (!artifacts || artifacts.length === 0) return null;

  return (
    <div className="mt-4 p-4 rounded-2xl bg-gradient-to-r from-moc-plum-950/70 via-moc-navy-900/80 to-moc-navy-950/90 border border-moc-coral-500/30 backdrop-blur-md shadow-card-elevated">
      <div className="flex items-center gap-2 mb-3">
        <div className="p-1.5 rounded-lg bg-moc-coral-500/20 text-moc-coral-400">
          <CheckCircle2 className="w-4 h-4" />
        </div>
        <h4 className="text-xs md:text-sm font-bold font-arabic text-white">
          {isEn ? "Generated Cultural Itinerary Artifacts" : "المخرجات والملفات المجهزة للرحلة"}
        </h4>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {artifacts.map((art, idx) => {
          const isPdf = art.type === "pdf" || art.filename?.endsWith(".pdf");
          const isIcs = art.type === "ics" || art.filename?.endsWith(".ics");

          return (
            <a
              key={idx}
              href={art.url}
              download={art.filename}
              target="_blank"
              rel="noopener noreferrer"
              className="group flex items-center justify-between p-3 rounded-xl bg-moc-navy-900/90 border border-moc-navy-700/60 hover:border-moc-coral-500/60 hover:bg-moc-navy-800/80 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500 transition-all duration-200"
            >
              <div className="flex items-center gap-3">
                <div
                  className={`p-2 rounded-xl flex items-center justify-center ${
                    isPdf
                      ? "bg-moc-crimson-700/20 text-moc-crimson-400 border border-moc-crimson-600/30"
                      : isIcs
                      ? "bg-moc-orange-600/20 text-moc-orange-400 border border-moc-orange-500/30"
                      : "bg-moc-sage-600/20 text-moc-sage-300 border border-moc-sage-500/30"
                  }`}
                >
                  {isPdf ? <FileText className="w-5 h-5" /> : isIcs ? <Calendar className="w-5 h-5" /> : <FileText className="w-5 h-5" />}
                </div>

                <div className="flex flex-col">
                  <span className="text-xs font-semibold text-white font-arabic group-hover:text-moc-peach-300 transition-colors">
                    {art.title || (isPdf ? "جدول الرحلة PDF" : "ملف التقويم .ICS")}
                  </span>
                  <span className="text-[10px] text-moc-navy-300/80 font-mono">
                    {art.filename}
                  </span>
                </div>
              </div>

              <div className="p-2 rounded-lg bg-moc-coral-500/10 text-moc-coral-400 group-hover:bg-moc-coral-500/25 transition-colors">
                <Download className="w-4 h-4" />
              </div>
            </a>
          );
        })}
      </div>
    </div>
  );
};
