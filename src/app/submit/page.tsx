"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Toast, useToast } from "@/components/feedback/toast";
import { AppShell } from "@/components/layout/app-shell";
import { PageHero } from "@/components/layout/page-hero";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { CheckboxLine, Input } from "@/components/ui/form-controls";

const supportedUrl =
  /(douyin\.com|bilibili\.com|b23\.tv|youtube\.com|youtu\.be)/i;

export default function SubmitPage() {
  const router = useRouter();
  const [links, setLinks] = useState([""]);
  const { message, showToast } = useToast();

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

  function submitTask() {
    const urls = links.map((link) => link.trim()).filter(Boolean);
    if (!urls.length) {
      showToast("请先粘贴至少一个视频链接");
      return;
    }
    if (urls.some((url) => !supportedUrl.test(url))) {
      showToast("有链接暂不受支持，请检查平台与格式");
      return;
    }
    sessionStorage.setItem("taskCount", String(urls.length));
    router.push("/progress");
  }

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
          action={<Badge tone="success">无需登录 · 即开即用</Badge>}
          description="粘贴 1–10 条抖音、Bilibili 或 YouTube 链接。我们会逐条生成逐字稿、结构化总结与精彩金句。"
          eyebrow="从视频到可用文字"
          title="把值得反复看的视频，变成随时可用的文案。"
        />
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
              私密、已删除或需要付费观看的视频可能无法读取。长视频会自动分段处理，不影响最终合并导出。
            </div>
            <div className="row row--between row--mobile-stack">
              <span className="meta">
                提交后可离开页面，任务会在浏览器中继续显示进度。
              </span>
              <Button onClick={submitTask}>开始提取文案</Button>
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
              <h3>支持平台</h3>
              <div className="platform-grid">
                <div className="platform-card">
                  <b>抖音</b>
                  <span>短视频与公开作品</span>
                </div>
                <div className="platform-card">
                  <b>Bilibili</b>
                  <span>单集与公开视频</span>
                </div>
                <div className="platform-card">
                  <b>YouTube</b>
                  <span>公开视频与字幕</span>
                </div>
              </div>
            </Card>
          </aside>
        </div>
      </section>
      <Toast message={message} />
    </AppShell>
  );
}
