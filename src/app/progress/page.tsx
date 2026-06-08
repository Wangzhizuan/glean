"use client";

import { Suspense, useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { useSearchParams } from "next/navigation";
import { Toast, useToast } from "@/components/feedback/toast";
import { AppShell } from "@/components/layout/app-shell";
import { PageHero } from "@/components/layout/page-hero";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import {
  controlBatch,
  controlTask,
  eventsUrl,
  getBatch,
  getTasks,
} from "@/lib/api";
import type { Batch, Task } from "@/lib/api-types";
import {
  formatDuration,
  platformLabels,
  statusLabels,
} from "@/lib/format";

const ACTIVE_TASK_STATUSES = new Set([
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

export default function ProgressPage() {
  return (
    <Suspense fallback={<ProgressLoading />}>
      <ProgressContent />
    </Suspense>
  );
}

function subscribeToLastBatchId(callback: () => void) {
  if (typeof window === "undefined") return () => {};
  window.addEventListener("storage", callback);
  return () => window.removeEventListener("storage", callback);
}

function ProgressContent() {
  const searchParams = useSearchParams();
  const { message, showToast } = useToast();
  const queryBatchId = searchParams.get("batchId");
  const storedBatchId = useSyncExternalStore(
    subscribeToLastBatchId,
    () => localStorage.getItem("shiju:lastBatchId"),
    () => null,
  );
  const [fallbackBatchId, setFallbackBatchId] = useState<string | null>(null);
  const batchId = queryBatchId ?? storedBatchId ?? fallbackBatchId;
  const [batch, setBatch] = useState<Batch | null>(null);
  const [errored, setErrored] = useState(false);
  const loading = Boolean(batchId) && !batch && !errored;

  // 没有显式 batchId 也没有 localStorage 时，自动从最近的任务里找一个批次
  // 优先选还在处理中的，没有再回退到最新的一条任务，避免落入空状态。
  useEffect(() => {
    if (queryBatchId || storedBatchId) return;
    let cancelled = false;
    getTasks()
      .then(({ items }) => {
        if (cancelled || !items.length) return;
        const sorted = [...items].sort((a, b) =>
          a.createdAt < b.createdAt ? 1 : -1,
        );
        const active = sorted.find((task) =>
          ACTIVE_TASK_STATUSES.has(task.status),
        );
        const candidate = active ?? sorted[0];
        if (candidate?.batchId) {
          setFallbackBatchId(candidate.batchId);
        }
      })
      .catch(() => {
        // 后端不可用时静默失败，落到原本的"还没有可查看的任务"空状态。
      });
    return () => {
      cancelled = true;
    };
  }, [queryBatchId, storedBatchId]);

  const loadBatch = useCallback(async () => {
    if (!batchId) return;
    try {
      setBatch(await getBatch(batchId));
    } catch {
      setErrored(true);
      showToast("无法读取任务批次，请确认本地服务已启动");
    }
  }, [batchId, showToast]);

  useEffect(() => {
    if (!batchId) return;
    getBatch(batchId)
      .then(setBatch)
      .catch(() => {
        setErrored(true);
        showToast("无法读取任务批次，请确认本地服务已启动");
      });
  }, [batchId, showToast]);

  useEffect(() => {
    if (!batchId) return;
    const source = new EventSource(eventsUrl(batchId));
    source.addEventListener("batch.updated", (event) => {
      setBatch(JSON.parse((event as MessageEvent).data) as Batch);
    });
    source.onerror = () => {
      source.close();
    };
    return () => source.close();
  }, [batchId]);

  async function toggleBatch() {
    if (!batch) return;
    const paused = batch.status === "paused";
    try {
      setBatch(await controlBatch(batch.id, paused ? "resume" : "pause"));
      showToast(paused ? "任务已继续" : "任务已暂停");
    } catch {
      showToast("更新批次状态失败");
    }
  }

  async function cancelTask(task: Task) {
    try {
      await controlTask(task.id, "cancel");
      showToast(`已取消：${task.title}`);
      await loadBatch();
    } catch {
      showToast("取消任务失败");
    }
  }

  async function retryTask(task: Task) {
    try {
      await controlTask(task.id, "retry");
      showToast(`已重新加入队列：${task.title}`);
      await loadBatch();
    } catch {
      showToast("重试任务失败");
    }
  }

  if (loading) return <ProgressLoading />;
  if (!batch) {
    return (
      <AppShell
        action={
          <Button href="/submit" variant="secondary">
            新建任务
          </Button>
        }
      >
        <section className="container">
          <Card className="empty-state stack" panel>
            <h3>还没有可查看的任务</h3>
            <p>请先提交一个视频或文章链接批次。</p>
            <Button href="/submit">前往新建任务</Button>
          </Card>
        </section>
      </AppShell>
    );
  }

  const activeCount = batch.tasks.filter(
    (task) => !["completed", "cancelled", "failed"].includes(task.status),
  ).length;
  const isPaused = batch.status === "paused";

  return (
    <AppShell
      action={
        <Button href="/submit" variant="secondary">
          继续添加链接
        </Button>
      }
    >
      <section className="container">
        <PageHero
          action={
            activeCount > 0 ? (
              <Button onClick={toggleBatch} variant="secondary">
                {isPaused ? "继续全部任务" : "暂停全部任务"}
              </Button>
            ) : undefined
          }
          description="进度由本地服务通过 SSE 实时推送。视频和文章任务并行处理，完成后即可查看，无需等待整个批次。失败任务可在右侧重试。"
          eyebrow="任务队列"
          title={`${batch.taskCount} 条内容正在变成文字。`}
        />
        <div className="grid grid--content-sidebar">
          <Card as="article" panel>
            {batch.tasks.map((task) => (
              <TaskRow
                key={task.id}
                onCancel={() => cancelTask(task)}
                onRetry={() => retryTask(task)}
                task={task}
              />
            ))}
          </Card>
          <aside className="stack">
            <Card as="article" className="stack" panel>
              <h3>本批次概览</h3>
              <OverviewLine label="任务总数" value={batch.taskCount} />
              <OverviewLine label="已完成" value={batch.completedCount} />
              <OverviewLine label="处理中" value={activeCount} />
              <OverviewLine label="失败" value={batch.failedCount} />
            </Card>
            <div className="hint">
              关闭页面不会删除任务。再次打开“处理中”即可从 SQLite 恢复进度。
            </div>
          </aside>
        </div>
      </section>
      <Toast message={message} />
    </AppShell>
  );
}

function TaskRow({
  onCancel,
  onRetry,
  task,
}: {
  onCancel: () => void;
  onRetry: () => void;
  task: Task;
}) {
  const completed = task.status === "completed";
  const failed = task.status === "failed";
  const cancelled = task.status === "cancelled";
  const isArticle = task.kind === "article";
  const platformLabel = platformLabels[task.platform] ?? task.platform;
  const badgeTone: "success" | "warning" | "working" = completed
    ? "success"
    : failed
      ? "warning"
      : "working";

  return (
    <div className="task">
      <div className="row row--between task__header">
        <div className="row">
          <div className="task__thumb">{platformLabel}</div>
          <div>
            <b>{task.title}</b>
            <p className="meta">
              {platformLabel}
              {!isArticle && ` · ${formatDuration(task.durationMs)}`}
            </p>
            <a
              className="source-link"
              href={task.canonicalUrl || task.sourceUrl}
              rel="noreferrer"
              target="_blank"
            >
              {isArticle ? "打开原文" : "打开原视频"} ↗
            </a>
          </div>
        </div>
        <div className="row task__status">
          <Badge tone={cancelled ? "neutral" : badgeTone}>
            {statusLabels[task.status]}
          </Badge>
          {(failed || cancelled) && (
            <Button onClick={onRetry} variant="secondary">
              重试
            </Button>
          )}
        </div>
      </div>
      {completed ? (
        <div className="row row--between task__footer">
          <span className="meta">结果已保存在当前 Mac 本机</span>
          <Button href={`/detail?taskId=${encodeURIComponent(task.id)}`}>
            查看文案
          </Button>
        </div>
      ) : (
        <>
          <div className="task__progress">
            <Progress value={Math.round(task.overallProgress * 100)} />
          </div>
          <div className="row row--between task__meta">
            <span className="meta mono">
              {Math.round(task.overallProgress * 100)}% ·{" "}
              {task.estimatedRemainingSeconds === null
                ? "正在处理"
                : `预计还需 ${task.estimatedRemainingSeconds} 秒`}
            </span>
            {!failed && !cancelled && (
              <Button onClick={onCancel} variant="quiet">
                取消
              </Button>
            )}
          </div>
        </>
      )}
      {task.error && <div className="task-error">{task.error.message}</div>}
    </div>
  );
}

function OverviewLine({ label, value }: { label: string; value: number }) {
  return (
    <div className="row row--between">
      <span className="muted">{label}</span>
      <strong className="mono">{value}</strong>
    </div>
  );
}

function ProgressLoading() {
  return (
    <AppShell action={<span />}>
      <section className="container">
        <Card className="empty-state" panel>
          正在读取本地任务...
        </Card>
      </section>
    </AppShell>
  );
}
