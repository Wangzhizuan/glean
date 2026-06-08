import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "拾句 - 视频与文章文案提取工具",
    template: "%s | 拾句",
  },
  description:
    "本机批量提取视频与文章的逐字稿/正文、结构化总结与精彩金句，支持抖音、Bilibili、YouTube、微信公众号、小红书、飞书文档及任意网页，结果可一键复制或导出。",
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
