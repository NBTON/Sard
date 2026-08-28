import { Sector } from "@/types";

export const SECTORS: Sector[] = [
  { id: "heritage", ar: "التراث", en: "Heritage", color: "#BE4A24", promptAr: "حدثني عن التراث السعودي وأهم المواقع التراثية", promptEn: "Tell me about Saudi heritage and its key sites" },
  { id: "literature", ar: "الأدب", en: "Literature", color: "#3D4A3A", promptAr: "ما دور قطاع الأدب والنشر والترجمة في الثقافة السعودية؟", promptEn: "What is the role of literature, publishing and translation in Saudi culture?" },
  { id: "fashion", ar: "الأزياء", en: "Fashion", color: "#8B3A4A", promptAr: "ما أبرز ملامح الأزياء التراثية السعودية وتنوعها المناطقي؟", promptEn: "What are the key features of Saudi traditional fashion across regions?" },
  { id: "culinary", ar: "فنون الطهي", en: "Culinary", color: "#C4782A", promptAr: "ما أشهر فنون الطهي والأطباق التراثية في السعودية؟", promptEn: "What are Saudi Arabia's most celebrated culinary traditions and dishes?" },
  { id: "music", ar: "الموسيقى", en: "Music", color: "#4A3B6B", promptAr: "حدثني عن قطاع الموسيقى والفنون الأدائية السعودية", promptEn: "Tell me about Saudi music and performing arts" },
  { id: "film", ar: "الأفلام", en: "Film", color: "#1E2A3A", promptAr: "كيف تطور قطاع الأفلام والسينما في السعودية؟", promptEn: "How has film and cinema evolved in Saudi Arabia?" },
  { id: "museums", ar: "المتاحف", en: "Museums", color: "#8A6A3A", promptAr: "ما أهم المتاحف السعودية وما الذي تعرضه؟", promptEn: "What are the key Saudi museums and what do they showcase?" },
  { id: "visual", ar: "الفنون البصرية", en: "Visual Arts", color: "#2A5A5A", promptAr: "كيف يعكس قطاع الفنون البصرية الهوية السعودية المعاصرة؟", promptEn: "How do visual arts reflect contemporary Saudi identity?" },
  { id: "architecture", ar: "العمارة", en: "Architecture", color: "#5A5848", promptAr: "ما أنماط العمارة التقليدية في نجد والحجاز وعسير؟", promptEn: "What are the traditional architectural styles of Najd, Hejaz and Asir?" },
  { id: "theatre", ar: "المسرح", en: "Theatre", color: "#7A2A2A", promptAr: "كيف ينمو قطاع المسرح والفنون الأدائية في السعودية؟", promptEn: "How is theatre and performing arts growing in Saudi Arabia?" },
  { id: "libraries", ar: "المكتبات", en: "Libraries", color: "#2A4A4A", promptAr: "ما دور قطاع المكتبات في حفظ ونشر الثقافة السعودية؟", promptEn: "What is the role of libraries in preserving and sharing Saudi culture?" },
];

export const WEAVE_COLORS: string[] = [
  "#BE4A24", "#6E1F1F", "#4A513C", "#C4A46A", "#141210",
  "#8F3518", "#3D4A3A", "#8B3A4A", "#1E2A3A", "#2A5A5A", "#5A5848", "#7A2A2A", "#2A4A4A",
];

// Starter chips for sidebar (paper)
export const STARTERS: { ar: string; en: string }[] = [
  { ar: "ما قصة السدو؟", en: "What is the story of Sadu?" },
  { ar: "خطط لي يومًا في الدرعية", en: "Plan a day in Diriyah" },
  { ar: "الخط العربي وأنواعه", en: "Arabic calligraphy styles" },
  { ar: "مواقع اليونسكو السعودية", en: "Saudi UNESCO sites" },
];
