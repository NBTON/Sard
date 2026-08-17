import React, { useRef, useEffect, useState } from "react";
import { Message } from "@/types";
import { MessageItem } from "./MessageItem";
import { WelcomeHero } from "./WelcomeHero";
import { ArrowDown } from "lucide-react";

interface ChatContainerProps {
  messages: Message[];
  onSelectPrompt: (query: string) => void;
  isEn?: boolean;
}

export const ChatContainer: React.FC<ChatContainerProps> = ({
  messages,
  onSelectPrompt,
  isEn = false,
}) => {
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [showScrollBottom, setShowScrollBottom] = useState(false);

  // Auto-scroll on new messages or deltas
  useEffect(() => {
    if (bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, messages[messages.length - 1]?.content]);

  // Handle scroll events to show/hide "Scroll to Bottom" pill
  const handleScroll = () => {
    if (!containerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    const distanceToBottom = scrollHeight - scrollTop - clientHeight;
    setShowScrollBottom(distanceToBottom > 150);
  };

  const scrollToBottom = () => {
    if (bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  };

  if (messages.length === 0) {
    return (
      <div className="flex-1 overflow-y-auto flex items-center justify-center p-4">
        <WelcomeHero onSelectPrompt={onSelectPrompt} isEn={isEn} />
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className="relative flex-1 overflow-y-auto px-2 md:px-6 py-6 space-y-4 scroll-smooth"
    >
      <div className="max-w-4xl mx-auto space-y-4">
        {messages.map((message) => (
          <MessageItem key={message.id} message={message} isEn={isEn} />
        ))}
        <div ref={bottomRef} className="h-4" />
      </div>

      {/* Floating Scroll to Bottom button */}
      {showScrollBottom && (
        <button
          onClick={scrollToBottom}
          className="fixed bottom-28 left-1/2 -translate-x-1/2 z-20 flex items-center gap-1.5 px-3.5 py-2 rounded-full bg-moc-navy-900/90 text-moc-peach-300 border border-moc-coral-500/40 shadow-coral-glow backdrop-blur-md text-xs font-arabic hover:bg-moc-navy-800 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500 transition-all duration-200 animate-fade-in"
        >
          <span>{isEn ? "Scroll to latest" : "الانتقال لآخر رسالة"}</span>
          <ArrowDown className="w-3.5 h-3.5 text-moc-coral-500" />
        </button>
      )}
    </div>
  );
};
