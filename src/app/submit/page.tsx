"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Toast, useToast } from "@/components/feedback/toast";
import { AppShell } from "@/components/layout/app-shell";
import { PageHero } from "@/components/layout/page-hero";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { CheckboxLine, Input } from "@/components/ui/form-controls";
import { ApiError, createBatch, getCapabilities } from "@/lib/api";
import type { Capabilities } from "@/lib/api-types";

const supportedUrl =
  /^https?:\/\/([^/]+\.)?(douyin\.com|bilibili\.com|b23\.tv|youtube\.com|youtu\.be)(\/|$)/i;

export default function SubmitPage() {
  const router = useRouter();
  const [links, setLinks] = useState([""]);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const { message, showToast } = useToast();

  useEffect(() => {
    getCapabilities()
      .then(setCapabilities)
      .catch(() => showToast("本地后端未启动，请运行 npm run dev:all"));
  }, [showToast]);

  function addLink() {
    if (links.length >= 10) {
      showToast("单次最多处理 10 条链接");
      return;
    }
    setLinks((current) => [...current, ""]);
  }

  function updateLink(index: number, value: string) {
    setLinks((current) =>
      current.map((link, linkIndex) => (linkIndex === index ? value : link)),
    );
  }

  function removeLink(index: number) {
    if (links.length === 1) {
      showToast("请至少保留一条链接");
      return;
    }
    setLinks((current) =>
      current.filter((_, linkIndex) => linkIndex !== index),
    );
  }

  async function submitTask() {
    const urls = links.map((link) => link.trim()).filter(Boolean);
    if (!urls.length) {
      showToast("请先粘贴至少一个视频链接");
      return;
    }
    if (urls.some((url) => !supportedUrl.test(url))) {
      showToast("有链接暂不受支持，请检查平台与格式");
      return;
    }

    setSubmitting(true);
    try {
      const batch = await createBatch(urls);
      localStorage.setItem("shiju:lastBatchId", batch.batchId);
      router.push(`/progress?batchId=${encodeURIComponent(batch.batchId)}`);
    } catch (error) {
      showToast(
        error instanceof ApiError ? error.message : "创建任务失败，请检查本地服务",
      );
      setSubmitting(false);
    }
  }

  const isDemo = capabilities?.processorMode === "demo";

  return (
    <AppShell
      action={
        <Button href="/history" variant="secondary">
          查看全部文案
        </Button>
      }
    >
      <section className="container">
        <PageHero
          action={
            <Badge tone={capabilities ? "success" : "warning"}>
              {capabilities
                ? "无需注册账号 · 内容保存在本机"
                : "正在连接本地服务"}
            </Badge>
          }
          description="粘贴 1–10 条抖音、Bilibili 或 YouTube 链接。任务和生成结果会保存到当前 Mac 本机。"
          eyebrow="从视频到可用文字"
          title="把值得反复看的视频，变成随时可用的文案。"
        />
        {isDemo && (
          <div className="system-notice system-notice--warning">
            <b>当前为演示处理模式</b>
            <span>
              任务、进度、历史、详情和导出均可完整运行，但不会下载或识别链接中的真实视频。
            </span>
          </div>
        )}
        <div className="grid grid--content-sidebar">
          <Card as="article" className="stack" panel>
            <div className="row row--between">
              <div>
                <h3>视频链接</h3>
                <p className="meta">支持混合平台批量提交，最多 10 条</p>
              </div>
              <Button onClick={addLink} variant="secondary">
                ＋ 添加链接
              </Button>
            </div>
            <div className="stack">
              {links.map((link, index) => (
                <div className="link-row" key={index}>
                  <span className="link-index">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <Input
                    aria-label={`第 ${index + 1} 个视频链接`}
                    autoFocus={index === links.length - 1 && index > 0}
                    onChange={(event) => updateLink(index, event.target.value)}
                    placeholder="粘贴抖音、Bilibili 或 YouTube 视频链接"
                    type="url"
                    value={link}
                  />
                  <Button
                    className="link-row__remove"
                    onClick={() => removeLink(index)}
                    variant="quiet"
                  >
                    移除
                  </Button>
                </div>
              ))}
            </div>
            <div className="hint">
              私密、已删除、付费或需要平台权限的视频不会被绕过限制。真实处理模式下，长视频会自动分段。
            </div>
            <div className="row row--between row--mobile-stack">
              <span className="meta">
                任务由本地 SQLite 保存，关闭页面后仍可恢复查看。
              </span>
              <Button
                disabled={submitting || !capabilities}
                onClick={submitTask}
              >
                {submitting ? "正在创建任务..." : "开始提取文案"}
              </Button>
            </div>
          </Card>
          <aside className="stack">
            <Card as="article" className="stack" panel>
              <h3>本次生成内容</h3>
              <CheckboxLine>视频逐字稿</CheckboxLine>
              <CheckboxLine>结构化内容总结</CheckboxLine>
              <CheckboxLine>精彩金句提炼</CheckboxLine>
            </Card>
            <Card as="article" className="stack" panel>
              <h3>本机能力</h3>
              <CapabilityLine
                available={capabilities?.dependencies.ytDlp.available}
                label="yt-dlp 视频解析"
              />
              <CapabilityLine
                available={capabilities?.dependencies.ffmpeg.available}
                label="FFmpeg 音频处理"
              />
              <CapabilityLine
                available={capabilities?.dependencies.mlxWhisper.available}
                label="mlx-whisper 本地转写"
              />
              <CapabilityLine
                available={capabilities?.dependencies.ollama.available}
                label="Ollama 本地总结"
              />
            </Card>
          </aside>
        </div>
      </section>
      <Toast message={message} />
    </AppShell>
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
