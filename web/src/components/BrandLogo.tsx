import React from "react";

interface BrandLogoProps {
  size?: "sm" | "md" | "lg";
  showSubtitle?: boolean;
  isEn?: boolean;
}

export const BrandLogo: React.FC<BrandLogoProps> = ({
  size = "md",
  showSubtitle = true,
  isEn = false,
}) => {
  const iconDimensions = size === "sm" ? "w-8 h-8" : size === "lg" ? "w-14 h-14" : "w-10 h-10";
  const titleSize = size === "sm" ? "text-lg" : size === "lg" ? "text-3xl" : "text-xl";

  return (
    <div className="flex items-center gap-3 select-none">
      {/* Official MOC 2019 Brand Emblem (Dark Navy & Plum with Coral & Sage Accents) */}
      <div className={`relative flex items-center justify-center rounded-2xl bg-gradient-to-br from-moc-navy-900 via-moc-plum-800 to-moc-navy-950 p-2 shadow-moc-glow border border-moc-coral-600/40 ${iconDimensions}`}>
        {/* Subtle geometric overlay inspired by traditional Saudi weaving */}
        <div className="absolute inset-0 rounded-2xl opacity-20 bg-[radial-gradient(#EB5A3C_1px,transparent_1px)] [background-size:6px_6px]" />
        <svg
          viewBox="0 0 48 48"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
          className="w-full h-full relative z-10 text-moc-coral-600"
        >
          {/* Stylized Heritage Palm & DNA/Weaving Ribbons from MOC Identity */}
          <path
            d="M24 6V30M24 6C20 10 16 16 16 22C16 26 19 29 24 30M24 6C28 10 32 16 32 22C32 26 29 29 24 30"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path
            d="M12 36C16 33 21 32 24 32C27 32 32 33 36 36M10 42C15 38 20 37 24 37C28 37 33 38 38 42"
            stroke="#91B9B4"
            strokeWidth="2"
            strokeLinecap="round"
          />
          {/* Accent dot (MOC Coral) */}
          <circle cx="24" cy="6" r="2.5" fill="#EB5A3C" />
        </svg>
      </div>

      <div className="flex flex-col">
        <div className="flex items-center gap-2">
          <span className={`font-bold font-arabic tracking-tight text-white ${titleSize}`}>
            {isEn ? "Sard" : "سَــرْد"}
          </span>
          <span className="text-[10px] uppercase font-bold tracking-widest px-2 py-0.5 rounded-full bg-moc-plum-800/60 text-moc-peach-400 border border-moc-coral-600/30">
            {isEn ? "MOC AI" : "وزارة الثقافة"}
          </span>
        </div>
        {showSubtitle && (
          <span className="text-xs text-moc-navy-200/90 font-arabic">
            {isEn ? "Saudi Cultural & Travel Assistant" : "المستشار الثقافي والسياحي"}
          </span>
        )}
      </div>
    </div>
  );
};
