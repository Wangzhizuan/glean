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
];

function trimTrailingPunctuation(value: string) {
  return value.replace(/[.,;:!?，。；：！？、)\]}>"']+$/g, "");
}

export function detectVideoPlatform(value: string): Platform | null {
  try {
    const url = new URL(trimTrailingPunctuation(value));
    if (!["http:", "https:"].includes(url.protocol)) return null;
    const host = url.hostname.toLowerCase();
    return (
      PLATFORM_HOSTS.find((candidate) => candidate.matches(host))?.platform ??
      null
    );
  } catch {
    return null;
  }
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

export function getSingleVideoUrl(value: string) {
  const supported = extractSupportedVideoUrls(value);
  return supported.length === 1 ? supported[0] : null;
}
