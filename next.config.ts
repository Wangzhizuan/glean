import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  turbopack: {
    root: process.cwd(),
  },
  async rewrites() {
    return [
      {
        source: "/local-api/:path*",
        destination: "http://127.0.0.1:8787/api/:path*",
      },
    ];
  },
};

export default nextConfig;
