# Design System Master File — Sard (سَــرْد)

> **LOGIC:** When building a specific page, first check `design-system/pages/[page-name].md` or `design-system/sard/pages/[page-name].md`.
> If that file exists, its rules **override** this Master file.
> If not, strictly follow the rules below.

---

**Project:** Sard (سَـرْد) — Saudi Cultural & Travel AI Platform  
**Authority:** Official Saudi Ministry of Culture (MOC) Brand Guidelines (March 2019)  
**Strapline:** "Our culture, our identity" (ثقافتنا، هويتنا)  
**Style Paradigm:** Official MOC Brand System (Dark Navy, Plum, Coral, Sage, Orange, Peach) + Cultural Glassmorphism  
**Accessibility Target:** WCAG AA (Minimum 4.5:1 text contrast)  
**Layout Model:** Split Conversational Shell + Bento Discovery Grid  

---

## 1. Official MOC Color Palette (March 2019 Guidelines)

| Segment | Ratio | RGB | Hex Code | CMYK | Pantone | Role in Platform |
|---|---|---|---|---|---|---|
| **Dark Navy** | **25%** | `R15 G40 B55` | `#0F2837` | `C60 M0 Y0 K90` | Pantone 546 C | Primary Dark Background & Surface Container |
| **Plum** | **21%** | `R110 G25 B70` | `#6E1946` | `C20 M75 Y0 K60` | Pantone 7650 C | Signature MOC Plum, Elevated Cards & Gradients |
| **Coral** | **12%** | `R235 G90 B60` | `#EB5A3C` | `C0 M80 Y70 K0` | Pantone 2348 C | Primary Action CTA, Send Button & Focus Glow |
| **Sage** | **12%** | `R145 G185 B180` | `#91B9B4` | `C45 M0 Y20 K15` | Pantone 5503 C | Verified RAG Badges, Borders & Serene Accents |
| **Crimson** | **10%** | `R180 G25 B50` | `#B41932` | `C0 M85 Y60 K25` | Pantone 703 C | Stop Generation, Destructive Actions & Alerts |
| **Orange** | **10%** | `R255 G150 B25` | `#FF9619` | `C0 M45 Y80 K0` | Pantone 7411 C | Warm Amber Highlights, Star Icons & Chips |
| **Light Peach** | **10%** | `R250 G195 B155` | `#FAC39B` | `C0 M30 Y40 K0` | Pantone 2437 C | Alabaster Surfaces, User Bubble Accent & Sand Tints |
| **Neutral Gray** | **—** | `R157 G157 B157` | `#9D9D9D` | `C0 M0 Y0 K50` | — | Microcopy, Inactive States & Timestamps |

---

## 2. Color Tokens Hierarchy

### Dark Mode (Official Default)
- `--moc-navy-bg`: `#08161F` (Deep obsidian navy canvas)
- `--moc-navy-surface`: `#0F2837` (MOC Dark Navy container)
- `--moc-navy-card`: `#16384C` (Glass cards with 85-95% opacity)
- `--moc-navy-border`: `#23506B` (Subtle navy border)
- `--moc-plum-primary`: `#6E1946` (MOC Plum signature)
- `--moc-plum-hover`: `#8C225B` (Plum luminous highlight)
- `--moc-coral-primary`: `#EB5A3C` (MOC Coral CTA)
- `--moc-coral-hover`: `#F0775F` (Coral hover glow)
- `--moc-sage`: `#91B9B4` (Verified RAG indicator)
- `--moc-orange`: `#FF9619` (Warm amber accent)
- `--moc-peach`: `#FAC39B` (Light peach sand)
- `--moc-text-primary`: `#FFFFFF` / `#F4F8FA`
- `--moc-text-secondary`: `#C5D9E5`
- `--moc-text-muted`: `#7A9CAD`

### Light Mode (Alabaster Desert Sand)
- `--moc-light-bg`: `#F8F6F1` (Alabaster sand background)
- `--moc-light-card`: `#FFFFFF` (Pure white card)
- `--moc-light-text`: `#0F2837` (Deep MOC navy text)
- `--moc-light-border`: `#E2DDD3` (Warm desert border)

---

## 3. Typography Hierarchy

- **Primary Typefaces:** `Effra` / `IBM Plex Sans Arabic`, `Tajawal`
- **Secondary / Body:** `IBM Plex Sans Arabic`, `Almarai`, `Calibri`
- **Latin Headings & Numbers:** `Outfit`, `Inter`, `Effra`
- **Monospace:** `JetBrains Mono`, `Fira Code`

---

## 4. UI Quality Checklist (Strict)

- [x] **Strict MOC 2019 Color Ratio:** Navy (25%), Plum (21%), Coral (12%), Sage (12%), Crimson (10%), Orange (10%), Peach (10%).
- [x] **No Emojis as Icons:** All UI icons use crisp vector SVGs from **Lucide React**.
- [x] **Cursor Pointer:** Added `cursor-pointer` to all interactive buttons, cards, pills, and dropdowns.
- [x] **WCAG AA Contrast:** Minimum 4.5:1 contrast on all light and dark text surfaces.
- [x] **Visible Focus States:** All buttons and inputs feature `:focus-visible` with Coral (`#EB5A3C`) or Sage (`#91B9B4`) ring highlights.
- [x] **Motion Preferences:** Animations respect `prefers-reduced-motion: reduce`.
- [x] **Responsive Breakpoints:** Fully tested at `375px` (Mobile), `768px` (Tablet), `1024px` (Laptop), and `1440px` (Desktop).
- [x] **Bilingual Native:** Arabic RTL layout (`dir="rtl"`) with seamless English LTR switch (`dir="ltr"`).
