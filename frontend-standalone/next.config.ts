import type { NextConfig } from "next";

// 生产环境由 Vercel 环境变量 BACKEND_URL 指定 Railway 后端地址（服务端变量，不进浏览器 bundle）。
// 本地开发未设置时回退到 http://localhost:8000。
const backendUrl = (process.env.BACKEND_URL ?? "http://localhost:8000").replace(/\/+$/, "");

const nextConfig: NextConfig = {
  images: { unoptimized: true },
  async rewrites() {
    return {
      beforeFiles: [
        { source: "/api/:path*", destination: `${backendUrl}/api/:path*` },
        // 高德 JS API 安全密钥：前端只引用同源地址，由后端注入安全码后透传动态密钥
        { source: "/amap-security/:path*", destination: `${backendUrl}/amap-security/:path*` },
      ],
    };
  },
};

export default nextConfig;
