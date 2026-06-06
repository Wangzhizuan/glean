"use client";

import { useState } from "react";
import { Toast, useToast } from "@/components/feedback/toast";
import { AppShell } from "@/components/layout/app-shell";
import { PageHero } from "@/components/layout/page-hero";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";

const workingTasks = [
  {
    platform: "YouTube",
    title: "Why great ideas need quiet time",
    duration: "12:08",
    status: "正在提炼金句",
    progress: 72,
    remaining: "01:26",
  },
  {
    platform: "抖音",
    title: "高效写作的三个日常练习",
    duration: "02:16",
    status: "正在识别语音",
    progress: 38,
    remaining: "00:42",
  },
];

export default function ProgressPage() {
  const [paused, setPaused] = useState(false);
  const { message, showToast } = useToast();

  function togglePaused() {
    setPaused((current) => {
      showToast(current ? "任务已继续" : "任务已暂停");
      return !current;
    });
  }

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
            <Button onClick={togglePaused} variant="secondary">
              {paused ? "继续全部任务" : "暂停全部任务"}
            </Button>
          }
          description="不同平台会并行处理。完成的文案可以立即查看，不必等待整个批次结束。"
          eyebrow="任务队列"
          title="三条视频正在变成文字。"
        />
        <div className="grid grid--content-sidebar">
          <Card as="article" panel>
            <div className="task">
              <div className="row row--between">
                <div className="row">
                  <div className="task__thumb">Bilibili</div>
                  <div>
                    <b>如何建立自己的知识输入系统</b>
                    <p className="meta">哔哩哔哩 · 18:42</p>
                  </div>
                </div>
                <Badge tone="success">已完成</Badge>
              </div>
              <div className="row row--between task__footer">
                <span className="meta">逐字稿 4,286 字 · 金句 8 条</span>
                <Button href="/detail">查看文案</Button>
              </div>
            </div>
            {workingTasks.map((task) => (
              <div className="task" key={task.title}>
                <div className="row row--between">
                  <div className="row">
                    <div className="task__thumb">{task.platform}</div>
                    <div>
                      <b>{task.title}</b>
                      <p className="meta">
                        {task.platform} · {task.duration}
                      </p>
                    </div>
                  </div>
                  <Badge tone="working">
                    {paused ? "已暂停" : task.status}
                  </Badge>
                </div>
                <div className="task__progress">
                  <Progress value={task.progress} />
                </div>
                <div className="row row--between task__meta">
                  <span className="meta mono">
                    {task.progress}% · 预计还需 {task.remaining}
                  </span>
                  <Button
                    onClick={() => showToast(`已取消：${task.title}`)}
                    variant="quiet"
                  >
                    取消
                  </Button>
                </div>
              </div>
            ))}
          </Card>
          <aside className="stack">
            <Card as="article" className="stack" panel>
              <h3>本批次概览</h3>
              <div className="row row--between">
                <span className="muted">任务总数</span>
                <strong className="mono">3</strong>
              </div>
              <div className="row row--between">
                <span className="muted">已完成</span>
                <strong className="mono">1</strong>
              </div>
              <div className="row row--between">
                <span className="muted">处理中</span>
                <strong className="mono">2</strong>
              </div>
              <div className="row row--between">
                <span className="muted">失败</span>
                <strong className="mono">0</strong>
              </div>
            </Card>
            <div className="hint">
              关闭页面不会删除任务。再次打开“处理中”即可继续查看结果。
            </div>
          </aside>
        </div>
      </section>
      <Toast message={message} />
    </AppShell>
  );
}
