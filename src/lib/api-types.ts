export type Platform = "bilibili" | "youtube" | "douyin";

export type TaskStatus =
  | "queued"
  | "resolving"
  | "fetching_subtitle"
  | "downloading"
  | "extracting_audio"
  | "transcribing"
  | "normalizing"
  | "summarizing"
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
  platforms: Platform[];
  notice?: string | null;
}

export interface Task {
  id: string;
  batchId: string;
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
    platform: Platform;
    platformLabel: string;
    title: string;
    author: string | null;
    durationMs: number;
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
