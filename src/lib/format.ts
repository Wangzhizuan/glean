import type { Platform, TaskStatus } from "./api-types";

export const platformLabels: Record<Platform, string> = {
  bilibili: "Bilibili",
  youtube: "YouTube",
  douyin: "抖音",
};

export const statusLabels: Record<TaskStatus, string> = {
  queued: "等待处理",
  resolving: "正在解析链接",
  fetching_subtitle: "正在查找字幕",
  downloading: "正在下载音频",
  extracting_audio: "正在处理音频",
  transcribing: "正在识别语音",
  normalizing: "正在整理逐字稿",
  summarizing: "正在生成总结",
  completed: "已完成",
  paused: "已暂停",
  cancelled: "已取消",
  failed: "处理失败",
};

export function formatDuration(durationMs: number | null) {
  if (!durationMs) return "--:--";
  const totalSeconds = Math.floor(durationMs / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
    : `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function formatTimestamp(durationMs: number) {
  const totalSeconds = Math.floor(durationMs / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export function formatDateTime(value: string | null) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}
