import React, { useState, useRef, useEffect } from "react";
import { Send, Square, Mic, MicOff, Sparkles, Compass, Waves, Landmark } from "lucide-react";

interface ChatInputProps {
  onSendMessage: (text: string) => void;
  onStopGeneration?: () => void;
  isStreaming?: boolean;
  isEn?: boolean;
  disabled?: boolean;
}

export const ChatInput: React.FC<ChatInputProps> = ({
  onSendMessage,
  onStopGeneration,
  isStreaming = false,
  isEn = false,
  disabled = false,
}) => {
  const [input, setInput] = useState("");
  const [isRecording, setIsRecording] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 180)}px`;
    }
  }, [input]);

  const handleSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!input.trim() || isStreaming || disabled) return;
    onSendMessage(input.trim());
    setInput("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleToggleVoice = () => {
    if (typeof window === "undefined") return;

    const SpeechRecognition =
      (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;

    if (!SpeechRecognition) {
      alert(isEn ? "Speech recognition is not supported in this browser." : "التعرف على الصوت غير مدعوم في متصفحك الحالي.");
      return;
    }

    if (isRecording) {
      setIsRecording(false);
      return;
    }

    try {
      const recognition = new SpeechRecognition();
      recognition.lang = isEn ? "en-US" : "ar-SA";
      recognition.interimResults = false;

      recognition.onstart = () => setIsRecording(true);
      recognition.onresult = (event: any) => {
        const transcript = event.results[0][0].transcript;
        setInput((prev) => (prev ? `${prev} ${transcript}` : transcript));
        setIsRecording(false);
      };
      recognition.onerror = () => setIsRecording(false);
      recognition.onend = () => setIsRecording(false);

      recognition.start();
    } catch (e) {
      console.error("Speech recognition error:", e);
      setIsRecording(false);
    }
  };

  const QUICK_CHIPS = [
    { label: isEn ? "Heritage Itinerary" : "مسار تراثي ليومين", query: "صمم لي مساراً تراثياً متكاملاً لمدة يومين في المنطقة الشرقية", icon: Compass },
    { label: isEn ? "Shrimp Craft" : "تجفيف الروبيان بتاروت", query: "حدثني عن حرفة تجفيف الروبيان التقليدية في جزيرة تاروت", icon: Waves },
    { label: isEn ? "UNESCO Sites" : "مواقع اليونسكو بالسعودية", query: "ما هي مواقع التراث العالمي لليونسكو في السعودية؟", icon: Landmark },
  ];

  return (
    <div className="w-full max-w-4xl mx-auto px-3 md:px-4 pb-3 pt-1">
      {/* Quick Prompt Chips with Vector Icons */}
      <div className="flex items-center gap-2 overflow-x-auto pb-2 scrollbar-none text-[11px]">
        <span className="flex items-center gap-1 text-moc-peach-300 font-semibold font-arabic whitespace-nowrap pl-1">
          <Sparkles className="w-3.5 h-3.5 text-moc-coral-500" />
          {isEn ? "Suggestions:" : "اقتراحات سريعة:"}
        </span>
        {QUICK_CHIPS.map((chip, idx) => {
          const ChipIcon = chip.icon;
          return (
            <button
              key={idx}
              onClick={() => onSendMessage(chip.query)}
              disabled={isStreaming || disabled}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-moc-navy-900/90 hover:bg-moc-navy-800 border border-moc-navy-700/60 hover:border-moc-coral-500/60 text-moc-navy-200 hover:text-white cursor-pointer transition-colors whitespace-nowrap font-arabic disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500"
            >
              <ChipIcon className="w-3.5 h-3.5 text-moc-coral-500" />
              <span>{chip.label}</span>
            </button>
          );
        })}
      </div>

      {/* Floating Glassmorphic Container (MOC Dark Navy & Plum with Coral Accent) */}
      <form
        onSubmit={handleSubmit}
        className="relative flex flex-col rounded-3xl bg-gradient-to-b from-moc-navy-950/95 via-moc-navy-900/90 to-moc-navy-950/95 border border-moc-navy-700/70 focus-within:border-moc-coral-500/80 focus-within:shadow-coral-glow shadow-card-elevated backdrop-blur-xl transition-all duration-300 overflow-hidden"
      >
        <div className="flex items-end gap-2 p-2.5 md:p-3.5">
          {/* Voice Input Button */}
          <button
            type="button"
            onClick={handleToggleVoice}
            disabled={disabled || isStreaming}
            className={`p-2.5 rounded-2xl cursor-pointer transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500 ${
              isRecording
                ? "bg-moc-crimson-600 text-white animate-pulse"
                : "text-moc-navy-300 hover:text-moc-coral-400 hover:bg-moc-navy-800/60"
            }`}
            title={isEn ? "Voice Input" : "إدخال صوتي"}
            aria-label={isEn ? "Voice Input" : "إدخال صوتي"}
          >
            {isRecording ? <MicOff className="w-5 h-5" /> : <Mic className="w-5 h-5" />}
          </button>

          {/* Textarea */}
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={disabled}
            rows={1}
            dir={isEn ? "ltr" : "rtl"}
            placeholder={
              isEn
                ? "Ask about Saudi heritage, plan a cultural trip, or explore traditions..."
                : "اسأل عن تراث المملكة، خطط لرحلة سياحية، أو استكشف الحرف التقليدية..."
            }
            className="w-full max-h-[180px] py-1.5 px-2 bg-transparent text-xs md:text-sm text-white placeholder:text-moc-navy-400/60 focus:outline-none resize-none font-arabic leading-relaxed scrollbar-none"
          />

          {/* Send / Stop Button */}
          {isStreaming ? (
            <button
              type="button"
              onClick={onStopGeneration}
              className="p-2.5 rounded-2xl bg-moc-crimson-700 hover:bg-moc-crimson-600 text-white shadow-lg cursor-pointer transition-all duration-200 flex-shrink-0 animate-pulse focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-crimson-400"
              title={isEn ? "Stop generating" : "إيقاف التوليد"}
              aria-label={isEn ? "Stop generating" : "إيقاف التوليد"}
            >
              <Square className="w-4 h-4 fill-current" />
            </button>
          ) : (
            <button
              type="submit"
              disabled={!input.trim() || disabled}
              className={`p-2.5 rounded-2xl transition-all duration-200 flex-shrink-0 shadow-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500 ${
                input.trim() && !disabled
                  ? "bg-gradient-to-r from-moc-coral-600 via-moc-coral-500 to-moc-coral-600 hover:from-moc-coral-500 hover:to-moc-coral-400 text-white shadow-coral-glow border border-moc-coral-400/50 cursor-pointer"
                  : "bg-moc-navy-900 text-moc-navy-600 border border-moc-navy-800 cursor-not-allowed"
              }`}
              title={isEn ? "Send message (Enter)" : "إرسال (Enter)"}
              aria-label={isEn ? "Send message" : "إرسال الرسالة"}
            >
              <Send className="w-4 h-4 rtl:-scale-x-100" />
            </button>
          )}
        </div>

        {/* Subtle Bottom Bar info (MOC Sage & Navy) */}
        <div className="flex items-center justify-between px-4 py-1.5 bg-moc-navy-900/90 border-t border-moc-navy-800/60 text-[10px] text-moc-navy-300 font-arabic">
          <span className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-moc-sage-400 animate-pulse" />
            <span className="text-moc-sage-300">{isEn ? "Always-On RAG Active" : "محرك الاسترجاع الثقافي RAG مُفعّل تلقائياً"}</span>
          </span>

          <span className="hidden sm:inline text-moc-navy-400">
            {isEn ? "Shift + Enter for newline" : "Shift + Enter لسطر جديد"}
          </span>
        </div>
      </form>

      {/* MOC Official Strapline & Disclaimer Footer */}
      <p className="text-center text-[10px] text-moc-navy-300/80 font-arabic mt-2 select-none">
        {isEn
          ? "Sard AI • Ministry of Culture — Our culture, our identity."
          : "سَـرْد — المنصة الثقافية الذكية • وزارة الثقافة (ثقافتنا، هويتنا)."}
      </p>
    </div>
  );
};
