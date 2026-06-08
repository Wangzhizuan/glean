import type {
  Batch,
  BatchCreateResponse,
  Capabilities,
  Task,
  TaskResult,
} from "./api-types";

const API_BASE =
  process.env.NEXT_PUBLIC_SHIJU_API_URL || "http://127.0.0.1:8787/api";

interface ApiErrorPayload {
  detail?: string | { code?: string; message?: string };
}

export class ApiError extends Error {
  code?: string;

  constructor(message: string, code?: string) {
    super(message);
    this.name = "ApiError";
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
    cache: "no-store",
  });

  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as ApiErrorPayload;
    const detail = payload.detail;
    if (typeof detail === "string") throw new ApiError(detail);
    throw new ApiError(
      detail?.message || `请求失败 (${response.status})`,
      detail?.code,
    );
  }

  return response.json() as Promise<T>;
}

export function getCapabilities() {
  return request<Capabilities>("/capabilities");
}

export function createBatch(urls: string[]) {
  return request<BatchCreateResponse>("/batches", {
    method: "POST",
    body: JSON.stringify({
      urls,
      outputs: { transcript: true, summary: true, quotes: true },
      options: {
        language: "auto",
        sourceLanguage: "auto",
        outputLanguage: "zh",
        subtitlePolicy: "prefer_platform",
        asrModel: "large-v3-turbo",
        useBrowserCookies: false,
        browser: null,
        enableOcr: false,
      },
    }),
  });
}

export function getBatch(batchId: string) {
  return request<Batch>(`/batches/${batchId}`);
}

export function getTasks(filters?: {
  status?: string;
  platform?: string;
  query?: string;
}) {
  const params = new URLSearchParams();
  if (filters?.status) params.set("status", filters.status);
  if (filters?.platform) params.set("platform", filters.platform);
  if (filters?.query) params.set("query", filters.query);
  const suffix = params.size ? `?${params}` : "";
  return request<{ items: Task[]; total: number }>(`/tasks${suffix}`);
}

export function getTaskResult(taskId: string) {
  return request<TaskResult>(`/tasks/${taskId}/result`);
}

export function controlBatch(batchId: string, action: "pause" | "resume") {
  return request<Batch>(`/batches/${batchId}/${action}`, { method: "POST" });
}

export function controlTask(
  taskId: string,
  action: "pause" | "resume" | "cancel" | "retry",
) {
  return request<Task>(`/tasks/${taskId}/${action}`, { method: "POST" });
}

export function eventsUrl(batchId: string) {
  return `${API_BASE}/events?batchId=${encodeURIComponent(batchId)}`;
}

export function exportUrl(taskId: string, format: "txt" | "md" | "json") {
  return `${API_BASE}/tasks/${taskId}/export?format=${format}`;
}

export function deleteTasks(taskIds: string[]) {
  return request<{ deleted: number }>("/tasks/delete", {
    method: "POST",
    body: JSON.stringify({ task_ids: taskIds }),
  });
}
