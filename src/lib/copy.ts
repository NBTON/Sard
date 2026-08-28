import { Lang } from "@/types";

type Copy = Record<string, { ar: string; en: string }>;

export const copy: Copy = {
  tagline: { ar: "وكيلك للثقافة السعودية", en: "Saudi cultural agent" },
  kicker: { ar: "مستوحى من هوية وزارة الثقافة — ١٣ خيطًا، نسيج وحمض نووي", en: "Inspired by the Ministry of Culture — 13 threads, woven heritage & DNA" },
  hero: { ar: "الثقافة السعودية، في محادثة.", en: "Saudi culture, as a conversation." },
  heroSupport: {
    ar: "اسأل عن السدو، الدرعية، الخط العربي، أو أي قطاع من قطاعات الوزارة الأحد عشر. سرد يجيب بلغة المكان، مع مصادر وهيئات.",
    en: "Ask about Sadu weaving, Diriyah, Arabic calligraphy, or any of the eleven cultural sectors. Sard answers in the language of place, with sources and authorities.",
  },
  startChat: { ar: "ابدأ الحوار", en: "Start conversation" },
  tryExample: { ar: "جرّب مثالًا", en: "Try an example" },
  sectorsTitle: { ar: "القطاعات الثقافية", en: "Cultural sectors" },
  newChat: { ar: "حوار جديد", en: "New conversation" },
  home: { ar: "الواجهة", en: "Home" },
  composerPlaceholder: { ar: "اسأل سرد عن التراث، الأدب، أو أي هيئة...", en: "Ask Sard about heritage, literature, or any authority..." },
  send: { ar: "أرسل", en: "Send" },
  disclaimer: { ar: "واجهة تجريبية مستوحاة من ألوان وزارة الثقافة ولغتها البصرية. ليست منتجًا رسميًا للوزارة.", en: "Experimental interface inspired by the Ministry of Culture palette and visual language. Not an official MoC product." },
  disclaimerShort: { ar: "ليست منتجًا رسميًا — مستوحاة من هوية الوزارة", en: "Not an official MoC product — inspired by its visual identity" },
  weaving: { ar: "سرد ينسج الإجابة…", en: "Sard is weaving an answer…" },
  startersLabel: { ar: "بدايات مقترحة", en: "Try asking" },
  chatEmptyHint: { ar: "ابدأ بكتابة سؤال أو اختر أحد القطاعات أعلاه", en: "Type a question or pick a sector above" },
  thinkingError: { ar: "تعذّر إتمام الإجابة. حاول مرة أخرى.", en: "Could not complete the answer. Please try again." },
  sources: { ar: "المصادر", en: "Sources" },
  attachments: { ar: "المرفقات", en: "Attachments" },
};

export function t(key: string, lang: Lang): string {
  const entry = copy[key];
  if (!entry) return key;
  return lang === "en" ? entry.en : entry.ar;
}
