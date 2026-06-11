"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Toast, useToast } from "@/components/feedback/toast";
import { AppShell } from "@/components/layout/app-shell";
import { PageHero } from "@/components/layout/page-hero";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { CheckboxLine, Textarea } from "@/components/ui/form-controls";
import { ApiError, createBatch, getCapabilities } from "@/lib/api";
import type { Capabilities } from "@/lib/api-types";
import {
  detectSourcePlatform,
  extractSupportedSourceUrls,
  getSingleSourceUrl,
} from "@/lib/video-url";
import { platformLabels } from "@/lib/format";

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
    const extracted = extractSupportedSourceUrls(value);
    if (extracted.length > 1) {
      setLinks((current) => {
        const retained = current.filter((_, linkIndex) => linkIndex !== index);
        return [...retained, ...extracted].slice(0, 10);
      });
      showToast(`已从文本中识别并拆分 ${Math.min(extracted.length, 10)} 条链接`);
      return;
    }
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
    const urls = Array.from(
      new Set(
        links
          .flatMap((value) => extractSupportedSourceUrls(value))
          .filter(Boolean),
      ),
    );
    if (!urls.length) {
      showToast("请先粘贴至少一个链接");
      return;
    }
    if (links.some((value) => value.trim() && !getSingleSourceUrl(value))) {
      showToast("有输入未识别到唯一的支持链接，请检查后再提交");
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
  const feishuReadiness = capabilities?.feishu;
  const hasFeishuLink = links.some(
    (value) => detectSourcePlatform(value) === "feishu",
  );
  const showFeishuWarning =
    !isDemo &&
    feishuReadiness !== undefined &&
    feishuReadiness.ready === false;

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
          description="粘贴 1–10 条视频或文章链接，支持抖音、Bilibili、YouTube、小宇宙、微信公众号、小红书、飞书文档以及任意网页。任务和生成结果会保存到当前 Mac 本机。"
          eyebrow="从视频与文章到可用文字"
          title="把值得反复看的内容，变成随时可用的文案。"
        />
        {isDemo && (
          <div className="system-notice system-notice--warning">
            <b>当前为演示处理模式</b>
            <span>
              任务、进度、历史、详情和导出均可完整运行，但不会下载或识别链接中的真实视频。
            </span>
          </div>
        )}
        {showFeishuWarning && (
          <div
            className={
              hasFeishuLink
                ? "system-notice system-notice--error"
                : "system-notice system-notice--warning"
            }
          >
            <b>飞书文档识别尚未就绪</b>
            <span style={{ whiteSpace: "pre-line" }}>
              {feishuReadiness?.message ??
                "未检测到 lark-cli，也未在本机 Chrome 找到飞书登录态。"}
            </span>
            <span className="meta">
              当前状态 · lark-cli：
              {feishuReadiness?.larkCli.available ? "已安装" : "未安装"} · Chrome
              飞书 cookies：
              {feishuReadiness?.browserCookies.available
                ? `已读取 ${feishuReadiness.browserCookies.count} 条`
                : "未找到"}
            </span>
          </div>
        )}
        <div className="grid grid--content-sidebar">
          <Card as="article" className="stack" panel>
            <div className="row row--between">
              <div>
                <h3>视频或文章链接</h3>
                <p className="meta">
                  可直接粘贴链接或包含链接的分享文案，最多 10 条
                </p>
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
                  <div className="link-input-wrap">
                    <Textarea
                    aria-label={`第 ${index + 1} 个链接`}
                    autoFocus={index === links.length - 1 && index > 0}
                    onChange={(event) => updateLink(index, event.target.value)}
                    placeholder="粘贴视频或文章链接，或包含链接的整段分享文案"
                    rows={2}
                    value={link}
                    />
                    <LinkDetection value={link} />
                  </div>
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
              视频走 yt-dlp + mlx-whisper 本地转写，文章走 trafilatura / Playwright / lark-cli 提取正文，再统一交给本地 Ollama 生成总结与金句。私密、付费或需要登录的内容不会被绕过限制。
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
              <CheckboxLine>逐字稿 / 文章正文</CheckboxLine>
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
              <CapabilityLine
                available={capabilities?.article?.trafilatura.available}
                label="trafilatura 文章提取"
              />
              <CapabilityLine
                available={capabilities?.article?.playwright.available}
                label="Playwright 飞书 / 动态页"
              />
              <CapabilityLine
                available={capabilities?.article?.larkCli.available}
                label="lark-cli 飞书文档"
              />
              <CapabilityLine
                available={capabilities?.feishu?.browserCookies.available}
                label="Chrome 飞书登录态"
              />
              <CapabilityLine
                available={capabilities?.feishu?.ready}
                label="飞书文档整体就绪"
              />
            </Card>
          </aside>
        </div>
      </section>
      <Toast message={message} />
    </AppShell>
  );
}

function LinkDetection({ value }: { value: string }) {
  if (!value.trim()) {
    return <span className="link-detection meta">等待识别平台</span>;
  }
  const urls = extractSupportedSourceUrls(value);
  if (!urls.length) {
    return (
      <span className="link-detection link-detection--error">
        未识别到支持的链接
      </span>
    );
  }
  if (urls.length > 1) {
    return (
      <span className="link-detection link-detection--success">
        已识别 {urls.length} 条支持链接，将自动拆分
      </span>
    );
  }
  const platform = detectSourcePlatform(urls[0]);
  return (
    <span className="link-detection link-detection--success">
      已识别：{platform ? platformLabels[platform] : "支持平台"}
    </span>
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
