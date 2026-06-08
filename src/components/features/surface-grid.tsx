"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getTasks } from "@/lib/api";
import type { Task } from "@/lib/api-types";

const ACTIVE_STATUSES = new Set([
  "queued",
  "resolving",
  "fetching_subtitle",
  "downloading",
  "extracting_audio",
  "transcribing",
  "normalizing",
  "summarizing",
  "fetching",
  "extracting",
  "paused",
]);

interface Surface {
  key: string;
  href: string;
  number: string;
  title: string;
  description: string;
  action: string;
}

const STATIC_SURFACES: Surface[] = [
  {
    key: "submit",
    href: "/submit",
    number: "01",
    title: "提交链接",
    description: "粘贴 1–10 条视频或文章链接，混合来源也能一次提交。",
    action: "打开工作台 →",
  },
  {
    key: "progress",
    href: "/progress",
    number: "02",
    title: "任务进度",
    description: "本地 SSE 实时推送解析、识别、提炼状态，失败可一键重试。",
    action: "查看任务队列 →",
  },
  {
    key: "detail",
    href: "/history",
    number: "03",
    title: "文案详情",
    description: "总结、逐字稿/正文与金句分页查看，支持复制整篇或导出。",
    action: "查看示例文案 →",
  },
  {
    key: "history",
    href: "/history",
    number: "04",
    title: "历史与下载",
    description: "按平台和时间筛选过往记录，多选后批量导出 TXT 或 Markdown。",
    action: "打开资料库 →",
  },
];

function pickLatest<T extends { createdAt: string }>(items: T[]): T | undefined {
  if (!items.length) return undefined;
  return [...items].sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1))[0];
}

export function SurfaceGrid() {
  const [progressHref, setProgressHref] = useState("/progress");
  const [detailHref, setDetailHref] = useState("/history");

  useEffect(() => {
    let cancelled = false;
    getTasks()
      .then(({ items }) => {
        if (cancelled) return;
        const completed = items.filter((task: Task) => task.status === "completed");
        const latestCompleted = pickLatest(completed);
        if (latestCompleted) {
          setDetailHref(
            `/detail?taskId=${encodeURIComponent(latestCompleted.id)}`,
          );
        }
        const active = items.filter((task: Task) =>
          ACTIVE_STATUSES.has(task.status),
        );
        const latestActive = pickLatest(active) ?? pickLatest(items);
        if (latestActive?.batchId) {
          setProgressHref(
            `/progress?batchId=${encodeURIComponent(latestActive.batchId)}`,
          );
        }
      })
      .catch(() => {
        // 没有后端连接时保持默认链接，落到对应页的空状态。
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const surfaces = STATIC_SURFACES.map((surface) => {
    if (surface.key === "progress") return { ...surface, href: progressHref };
    if (surface.key === "detail") return { ...surface, href: detailHref };
    return surface;
  });

  return (
    <div className="surface-grid">
      {surfaces.map((surface) => (
        <Link className="surface-card" href={surface.href} key={surface.key}>
          <span className="surface-card__number">{surface.number}</span>
          <h3>{surface.title}</h3>
          <p>{surface.description}</p>
          <span>{surface.action}</span>
        </Link>
      ))}
    </div>
  );
}
