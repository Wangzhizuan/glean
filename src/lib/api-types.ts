export type Platform =
  | "bilibili"
  | "youtube"
  | "douyin"
  | "xiaoyuzhou"
  | "wechat"
  | "xiaohongshu"
  | "feishu"
  | "web";

export type TaskKind = "video" | "article";

export type TaskStatus =
  | "queued"
  | "resolving"
  | "fetching_subtitle"
  | "downloading"
  | "extracting_audio"
  | "transcribing"
  | "normalizing"
  | "summarizing"
  | "fetching"
  | "extracting"
  | "completed"
  | "paused"
  | "cancelled"
  | "failed";

export interface CapabilityDependency {
  available: boolean;
  modelReady?: boolean;
  path?: string | null;
}

export interface Capabilities {
  status: "ready" | "needs_setup";
  processorMode: "demo" | "real";
  dependencies: {
    ffmpeg: CapabilityDependency;
    ytDlp: CapabilityDependency;
    mlxWhisper: CapabilityDependency;
    ollama: CapabilityDependency;
  };
  article?: {
    trafilatura: CapabilityDependency;
    playwright: CapabilityDependency;
    curlCffi: CapabilityDependency;
    browserCookie3: CapabilityDependency;
    lxml: CapabilityDependency;
    larkCli: CapabilityDependency;
  };
  feishu?: {
    ready: boolean;
    larkCli: { available: boolean };
    browserCookies: {
      available: boolean;
      count: number;
      error: string | null;
    };
    message: string | null;
  };
  platforms: Platform[];
  sources?: Platform[];
  notice?: string | null;
}

export interface Task {
  id: string;
  batchId: string;
  kind: TaskKind;
  platform: Platform;
  sourceUrl: string;
  canonicalUrl: string | null;
  title: string;
  author: string | null;
  durationMs: number | null;
  status: TaskStatus;
  stageProgress: number;
  overallProgress: number;
  estimatedRemainingSeconds: number | null;
  createdAt: string;
  updatedAt: string;
  completedAt: string | null;
  error: { code: string; message: string } | null;
}

export interface Batch {
  id: string;
  status: string;
  taskCount: number;
  completedCount: number;
  failedCount: number;
  createdAt: string;
  updatedAt: string;
  tasks: Task[];
}

export interface BatchCreateResponse {
  batchId: string;
  taskIds: string[];
  createdAt: string;
}

export interface TranscriptSegment {
  index: number;
  startMs: number;
  endMs: number;
  text: string;
}

export interface TaskResult {
  taskId: string;
  metadata: {
    kind?: TaskKind;
    platform: Platform;
    platformLabel: string;
    title: string;
    author: string | null;
    durationMs: number;
    publishedAt?: string | null;
    generatedAt: string;
    sourceUrl: string;
  };
  transcript: {
    source: string;
    language: string;
    wordCount: number;
    plainText: string;
    segments: TranscriptSegment[];
  };
  summary: {
    overview: string;
    coreThesis?: string;
    detailedSummary?: string;
    keyPoints: Array<{ title: string; content: string }>;
    contentStructure?: Array<{ section: string; summary: string }>;
    actionItems: string[];
    targetAudience?: string[];
    terms?: Array<{ term: string; explanation: string }>;
    conclusions?: string[];
  };
  quotes: Array<{
    text: string;
    startMs: number;
    endMs: number;
    sourceSegmentIds: number[];
    isPolished: boolean;
  }>;
  processor: {
    mode: "demo" | "real";
    notice?: string;
  };
}

export type CreatorJobStatus =
  | "discovering"
  | "processing"
  | "transcribed"
  | "syncing"
  | "completed"
  | "cancelled"
  | "failed";

export type CreatorVideoStatus =
  | "pending"
  | "queued"
  | "processing"
  | "done"
  | "failed"
  | "cancelled";

export interface CreatorVideo {
  id: string;
  awemeId: string;
  videoUrl: string;
  title: string | null;
  durationMs: number | null;
  likeCount: number | null;
  commentCount: number | null;
  collectCount: number | null;
  shareCount: number | null;
  playCount: number | null;
  coverUrl: string | null;
  publishedAt: string | null;
  tags?: string[];
  taskId: string | null;
  transcribeStatus: CreatorVideoStatus;
}

export interface CreatorJob {
  id: string;
  platform: string;
  inputType: "url" | "name";
  inputValue: string;
  creatorUrl: string | null;
  creatorName: string | null;
  creatorSecUid: string | null;
  requestedLimit: number;
  discoveredCount: number;
  completedCount: number;
  failedCount: number;
  status: CreatorJobStatus;
  bitableUrl: string | null;
  createdAt: string;
  updatedAt: string;
  error: { code: string; message: string } | null;
  videos?: CreatorVideo[];
}

export interface CreatorCapabilities {
  processorMode: "demo" | "real";
  harvest: {
    ready: boolean;
    playwright: { available: boolean };
    browserCookies: { available: boolean; count: number; error: string | null };
    message: string | null;
  };
  feishu: {
    ready: boolean;
    larkCli: { available: boolean };
    message: string | null;
  };
}
