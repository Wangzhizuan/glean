"use client";

import { useState } from "react";
import { Toast, useToast } from "@/components/feedback/toast";
import { AppShell } from "@/components/layout/app-shell";
import { PageHero } from "@/components/layout/page-hero";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { downloadText } from "@/lib/download";
import { cx } from "@/lib/class-names";

type TabId = "summary" | "transcript" | "quotes";

const tabs: Array<{ id: TabId; label: string }> = [
  { id: "summary", label: "内容总结" },
  { id: "transcript", label: "视频逐字稿" },
  { id: "quotes", label: "精彩金句" },
];

const fullCopy = `内容总结
这期视频讨论了如何把零散的信息消费，转化为能够长期积累、持续调用的个人知识系统。核心不在于收藏更多内容，而在于明确输入目的、压缩信息并建立稳定的回顾机制。

三个关键观点
01 · 先提出问题，再开始输入
带着具体问题阅读或观看，能显著减少“看过但没有留下”的无效输入。
02 · 用自己的语言完成一次压缩
摘抄只是保存，转述才是理解。每次输入后，用三句话写出结论、依据与行动。
03 · 让笔记进入真实任务
知识只有在写作、决策或讨论中被调用，才会从资料变成能力。

可执行清单
本周选择一个正在解决的问题；每天只收集与它相关的三条材料；周末把材料整理成一页主题笔记，并至少在一次输出中引用它。`;

const transcript = [
  [
    "00:00",
    "我们每天都会看到很多信息，但真正能留下来的非常少。问题往往不是输入不够，而是输入之前没有明确自己想解决什么。",
  ],
  [
    "02:14",
    "收藏夹给人的感觉是拥有了知识，可它更像一个没有索引的仓库。你需要做的第一件事，是把内容放回一个具体问题里。",
  ],
  [
    "06:38",
    "我习惯在看完一段内容后，强迫自己只写三句话：作者的结论是什么，为什么，以及这件事接下来会改变我的哪个行动。",
  ],
  [
    "12:05",
    "笔记系统不是为了让笔记变得漂亮，而是为了在你写文章、做方案、跟人讨论的时候，能更快地找到已经思考过的东西。",
  ],
  [
    "17:26",
    "所以最小的闭环不是输入到收藏，而是问题、输入、转述、调用。只要这个循环跑起来，你的知识系统就会慢慢长出来。",
  ],
];

const quotes = [
  "“收藏只是把信息留下，转述才是把理解留下。”",
  "“知识系统的价值，不在于存了多少，而在于需要时能否被调用。”",
  "“先有问题，再有输入；先有转述，再有积累。”",
  "“最小的学习闭环，是让一个观点真正改变下一次行动。”",
];

export default function DetailPage() {
  const [activeTab, setActiveTab] = useState<TabId>("summary");
  const { message, showToast } = useToast();

  async function copyArticle() {
    try {
      await navigator.clipboard.writeText(fullCopy);
      showToast("已复制到剪贴板");
    } catch {
      showToast("复制失败，请手动选择文本");
    }
  }

  function exportArticle(format: "txt" | "word") {
    const extension = format === "word" ? "doc" : "txt";
    downloadText(
      `视频文案.${extension}`,
      fullCopy,
      format === "word"
        ? "application/msword"
        : "text/plain;charset=utf-8",
    );
    showToast(`正在导出 ${format === "word" ? "Word" : "TXT"} 文件`);
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
            <div className="row row--mobile-stack">
              <Button onClick={copyArticle} variant="secondary">
                复制整篇
              </Button>
              <Button onClick={() => exportArticle("txt")} variant="quiet">
                导出 TXT
              </Button>
              <Button onClick={() => exportArticle("word")}>
                导出 Word
              </Button>
            </div>
          }
          description="18:42 · 逐字稿 4,286 字 · 2026-06-07 生成"
          eyebrow="文案详情 · Bilibili"
          title="如何建立自己的知识输入系统"
        />
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
              <p>
                这期视频讨论了如何把零散的信息消费，转化为能够长期积累、持续调用的个人知识系统。核心不在于收藏更多内容，而在于明确输入目的、压缩信息并建立稳定的回顾机制。
              </p>
              <h3>三个关键观点</h3>
              <div className="stack">
                <div className="hint">
                  <b>01 · 先提出问题，再开始输入</b>
                  <br />
                  带着具体问题阅读或观看，能显著减少“看过但没有留下”的无效输入。
                </div>
                <div className="hint">
                  <b>02 · 用自己的语言完成一次压缩</b>
                  <br />
                  摘抄只是保存，转述才是理解。每次输入后，用三句话写出结论、依据与行动。
                </div>
                <div className="hint">
                  <b>03 · 让笔记进入真实任务</b>
                  <br />
                  知识只有在写作、决策或讨论中被调用，才会从资料变成能力。
                </div>
              </div>
              <h3>可执行清单</h3>
              <p>
                本周选择一个正在解决的问题；每天只收集与它相关的三条材料；周末把材料整理成一页主题笔记，并至少在一次输出中引用它。
              </p>
            </div>
          )}
          {activeTab === "transcript" && (
            <div>
              {transcript.map(([time, content]) => (
                <div className="transcript-segment" key={time}>
                  <span className="timestamp">{time}</span>
                  <p>{content}</p>
                </div>
              ))}
            </div>
          )}
          {activeTab === "quotes" && (
            <div>
              {quotes.map((quote) => (
                <div className="quote" key={quote}>
                  {quote}
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
