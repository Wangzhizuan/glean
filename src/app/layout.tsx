import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "拾句 - 视频文案提取工具",
    template: "%s | 拾句",
  },
  description:
    "批量提取视频逐字稿、结构化总结与精彩金句，并支持复制和导出。",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
