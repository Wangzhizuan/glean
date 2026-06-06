"use client";

import { useState } from "react";
import { Toast, useToast } from "@/components/feedback/toast";
import { AppShell } from "@/components/layout/app-shell";
import { PageHero } from "@/components/layout/page-hero";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  CheckboxLine,
  Input,
  Select,
} from "@/components/ui/form-controls";
import { Progress } from "@/components/ui/progress";

const colors = [
  ["背景", "#f5f4ed", "var(--color-background)", "var(--color-text)"],
  ["表面", "#faf9f5", "var(--color-surface)", "var(--color-text)"],
  ["暖表面", "#e8e6dc", "var(--color-surface-warm)", "var(--color-text)"],
  ["正文", "#141413", "var(--color-text)", "var(--color-accent-on)"],
  ["弱正文", "#5e5d59", "var(--color-muted)", "var(--color-accent-on)"],
  ["强调色", "#c96442", "var(--color-accent)", "var(--color-text)"],
  ["成功", "#17a34a", "var(--color-success)", "var(--color-text)"],
  ["危险", "#b53333", "var(--color-danger)", "var(--color-accent-on)"],
];

export function DesignSystemPreview() {
  const [progress, setProgress] = useState(72);
  const { message, showToast } = useToast();

  return (
    <AppShell
      action={
        <Button href="/" variant="secondary">
          返回首页
        </Button>
      }
    >
      <section className="container design-preview">
        <PageHero
          action={<Badge tone="working">仅开发环境</Badge>}
          description="集中检查设计令牌、字体层级、组件变体、表单状态与响应式布局。此页面不依赖服务端数据。"
          eyebrow="Development reference"
          title="设计系统预览"
        />

        <section className="design-preview__section">
          <h2>颜色</h2>
          <div className="swatch-grid">
            {colors.map(([label, value, variable, textColor]) => (
              <div
                className="swatch"
                key={variable}
                style={{ background: variable, color: textColor }}
              >
                <b>{label}</b>
                <span className="mono">{value}</span>
                <span className="mono">{variable}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="design-preview__section">
          <h2>字体</h2>
          <Card panel>
            <div className="type-sample">
              <span className="meta">Display / 64</span>
              <h1>让好内容真正留下来。</h1>
            </div>
            <div className="type-sample">
              <span className="meta">Heading / 52</span>
              <h2>从提交到下载</h2>
            </div>
            <div className="type-sample">
              <span className="meta">Heading / 25</span>
              <h3>结构化内容总结</h3>
            </div>
            <div className="type-sample">
              <span className="meta">Body / 16</span>
              <p>
                正文字体强调舒适阅读，并通过展示字体建立清晰的标题层级。
              </p>
            </div>
            <div className="type-sample mono">
              <span className="meta">Mono / 14</span>
              <p>72% · 预计还需 01:26</p>
            </div>
          </Card>
        </section>

        <section className="design-preview__section">
          <h2>按钮与状态</h2>
          <Card className="stack" panel>
            <div className="component-row">
              <Button onClick={() => showToast("主要操作已触发")}>
                主要按钮
              </Button>
              <Button variant="secondary">次要按钮</Button>
              <Button variant="quiet">安静按钮</Button>
              <Button variant="danger">危险按钮</Button>
              <Button disabled>禁用状态</Button>
            </div>
            <div className="component-row">
              <Badge>默认状态</Badge>
              <Badge tone="success">已完成</Badge>
              <Badge tone="working">处理中</Badge>
              <Badge tone="warning">需注意</Badge>
            </div>
          </Card>
        </section>

        <section className="design-preview__section">
          <h2>表单</h2>
          <div className="grid grid--content-sidebar">
            <Card className="stack" panel>
              <label>
                <span className="field__label">视频链接</span>
                <Input placeholder="粘贴视频链接" type="url" />
              </label>
              <label>
                <span className="field__label">平台</span>
                <Select defaultValue="Bilibili">
                  <option>抖音</option>
                  <option>Bilibili</option>
                  <option>YouTube</option>
                </Select>
              </label>
              <CheckboxLine>生成结构化内容总结</CheckboxLine>
            </Card>
            <Card className="stack" panel>
              <h3>处理进度</h3>
              <Progress value={progress} />
              <div className="component-row">
                <Button
                  onClick={() =>
                    setProgress((current) => Math.min(100, current + 10))
                  }
                  variant="secondary"
                >
                  增加 10%
                </Button>
                <span className="meta mono">{progress}%</span>
              </div>
              <div className="hint">
                提示信息使用暖灰表面，保持低干扰但仍清晰可见。
              </div>
            </Card>
          </div>
        </section>

        <section className="design-preview__section">
          <h2>卡片与布局</h2>
          <div className="surface-grid">
            {["基础卡片", "内容卡片", "操作卡片", "响应式卡片"].map(
              (title, index) => (
                <Card className="stack" key={title} panel>
                  <span className="surface-card__number">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <h3>{title}</h3>
                  <p className="muted">
                    使用统一边框、圆角与阴影，并通过组合类控制内部间距。
                  </p>
                </Card>
              ),
            )}
          </div>
        </section>
      </section>
      <Toast message={message} />
    </AppShell>
  );
}
