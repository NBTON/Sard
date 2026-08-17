import React from "react";
import { MOC_SUGGESTIONS, MOC_CATEGORY_TAGS } from "@/lib/constants";
import { BrandLogo } from "./BrandLogo";
import {
  Compass,
  Waves,
  Landmark,
  Sparkles,
  ArrowUpRight,
  ShieldCheck,
  Zap,
  Calendar,
  Utensils,
  Palette,
  Music,
  BookOpen,
  Building2,
} from "lucide-react";

interface WelcomeHeroProps {
  onSelectPrompt: (query: string) => void;
  isEn?: boolean;
}

export const WelcomeHero: React.FC<WelcomeHeroProps> = ({ onSelectPrompt, isEn = false }) => {
  const getIcon = (name: string) => {
    switch (name) {
      case "Compass":
        return <Compass className="w-5 h-5" />;
      case "Waves":
        return <Waves className="w-5 h-5" />;
      case "Landmark":
        return <Landmark className="w-5 h-5" />;
      case "Utensils":
        return <Utensils className="w-4 h-4" />;
      case "Palette":
        return <Palette className="w-4 h-4" />;
      case "Music":
        return <Music className="w-4 h-4" />;
      case "BookOpen":
        return <BookOpen className="w-4 h-4" />;
      case "Building2":
        return <Building2 className="w-4 h-4" />;
      default:
        return <Sparkles className="w-5 h-5" />;
    }
  };

  return (
    <div className="flex flex-col items-center justify-center max-w-4xl mx-auto px-4 py-8 md:py-10 text-center animate-fade-in select-none">
      {/* Brand Emblem Header with MOC Plum & Coral Luminous Mesh Aura */}
      <div className="mb-5 relative">
        <div className="absolute -inset-6 bg-gradient-to-r from-moc-plum-700/40 via-moc-coral-600/25 to-moc-navy-700/30 rounded-full blur-2xl opacity-75 animate-pulse-subtle pointer-events-none" />
        <BrandLogo size="lg" showSubtitle={false} isEn={isEn} />
      </div>

      <h1 className="text-2xl md:text-4xl font-extrabold font-arabic text-white mb-2.5 tracking-tight">
        {isEn ? (
          <>
            Welcome to <span className="text-transparent bg-clip-text bg-gradient-to-r from-moc-coral-400 via-moc-peach-300 to-moc-orange-400">Sard</span>
          </>
        ) : (
          <>
            أهلاً بك في <span className="text-transparent bg-clip-text bg-gradient-to-r from-moc-coral-400 via-moc-peach-300 to-moc-orange-400">سَــرْد</span>
          </>
        )}
      </h1>

      <p className="text-xs md:text-sm text-moc-navy-200/90 font-arabic max-w-xl mb-6 leading-relaxed">
        {isEn
          ? "The official AI platform for Saudi cultural discovery, curated travel itineraries, and verified heritage archives."
          : "المنصة الذكية لاستكشاف كنوز المملكة الثقافية والتراثية، وتصميم برامج رحلات موثقة من مصادر وزارة الثقافة المعتمدة."}
      </p>

      {/* Feature Badges (MOC Sage & Coral Highlights) */}
      <div className="flex flex-wrap items-center justify-center gap-2 mb-7 text-[11px] md:text-xs">
        <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-moc-navy-900/80 border border-moc-navy-700/60 text-moc-navy-200 backdrop-blur-md">
          <ShieldCheck className="w-3.5 h-3.5 text-moc-sage-400" />
          <span className="font-arabic">{isEn ? "Always-On RAG Verification" : "إسناد مرجعي دائم (Always-On RAG)"}</span>
        </div>
        <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-moc-navy-900/80 border border-moc-navy-700/60 text-moc-navy-200 backdrop-blur-md">
          <Zap className="w-3.5 h-3.5 text-moc-coral-500" />
          <span className="font-arabic">{isEn ? "Instant PDF & Calendar Downloads" : "توليد جداول PDF وتقويم .ICS"}</span>
        </div>
      </div>

      {/* Modern Bento Grid Discovery Section (MOC Color System: Navy, Plum, Coral, Sage) */}
      <div className="w-full grid grid-cols-1 md:grid-cols-3 gap-3.5 mb-8 text-right">
        {/* Bento Card 1 (Large - Spans 2 cols): Featured Eastern Province Itinerary */}
        <button
          onClick={() => onSelectPrompt(MOC_SUGGESTIONS[0].query)}
          className="group relative md:col-span-2 flex flex-col justify-between p-5 rounded-3xl bg-gradient-to-br from-moc-navy-900/95 via-moc-plum-950/60 to-moc-navy-900/90 border border-moc-coral-600/30 hover:border-moc-coral-500/70 hover:shadow-coral-glow transition-all duration-300 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500 overflow-hidden text-right"
        >
          {/* Subtle Ambient Radial Highlight */}
          <div className="absolute top-0 right-0 w-36 h-36 bg-moc-coral-500/10 rounded-full blur-2xl pointer-events-none group-hover:bg-moc-coral-500/20 transition-all duration-500" />

          <div>
            <div className="flex items-center justify-between gap-2 mb-3">
              <div className="flex items-center gap-2">
                <div className="p-2.5 rounded-2xl bg-gradient-to-br from-moc-coral-600 to-moc-coral-700 text-white border border-moc-coral-400/40 shadow-sm group-hover:scale-105 transition-transform">
                  <Compass className="w-5 h-5" />
                </div>
                <span className="text-[11px] font-bold px-2.5 py-1 rounded-full bg-moc-plum-800/60 text-moc-peach-300 border border-moc-plum-700/40 font-arabic">
                  {isEn ? "Featured Cultural Route" : "مسار ثقافي مميز"}
                </span>
              </div>

              <div className="flex items-center gap-1 text-[11px] text-moc-navy-300 font-arabic">
                <Calendar className="w-3.5 h-3.5 text-moc-coral-500" />
                <span>{isEn ? "2 Days" : "يومان"}</span>
              </div>
            </div>

            <h3 className="text-sm md:text-base font-bold text-white font-arabic group-hover:text-moc-peach-300 transition-colors mb-1.5">
              {MOC_SUGGESTIONS[0].title}
            </h3>
            <p className="text-xs text-moc-navy-200/85 font-arabic leading-relaxed">
              {MOC_SUGGESTIONS[0].description}
            </p>
          </div>

          <div className="flex items-center justify-between pt-4 mt-3 border-t border-white/5 text-xs text-moc-coral-400 font-semibold font-arabic">
            <span>{isEn ? "Generate complete itinerary" : "توليد البرنامج الكامل مع المستندات"}</span>
            <div className="p-1 rounded-lg bg-moc-coral-500/15 text-moc-coral-400 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 rtl:group-hover:-translate-x-0.5 transition-transform">
              <ArrowUpRight className="w-4 h-4 rtl:-scale-x-100" />
            </div>
          </div>
        </button>

        {/* Bento Card 2 (Span 1 col): Intangible Craft */}
        <button
          onClick={() => onSelectPrompt(MOC_SUGGESTIONS[1].query)}
          className="group relative flex flex-col justify-between p-5 rounded-3xl bg-gradient-to-br from-moc-navy-900/90 via-moc-navy-800/50 to-moc-navy-900/90 border border-moc-navy-700/60 hover:border-moc-coral-500/60 hover:shadow-sm transition-all duration-300 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500 text-right"
        >
          <div>
            <div className="flex items-center justify-between mb-3">
              <div className="p-2.5 rounded-2xl bg-moc-navy-800 text-moc-sage-400 border border-moc-navy-700/70 group-hover:scale-105 transition-transform">
                <Waves className="w-5 h-5" />
              </div>
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-moc-sage-900/40 text-moc-sage-300 border border-moc-sage-700/40 font-arabic">
                {isEn ? "Intangible Heritage" : "تراث غير مادي"}
              </span>
            </div>

            <h3 className="text-sm font-bold text-white font-arabic group-hover:text-moc-peach-300 transition-colors mb-1">
              {MOC_SUGGESTIONS[1].title}
            </h3>
            <p className="text-xs text-moc-navy-300/90 font-arabic line-clamp-2 leading-relaxed">
              {MOC_SUGGESTIONS[1].description}
            </p>
          </div>

          <div className="flex items-center justify-between pt-3 mt-2 text-xs text-moc-navy-300 group-hover:text-moc-peach-300 transition-colors font-arabic">
            <span className="text-[11px]">{isEn ? "Explore tradition" : "استكشف التقاليد"}</span>
            <ArrowUpRight className="w-4 h-4 rtl:-scale-x-100" />
          </div>
        </button>

        {/* Bento Card 3 (Span 1 col): UNESCO Heritage */}
        <button
          onClick={() => onSelectPrompt(MOC_SUGGESTIONS[2].query)}
          className="group relative flex flex-col justify-between p-5 rounded-3xl bg-gradient-to-br from-moc-navy-900/90 via-moc-navy-800/50 to-moc-navy-900/90 border border-moc-navy-700/60 hover:border-moc-coral-500/60 hover:shadow-sm transition-all duration-300 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500 text-right"
        >
          <div>
            <div className="flex items-center justify-between mb-3">
              <div className="p-2.5 rounded-2xl bg-moc-navy-800 text-moc-orange-500 border border-moc-navy-700/70 group-hover:scale-105 transition-transform">
                <Landmark className="w-5 h-5" />
              </div>
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-moc-plum-900/40 text-moc-peach-300 border border-moc-plum-700/40 font-arabic">
                {isEn ? "World Heritage" : "تراث عالمي"}
              </span>
            </div>

            <h3 className="text-sm font-bold text-white font-arabic group-hover:text-moc-peach-300 transition-colors mb-1">
              {MOC_SUGGESTIONS[2].title}
            </h3>
            <p className="text-xs text-moc-navy-300/90 font-arabic line-clamp-2 leading-relaxed">
              {MOC_SUGGESTIONS[2].description}
            </p>
          </div>

          <div className="flex items-center justify-between pt-3 mt-2 text-xs text-moc-navy-300 group-hover:text-moc-peach-300 transition-colors font-arabic">
            <span className="text-[11px]">{isEn ? "View all sites" : "استعراض المواقع"}</span>
            <ArrowUpRight className="w-4 h-4 rtl:-scale-x-100" />
          </div>
        </button>

        {/* Bento Card 4 (Large - Spans 2 cols): Al-Ula & Hegra Exploration */}
        <button
          onClick={() => onSelectPrompt(MOC_SUGGESTIONS[3].query)}
          className="group relative md:col-span-2 flex flex-col justify-between p-5 rounded-3xl bg-gradient-to-br from-moc-navy-900/95 via-moc-plum-950/60 to-moc-navy-900/90 border border-moc-navy-700/60 hover:border-moc-coral-500/70 hover:shadow-coral-glow transition-all duration-300 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500 overflow-hidden text-right"
        >
          <div>
            <div className="flex items-center justify-between gap-2 mb-3">
              <div className="flex items-center gap-2">
                <div className="p-2.5 rounded-2xl bg-gradient-to-br from-moc-plum-800 to-moc-navy-800 text-moc-peach-300 border border-moc-coral-500/30 group-hover:scale-105 transition-transform">
                  <Sparkles className="w-5 h-5" />
                </div>
                <span className="text-[11px] font-bold px-2.5 py-1 rounded-full bg-moc-navy-800 text-moc-sage-300 border border-moc-navy-700 font-arabic">
                  {isEn ? "Ancient Civilizations" : "حضارات عريقة"}
                </span>
              </div>
            </div>

            <h3 className="text-sm md:text-base font-bold text-white font-arabic group-hover:text-moc-peach-300 transition-colors mb-1.5">
              {MOC_SUGGESTIONS[3].title}
            </h3>
            <p className="text-xs text-moc-navy-200/85 font-arabic leading-relaxed">
              {MOC_SUGGESTIONS[3].description}
            </p>
          </div>

          <div className="flex items-center justify-between pt-4 mt-3 border-t border-white/5 text-xs text-moc-coral-400 font-semibold font-arabic">
            <span>{isEn ? "Plan Al-Ula cultural trip" : "تخطيط رحلة استكشاف العلا"}</span>
            <div className="p-1 rounded-lg bg-moc-coral-500/15 text-moc-coral-400 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 rtl:group-hover:-translate-x-0.5 transition-transform">
              <ArrowUpRight className="w-4 h-4 rtl:-scale-x-100" />
            </div>
          </div>
        </button>
      </div>

      {/* Cultural Categories Pills (MOC 2019 Palette with Crisp Lucide Icons) */}
      <div className="w-full flex flex-wrap items-center justify-center gap-2">
        {MOC_CATEGORY_TAGS.map((tag) => (
          <button
            key={tag.id}
            onClick={() => onSelectPrompt(isEn ? tag.queryEn : tag.queryAr)}
            className="flex items-center gap-2 px-3.5 py-2 rounded-2xl bg-moc-navy-900/80 hover:bg-moc-navy-800 border border-moc-navy-700/60 hover:border-moc-coral-500/50 text-xs text-moc-navy-200 hover:text-white transition-all cursor-pointer font-arabic focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-moc-coral-500 shadow-sm"
          >
            <span className="text-moc-coral-500">{getIcon(tag.iconName)}</span>
            <span>{isEn ? tag.labelEn : tag.labelAr}</span>
          </button>
        ))}
      </div>
    </div>
  );
};
