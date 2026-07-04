"use client";

import { useCallback, useEffect, useState } from "react";
import { Toast, useToast } from "@/components/feedback/toast";
import { AppShell } from "@/components/layout/app-shell";
import { PageHero } from "@/components/layout/page-hero";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/form-controls";
import { Progress } from "@/components/ui/progress";
import {
  cancelCreatorJob,
  exportUrl,
  deleteTasks,
  getTasks,
  listCreatorJobs,
} from "@/lib/api";
import type { CreatorJob, CreatorJobStatus, Task } from "@/lib/api-types";
import {
  formatDateTime,
  formatDuration,
  platformLabels,
  statusLabels,
} from "@/lib/format";

const CREATOR_JOB_STATUS_LABELS: Record<CreatorJobStatus, string> = {
  discovering: "正在抓取列表",
  processing: "正在逐条识别",
  transcribed: "识别完成，待同步",
  syncing: "正在同步飞书",
  completed: "已完成",
  cancelled: "已终止",
  failed: "处理失败",
};

const CREATOR_ACTIVE = new Set<CreatorJobStatus>([
  "discovering",
  "processing",
  "syncing",
  "transcribed",
]);

function filterByTime(tasks: Task[], range: string): Task[] {
  if (range === "all") return tasks;
  const days = Number(range);
  const cutoff = new Date(Date.now() - days * 86_400_000).toISOString();
  return tasks.filter((t) => t.createdAt >= cutoff);
}

export default function HistoryPage() {
  const [keyword, setKeyword] = useState("");
  const [platform, setPlatform] = useState("全部平台");
  const [timeRange, setTimeRange] = useState("all");
  const [records, setRecords] = useState<Task[]>([]);
  const [creatorJobs, setCreatorJobs] = useState<CreatorJob[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const { message, showToast } = useToast();

  const loadRecords = useCallback(async () => {
    try {
      const [tasksResult, jobsResult] = await Promise.all([
        getTasks({
          platform: platform === "全部平台" ? undefined : platform,
          query: keyword || undefined,
        }),
        listCreatorJobs().catch(() => ({ items: [] as CreatorJob[] })),
      ]);
      setRecords(tasksResult.items);
      setCreatorJobs(jobsResult.items);
    } catch {
      showToast("无法读取本机历史记录，请启动本地服务");
    } finally {
      setLoading(false);
    }
  }, [keyword, platform, showToast]);

  useEffect(() => {
    const timer = setTimeout(() => void loadRecords(), 180);
    return () => clearTimeout(timer);
  }, [loadRecords]);

  // Poll while any creator job is still running so progress stays fresh.
  useEffect(() => {
    const hasActive = creatorJobs.some((job) => CREATOR_ACTIVE.has(job.status));
    if (!hasActive) return;
    const timer = setInterval(() => void loadRecords(), 2500);
    return () => clearInterval(timer);
  }, [creatorJobs, loadRecords]);

  async function cancelJob(jobId: string) {
    if (!window.confirm("确定终止这个博主任务吗？正在进行的识别会被停止。")) {
      return;
    }
    try {
      await cancelCreatorJob(jobId);
      showToast("已终止任务");
      await loadRecords();
    } catch {
      showToast("终止任务失败");
    }
  }

  // Child tasks of creator jobs use the job id as their batchId; hide them from
  // the flat list so the big grouped task represents them instead.
  const creatorJobIds = new Set(creatorJobs.map((job) => job.id));
  const flatRecords = records.filter(
    (record) => !creatorJobIds.has(record.batchId),
  );

  const visibleRecords = filterByTime(flatRecords, timeRange);
  const allVisibleSelected =
    visibleRecords.length > 0 &&
    visibleRecords.every((record) => selected.includes(record.id));

  function toggleAll(checked: boolean) {
    const visibleIds = visibleRecords.map((record) => record.id);
    setSelected((current) =>
      checked
        ? Array.from(new Set([...current, ...visibleIds]))
        : current.filter((id) => !visibleIds.includes(id)),
    );
  }

  function toggleRecord(id: string, checked: boolean) {
    setSelected((current) =>
      checked ? [...current, id] : current.filter((item) => item !== id),
    );
  }

  function exportSelected(format: "txt" | "md") {
    const completed = records.filter(
      (record) =>
        selected.includes(record.id) && record.status === "completed",
    );
    if (!completed.length) {
      showToast("请先选择已完成的文案");
      return;
    }
    completed.forEach((record, index) => {
      setTimeout(() => {
        const anchor = document.createElement("a");
        anchor.href = exportUrl(record.id, format);
        anchor.click();
      }, index * 150);
    });
    showToast(`正在导出 ${completed.length} 条 ${format.toUpperCase()} 文案`);
  }

  async function deleteSelected() {
    if (!selected.length) {
      showToast("请先勾选要删除的记录");
      return;
    }
    if (!window.confirm(`确定删除选中的 ${selected.length} 条记录？此操作不可恢复。`)) {
      return;
    }
    try {
      await deleteTasks(selected);
      setRecords((prev) => prev.filter((r) => !selected.includes(r.id)));
      showToast(`已删除 ${selected.length} 条记录`);
      setSelected([]);
    } catch {
      showToast("删除失败，请检查后端服务");
    }
  }

  return (
    <AppShell action={<Button href="/submit">新建提取任务</Button>}>
      <section className="container">
        <PageHero
          action={
            <Badge tone="success">共 {records.length} 条本地任务</Badge>
          }
          description="筛选、查看或批量导出过去生成的视频与文章文案。记录和结果保存在当前 Mac 本机。"
          eyebrow="文案资料库"
          title="看过的内容，已经整理好了。"
        />
        {creatorJobs.length > 0 && (
          <div className="creator-jobs-group">
            <div className="surface-section__head creator-jobs-group__head">
              <h2>博主批量任务</h2>
              <p>每个博主是一个大任务，点击可展开查看逐条视频，进行中可随时终止。</p>
            </div>
            <div className="stack">
              {creatorJobs.map((job) => (
                <CreatorJobCard
                  key={job.id}
                  job={job}
                  onCancel={() => cancelJob(job.id)}
                />
              ))}
            </div>
          </div>
        )}
        <Card as="article" panel>
          <div className="history-toolbar">
            <div className="history-filters">
              <Input
                className="history-search"
                onChange={(event) => setKeyword(event.target.value)}
                placeholder="搜索标题或平台"
                value={keyword}
              />
              <Select
                onChange={(event) => setPlatform(event.target.value)}
                value={platform}
              >
                <option>全部平台</option>
                <option value="douyin">抖音</option>
                <option value="bilibili">Bilibili</option>
                <option value="youtube">YouTube</option>
                <option value="xiaoyuzhou">小宇宙</option>
                <option value="wechat">微信公众号</option>
                <option value="xiaohongshu">小红书</option>
                <option value="feishu">飞书文档</option>
                <option value="web">网页文章</option>
              </Select>
              <Select
                aria-label="生成时间"
                onChange={(event) => setTimeRange(event.target.value)}
                value={timeRange}
              >
                <option value="all">全部时间</option>
                <option value="1">最近 1 天</option>
                <option value="3">最近 3 天</option>
                <option value="7">最近 7 天</option>
                <option value="14">最近 14 天</option>
                <option value="30">最近 30 天</option>
              </Select>
            </div>
            <div className="row">
              <Button
                disabled={!selected.length}
                onClick={deleteSelected}
                variant="secondary"
              >
                删除选中
              </Button>
              <Button onClick={() => exportSelected("txt")} variant="secondary">
                批量 TXT
              </Button>
              <Button onClick={() => exportSelected("md")}>批量 Markdown</Button>
            </div>
          </div>
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>
                    <input
                      aria-label="全选"
                      checked={allVisibleSelected}
                      className="table-checkbox"
                      onChange={(event) => toggleAll(event.target.checked)}
                      type="checkbox"
                    />
                  </th>
                  <th>标题</th>
                  <th>时长</th>
                  <th>平台</th>
                  <th>创建时间</th>
                  <th>进度</th>
                  <th>状态</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {visibleRecords.map((record) => (
                  <tr key={record.id}>
                    <td>
                      <input
                        aria-label={`选择${record.title}`}
                        checked={selected.includes(record.id)}
                        className="table-checkbox"
                        onChange={(event) =>
                          toggleRecord(record.id, event.target.checked)
                        }
                        type="checkbox"
                      />
                    </td>
                    <td>
                      <a
                        className="history-title-link"
                        href={record.canonicalUrl || record.sourceUrl}
                        rel="noreferrer"
                        target="_blank"
                        title={record.title}
                      >
                        <b>{record.title}</b>
                      </a>
                    </td>
                    <td className="mono">
                      {record.kind === "article" || !record.durationMs
                        ? "-"
                        : formatDuration(record.durationMs)}
                    </td>
                    <td>{platformLabels[record.platform] ?? record.platform}</td>
                    <td className="mono">{formatDateTime(record.createdAt)}</td>
                    <td>{Math.round(record.overallProgress * 100)}%</td>
                    <td>
                      <Badge
                        tone={
                          record.status === "completed"
                            ? "success"
                            : record.status === "failed"
                              ? "warning"
                              : "working"
                        }
                      >
                        {statusLabels[record.status]}
                      </Badge>
                    </td>
                    <td>
                      {record.status === "completed" ? (
                        <Button
                          href={`/detail?taskId=${encodeURIComponent(record.id)}`}
                          variant="quiet"
                        >
                          查看
                        </Button>
                      ) : (
                        <Button
                          href={`/progress?batchId=${encodeURIComponent(record.batchId)}`}
                          variant="quiet"
                        >
                          进度
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!visibleRecords.length && (
              <div className="empty-state">
                {loading ? "正在读取本地记录..." : "没有找到匹配的任务记录。"}
              </div>
            )}
          </div>
        </Card>
      </section>
      <Toast message={message} />
    </AppShell>
  );
}

function CreatorJobCard({
  job,
  onCancel,
}: {
  job: CreatorJob;
  onCancel: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const total = job.discoveredCount || job.videos?.length || 0;
  const done = job.completedCount;
  const progress = total > 0 ? Math.round((done / total) * 100) : 0;
  const active = CREATOR_ACTIVE.has(job.status);
  const badgeTone: "success" | "warning" | "working" | "neutral" =
    job.status === "completed"
      ? "success"
      : job.status === "failed"
        ? "warning"
        : job.status === "cancelled"
          ? "neutral"
          : "working";
  const videos = job.videos ?? [];

  return (
    <Card as="article" className="creator-job-card" panel>
      <div className="row row--between row--mobile-stack">
        <button
          className="creator-job-card__summary"
          onClick={() => setExpanded((value) => !value)}
          type="button"
        >
          <span className="creator-job-card__caret">{expanded ? "▾" : "▸"}</span>
          <span>
            <b>{job.creatorName || "抖音博主"}</b>
            <span className="meta">
              {CREATOR_JOB_STATUS_LABELS[job.status]} · 已发现 {total} 条 · 已识别{" "}
              {done} 条
              {job.failedCount > 0 ? ` · 失败 ${job.failedCount} 条` : ""}
            </span>
          </span>
        </button>
        <div className="row task__status">
          <Badge tone={badgeTone}>{CREATOR_JOB_STATUS_LABELS[job.status]}</Badge>
          <Button
            href={`/creator?jobId=${encodeURIComponent(job.id)}`}
            variant="quiet"
          >
            打开
          </Button>
          {active && (
            <Button onClick={onCancel} variant="secondary">
              终止
            </Button>
          )}
        </div>
      </div>

      {active && total > 0 && (
        <div className="creator-job-card__progress">
          <Progress value={progress} />
          <span className="meta mono">{progress}%</span>
        </div>
      )}

      {job.status === "completed" && job.bitableUrl && (
        <a
          className="creator-job-card__bitable"
          href={job.bitableUrl}
          rel="noreferrer"
          target="_blank"
        >
          打开飞书多维表格 ↗
        </a>
      )}

      {job.error && <div className="task-error">{job.error.message}</div>}

      {expanded && videos.length > 0 && (
        <ul className="creator-job-card__videos">
          {videos.map((video) => (
            <li key={video.id}>
              <a href={video.videoUrl} rel="noreferrer" target="_blank">
                {video.title || "（无标题）"}
              </a>
              <span
                className={
                  video.transcribeStatus === "done"
                    ? "capability-ready"
                    : video.transcribeStatus === "failed"
                      ? "capability-missing"
                      : "meta"
                }
              >
                {video.transcribeStatus === "done"
                  ? "已完成"
                  : video.transcribeStatus === "failed"
                    ? "失败"
                    : video.transcribeStatus === "processing"
                      ? "识别中"
                      : video.transcribeStatus === "cancelled"
                        ? "已终止"
                        : "排队中"}
              </span>
            </li>
          ))}
        </ul>
      )}
      {expanded && videos.length === 0 && (
        <div className="meta creator-job-card__empty">
          还没有抓到视频，请稍候或到「打开」页查看详情。
        </div>
      )}
    </Card>
  );
}
