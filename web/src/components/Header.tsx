import React from "react";
import { BrandLogo } from "./BrandLogo";
import { SystemStatus } from "@/types";
import {
  Menu,
  Sparkles,
  Globe,
  Sun,
  Moon,
  Trash2,
  Palette,
  ShieldCheck,
} from "lucide-react";

interface HeaderProps {
  activeSessionTitle?: string;
  systemStatus: SystemStatus | null;
  theme: "dark" | "light" | "moc";
  isEn: boolean;
  onToggleSidebar: () => void;
  onToggleTheme: () => void;
  onToggleLang: () => void;
  onClearSession?: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  activeSessionTitle,
  systemStatus,
  theme,
  isEn,
  onToggleSidebar,
  onToggleTheme,
  onToggleLang,
  onClearSession,
}) => {
  const isRagReady = systemStatus?.rag?.available ?? false;

  return (
    <header className="sticky top-0 z-30 flex items-center justify-between h-16 px-3 md:px-6 bg-moc-navy-950/90 md:bg-moc-navy-950/80 backdrop-blur-2xl border-b border-moc-navy-700/50 select-none transition-colors duration-200">
      {/* Left / Right Start: Sidebar Toggle & Brand Title */}
      <div className="flex items-center gap-3 min-w-0">
        <button
          onClick={onToggleSidebar}
          className="p-2 rounded-xl text-moc-navy-200 hover:text-white hover:bg-moc-navy-800/60 cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500"
          title={isEn ? "Toggle Sidebar" : "القائمة الجانبية"}
          aria-label={isEn ? "Toggle Sidebar" : "القائمة الجانبية"}
        >
          <Menu className="w-5 h-5" />
        </button>

        <div className="flex items-center gap-2 min-w-0">
          <BrandLogo size="sm" showSubtitle={false} isEn={isEn} />
          {activeSessionTitle && (
            <div className="hidden sm:flex items-center gap-2 pr-2 border-r border-moc-navy-700/60 rtl:border-r rtl:border-l-0 ltr:border-l ltr:border-r-0 ltr:pl-2">
              <span className="text-xs font-bold font-arabic text-moc-navy-200 truncate max-w-[200px] md:max-w-[320px]">
                {activeSessionTitle}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Center Status Badge */}
      <div className="hidden lg:flex items-center gap-2.5 px-3.5 py-1.5 rounded-full bg-moc-navy-900/90 border border-moc-navy-700/60 text-xs shadow-inner">
        <div className="flex items-center gap-1.5 text-moc-peach-400 font-medium">
          <Sparkles className="w-3.5 h-3.5 text-moc-coral-500" />
          <span className="font-mono text-[11px]">
            {systemStatus?.status_label || "Sard AI Agent"}
          </span>
        </div>
        <span className="text-moc-navy-500">•</span>
        <div className="flex items-center gap-1.5 text-moc-sage-400 text-[11px] font-arabic">
          <ShieldCheck className="w-3.5 h-3.5 text-moc-sage-400" />
          <span>{isRagReady ? (isEn ? "Always-On RAG" : "مُسنَد بالمصادر") : (isEn ? "Direct Chat" : "محادثة مباشرة")}</span>
        </div>
      </div>

      {/* Right Controls */}
      <div className="flex items-center gap-1.5 md:gap-2">
        {/* Language Switcher */}
        <button
          onClick={onToggleLang}
          className="flex items-center gap-1 px-3 py-1.5 rounded-xl bg-moc-navy-900/80 hover:bg-moc-navy-800 border border-moc-navy-700 text-xs text-moc-navy-200 hover:text-white cursor-pointer transition-colors font-medium font-arabic focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500"
          title={isEn ? "التبديل إلى العربية" : "Switch to English"}
        >
          <Globe className="w-3.5 h-3.5 text-moc-coral-500" />
          <span>{isEn ? "العربية" : "EN"}</span>
        </button>

        {/* Theme Switcher */}
        <button
          onClick={onToggleTheme}
          className="p-2 rounded-xl bg-moc-navy-900/80 hover:bg-moc-navy-800 border border-moc-navy-700 text-moc-navy-200 hover:text-moc-peach-300 cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500"
          title={
            theme === "dark"
              ? isEn
                ? "Switch to MOC Royal Theme"
                : "النمط الملكي لوزارة الثقافة"
              : theme === "moc"
              ? isEn
                ? "Switch to Light Mode"
                : "النمط الفاتح"
              : isEn
              ? "Switch to Dark Mode"
              : "النمط الداكن"
          }
          aria-label="Toggle theme"
        >
          {theme === "dark" ? (
            <Moon className="w-4 h-4 text-moc-navy-300" />
          ) : theme === "moc" ? (
            <Palette className="w-4 h-4 text-moc-coral-500" />
          ) : (
            <Sun className="w-4 h-4 text-moc-orange-500" />
          )}
        </button>

        {/* Clear Current Chat Button */}
        {onClearSession && (
          <button
            onClick={onClearSession}
            className="p-2 rounded-xl bg-moc-navy-900/80 hover:bg-moc-crimson-900/30 border border-moc-navy-700 text-moc-navy-300 hover:text-moc-crimson-500 cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-crimson-500"
            title={isEn ? "Clear conversation" : "مسح المحادثة الحالية"}
            aria-label={isEn ? "Clear conversation" : "مسح المحادثة الحالية"}
          >
            <Trash2 className="w-4 h-4" />
          </button>
        )}
      </div>
    </header>
  );
};
