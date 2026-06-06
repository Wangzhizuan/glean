"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
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
} from "@/lib/api";
import type { Batch, Task } from "@/lib/api-types";
import {
  formatDuration,
  platformLabels,
  statusLabels,
} from "@/lib/format";

export default function ProgressPage() {
  return (
    <Suspense fallback={<ProgressLoading />}>
      <ProgressContent />
    </Suspense>
  );
}

function ProgressContent() {
  const searchParams = useSearchParams();
  const [batch, setBatch] = useState<Batch | null>(null);
  const [loading, setLoading] = useState(true);
  const { message, showToast } = useToast();
  const queryBatchId = searchParams.get("batchId");
  const batchId =
    queryBatchId ||
    (typeof window !== "undefined"
      ? localStorage.getItem("shiju:lastBatchId")
      : null);

  const loadBatch = useCallback(async () => {
    if (!batchId) {
      setLoading(false);
      return;
    }
    try {
      setBatch(await getBatch(batchId));
    } catch {
      showToast("无法读取任务批次，请确认本地服务已启动");
    } finally {
      setLoading(false);
    }
  }, [batchId, showToast]);

  useEffect(() => {
    if (!batchId) return;
    getBatch(batchId)
      .then(setBatch)
      .catch(() => showToast("无法读取任务批次，请确认本地服务已启动"))
      .finally(() => setLoading(false));
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
            <p>请先提交一个视频链接批次。</p>
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
          description="进度由本地服务通过 SSE 实时推送。完成的文案可以立即查看，不必等待整个批次结束。"
          eyebrow="任务队列"
          title={`${batch.taskCount} 条视频正在变成文字。`}
        />
        <div className="grid grid--content-sidebar">
          <Card as="article" panel>
            {batch.tasks.map((task) => (
              <TaskRow
                key={task.id}
                onCancel={() => cancelTask(task)}
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
  task,
}: {
  onCancel: () => void;
  task: Task;
}) {
  const completed = task.status === "completed";
  const failed = task.status === "failed";
  const cancelled = task.status === "cancelled";
  const badgeTone: "success" | "warning" | "working" = completed
    ? "success"
    : failed
      ? "warning"
      : "working";

  return (
    <div className="task">
      <div className="row row--between task__header">
        <div className="row">
          <div className="task__thumb">{platformLabels[task.platform]}</div>
          <div>
            <b>{task.title}</b>
            <p className="meta">
              {platformLabels[task.platform]} · {formatDuration(task.durationMs)}
            </p>
          </div>
        </div>
        <Badge tone={cancelled ? "neutral" : badgeTone}>
          {statusLabels[task.status]}
        </Badge>
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
