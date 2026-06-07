"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Toast, useToast } from "@/components/feedback/toast";
import { AppShell } from "@/components/layout/app-shell";
import { PageHero } from "@/components/layout/page-hero";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { exportUrl, getTaskResult } from "@/lib/api";
import type { TaskResult } from "@/lib/api-types";
import {
  formatDateTime,
  formatDuration,
  formatTimestamp,
} from "@/lib/format";
import { cx } from "@/lib/class-names";

type TabId = "summary" | "transcript" | "quotes";

const tabs: Array<{ id: TabId; label: string }> = [
  { id: "summary", label: "内容总结" },
  { id: "transcript", label: "视频逐字稿" },
  { id: "quotes", label: "精彩金句" },
];

export default function DetailPage() {
  return (
    <Suspense fallback={<DetailLoading />}>
      <DetailContent />
    </Suspense>
  );
}

function DetailContent() {
  const searchParams = useSearchParams();
  const taskId = searchParams.get("taskId");
  const [activeTab, setActiveTab] = useState<TabId>("summary");
  const [result, setResult] = useState<TaskResult | null>(null);
  const [loading, setLoading] = useState(Boolean(taskId));
  const { message, showToast } = useToast();

  useEffect(() => {
    if (!taskId) return;
    getTaskResult(taskId)
      .then(setResult)
      .catch(() => showToast("文案尚未完成或本地服务未启动"))
      .finally(() => setLoading(false));
  }, [taskId, showToast]);

  const fullCopy = useMemo(() => {
    if (!result) return "";
    return [
      result.metadata.title,
      "",
      "内容总结",
      result.summary.overview,
      result.summary.coreThesis
        ? `\n核心论点\n${result.summary.coreThesis}`
        : "",
      result.summary.detailedSummary
        ? `\n详细总结\n${result.summary.detailedSummary}`
        : "",
      "",
      "精彩金句",
      ...result.quotes.map((quote) => `- ${quote.text}`),
      "",
      "逐字稿",
      result.transcript.plainText,
    ].join("\n");
  }, [result]);

  async function copyArticle() {
    try {
      await navigator.clipboard.writeText(fullCopy);
      showToast("已复制到剪贴板");
    } catch {
      showToast("复制失败，请手动选择文本");
    }
  }

  function exportArticle(format: "txt" | "srt" | "md" | "json") {
    if (!taskId) return;
    const anchor = document.createElement("a");
    anchor.href = exportUrl(taskId, format);
    anchor.click();
    showToast(`正在导出 ${format.toUpperCase()} 文件`);
  }

  if (loading) return <DetailLoading />;
  if (!result) {
    return (
      <AppShell
        action={
          <Button href="/history" variant="secondary">
            返回历史
          </Button>
        }
      >
        <section className="container">
          <Card className="empty-state stack" panel>
            <h3>没有可显示的文案</h3>
            <p>请从历史记录或已完成任务进入详情页。</p>
            <Button href="/history">查看历史记录</Button>
          </Card>
        </section>
        <Toast message={message} />
      </AppShell>
    );
  }

  return (
    <AppShell
      action={
        <Button href="/history" variant="secondary">
          返回历史
        </Button>
      }
    >
      <section className="container">
        <PageHero
          action={
            <div className="row row--mobile-stack detail-actions">
              <Button onClick={copyArticle} variant="secondary">
                复制整篇
              </Button>
              <Button onClick={() => exportArticle("txt")} variant="quiet">
                TXT
              </Button>
              <Button onClick={() => exportArticle("srt")} variant="quiet">
                SRT
              </Button>
              <Button onClick={() => exportArticle("md")} variant="quiet">
                Markdown
              </Button>
              <Button onClick={() => exportArticle("json")}>JSON</Button>
            </div>
          }
          description={`${formatDuration(result.metadata.durationMs)} · 逐字稿 ${result.transcript.wordCount} 字 · ${formatDateTime(result.metadata.generatedAt)} 生成`}
          eyebrow={`文案详情 · ${result.metadata.platformLabel}`}
          title={result.metadata.title}
        />
        {result.processor.mode === "demo" && (
          <div className="system-notice system-notice--warning">
            <b>演示结果</b>
            <span>{result.processor.notice}</span>
          </div>
        )}
        <a
          className="source-link source-link--detail"
          href={result.metadata.sourceUrl}
          rel="noreferrer"
          target="_blank"
        >
          查看原视频 ↗
        </a>
        <div aria-label="文案内容类型" className="tabs" role="tablist">
          {tabs.map((tab) => (
            <button
              aria-selected={activeTab === tab.id}
              className={cx("tab", activeTab === tab.id && "tab--active")}
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              role="tab"
              type="button"
            >
              {tab.label}
            </button>
          ))}
        </div>
        <Card as="article" className="detail-card" panel>
          {activeTab === "summary" && (
            <div className="detail-content stack">
              <h3>内容总结</h3>
              <p>{result.summary.overview}</p>
              {result.summary.coreThesis && (
                <>
                  <h3>核心论点</h3>
                  <div className="summary-thesis">
                    {result.summary.coreThesis}
                  </div>
                </>
              )}
              {result.summary.detailedSummary && (
                <>
                  <h3>详细总结</h3>
                  <p className="detailed-summary">
                    {result.summary.detailedSummary}
                  </p>
                </>
              )}
              <h3>关键观点</h3>
              <div className="stack">
                {result.summary.keyPoints.map((point, index) => (
                  <div className="hint" key={point.title}>
                    <b>
                      {String(index + 1).padStart(2, "0")} · {point.title}
                    </b>
                    <br />
                    {point.content}
                  </div>
                ))}
              </div>
              {Boolean(result.summary.contentStructure?.length) && (
                <>
                  <h3>内容结构</h3>
                  <div className="content-structure">
                    {result.summary.contentStructure?.map((section, index) => (
                      <div className="content-structure__item" key={`${section.section}-${index}`}>
                        <span className="mono">
                          {String(index + 1).padStart(2, "0")}
                        </span>
                        <div>
                          <b>{section.section}</b>
                          <p>{section.summary}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              )}
              {Boolean(result.summary.conclusions?.length) && (
                <>
                  <h3>核心结论</h3>
                  <ul className="action-list">
                    {result.summary.conclusions?.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </>
              )}
              <h3>可执行清单</h3>
              <ul className="action-list">
                {result.summary.actionItems.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
              {Boolean(result.summary.terms?.length) && (
                <>
                  <h3>重要术语</h3>
                  <div className="terms-grid">
                    {result.summary.terms?.map((item) => (
                      <div className="hint" key={item.term}>
                        <b>{item.term}</b>
                        <br />
                        {item.explanation}
                      </div>
                    ))}
                  </div>
                </>
              )}
              {Boolean(result.summary.targetAudience?.length) && (
                <>
                  <h3>适合谁看</h3>
                  <div className="audience-list">
                    {result.summary.targetAudience?.map((item) => (
                      <span className="badge" key={item}>
                        {item}
                      </span>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}
          {activeTab === "transcript" && (
            <div>
              {result.transcript.segments.map((segment) => (
                <div className="transcript-segment" key={segment.index}>
                  <span className="timestamp">
                    {formatTimestamp(segment.startMs)}
                  </span>
                  <p>{segment.text}</p>
                </div>
              ))}
            </div>
          )}
          {activeTab === "quotes" && (
            <div>
              {result.quotes.map((quote) => (
                <div className="quote quote--with-meta" key={quote.text}>
                  <p>“{quote.text}”</p>
                  <span className="meta mono">
                    {formatTimestamp(quote.startMs)} ·{" "}
                    {quote.isPolished ? "AI 润色" : "原话摘录"}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Card>
      </section>
      <Toast message={message} />
    </AppShell>
  );
}

function DetailLoading() {
  return (
    <AppShell action={<span />}>
      <section className="container">
        <Card className="empty-state" panel>
          正在读取本地文案...
        </Card>
      </section>
    </AppShell>
  );
}
