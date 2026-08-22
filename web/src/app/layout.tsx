import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "سَــرْد | المستشار الثقافي والسياحي — وزارة الثقافة",
  description:
    "مساعد الذكاء الاصطناعي الثقافي والسياحي الرسمي لاستكشاف تراث المملكة العربية السعودية ومساراتها السياحية، مدعوم بأرشيف وزارة الثقافة ومصادر موثَّقة.",
  keywords: [
    "سرد",
    "وزارة الثقافة",
    "السياحة السعودية",
    "تراث المملكة",
    "المنطقة الشرقية",
    "الأحساء",
    "العلا",
    "الدرعية",
    "الذكاء الاصطناعي الثقافي",
  ],
  icons: {
    icon: "/favicon.ico",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ar" dir="rtl" className="h-full">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
        <meta name="theme-color" content="#0F2837" />
      </head>
      <body className="h-full antialiased bg-arabesque font-arabic selection:bg-moc-coral-600/30 selection:text-moc-peach-300">
        {children}
      </body>
    </html>
  );
}
