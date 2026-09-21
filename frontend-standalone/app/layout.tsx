import type { Metadata } from "next";
import { StorageWarningBridge } from "@/components/storage-warning-bridge";
import "./globals.css";

export const metadata: Metadata = {
  title: "行迹 Journey Notes",
  description: "AI 驱动的国内自由行规划工具",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body><StorageWarningBridge />{children}</body>
    </html>
  );
}
