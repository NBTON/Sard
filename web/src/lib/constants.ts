import { CulturalSuggestion } from "@/types";

export interface CulturalCategoryTag {
  id: string;
  labelAr: string;
  labelEn: string;
  iconName: string;
  queryAr: string;
  queryEn: string;
}

export const MOC_SUGGESTIONS: CulturalSuggestion[] = [
  {
    id: "east-heritage",
    title: "مسار تراث المنطقة الشرقية",
    description: "برنامج سياحي وثقافي لمدة يومين في واحة الأحساء وجزيرة تاروت والبلدة القديمة",
    query: "أنشئ برنامجاً سياحياً وثقافياً متكاملاً لمدة يومين في المنطقة الشرقية يركز على التراث والمواقع الأثرية",
    category: "itinerary",
    iconName: "Compass",
  },
  {
    id: "shrimp-craft",
    title: "حرفة تجفيف الروبيان",
    description: "استكشف أسرار وتاريخ الصيد والتجفيف التقليدي في جزيرة تاروت التراثية",
    query: "حدثني بالتفصيل عن حرفة تجفيف الروبيان في جزيرة تاروت وتاريخها في التراث البحري للشرقية",
    category: "heritage",
    iconName: "Waves",
  },
  {
    id: "unesco-sites",
    title: "مواقع اليونسكو في المملكة",
    description: "استعراض لجميع مواقع التراث العالمي الثقافية والطبيعية في المملكة المسجلة رسمياً",
    query: "ما هي مواقع التراث العالمي المسجلة لدى اليونسكو في المملكة العربية السعودية وأهميتها التاريخية؟",
    category: "arts",
    iconName: "Landmark",
  },
  {
    id: "al-ula-route",
    title: "سحر العلا ومدائن صالح",
    description: "دليل استكشاف الحِجر، جبل عكمة، ومسار الفنون في وادي الفن بالعلا",
    query: "كيف أخطط لرحلة ثقافية ملهمة في محافظة العلا لاستكشاف الحِجر والآثار النبطية؟",
    category: "nature",
    iconName: "Sparkles",
  },
];

export const MOC_CATEGORY_TAGS: CulturalCategoryTag[] = [
  {
    id: "heritage",
    labelAr: "التراث والآثار",
    labelEn: "Heritage & Antiquities",
    iconName: "Landmark",
    queryAr: "حدثني عن أهم المعالم الأثرية والتراثية في المملكة",
    queryEn: "Tell me about key heritage and archaeological sites in Saudi Arabia",
  },
  {
    id: "culinary",
    labelAr: "فنون الطهي التقليدي",
    labelEn: "Traditional Culinary Arts",
    iconName: "Utensils",
    queryAr: "ما هي أشهر الأطباق والمأكولات الشعبية التقليدية في مناطق السعودية؟",
    queryEn: "What are the most famous traditional dishes in Saudi regions?",
  },
  {
    id: "crafts",
    labelAr: "الحرف اليدوية",
    labelEn: "Handicrafts & Artisans",
    iconName: "Palette",
    queryAr: "ما هي أبرز الحرف اليدوية التقليدية المحمية كـ تراث غير مادي؟",
    queryEn: "What are the top traditional handicrafts protected as intangible heritage?",
  },
  {
    id: "performing",
    labelAr: "الفنون الأدائية والموسيقى",
    labelEn: "Performing Arts & Music",
    iconName: "Music",
    queryAr: "حدثني عن الفنون الأدائية الشعبية مثل العرضة والسامري",
    queryEn: "Tell me about traditional performing arts like Ardah and Samri",
  },
  {
    id: "literature",
    labelAr: "الأدب والشعر العربي",
    labelEn: "Arabic Literature & Poetry",
    iconName: "BookOpen",
    queryAr: "ما هي مبادرات وزارة الثقافة لتعزيز الأدب والشعر العربي؟",
    queryEn: "What are the Ministry of Culture initiatives to promote Arabic literature?",
  },
  {
    id: "architecture",
    labelAr: "العمارة والتصميم",
    labelEn: "Architecture & Design",
    iconName: "Building2",
    queryAr: "كيف تختلف أنماط العمارة التقليدية بين نجد والحجاز والجنوب؟",
    queryEn: "How do traditional architectural styles vary between Najd, Hejaz, and the South?",
  },
];
