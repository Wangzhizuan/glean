"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Toast, useToast } from "@/components/feedback/toast";
import { AppShell } from "@/components/layout/app-shell";
import { PageHero } from "@/components/layout/page-hero";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/form-controls";
import { exportUrl, getTasks } from "@/lib/api";
import type { Task } from "@/lib/api-types";
import {
  formatDateTime,
  formatDuration,
  platformLabels,
  statusLabels,
} from "@/lib/format";

export default function HistoryPage() {
  const [keyword, setKeyword] = useState("");
  const [platform, setPlatform] = useState("全部平台");
  const [records, setRecords] = useState<Task[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const { message, showToast } = useToast();

  const loadRecords = useCallback(async () => {
    try {
      const result = await getTasks({
        platform: platform === "全部平台" ? undefined : platform,
        query: keyword || undefined,
      });
      setRecords(result.items);
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

  const visibleRecords = useMemo(() => records, [records]);
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

  return (
    <AppShell action={<Button href="/submit">新建提取任务</Button>}>
      <section className="container">
        <PageHero
          action={
            <Badge tone="success">共 {records.length} 条本地任务</Badge>
          }
          description="筛选、查看或批量导出过去生成的视频文案。记录和结果保存在当前 Mac 本机。"
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
                <option value="douyin">抖音</option>
                <option value="bilibili">Bilibili</option>
                <option value="youtube">YouTube</option>
              </Select>
              <Select aria-label="生成时间">
                <option>全部时间</option>
                {/* TODO(history-filter): add createdFrom/createdTo API filters. */}
                <option disabled>最近 7 天（TODO）</option>
                <option disabled>最近 30 天（TODO）</option>
              </Select>
            </div>
            <div className="row">
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
                  <th>视频</th>
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
                      <b>{record.title}</b>
                      <div className="meta">
                        {formatDuration(record.durationMs)}
                      </div>
                      <a
                        className="source-link"
                        href={record.canonicalUrl || record.sourceUrl}
                        rel="noreferrer"
                        target="_blank"
                      >
                        打开原视频 ↗
                      </a>
                    </td>
                    <td>{platformLabels[record.platform]}</td>
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
