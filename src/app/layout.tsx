import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "سرد — وكيلك للثقافة السعودية",
  description: "سرد — Saudi cultural agent. اسأل عن التراث، الأدب، الأزياء، فنون الطهي، الموسيقى، والقطاعات الثقافية الأحد عشر.",
  keywords: ["سرد", "Sard", "الثقافة السعودية", "Saudi culture", "Sadu", "الدرعية", "الخط العربي"],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ar" dir="rtl" className="h-full">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <meta name="theme-color" content="#F3EEE4" />
      </head>
      <body className="h-full antialiased">
        {children}
      </body>
    </html>
  );
}
