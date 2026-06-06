"use client";

import { useMemo, useState } from "react";
import { Toast, useToast } from "@/components/feedback/toast";
import { AppShell } from "@/components/layout/app-shell";
import { PageHero } from "@/components/layout/page-hero";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/form-controls";
import { downloadText } from "@/lib/download";

const records = [
  {
    id: "knowledge-system",
    title: "如何建立自己的知识输入系统",
    duration: "18:42",
    platform: "Bilibili",
    generatedAt: "06-07 10:24",
    content: "4,286 字 · 8 金句",
  },
  {
    id: "writing-practice",
    title: "高效写作的三个日常练习",
    duration: "02:16",
    platform: "抖音",
    generatedAt: "06-06 22:18",
    content: "682 字 · 5 金句",
  },
  {
    id: "quiet-time",
    title: "Why great ideas need quiet time",
    duration: "12:08",
    platform: "YouTube",
    generatedAt: "06-06 18:03",
    content: "3,120 字 · 6 金句",
  },
  {
    id: "content-product",
    title: "从零开始做内容产品",
    duration: "24:31",
    platform: "Bilibili",
    generatedAt: "06-05 14:36",
    content: "5,904 字 · 10 金句",
  },
];

export default function HistoryPage() {
  const [keyword, setKeyword] = useState("");
  const [platform, setPlatform] = useState("全部平台");
  const [selected, setSelected] = useState<string[]>([]);
  const { message, showToast } = useToast();

  const visibleRecords = useMemo(() => {
    const normalizedKeyword = keyword.trim().toLowerCase();
    return records.filter((record) => {
      const matchesKeyword =
        !normalizedKeyword ||
        `${record.title} ${record.platform}`
          .toLowerCase()
          .includes(normalizedKeyword);
      const matchesPlatform =
        platform === "全部平台" || platform === record.platform;
      return matchesKeyword && matchesPlatform;
    });
  }, [keyword, platform]);

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

  function exportSelected(format: "txt" | "word") {
    const selectedRecords = records.filter((record) =>
      selected.includes(record.id),
    );
    if (!selectedRecords.length) {
      showToast("请先选择要导出的记录");
      return;
    }
    const extension = format === "word" ? "doc" : "txt";
    const type =
      format === "word" ? "application/msword" : "text/plain;charset=utf-8";
    downloadText(
      `批量视频文案-${selectedRecords.length}条.${extension}`,
      selectedRecords.map((record) => record.title).join("\n"),
      type,
    );
    showToast(
      `已开始导出 ${selectedRecords.length} 条 ${
        format === "word" ? "Word" : "TXT"
      } 文案`,
    );
  }

  return (
    <AppShell
      action={<Button href="/submit">新建提取任务</Button>}
    >
      <section className="container">
        <PageHero
          action={<Badge tone="success">共 12 条文案</Badge>}
          description="筛选、复制或批量导出过去生成的视频文案。记录保存在当前浏览器中。"
          eyebrow="文案资料库"
          title="看过的内容，已经整理好了。"
        />
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
                <option>抖音</option>
                <option>Bilibili</option>
                <option>YouTube</option>
              </Select>
              <Select aria-label="生成时间">
                <option>最近 30 天</option>
                <option>最近 7 天</option>
                <option>全部时间</option>
              </Select>
            </div>
            <div className="row">
              <Button
                onClick={() => exportSelected("txt")}
                variant="secondary"
              >
                批量 TXT
              </Button>
              <Button onClick={() => exportSelected("word")}>
                批量 Word
              </Button>
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
                  <th>视频</th>
                  <th>平台</th>
                  <th>生成时间</th>
                  <th>内容</th>
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
                      <b>{record.title}</b>
                      <div className="meta">{record.duration}</div>
                    </td>
                    <td>{record.platform}</td>
                    <td className="mono">{record.generatedAt}</td>
                    <td>{record.content}</td>
                    <td>
                      <Badge tone="success">已完成</Badge>
                    </td>
                    <td>
                      <Button href="/detail" variant="quiet">
                        查看
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!visibleRecords.length && (
              <div className="empty-state">没有找到匹配的文案记录。</div>
            )}
          </div>
        </Card>
      </section>
      <Toast message={message} />
    </AppShell>
  );
}
