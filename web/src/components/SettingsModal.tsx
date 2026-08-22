import React from "react";
import { SystemStatus, ChatSession } from "@/types";
import {
  X,
  Settings,
  Moon,
  Sun,
  Palette,
  Globe,
  Database,
  Download,
  Trash2,
  Cpu,
  ExternalLink,
  ShieldCheck,
} from "lucide-react";

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  systemStatus: SystemStatus | null;
  theme: "dark" | "light" | "moc";
  isEn: boolean;
  sessions: ChatSession[];
  onSelectTheme: (t: "dark" | "light" | "moc") => void;
  onToggleLang: () => void;
  onClearAllSessions: () => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  systemStatus,
  theme,
  isEn,
  sessions,
  onSelectTheme,
  onToggleLang,
  onClearAllSessions,
}) => {
  if (!isOpen) return null;

  const handleExportJSON = () => {
    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(sessions, null, 2));
    const downloadAnchor = document.createElement("a");
    downloadAnchor.setAttribute("href", dataStr);
    downloadAnchor.setAttribute("download", `sard_conversations_${Date.now()}.json`);
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();
  };

  const handleExportMarkdown = () => {
    let md = `# محادثات سَــرْد الثقافية — وزارة الثقافة (ثقافتنا، هويتنا)\n\nتاريخ التصدير: ${new Date().toLocaleString()}\n\n---\n\n`;
    sessions.forEach((s, idx) => {
      md += `## ${idx + 1}. ${s.title}\n\n`;
      s.messages.forEach((m) => {
        md += `### ${m.role === "user" ? "المستخدم" : "سرد"}:\n${m.content}\n\n`;
      });
      md += "\n---\n\n";
    });

    const dataStr = "data:text/markdown;charset=utf-8," + encodeURIComponent(md);
    const downloadAnchor = document.createElement("a");
    downloadAnchor.setAttribute("href", dataStr);
    downloadAnchor.setAttribute("download", `sard_conversations_${Date.now()}.md`);
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-md animate-fade-in select-none">
      <div
        onClick={(e) => e.stopPropagation()}
        className="relative w-full max-w-lg rounded-3xl bg-gradient-to-b from-moc-navy-900 via-moc-navy-950 to-moc-navy-950 border border-moc-coral-500/30 p-6 shadow-2xl overflow-hidden text-white"
      >
        {/* Header */}
        <div className="flex items-center justify-between pb-4 border-b border-moc-navy-800/80">
          <div className="flex items-center gap-2.5">
            <div className="p-2 rounded-xl bg-moc-coral-500/20 text-moc-coral-400">
              <Settings className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base font-bold font-arabic text-white">
                {isEn ? "Settings & System Details" : "الإعدادات وبيانات النظام"}
              </h3>
              <p className="text-[11px] text-moc-navy-300 font-arabic">
                {isEn ? "Configure preferences and review active AI models" : "تخصيص المظهر واستعراض نماذج الذكاء الاصطناعي"}
              </p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="p-2 rounded-xl text-moc-navy-400 hover:text-white hover:bg-moc-navy-800 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content Body */}
        <div className="py-5 space-y-6 max-h-[70vh] overflow-y-auto pr-1">
          {/* Theme Selector */}
          <div>
            <label className="block text-xs font-bold text-moc-peach-300 font-arabic mb-2">
              {isEn ? "Appearance Theme" : "مظهر الواجهة"}
            </label>
            <div className="grid grid-cols-3 gap-2.5">
              <button
                onClick={() => onSelectTheme("dark")}
                className={`flex flex-col items-center justify-center p-3 rounded-2xl border text-xs font-arabic cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500 transition-all ${
                  theme === "dark"
                    ? "bg-moc-navy-800 border-moc-coral-500 text-white shadow-coral-glow"
                    : "bg-moc-navy-900/60 border-moc-navy-700 text-moc-navy-300 hover:bg-moc-navy-800/50"
                }`}
              >
                <Moon className="w-4 h-4 mb-1.5 text-moc-navy-300" />
                <span>{isEn ? "Dark Navy" : "الداكن"}</span>
              </button>

              <button
                onClick={() => onSelectTheme("moc")}
                className={`flex flex-col items-center justify-center p-3 rounded-2xl border text-xs font-arabic cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500 transition-all ${
                  theme === "moc"
                    ? "bg-moc-plum-800 border-moc-coral-500 text-white shadow-plum-glow"
                    : "bg-moc-navy-900/60 border-moc-navy-700 text-moc-navy-300 hover:bg-moc-navy-800/50"
                }`}
              >
                <Palette className="w-4 h-4 mb-1.5 text-moc-peach-300" />
                <span>{isEn ? "MOC Plum" : "الملكي الثقافي"}</span>
              </button>

              <button
                onClick={() => onSelectTheme("light")}
                className={`flex flex-col items-center justify-center p-3 rounded-2xl border text-xs font-arabic cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500 transition-all ${
                  theme === "light"
                    ? "bg-moc-peach-600/30 border-moc-coral-500 text-white shadow-coral-glow"
                    : "bg-moc-navy-900/60 border-moc-navy-700 text-moc-navy-300 hover:bg-moc-navy-800/50"
                }`}
              >
                <Sun className="w-4 h-4 mb-1.5 text-moc-orange-500" />
                <span>{isEn ? "Alabaster Light" : "الفاتح"}</span>
              </button>
            </div>
          </div>

          {/* System Status - Public Presentation */}
          <div className="p-4 rounded-2xl bg-moc-navy-900/80 border border-moc-navy-700/70 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-xs font-bold text-moc-peach-300 font-arabic">
                <Cpu className="w-4 h-4 text-moc-coral-500" />
                <span>{isEn ? "System Status" : "حالة النظام"}</span>
              </div>
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-moc-sage-500/20 text-moc-sage-300 border border-moc-sage-500/30">
                {isEn ? "Operational" : "جاهز للعمل"}
              </span>
            </div>

            <div className="text-xs space-y-1.5 font-arabic text-moc-navy-200">
              <div className="flex justify-between">
                <span className="text-moc-navy-400">{isEn ? "Mode:" : "النمط:"}</span>
                <span className="text-white font-semibold">
                  {isEn ? "Auto" : "تلقائي"}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-moc-navy-400">{isEn ? "Sources:" : "المصادر:"}</span>
                <span className="text-moc-sage-400">
                  {(systemStatus?.sources?.verified ?? systemStatus?.verified) ? (isEn ? "Verified & up-to-date" : "موثَّقة ومُحدَّثة") : (isEn ? "Ready" : "جاهز")}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-moc-navy-400">{isEn ? "Authority:" : "الجهة:"}</span>
                <span className="text-moc-peach-300">
                  {isEn ? "Ministry of Culture" : "وزارة الثقافة"}
                </span>
              </div>
            </div>
          </div>

          {/* Export Conversations */}
          <div>
            <label className="block text-xs font-bold text-moc-peach-300 font-arabic mb-2">
              {isEn ? "Export Conversations" : "تصدير المحادثات والرحلات"}
            </label>
            <div className="grid grid-cols-2 gap-2.5">
              <button
                onClick={handleExportMarkdown}
                disabled={sessions.length === 0}
                className="flex items-center justify-center gap-2 p-2.5 rounded-2xl bg-moc-navy-900 hover:bg-moc-navy-800 border border-moc-navy-700/60 text-xs text-white font-arabic cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Download className="w-4 h-4 text-moc-coral-500" />
                <span>{isEn ? "Markdown (.md)" : "ملف Markdown (.md)"}</span>
              </button>

              <button
                onClick={handleExportJSON}
                disabled={sessions.length === 0}
                className="flex items-center justify-center gap-2 p-2.5 rounded-2xl bg-moc-navy-900 hover:bg-moc-navy-800 border border-moc-navy-700/60 text-xs text-white font-arabic cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Download className="w-4 h-4 text-moc-coral-500" />
                <span>{isEn ? "JSON (.json)" : "ملف JSON (.json)"}</span>
              </button>
            </div>
          </div>

          {/* Danger Zone: Clear History */}
          <div className="pt-2 border-t border-moc-navy-800/60">
            <button
              onClick={() => {
                if (confirm(isEn ? "Are you sure you want to clear all conversations?" : "هل أنت متأكد من رغبتك في حذف جميع المحادثات؟")) {
                  onClearAllSessions();
                  onClose();
                }
              }}
              className="w-full flex items-center justify-center gap-2 p-3 rounded-2xl bg-moc-crimson-900/30 hover:bg-moc-crimson-900/50 border border-moc-crimson-700/50 text-xs text-moc-crimson-300 font-arabic cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-crimson-500 transition-colors"
            >
              <Trash2 className="w-4 h-4" />
              <span>{isEn ? "Delete All Chat History" : "مسح جميع سجلات المحادثات"}</span>
            </button>
          </div>
        </div>

        {/* Footer */}
        <div className="pt-4 border-t border-moc-navy-800/80 flex items-center justify-between text-[11px] text-moc-navy-300 font-arabic">
          <span>سرد • وزارة الثقافة</span>
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-xl bg-gradient-to-r from-moc-coral-600 to-moc-coral-500 hover:from-moc-coral-500 hover:to-moc-coral-400 text-white font-bold cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-400 transition-colors"
          >
            {isEn ? "Done" : "إغلاق"}
          </button>
        </div>
      </div>
    </div>
  );
};
