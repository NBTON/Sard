import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  transpilePackages: ["react-markdown", "remark-gfm"],
  async rewrites() {
    // Development: proxy to local FastAPI unless an explicit backend origin is set.
    // Supported env: SARD_BACKEND_ORIGIN (e.g. http://127.0.0.1:8000) or SARD_BACKEND_URL.
    // Production: no self-referential rewrite — same-origin /api is served by
    // Vercel functions (see vercel.json) or by NEXT_PUBLIC_API_BASE when set.
    if (process.env.NODE_ENV === "development") {
      const backend =
        process.env.SARD_BACKEND_ORIGIN ||
        process.env.SARD_BACKEND_URL ||
        `http://127.0.0.1:${process.env.SARD_PORT || "8000"}`;
      return [
        {
          source: "/api/:path*",
          destination: `${backend}/api/:path*`,
        },
      ];
    }
    return [];
  },
};

export default nextConfig;
