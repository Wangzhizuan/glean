import type { TaskResult } from "@/lib/api-types";

/** 生图目标平台。 */
export type ImageProvider = "chatgpt" | "gemini";

/** 弹窗里可选的画面风格预设。 */
export interface StylePreset {
  id: string;
  label: string;
  /** 拼进 prompt 的英文风格描述，便于模型理解。 */
  hint: string;
}

/** 弹窗里可选的画面比例。 */
export interface RatioPreset {
  id: string;
  label: string;
}

export const STYLE_PRESETS: StylePreset[] = [
  { id: "infographic", label: "信息图", hint: "clean infographic style, structured layout, icons and labels, data-visualization aesthetic" },
  { id: "none", label: "无", hint: "" },
  { id: "tech", label: "科技未来感", hint: "modern, futuristic tech aesthetic, clean, high-tech mood" },
  { id: "editorial", label: "杂志封面感", hint: "editorial magazine cover style, bold typography space, premium" },
  { id: "minimal", label: "极简留白", hint: "minimalist, lots of negative space, refined and calm" },
  { id: "warm", label: "暖色叙事", hint: "warm color palette, cinematic storytelling lighting" },
  { id: "illustration", label: "插画风", hint: "flat illustration, friendly vector art style" },
];

export const RATIO_PRESETS: RatioPreset[] = [
  { id: "9:16", label: "9:16 竖版" },
  { id: "16:9", label: "16:9 横版封面" },
  { id: "1:1", label: "1:1 方图" },
  { id: "4:3", label: "4:3 文章配图" },
];

/** 各平台首页（不带 query）。 */
const PROVIDER_HOME: Record<ImageProvider, string> = {
  chatgpt: "https://chatgpt.com/",
  gemini: "https://gemini.google.com/app",
};

/**
 * URL query 的安全上限（按 encodeURIComponent 之后的字符数算）。
 * ChatGPT 服务端对请求头/请求行有限制，过长会直接返回 HTTP 431。
 * 中文经过百分号编码后体积约 ×9，这里取一个保守值。
 */
export const SAFE_QUERY_LENGTH = 1200;

export const PROVIDER_LABEL: Record<ImageProvider, string> = {
  chatgpt: "ChatGPT",
  gemini: "Gemini",
};

/** 从任务结果里抽取一段适合喂给生图模型的文章要点。 */
export function extractArticleContext(result: TaskResult): string {
  const { summary } = result;
  const parts = [
    summary.coreThesis || summary.overview,
    summary.keyPoints
      .slice(0, 4)
      .map((point) => `- ${point.title}：${point.content}`)
      .join("\n"),
  ].filter(Boolean);
  return parts.join("\n").trim();
}

/** 取整段逐字稿/正文全文，作为弹窗默认填入的内容。 */
export function extractTranscript(result: TaskResult): string {
  return (result.transcript.plainText || "").trim();
}

export interface BuildPromptInput {
  title: string;
  context: string;
  styleHint: string;
  ratio: string;
  extra: string;
}

/** 把标题、要点、风格、比例和补充要求拼成一段完整的生图指令。 */
export function buildImagePrompt(input: BuildPromptInput): string {
  const { title, context, styleHint, ratio, extra } = input;
  return [
    `请为文章《${title}》生成一张配图/封面。`,
    "",
    "文章核心内容：",
    context,
    "",
    styleHint.trim() ? `画面风格：${styleHint}` : "",
    `画面比例：${ratio}`,
    extra.trim() ? `补充要求：${extra.trim()}` : "",
    "",
    "请直接生成图片，不要输出文字解释。",
  ]
    .filter((line) => line !== "" || true)
    .join("\n")
    .trim();
}

export interface ProviderTarget {
  /** 最终要打开的 URL。 */
  url: string;
  /**
   * 打开方式：
   * - "prefilled" 完整 prompt 已塞进 URL，用户按回车即可；
   * - "paste"     prompt 太长走剪贴板，打开空白首页，需用户粘贴。
   */
  mode: "prefilled" | "paste";
}

/**
 * 根据完整 prompt 的长度决定如何打开目标平台：
 * - 编码后未超 SAFE_QUERY_LENGTH → 直接把完整 prompt 放进 ?q=（prefilled）；
 * - 否则打开空白首页，完整 prompt 交给剪贴板（paste），避免 HTTP 431。
 */
/**
 * 仅判断手动模式下当前 prompt 会走哪种打开方式(不构造 URL),
 * 供 UI 提前告知用户「短内容预填 / 长内容需粘贴」。
 */
export function getProviderMode(prompt: string): "prefilled" | "paste" {
  return encodeURIComponent(prompt).length <= SAFE_QUERY_LENGTH
    ? "prefilled"
    : "paste";
}

export function buildProviderTarget(
  provider: ImageProvider,
  prompt: string,
): ProviderTarget {
  const encoded = encodeURIComponent(prompt);
  if (encoded.length <= SAFE_QUERY_LENGTH) {
    return {
      url: `${PROVIDER_HOME[provider]}?q=${encoded}`,
      mode: "prefilled",
    };
  }
  // 太长：打开干净首页(输入框留空)，完整 prompt 走剪贴板，用户粘贴即可。
  return {
    url: PROVIDER_HOME[provider],
    mode: "paste",
  };
}


/**
 * 方案①(扩展自动发送)：把完整 prompt 放进 URL 的 hash 部分。
 * hash 不会发送到服务器，因此不受 HTTP 431 请求头长度限制，
 * 可承载任意长度的 prompt。需要配合 browser-extension/ 里的扩展使用。
 */
export function buildExtensionTarget(
  provider: ImageProvider,
  prompt: string,
): string {
  return `${PROVIDER_HOME[provider]}#glean=${encodeURIComponent(prompt)}`;
}

/**
 * 方案③(浏览器搜索引擎模式)：返回用于注册为自定义搜索引擎的模板，
 * 以及一次性触发用的 URL。部分浏览器(如 Vivaldi)通过 omnibox 触发时会自动提交。
 */
export const SEARCH_ENGINE_TEMPLATE: Record<ImageProvider, string> = {
  chatgpt: "https://chatgpt.com/?q=%s",
  gemini: "https://gemini.google.com/app?q=%s",
};

/** 方案③ 一次性打开用的 URL(短 prompt 时可直接预填)。 */
export function buildSearchEngineUrl(
  provider: ImageProvider,
  prompt: string,
): string {
  return SEARCH_ENGINE_TEMPLATE[provider].replace(
    "%s",
    encodeURIComponent(prompt),
  );
}

/* ------------------------------------------------------------------ *
 * 快捷指令记忆：记住用户历史选过的「画面风格 / 画面比例」，          *
 * 第二次打开弹窗时在下方显示快捷标签，点击即可直接匹配。            *
 * 通过 localStorage 持久化，按最近使用排序，最多保留若干个。       *
 * ------------------------------------------------------------------ */

const RECENT_STYLE_KEY = "glean:image:recentStyles";
const RECENT_RATIO_KEY = "glean:image:recentRatios";
const RECENT_MAX = 5;

function readRecent(key: string): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.filter((x) => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function writeRecent(key: string, id: string): string[] {
  if (typeof window === "undefined") return [];
  const next = [id, ...readRecent(key).filter((x) => x !== id)].slice(
    0,
    RECENT_MAX,
  );
  try {
    window.localStorage.setItem(key, JSON.stringify(next));
  } catch {
    /* ignore quota / privacy mode */
  }
  return next;
}

/** 读取最近用过的画面风格 id 列表(最近的在前)。 */
export function getRecentStyleIds(): string[] {
  return readRecent(RECENT_STYLE_KEY);
}

/** 读取最近用过的画面比例 id 列表(最近的在前)。 */
export function getRecentRatioIds(): string[] {
  return readRecent(RECENT_RATIO_KEY);
}

/** 记录一次画面风格的使用,返回更新后的最近列表。 */
export function rememberStyleId(id: string): string[] {
  return writeRecent(RECENT_STYLE_KEY, id);
}

/** 记录一次画面比例的使用,返回更新后的最近列表。 */
export function rememberRatioId(id: string): string[] {
  return writeRecent(RECENT_RATIO_KEY, id);
}
