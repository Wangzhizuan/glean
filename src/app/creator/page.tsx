"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Toast, useToast } from "@/components/feedback/toast";
import { AppShell } from "@/components/layout/app-shell";
import { PageHero } from "@/components/layout/page-hero";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/form-controls";
import { Progress } from "@/components/ui/progress";
import {
  ApiError,
  cancelCreatorJob,
  createCreatorJob,
  creatorEventsUrl,
  getCreatorCapabilities,
  getCreatorJob,
  retryCreatorJob,
  syncCreatorToFeishu,
} from "@/lib/api";
import type {
  CreatorCapabilities,
  CreatorJob,
  CreatorJobStatus,
  CreatorVideo,
} from "@/lib/api-types";
import { formatDuration } from "@/lib/format";

const JOB_STATUS_LABELS: Record<CreatorJobStatus, string> = {
  discovering: "正在抓取视频列表",
  processing: "正在逐条识别文案",
  transcribed: "识别完成，待同步",
  syncing: "正在写入飞书多维表格",
  completed: "已完成",
  cancelled: "已终止",
  failed: "处理失败",
};

const VIDEO_STATUS_LABELS: Record<string, string> = {
  pending: "等待",
  queued: "排队中",
  processing: "识别中",
  done: "已完成",
  failed: "失败",
  cancelled: "已终止",
};

function formatCount(value: number | null): string {
  if (value === null || value === undefined) return "-";
  if (value >= 10000) return `${(value / 10000).toFixed(1)}w`;
  return String(value);
}

// Extract #hashtags from a Douyin title and return the tags plus the
// title with those hashtags removed for cleaner display.
function splitTitleAndTags(raw: string | null): {
  title: string;
  tags: string[];
} {
  const text = raw ?? "";
  const tags: string[] = [];
  const tagPattern = /#([^\s#@]+)/g;
  let match: RegExpExecArray | null;
  while ((match = tagPattern.exec(text)) !== null) {
    const tag = match[1].trim();
    if (tag && !tags.includes(tag)) tags.push(tag);
  }
  const title = text.replace(/#[^\s#@]+/g, "").replace(/\s+/g, " ").trim();
  return { title: title || (raw ?? ""), tags };
}

export default function CreatorPage() {
  return (
    <Suspense fallback={<CreatorLoading />}>
      <CreatorContent />
    </Suspense>
  );
}

function CreatorContent() {
  const searchParams = useSearchParams();
  const { message, showToast } = useToast();
  const [capabilities, setCapabilities] = useState<CreatorCapabilities | null>(
    null,
  );
  const [inputType, setInputType] = useState<"url" | "name">("url");
  const [inputValue, setInputValue] = useState("");
  const [limit, setLimit] = useState(50);
  const [submitting, setSubmitting] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [job, setJob] = useState<CreatorJob | null>(null);
  const jobIdRef = useRef<string | null>(null);

  useEffect(() => {
    getCreatorCapabilities()
      .then(setCapabilities)
      .catch(() => showToast("本地后端未启动，请运行 npm run dev:all"));
  }, [showToast]);

  // Resume an existing job from ?jobId=
  useEffect(() => {
    const queryJobId = searchParams.get("jobId");
    if (queryJobId && !jobIdRef.current) {
      jobIdRef.current = queryJobId;
      getCreatorJob(queryJobId).then(setJob).catch(() => undefined);
    }
  }, [searchParams]);

  // Subscribe to SSE updates whenever we have a job id.
  const subscribe = useCallback((jobId: string) => {
    const source = new EventSource(creatorEventsUrl(jobId));
    source.addEventListener("creator.updated", (event) => {
      setJob(JSON.parse((event as MessageEvent).data) as CreatorJob);
    });
    source.addEventListener("creator.finished", () => source.close());
    source.onerror = () => source.close();
    return source;
  }, []);

  useEffect(() => {
    if (!job?.id) return;
    const source = subscribe(job.id);
    return () => source.close();
  }, [job?.id, subscribe]);

  async function submit() {
    const value = inputValue.trim();
    if (!value) {
      showToast(inputType === "url" ? "请粘贴博主主页链接" : "请输入博主名字");
      return;
    }
    setSubmitting(true);
    try {
      const created = await createCreatorJob({ input: value, inputType, limit });
      jobIdRef.current = created.id;
      setJob(created);
    } catch (error) {
      showToast(
        error instanceof ApiError ? error.message : "创建博主任务失败，请检查本地服务",
      );
    } finally {
      setSubmitting(false);
    }
  }

  async function sync() {
    if (!job) return;
    setSyncing(true);
    try {
      const updated = await syncCreatorToFeishu(job.id);
      setJob(updated);
      showToast("已开始同步到飞书多维表格");
    } catch (error) {
      showToast(error instanceof ApiError ? error.message : "同步飞书失败");
    } finally {
      setSyncing(false);
    }
  }

  async function retry() {
    if (!job) return;
    try {
      const updated = await retryCreatorJob(job.id);
      setJob(updated);
      showToast("已重新加入队列");
    } catch (error) {
      showToast(error instanceof ApiError ? error.message : "重试失败");
    }
  }

  async function cancel() {
    if (!job) return;
    if (!window.confirm("确定终止这个博主任务吗？正在进行的识别会被停止。")) {
      return;
    }
    try {
      const updated = await cancelCreatorJob(job.id);
      setJob(updated);
      showToast("已终止任务");
    } catch (error) {
      showToast(error instanceof ApiError ? error.message : "终止失败");
    }
  }

  const harvest = capabilities?.harvest;
  const feishu = capabilities?.feishu;
  const isDemo = capabilities?.processorMode === "demo";
  const notReady = harvest !== undefined && harvest.ready === false;

  return (
    <AppShell
      action={
        <Button href="/" variant="secondary">
          返回首页
        </Button>
      }
    >
      <section className="container">
        <PageHero
          action={
            <Badge tone={capabilities ? "success" : "warning"}>
              {capabilities ? "抓取与识别都在本机完成" : "正在连接本地服务"}
            </Badge>
          }
          description="粘贴一个抖音博主的主页链接（或输入博主名字），本机自动抓取其视频列表与点赞、评论、收藏、转发等指标，逐条识别文案，最后一键汇总写入飞书多维表格（含封面链接）。"
          eyebrow="博主批量提取"
          title="把一个博主的全部视频，变成一张可用的表。"
        />

        {isDemo && (
          <div className="system-notice system-notice--warning">
            <b>当前为演示处理模式</b>
            <span>博主批量抓取需要真实模式，请用 npm run dev:real 启动后端。</span>
          </div>
        )}
        {notReady && !isDemo && (
          <div className="system-notice system-notice--error">
            <b>抖音抓取尚未就绪</b>
            <span style={{ whiteSpace: "pre-line" }}>
              {harvest?.message ??
                "未检测到 Playwright 或本机抖音登录态。"}
            </span>
            <span className="meta">
              当前状态 · Playwright：
              {harvest?.playwright.available ? "已就绪" : "未安装"} · Chrome 抖音
              cookies：
              {harvest?.browserCookies.available
                ? `已读取 ${harvest.browserCookies.count} 条`
                : "未找到"}
            </span>
          </div>
        )}

        <div className="grid grid--content-sidebar">
          <Card as="article" className="stack" panel>
            <div>
              <h3>博主来源</h3>
              <p className="meta">
                推荐直接粘贴主页链接，最稳定；名字搜索为兜底能力，可能定位失败。
              </p>
            </div>
            <div className="creator-form">
              <Select
                aria-label="输入方式"
                onChange={(event) =>
                  setInputType(event.target.value as "url" | "name")
                }
                value={inputType}
              >
                <option value="url">主页链接</option>
                <option value="name">博主名字</option>
              </Select>
              <Input
                aria-label="博主链接或名字"
                onChange={(event) => setInputValue(event.target.value)}
                placeholder={
                  inputType === "url"
                    ? "https://www.douyin.com/user/MS4wLj..."
                    : "输入抖音博主名字（兜底能力，可能失败）"
                }
                value={inputValue}
              />
            </div>
            <div className="creator-form">
              <label className="creator-form__label" htmlFor="creator-limit">
                最多抓取
              </label>
              <Select
                aria-label="抓取条数"
                id="creator-limit"
                onChange={(event) => setLimit(Number(event.target.value))}
                value={String(limit)}
              >
                <option value="1">最近 1 条</option>
                <option value="3">最近 3 条</option>
                <option value="5">最近 5 条</option>
                <option value="10">最近 10 条</option>
                <option value="20">最近 20 条</option>
                <option value="50">最近 50 条</option>
                <option value="100">最近 100 条</option>
                <option value="200">最近 200 条</option>
              </Select>
            </div>
            <div className="hint">
              抓取走 Playwright + 本机 Chrome 登录态，识别走 yt-dlp +
              mlx-whisper，总结走本地 Ollama。仅抓取公开数据，结果保存在本机。
            </div>
            <div className="row row--between row--mobile-stack">
              <span className="meta">任务保存在本地，关闭页面后仍可恢复。</span>
              <Button
                disabled={submitting || !capabilities || Boolean(job)}
                onClick={submit}
              >
                {submitting ? "正在创建..." : "开始抓取并识别"}
              </Button>
            </div>
          </Card>

          <aside className="stack">
            <Card as="article" className="stack" panel>
              <h3>本机能力</h3>
              <CapabilityLine
                available={harvest?.playwright.available}
                label="Playwright 浏览器抓取"
              />
              <CapabilityLine
                available={harvest?.browserCookies.available}
                label="Chrome 抖音登录态"
              />
              <CapabilityLine
                available={feishu?.larkCli.available}
                label="lark-cli 飞书写入"
              />
            </Card>
            <div className="hint">
              飞书表会自动新建一张「抖音博主-昵称-文案库」，字段含标题、链接、封面、点赞、评论、收藏、转发、时长、发布时间、逐字稿、总结与金句。
            </div>
          </aside>
        </div>

        {job && (
          <CreatorJobPanel
            job={job}
            onCancel={cancel}
            onRetry={retry}
            onSync={sync}
            syncing={syncing}
          />
        )}
      </section>
      <Toast message={message} />
    </AppShell>
  );
}

function CreatorJobPanel({
  job,
  onCancel,
  onRetry,
  onSync,
  syncing,
}: {
  job: CreatorJob;
  onCancel: () => void;
  onRetry: () => void;
  onSync: () => void;
  syncing: boolean;
}) {
  const videos = job.videos ?? [];
  const total = job.discoveredCount || videos.length;
  const done = job.completedCount;
  const progress = total > 0 ? Math.round((done / total) * 100) : 0;
  const isActive = ["discovering", "processing", "syncing", "transcribed"].includes(
    job.status,
  );
  const canSync =
    job.status === "transcribed" ||
    job.status === "processing" ||
    job.status === "completed" ||
    (job.status === "failed" && videos.length > 0);
  const badgeTone: "success" | "warning" | "working" | "neutral" =
    job.status === "completed"
      ? "success"
      : job.status === "failed"
        ? "warning"
        : job.status === "cancelled"
          ? "neutral"
          : "working";

  return (
    <Card as="article" className="stack creator-panel" panel>
      <div className="row row--between row--mobile-stack">
        <div>
          <h3>{job.creatorName || "抖音博主"}</h3>
          {!(job.status === "completed" && job.bitableUrl) && (
            <p className="meta">
              {JOB_STATUS_LABELS[job.status]} · 已发现 {total} 条 · 已识别{" "}
              {done} 条
              {job.failedCount > 0 ? ` · 失败 ${job.failedCount} 条` : ""}
            </p>
          )}
        </div>
        <div className="row task__status">
          <Badge tone={badgeTone}>{JOB_STATUS_LABELS[job.status]}</Badge>
          {isActive && (
            <Button onClick={onCancel} variant="secondary">
              终止任务
            </Button>
          )}
          {job.status === "failed" && (
            <Button onClick={onRetry} variant="secondary">
              重试
            </Button>
          )}
        </div>
      </div>

      {job.error && <div className="task-error">{job.error.message}</div>}

      {total > 0 && job.status !== "completed" && (
        <div className="task__progress">
          <Progress value={progress} />
        </div>
      )}

      {job.status === "completed" && job.bitableUrl && (
        <div className="system-notice system-notice--success">
          <b>已写入飞书多维表格</b>
          <span className="meta">
            已发现 {total} 条 · 已识别 {done} 条
            {job.failedCount > 0 ? ` · 失败 ${job.failedCount} 条` : ""}
          </span>
          <a href={job.bitableUrl} rel="noreferrer" target="_blank">
            打开飞书多维表格 ↗
          </a>
        </div>
      )}

      {canSync && job.status !== "completed" && (
        <div className="row row--between row--mobile-stack">
          <span className="meta">
            {job.status === "processing"
              ? "识别进行中，也可以先同步已完成的部分。"
              : "识别完成，可以同步到飞书多维表格了。"}
          </span>
          <Button disabled={syncing} onClick={onSync}>
            {syncing ? "正在同步..." : "同步到飞书多维表格"}
          </Button>
        </div>
      )}

      {videos.length > 0 && (
        <div className="creator-table-wrap">
          <table className="creator-table">
            <colgroup>
              <col className="creator-table__col-cover" />
              <col className="creator-table__col-title" />
              <col className="creator-table__col-num" />
              <col className="creator-table__col-num" />
              <col className="creator-table__col-num" />
              <col className="creator-table__col-num" />
              <col className="creator-table__col-dur" />
              <col className="creator-table__col-status" />
            </colgroup>
            <thead>
              <tr>
                <th>封面</th>
                <th>标题 / 标签</th>
                <th>点赞</th>
                <th>评论</th>
                <th>收藏</th>
                <th>转发</th>
                <th>时长</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {videos.map((video) => (
                <CreatorVideoRow key={video.id} video={video} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function CreatorVideoRow({ video }: { video: CreatorVideo }) {
  const statusLabel =
    VIDEO_STATUS_LABELS[video.transcribeStatus] ?? video.transcribeStatus;
  const parsed = splitTitleAndTags(video.title);
  // Prefer backend tags (from Douyin's structured text_extra); fall back to
  // hashtags parsed from the raw title.
  const tags = video.tags?.length ? video.tags : parsed.tags;
  const title = parsed.title;
  return (
    <tr>
      <td className="creator-table__cover">
        {video.coverUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img alt="封面" loading="lazy" src={video.coverUrl} />
        ) : (
          <span className="meta">无</span>
        )}
      </td>
      <td className="creator-table__title">
        <a href={video.videoUrl} rel="noreferrer" target="_blank">
          {title || "（无标题）"}
        </a>
        {tags.length > 0 && (
          <div className="creator-table__tags">
            {tags.map((tag) => (
              <span className="creator-table__tag" key={tag}>
                #{tag}
              </span>
            ))}
          </div>
        )}
      </td>
      <td className="mono">{formatCount(video.likeCount)}</td>
      <td className="mono">{formatCount(video.commentCount)}</td>
      <td className="mono">{formatCount(video.collectCount)}</td>
      <td className="mono">{formatCount(video.shareCount)}</td>
      <td className="mono">{formatDuration(video.durationMs)}</td>
      <td>
        <span
          className={
            video.transcribeStatus === "done"
              ? "capability-ready"
              : video.transcribeStatus === "failed"
                ? "capability-missing"
                : "meta"
          }
        >
          {statusLabel}
          {video.transcribeStatus === "processing" && " ⋯"}
        </span>
        {video.kind === "image_text" && (
          <span className="creator-table__kind meta">图文</span>
        )}
        {video.transcribeStatus === "failed" && video.error?.message && (
          <div className="creator-table__error" title={video.error.message}>
            {video.error.message}
          </div>
        )}
      </td>
    </tr>
  );
}

function CapabilityLine({
  available,
  label,
}: {
  available: boolean | undefined;
  label: string;
}) {
  return (
    <div className="row row--between capability-line">
      <span>{label}</span>
      <span className={available ? "capability-ready" : "capability-missing"}>
        {available ? "已就绪" : "未安装"}
      </span>
    </div>
  );
}

function CreatorLoading() {
  return (
    <AppShell action={<span />}>
      <section className="container">
        <Card className="empty-state" panel>
          正在读取本地能力...
        </Card>
      </section>
    </AppShell>
  );
}
