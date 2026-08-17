# Page Override: Chat & Cultural Discovery Shell (`/`)

**Target Page:** Main Conversational Platform (`src/app/page.tsx`)  
**Parent System:** `design-system/MASTER.md` (MOC March 2019 Brand Guidelines)  

---

## 1. Architectural Layout (Split View + Bento Discovery)

```text
┌───────────────────────────────────────────────────────────────────────────┐
│ HEADER: Brand Logo | Model Status & RAG Badge | Lang (AR/EN) | Theme | New│
├─────────────────┬─────────────────────────────────────────────────────────┤
│ SIDEBAR (Collapsible):│ MAIN CHAT CANVAS                                         │
│ • + New Journey │ ┌─────────────────────────────────────────────────────┐ │
│ • Search        │ │ EMPTY STATE / HERO:                                 │ │
│ • History Feed  │ │ • MOC Crest + Plum & Coral Glow                     │ │
│ • Cultural Bento│ │ • Bento Grid Discovery (Heritage, Culinary, Taif...)│ │
│ • RAG Status    │ │ • Verified Source Stats Card (MOC Sage)             │ │
│ • Preferences   │ └─────────────────────────────────────────────────────┘ │
│                 │                                                         │
│                 │ MESSAGE STREAM:                                         │
│                 │ • User Bubble (Dark Navy + Coral Border)                │ │
│                 │ • Assistant Bubble (MOC Dark Navy + Plum Badge)         │ │
│                 │   - Real-time Stage Status (Query / Dense / Rerank)     │ │
│                 │   - Streaming Markdown / Syntax-highlighted Code        │ │
│                 │   - Collapsible Reference Footnotes ([1], [2], Source) │ │
│                 │   - Itinerary Artifacts Box (PDF & iCal Sync)          │ │
│                 │   - Actions: Read Aloud (TTS), Copy, Technical Route    │ │
│                 ├─────────────────────────────────────────────────────────┤
│                 │ FLOATING INPUT CONTAINER (Glassmorphic Pill):           │
│                 │ • Quick Suggestion Chips                                │ │
│                 │ • Auto-growing Textarea + Voice Dictation               │ │
│                 │ • Send (MOC Coral) / Stop (MOC Crimson) Button          │ │
│                 │ • MOC Official Strapline: "Our culture, our identity"   │ │
└─────────────────┴─────────────────────────────────────────────────────────┘
```

---

## 2. Interaction Requirements

1. **Focus States:** Every interactive component must have `:focus-visible:ring-2 :focus-visible:ring-moc-coral-500 :focus-visible:ring-offset-2`.
2. **Cursor Pointer:** Must be applied on every clickable card, chip, button, tab, and citation link.
3. **No Raw Emojis:** All badges and icons MUST use crisp vector Lucide SVG icons.
4. **Bento Grid:** Welcome screen hero features an interactive 4-card Bento layout with hover lift, gradient borders, and direct prompt execution.
5. **Interactive Citations Drawer:** Displays retrieved sources with title, institution, external URL link (`↗`), topic badge, and chunk excerpt.
6. **Downloadable Artifacts:** Render PDF and iCalendar `.ics` action buttons with download icons.
7. **Bilingual RTL / LTR:** Flawless alignment and font switching across Arabic (`dir="rtl"`) and English (`dir="ltr"`).
