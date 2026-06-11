import type { Platform } from "./api-types";

const URL_PATTERN = /https?:\/\/[^\s<>"'，。；！？、）】》]+/gi;

const PLATFORM_HOSTS: Array<{
  platform: Platform;
  matches: (host: string) => boolean;
}> = [
  {
    platform: "bilibili",
    matches: (host) =>
      host === "b23.tv" ||
      host === "bilibili.com" ||
      host.endsWith(".bilibili.com"),
  },
  {
    platform: "youtube",
    matches: (host) =>
      host === "youtu.be" ||
      host === "youtube.com" ||
      host.endsWith(".youtube.com"),
  },
  {
    platform: "douyin",
    matches: (host) =>
      host === "douyin.com" || host.endsWith(".douyin.com"),
  },
  {
    platform: "xiaoyuzhou",
    matches: (host) =>
      host === "xiaoyuzhoufm.com" || host.endsWith(".xiaoyuzhoufm.com"),
  },
];

const ARTICLE_HOSTS: Array<{
  platform: Platform;
  matches: (host: string) => boolean;
}> = [
  {
    platform: "wechat",
    matches: (host) => host === "mp.weixin.qq.com",
  },
  {
    platform: "xiaohongshu",
    matches: (host) =>
      host === "xiaohongshu.com" ||
      host.endsWith(".xiaohongshu.com") ||
      host === "xhslink.com",
  },
  {
    platform: "feishu",
    matches: (host) =>
      host.endsWith(".feishu.cn") ||
      host.endsWith(".larkoffice.com") ||
      host.endsWith(".feishu-pre.cn"),
  },
];

function trimTrailingPunctuation(value: string) {
  return value.replace(/[.,;:!?，。；：！？、)\]}>"']+$/g, "");
}

function parseUrl(value: string): URL | null {
  try {
    const url = new URL(trimTrailingPunctuation(value));
    if (!["http:", "https:"].includes(url.protocol)) return null;
    return url;
  } catch {
    return null;
  }
}

export function detectVideoPlatform(value: string): Platform | null {
  const url = parseUrl(value);
  if (!url) return null;
  const host = url.hostname.toLowerCase();
  return (
    PLATFORM_HOSTS.find((candidate) => candidate.matches(host))?.platform ??
    null
  );
}

export function detectArticlePlatform(value: string): Platform | null {
  const url = parseUrl(value);
  if (!url) return null;
  const host = url.hostname.toLowerCase();
  if (host === "localhost" || host === "127.0.0.1") return null;
  const matched = ARTICLE_HOSTS.find((candidate) => candidate.matches(host));
  if (matched) return matched.platform;
  return "web";
}

export function detectSourcePlatform(value: string): Platform | null {
  return detectVideoPlatform(value) ?? detectArticlePlatform(value);
}

export function extractUrls(value: string) {
  return Array.from(value.matchAll(URL_PATTERN), (match) =>
    trimTrailingPunctuation(match[0]),
  );
}

export function extractSupportedVideoUrls(value: string) {
  return Array.from(
    new Set(extractUrls(value).filter((url) => detectVideoPlatform(url))),
  );
}

export function extractSupportedSourceUrls(value: string) {
  return Array.from(
    new Set(extractUrls(value).filter((url) => detectSourcePlatform(url))),
  );
}

export function getSingleVideoUrl(value: string) {
  const supported = extractSupportedVideoUrls(value);
  return supported.length === 1 ? supported[0] : null;
}

export function getSingleSourceUrl(value: string) {
  const supported = extractSupportedSourceUrls(value);
  return supported.length === 1 ? supported[0] : null;
}
