import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async headers() {
    return [{ source: "/share/:path*", headers: [
      { key: "Cache-Control", value: "private, no-store, max-age=0" },
      { key: "CDN-Cache-Control", value: "no-store" },
      { key: "Cloudflare-CDN-Cache-Control", value: "no-store" },
      { key: "Referrer-Policy", value: "no-referrer" },
      { key: "X-Robots-Tag", value: "noindex, nofollow, noarchive" },
      { key: "X-Content-Type-Options", value: "nosniff" },
    ] }];
  },
};

export default nextConfig;
