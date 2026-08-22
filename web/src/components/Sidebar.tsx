import React, { useState } from "react";
import { ChatSession, SystemStatus } from "@/types";
import { BrandLogo } from "./BrandLogo";
import {
  Plus,
  MessageSquare,
  Trash2,
  Edit2,
  Check,
  X,
  Search,
  Settings,
  Database,
  ExternalLink,
  ChevronLeft,
  ChevronRight,
  Compass,
  ShieldCheck,
} from "lucide-react";

interface SidebarProps {
  sessions: ChatSession[];
  activeSessionId: string | null;
  systemStatus: SystemStatus | null;
  onSelectSession: (id: string) => void;
  onNewChat: () => void;
  onDeleteSession: (id: string) => void;
  onRenameSession: (id: string, newTitle: string) => void;
  onOpenSettings: () => void;
  isOpen: boolean;
  onToggle: () => void;
  isEn?: boolean;
}

export const Sidebar: React.FC<SidebarProps> = ({
  sessions,
  activeSessionId,
  systemStatus,
  onSelectSession,
  onNewChat,
  onDeleteSession,
  onRenameSession,
  onOpenSettings,
  isOpen,
  onToggle,
  isEn = false,
}) => {
  const [searchQuery, setSearchQuery] = useState("");
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");

  const filteredSessions = sessions.filter((s) =>
    s.title.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const handleStartEdit = (session: ChatSession, e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingSessionId(session.id);
    setEditTitle(session.title);
  };

  const handleSaveEdit = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (editTitle.trim()) {
      onRenameSession(id, editTitle.trim());
    }
    setEditingSessionId(null);
  };

  const handleCancelEdit = (e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingSessionId(null);
  };

  const isSourcesReady = systemStatus?.sources?.verified ?? systemStatus?.verified ?? false;

  return (
    <>
      {/* Mobile Backdrop */}
      {isOpen && (
        <div
          onClick={onToggle}
          className="fixed inset-0 bg-black/70 backdrop-blur-sm z-40 md:hidden"
        />
      )}

      {/* Sidebar Container */}
      <aside
        className={`fixed md:static inset-y-0 ${
          isEn ? "left-0" : "right-0"
        } z-50 flex flex-col w-72 md:w-80 h-full bg-moc-navy-950/95 md:bg-moc-navy-950 border-x border-moc-navy-800/80 transition-all duration-300 ease-in-out select-none ${
          isOpen
            ? "translate-x-0"
            : isEn
            ? "-translate-x-full md:w-0 md:border-none md:overflow-hidden"
            : "translate-x-full md:w-0 md:border-none md:overflow-hidden"
        }`}
      >
        {/* Top Header */}
        <div className="p-4 border-b border-moc-navy-800/60 flex items-center justify-between">
          <BrandLogo size="sm" isEn={isEn} />
          <button
            onClick={onToggle}
            className="p-1.5 rounded-lg text-moc-navy-300 hover:text-white hover:bg-moc-navy-800/60 cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500"
            title={isEn ? "Collapse sidebar" : "طي القائمة"}
            aria-label={isEn ? "Collapse sidebar" : "طي القائمة"}
          >
            {isEn ? <ChevronLeft className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
          </button>
        </div>

        {/* New Chat Button (MOC Coral Gradient) */}
        <div className="p-3.5 pb-2">
          <button
            onClick={onNewChat}
            className="w-full flex items-center justify-between px-4 py-3 rounded-2xl bg-gradient-to-r from-moc-coral-600 via-moc-coral-500 to-moc-coral-600 hover:from-moc-coral-500 hover:to-moc-coral-400 text-white font-bold font-arabic shadow-coral-glow border border-moc-coral-400/40 transition-all duration-200 group cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-400"
          >
            <div className="flex items-center gap-2.5">
              <div className="p-1 rounded-lg bg-white/20 text-white group-hover:rotate-90 transition-transform duration-300">
                <Plus className="w-4 h-4" />
              </div>
              <span className="text-sm">{isEn ? "New Journey" : "محادثة جديدة"}</span>
            </div>
            <kbd className="hidden sm:inline-block text-[10px] font-mono px-2 py-0.5 rounded bg-black/25 text-moc-peach-200 border border-white/20">
              Ctrl+K
            </kbd>
          </button>
        </div>

        {/* Search Sessions */}
        {sessions.length > 2 && (
          <div className="px-3.5 py-1">
            <div className="relative">
              <Search className="w-4 h-4 absolute top-2.5 right-3 rtl:right-3 rtl:left-auto ltr:left-3 ltr:right-auto text-moc-navy-400 pointer-events-none" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder={isEn ? "Search conversations..." : "بحث في المحادثات..."}
                className="w-full py-1.5 pr-9 pl-3 rtl:pr-9 rtl:pl-3 ltr:pl-9 ltr:pr-3 text-xs bg-moc-navy-900/80 border border-moc-navy-700/60 rounded-xl text-white placeholder:text-moc-navy-400/70 focus:outline-none focus:border-moc-coral-500/60 font-arabic"
              />
            </div>
          </div>
        )}

        {/* Sessions History List */}
        <div className="flex-1 overflow-y-auto px-3.5 py-2 space-y-1.5 scrollbar-thin">
          {filteredSessions.length === 0 ? (
            <div className="text-center py-10 px-4">
              <Compass className="w-8 h-8 text-moc-navy-600 mx-auto mb-2 opacity-60" />
              <p className="text-xs text-moc-navy-300 font-arabic">
                {searchQuery
                  ? isEn
                    ? "No matching conversations"
                    : "لا توجد محادثات مطابقة"
                  : isEn
                  ? "No conversations yet. Start a new cultural journey!"
                  : "لا توجد محادثات سابقة. ابدأ رحلتك الثقافية الآن!"}
              </p>
            </div>
          ) : (
            filteredSessions.map((s) => {
              const isActive = s.id === activeSessionId;
              const isEditing = s.id === editingSessionId;

              return (
                <div
                  key={s.id}
                  onClick={() => onSelectSession(s.id)}
                  className={`group relative flex items-center justify-between px-3 py-2.5 rounded-xl cursor-pointer transition-all duration-200 ${
                    isActive
                      ? "bg-gradient-to-r from-moc-navy-800 to-moc-plum-900/70 text-white border border-moc-coral-500/40 shadow-sm"
                      : "text-moc-navy-200 hover:bg-moc-navy-800/50 hover:text-white border border-transparent"
                  }`}
                >
                  <div className="flex items-center gap-2.5 min-w-0 flex-1">
                    <MessageSquare
                      className={`w-4 h-4 flex-shrink-0 ${
                        isActive ? "text-moc-coral-500" : "text-moc-navy-400"
                      }`}
                    />

                    {isEditing ? (
                      <input
                        type="text"
                        value={editTitle}
                        onChange={(e) => setEditTitle(e.target.value)}
                        onClick={(e) => e.stopPropagation()}
                        className="w-full text-xs bg-moc-navy-900 border border-moc-coral-500 rounded px-1.5 py-0.5 text-white font-arabic focus:outline-none"
                        autoFocus
                      />
                    ) : (
                      <span className="text-xs font-arabic truncate">{s.title}</span>
                    )}
                  </div>

                  {/* Actions on hover / active */}
                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    {isEditing ? (
                      <>
                        <button
                          onClick={(e) => handleSaveEdit(s.id, e)}
                          className="p-1 rounded text-moc-sage-400 hover:bg-moc-sage-900/40 cursor-pointer"
                          title={isEn ? "Save" : "حفظ"}
                        >
                          <Check className="w-3.5 h-3.5" />
                        </button>
                        <button
                          onClick={handleCancelEdit}
                          className="p-1 rounded text-moc-crimson-400 hover:bg-moc-crimson-900/40 cursor-pointer"
                          title={isEn ? "Cancel" : "إلغاء"}
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          onClick={(e) => handleStartEdit(s, e)}
                          className="p-1 rounded text-moc-navy-300 hover:text-moc-peach-300 hover:bg-moc-navy-800/60 cursor-pointer transition-colors"
                          title={isEn ? "Rename" : "إعادة تسمية"}
                        >
                          <Edit2 className="w-3.5 h-3.5" />
                        </button>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            onDeleteSession(s.id);
                          }}
                          className="p-1 rounded text-moc-navy-300 hover:text-moc-crimson-400 hover:bg-moc-crimson-950/40 cursor-pointer transition-colors"
                          title={isEn ? "Delete" : "حذف"}
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* System Status Widget (MOC Sage & Plum Highlights) */}
        <div className="p-3.5 mx-3 mb-2.5 rounded-2xl bg-gradient-to-br from-moc-navy-900/90 via-moc-navy-800/50 to-moc-plum-950/40 border border-moc-navy-700/60 shadow-inner">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-1.5 text-xs font-bold text-moc-peach-300 font-arabic">
              <Database className="w-3.5 h-3.5 text-moc-coral-500" />
              <span>{isEn ? "Cultural Knowledge Base" : "المعرفة الثقافية"}</span>
            </div>
            <span
              className={`flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full font-medium ${
                isSourcesReady
                  ? "bg-moc-sage-500/20 text-moc-sage-300 border border-moc-sage-500/30"
                  : "bg-moc-orange-500/20 text-moc-orange-300 border border-moc-orange-500/30"
              }`}
            >
              <ShieldCheck className="w-3 h-3" />
              {isSourcesReady ? (isEn ? "Active" : "مفهرس وجاهز") : (isEn ? "Ready" : "جاهز")}
            </span>
          </div>

          <div className="text-[11px] text-moc-navy-200 space-y-1.5 font-arabic">
            <div className="flex justify-between">
              <span className="text-moc-navy-300">النموذج:</span>
              <span className="text-white font-mono text-[10px] truncate max-w-[130px]">
                {systemStatus?.status_label || "سرد الذكي"}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-moc-navy-300">المصدر:</span>
              <span className="text-moc-peach-400 text-[10px] font-semibold">وزارة الثقافة (MOC)</span>
            </div>
          </div>
        </div>

        {/* Bottom Bar: Settings */}
        <div className="p-3 border-t border-moc-navy-800/60 flex items-center justify-between bg-moc-navy-950/60">
          <button
            onClick={onOpenSettings}
            className="flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-arabic text-moc-navy-200 hover:text-white hover:bg-moc-navy-800/60 cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500"
          >
            <Settings className="w-4 h-4 text-moc-coral-500" />
            <span>{isEn ? "Settings & Preferences" : "الإعدادات والمظهر"}</span>
          </button>

          <a
            href="https://www.moc.gov.sa/ar"
            target="_blank"
            rel="noopener noreferrer"
            className="p-2 rounded-xl text-moc-navy-300 hover:text-moc-peach-300 hover:bg-moc-navy-800/60 cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500"
            title={isEn ? "Visit Ministry of Culture" : "بوابة وزارة الثقافة"}
            aria-label={isEn ? "Visit Ministry of Culture" : "بوابة وزارة الثقافة"}
          >
            <ExternalLink className="w-4 h-4" />
          </a>
        </div>
      </aside>
    </>
  );
};
