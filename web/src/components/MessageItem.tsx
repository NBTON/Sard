import React, { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Message } from "@/types";
import { CitationsDrawer } from "./CitationsDrawer";
import { ArtifactCard } from "./ArtifactCard";
import {
  Copy,
  Check,
  Volume2,
  VolumeX,
  Sparkles,
  Info,
  ChevronDown,
  ChevronUp,
  Clock,
  Cpu,
  User,
} from "lucide-react";

interface MessageItemProps {
  message: Message;
  isEn?: boolean;
}

export const MessageItem: React.FC<MessageItemProps> = ({ message, isEn = false }) => {
  const [copied, setCopied] = useState(false);
  const [isPlayingAudio, setIsPlayingAudio] = useState(false);
  const [showTechDetails, setShowTechDetails] = useState(false);

  const isUser = message.role === "user";

  const handleCopy = () => {
    navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleToggleSpeech = () => {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) return;

    if (isPlayingAudio) {
      window.speechSynthesis.cancel();
      setIsPlayingAudio(false);
      return;
    }

    window.speechSynthesis.cancel();
    // Clean markdown symbols for clearer speech
    const cleanText = message.content.replace(/[#*`_\[\]()]/g, " ");
    const utterance = new SpeechSynthesisUtterance(cleanText);
    utterance.lang = isEn ? "en-US" : "ar-SA";
    utterance.rate = 0.95;

    utterance.onend = () => setIsPlayingAudio(false);
    utterance.onerror = () => setIsPlayingAudio(false);

    setIsPlayingAudio(true);
    window.speechSynthesis.speak(utterance);
  };

  return (
    <div
      className={`group w-full py-4 px-2 md:px-4 rounded-2xl transition-all duration-200 animate-slide-up ${
        isUser
          ? "bg-moc-navy-900/60 dark:bg-moc-navy-900/70 border border-moc-coral-500/25"
          : "bg-moc-navy-950/70 dark:bg-moc-navy-950/85 border border-moc-navy-700/60"
      }`}
    >
      <div className="max-w-4xl mx-auto flex items-start gap-3 md:gap-4">
        {/* Avatar */}
        <div className="flex-shrink-0 mt-0.5">
          {isUser ? (
            <div className="w-8 h-8 md:w-9 md:h-9 rounded-xl bg-gradient-to-br from-moc-coral-600 to-moc-coral-700 text-white flex items-center justify-center shadow-coral-glow border border-moc-coral-400/40">
              <User className="w-4 h-4 md:w-5 md:h-5 text-white" />
            </div>
          ) : (
            <div className="relative w-8 h-8 md:w-9 md:h-9 rounded-xl bg-gradient-to-br from-moc-navy-900 via-moc-plum-800 to-moc-navy-950 text-moc-coral-500 flex items-center justify-center shadow-moc-glow border border-moc-coral-500/30">
              <Sparkles className="w-4 h-4 md:w-5 md:h-5 text-moc-coral-500" />
              {message.isStreaming && (
                <span className="absolute -top-1 -right-1 flex h-3 w-3">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-moc-coral-500 opacity-75" />
                  <span className="relative inline-flex rounded-full h-3 w-3 bg-moc-coral-600" />
                </span>
              )}
            </div>
          )}
        </div>

        {/* Content Box */}
        <div className="flex-1 min-w-0">
          {/* Header Row */}
          <div className="flex items-center justify-between gap-2 mb-1.5">
            <div className="flex items-center gap-2">
              <span className="text-xs md:text-sm font-bold font-arabic text-white">
                {isUser ? (isEn ? "You" : "أنت") : (isEn ? "Sard Cultural AI" : "سَـرْد — المستشار الثقافي")}
              </span>
              {!isUser && (
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-moc-plum-900/50 text-moc-peach-300 border border-moc-plum-700/40 font-medium">
                  {message.modelUsed || (isEn ? "MOC Engine" : "منظومة الثقافة")}
                </span>
              )}
            </div>

            <span className="text-[11px] text-moc-navy-400 font-mono">
              {new Date(message.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            </span>
          </div>

          {/* Streaming Status Pill */}
          {message.statusText && message.isStreaming && (
            <div className="inline-flex items-center gap-2 mb-3 px-3 py-1.5 rounded-xl bg-moc-plum-950/70 border border-moc-coral-500/40 text-xs text-moc-peach-300 animate-pulse">
              <span className="w-2 h-2 rounded-full bg-moc-coral-500 animate-ping" />
              <span className="font-arabic">{message.statusText}</span>
            </div>
          )}

          {/* Main Message Markdown Body */}
          <div className="prose prose-invert max-w-none text-xs md:text-sm leading-relaxed text-moc-navy-50 font-arabic space-y-2">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                h1: ({ children }) => (
                  <h1 className="text-base md:text-lg font-bold text-moc-coral-400 mt-4 mb-2 pb-1 border-b border-moc-coral-500/20 font-arabic">
                    {children}
                  </h1>
                ),
                h2: ({ children }) => (
                  <h2 className="text-sm md:text-base font-bold text-moc-peach-300 mt-3 mb-1.5 font-arabic">
                    {children}
                  </h2>
                ),
                h3: ({ children }) => (
                  <h3 className="text-xs md:text-sm font-semibold text-moc-navy-200 mt-2 mb-1 font-arabic">
                    {children}
                  </h3>
                ),
                p: ({ children }) => <p className="mb-2 leading-relaxed text-moc-navy-100">{children}</p>,
                ul: ({ children }) => <ul className="list-disc list-inside space-y-1 mb-2 text-moc-navy-100">{children}</ul>,
                ol: ({ children }) => <ol className="list-decimal list-inside space-y-1 mb-2 text-moc-navy-100">{children}</ol>,
                li: ({ children }) => <li className="text-moc-navy-100 leading-relaxed">{children}</li>,
                strong: ({ children }) => <strong className="font-bold text-moc-coral-300">{children}</strong>,
                blockquote: ({ children }) => (
                  <blockquote className="border-r-4 border-moc-coral-500 pr-3 py-1 my-2 bg-moc-plum-950/40 rounded-l-lg italic text-moc-peach-200">
                    {children}
                  </blockquote>
                ),
                code: ({ className, children, ...props }) => {
                  const isInline = !className;
                  return isInline ? (
                    <code className="px-1.5 py-0.5 rounded bg-moc-navy-900 text-moc-peach-300 border border-moc-navy-700/60 font-mono text-[11px]">
                      {children}
                    </code>
                  ) : (
                    <div className="relative my-3 rounded-xl overflow-hidden bg-moc-navy-950 border border-moc-navy-800">
                      <div className="flex items-center justify-between px-3 py-1.5 bg-moc-navy-900 border-b border-moc-navy-800 text-[11px] text-moc-navy-300">
                        <span className="font-mono">Code</span>
                      </div>
                      <pre className="p-3 overflow-x-auto text-[11px] font-mono text-moc-navy-100 leading-normal">
                        <code>{children}</code>
                      </pre>
                    </div>
                  );
                },
                table: ({ children }) => (
                  <div className="overflow-x-auto my-3 rounded-xl border border-moc-navy-700/60">
                    <table className="min-w-full text-xs text-right border-collapse">{children}</table>
                  </div>
                ),
                thead: ({ children }) => <thead className="bg-moc-navy-900 text-moc-peach-300">{children}</thead>,
                tbody: ({ children }) => <tbody className="divide-y divide-moc-navy-800/60">{children}</tbody>,
                tr: ({ children }) => <tr className="hover:bg-moc-navy-800/40">{children}</tr>,
                th: ({ children }) => <th className="p-2 font-bold font-arabic">{children}</th>,
                td: ({ children }) => <td className="p-2 text-moc-navy-100 font-arabic">{children}</td>,
              }}
            >
              {message.content}
            </ReactMarkdown>

            {message.isStreaming && (
              <span className="inline-block w-2 h-4 bg-moc-coral-500 animate-pulse ml-1 align-middle" />
            )}
          </div>

          {/* Citations Drawer */}
          {message.citations && message.citations.length > 0 && (
            <CitationsDrawer citations={message.citations} isEn={isEn} />
          )}

          {/* Artifacts Cards (PDF / ICS) */}
          {message.artifacts && message.artifacts.length > 0 && (
            <ArtifactCard artifacts={message.artifacts} isEn={isEn} />
          )}

          {/* Action Toolbar */}
          {!isUser && !message.isStreaming && message.content && (
            <div className="flex flex-wrap items-center justify-between gap-2 mt-4 pt-2.5 border-t border-white/5 text-xs text-moc-navy-300">
              <div className="flex items-center gap-1.5">
                {/* Copy Button */}
                <button
                  onClick={handleCopy}
                  className="flex items-center gap-1 px-2.5 py-1 rounded-lg hover:bg-moc-navy-800/60 hover:text-white cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500 transition-colors"
                  title={isEn ? "Copy response" : "نسخ الإجابة"}
                >
                  {copied ? <Check className="w-3.5 h-3.5 text-moc-sage-400" /> : <Copy className="w-3.5 h-3.5" />}
                  <span className="text-[11px] font-arabic">{copied ? (isEn ? "Copied" : "تم النسخ") : (isEn ? "Copy" : "نسخ")}</span>
                </button>

                {/* Text-to-Speech Button */}
                <button
                  onClick={handleToggleSpeech}
                  className={`flex items-center gap-1 px-2.5 py-1 rounded-lg hover:bg-moc-navy-800/60 hover:text-white cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500 transition-colors ${
                    isPlayingAudio ? "text-moc-coral-400 bg-moc-navy-800/80" : ""
                  }`}
                  title={isPlayingAudio ? (isEn ? "Stop reading" : "إيقاف القراءة") : (isEn ? "Read aloud" : "استماع")}
                >
                  {isPlayingAudio ? <VolumeX className="w-3.5 h-3.5 animate-pulse" /> : <Volume2 className="w-3.5 h-3.5" />}
                  <span className="text-[11px] font-arabic">{isPlayingAudio ? (isEn ? "Playing" : "جارٍ الاستماع...") : (isEn ? "Listen" : "استماع")}</span>
                </button>
              </div>

              {/* Technical Details Inspector */}
              <div className="relative">
                <button
                  onClick={() => setShowTechDetails(!showTechDetails)}
                  className="flex items-center gap-1 text-[11px] text-moc-navy-400 hover:text-moc-peach-300 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500 rounded px-1.5 py-0.5 transition-colors"
                >
                  <Info className="w-3.5 h-3.5" />
                  <span className="font-arabic">{isEn ? "Details" : "مسار الاستجابة"}</span>
                  {showTechDetails ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                </button>

                {showTechDetails && (
                  <div className="absolute left-0 sm:right-0 mt-2 w-64 p-3 rounded-xl bg-moc-navy-900 border border-moc-navy-700 shadow-xl z-20 text-[11px] font-mono text-moc-navy-200 animate-fade-in">
                    <div className="flex items-center gap-2 mb-2 pb-1.5 border-b border-moc-navy-800 text-moc-peach-300 font-bold font-arabic">
                      <Cpu className="w-3.5 h-3.5 text-moc-coral-500" />
                      <span>{isEn ? "Technical Metadata" : "بيانات المعالجة والنموذج"}</span>
                    </div>

                    <div className="space-y-1.5">
                      <div className="flex justify-between">
                        <span className="text-moc-navy-400">النموذج:</span>
                        <span className="text-white truncate max-w-[120px]">{message.modelUsed || "سرد"}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-moc-navy-400">نمط الاسترجاع:</span>
                        <span className="text-moc-coral-400">{message.retrievalMode || "Always-On RAG"}</span>
                      </div>
                      {message.timings?.total_ms && (
                        <div className="flex justify-between items-center">
                          <span className="text-moc-navy-400 flex items-center gap-1">
                            <Clock className="w-3 h-3" /> زمن الاستجابة:
                          </span>
                          <span className="text-moc-sage-400 font-bold">{message.timings.total_ms} ms</span>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
