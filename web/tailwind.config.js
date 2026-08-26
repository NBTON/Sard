/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        paper: "#F3EEE4",
        "paper-2": "#E8E0D2",
        ink: "#141210",
        "ink-soft": "#3A342E",
        stone: "#8A8178",
        line: "#D4CBBD",
        clay: "#BE4A24",
        "clay-deep": "#8F3518",
        date: "#6E1F1F",
        olive: "#4A513C",
        gold: "#C4A46A",
        card: "#FAF7F1",
        sidebar: "#EFE8DB",
      },
      fontFamily: {
        sans: ["'IBM Plex Sans Arabic'", "'IBM Plex Sans'", "sans-serif"],
        display: ["'Noto Naskh Arabic'", "serif"],
        arabic: ["'IBM Plex Sans Arabic'", "'IBM Plex Sans'", "sans-serif"],
      },
      borderRadius: {
        card: "18px",
        weave: "28px",
        bubble: "16px",
      },
      boxShadow: {
        weave: "0 10px 40px -12px rgba(20,18,16,0.12), 0 4px 16px -4px rgba(20,18,16,0.07)",
        composer: "0 8px 32px -8px rgba(20,18,16,0.12), 0 2px 8px -2px rgba(20,18,16,0.06)",
        tile: "0 2px 12px -2px rgba(20,18,16,0.06)",
      },
      keyframes: {
        "weave-in": {
          "0%": { transform: "scaleY(0.18)", opacity: "0" },
          "100%": { transform: "scaleY(1)", opacity: "1" },
        },
        wave: {
          "0%, 100%": { transform: "scaleY(0.42)" },
          "50%": { transform: "scaleY(1)" },
        },
        "fade-in": {
          "0%": { opacity: "0", transform: "translateY(6px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "weave-in": "weave-in 0.55s cubic-bezier(0.16,1,0.3,1) forwards",
        wave: "wave 1.6s ease-in-out infinite",
        "fade-in": "fade-in 0.4s ease-out forwards",
      },
    },
  },
  plugins: [],
};
