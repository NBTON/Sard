/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: ["class"],
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        moc: {
          // Official Ministry of Culture (March 2019) Color Palette
          navy: {
            50: "#F0F6F9",
            100: "#D8E8F0",
            200: "#B0D0E0",
            300: "#7AADC6",
            400: "#4D8CAE",
            500: "#357295",
            600: "#295D7B",
            700: "#1F4A63",
            800: "#16384C",
            900: "#0F2837", // Pantone 546 C (25% Ratio - Dark Navy)
            950: "#08161F", // Deep Obsidian Navy
          },
          plum: {
            50: "#FCF2F7",
            100: "#F9E0EE",
            200: "#F2BFD9",
            300: "#E68CBB",
            400: "#D75A9D",
            500: "#C43280",
            600: "#A5276B",
            700: "#871F56",
            800: "#6E1946", // Pantone 7650 C (21% Ratio - MOC Plum)
            900: "#4E1030",
            950: "#360B21",
          },
          coral: {
            50: "#FEF5F3",
            100: "#FDE8E4",
            200: "#FBD1C9",
            300: "#F8B0A2",
            400: "#F48F7B",
            500: "#F0735A",
            600: "#EB5A3C", // Pantone 2348 C (12% Ratio - MOC Coral CTA)
            700: "#CE3E24",
            800: "#A92D17",
            900: "#7D1F0E",
            950: "#4F1106",
          },
          sage: {
            50: "#F8FBFA",
            100: "#EEF5F4",
            200: "#D9E8E6",
            300: "#C1D9D6",
            400: "#A9C9C5",
            500: "#91B9B4", // Pantone 5503 C (12% Ratio - MOC Sage)
            600: "#749F99",
            700: "#5D837E",
            800: "#466662",
            900: "#314845",
            950: "#1E2D2B",
          },
          crimson: {
            500: "#E5455E",
            600: "#D22843",
            700: "#B41932", // Pantone 703 C (10% Ratio - MOC Crimson)
            800: "#821124",
            900: "#5C0A18",
          },
          orange: {
            300: "#FFDCAD",
            400: "#FFC57A",
            500: "#FFAE47",
            600: "#FF9619", // Pantone 7411 C (10% Ratio - MOC Orange)
            700: "#D6770C",
            800: "#A85C08",
            900: "#7A4205",
          },
          peach: {
            300: "#FEEFE4",
            400: "#FDE1CD",
            500: "#FCD3B5",
            600: "#FAC39B", // Pantone 2437 C (10% Ratio - MOC Light Peach)
            700: "#C78250",
            800: "#A3653A",
            900: "#754829",
          },
          gray: {
            400: "#B5B5B5",
            500: "#9D9D9D", // MOC Neutral Gray
            600: "#7C7C7C",
          },
          dark: {
            bg: "#08161F",
            surface: "#0F2837",
            card: "#16384C",
            border: "#23506B",
            hover: "#1D455D",
            muted: "#7A9CAD",
          },
          light: {
            bg: "#F8F6F1",
            surface: "#FFFFFF",
            card: "#FFFFFF",
            border: "#E2DDD3",
            hover: "#F0ECE3",
            muted: "#647D8D",
          }
        },
      },
      fontFamily: {
        arabic: ["'IBM Plex Sans Arabic'", "'Tajawal'", "'Almarai'", "'Effra'", "sans-serif"],
        sans: ["'Outfit'", "'Inter'", "'Effra'", "sans-serif"],
      },
      boxShadow: {
        'moc-glow': '0 0 25px -5px rgba(235, 90, 60, 0.28)',
        'coral-glow': '0 0 20px -3px rgba(235, 90, 60, 0.35)',
        'plum-glow': '0 0 25px -5px rgba(110, 25, 70, 0.45)',
        'sage-glow': '0 0 20px -3px rgba(145, 185, 180, 0.3)',
        'card-elevated': '0 10px 30px -10px rgba(8, 22, 31, 0.5)',
      },
      animation: {
        'pulse-subtle': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'float': 'float 6s ease-in-out infinite',
        'fade-in': 'fadeIn 0.3s ease-out forwards',
        'slide-up': 'slideUp 0.35s cubic-bezier(0.16, 1, 0.3, 1) forwards',
      },
      keyframes: {
        float: {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-6px)' },
        },
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(12px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
    },
  },
  plugins: [],
};
