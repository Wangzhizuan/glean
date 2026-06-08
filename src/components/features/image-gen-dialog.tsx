"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  buildExtensionTarget,
  buildImagePrompt,
  buildProviderTarget,
  buildSearchEngineUrl,
  extractTranscript,
  getProviderMode,
  getRecentRatioIds,
  getRecentStyleIds,
  PROVIDER_LABEL,
  RATIO_PRESETS,
  rememberRatioId,
  rememberStyleId,
  SEARCH_ENGINE_TEMPLATE,
  STYLE_PRESETS,
  type ImageProvider,
} from "@/lib/image-prompt";
import type { TaskResult } from "@/lib/api-types";

interface ImageGenDialogProps {
  open: boolean;
  result: TaskResult;
  onClose: () => void;
  onNotice: (message: string) => void;
}

function sidePanelFeatures(): string {
  const width = Math.min(520, window.screen.availWidth);
  const height = window.screen.availHeight;
  const left = window.screen.availWidth - width;
  return [
    `width=${width}`,
    `height=${height}`,
    `left=${left}`,
    "top=0",
    "popup=yes",
    "noopener=no",
  ].join(",");
}

/**
 * 一键生图弹窗：同时提供三种跳转方式
 * - 方案一「自动生成」：打开 hash URL，由配套浏览器扩展自动填入并发送(无需回车、不受 431 限制)
 * - 通用「去 ChatGPT / Gemini」：?q= 预填或剪贴板降级(无需装扩展)
 * - 方案三「搜索引擎模式」：注册成浏览器自定义搜索引擎，部分浏览器可自动发送
 */
export function ImageGenDialog({
  open,
  result,
  onClose,
  onNotice,
}: ImageGenDialogProps) {
  const [context, setContext] = useState(() => extractTranscript(result));
  const [styleId, setStyleId] = useState(STYLE_PRESETS[0].id);
  const [ratio, setRatio] = useState(RATIO_PRESETS[0].id);
  const [extra, setExtra] = useState("");
  const [showSearchEngine, setShowSearchEngine] = useState(false);
  // 历史选过的风格/比例(最近在前),用于渲染下方快捷标签。
  const [recentStyles, setRecentStyles] = useState<string[]>(() =>
    getRecentStyleIds(),
  );
  const [recentRatios, setRecentRatios] = useState<string[]>(() =>
    getRecentRatioIds(),
  );

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const prompt = buildImagePrompt({
    title: result.metadata.title,
    context,
    styleHint:
      STYLE_PRESETS.find((s) => s.id === styleId)?.hint ?? STYLE_PRESETS[0].hint,
    ratio,
    extra,
  });

  // 手动模式当前会走哪种方式:short=直接预填(按回车) / long=需手动粘贴。
  const manualMode = getProviderMode(prompt);

  function openSidePanel(url: string): Window | null {
    return window.open(url, "shiju_image_panel", sidePanelFeatures());
  }

  // 每次真正发起生图时记录当前风格/比例,供下次以快捷标签展示。
  function recordSelections() {
    setRecentStyles(rememberStyleId(styleId));
    setRecentRatios(rememberRatioId(ratio));
  }

  function launchAuto(provider: ImageProvider) {
    recordSelections();
    const url = buildExtensionTarget(provider, prompt);
    const win = openSidePanel(url);
    if (!win) {
      onNotice("弹窗被拦截，请允许本站弹窗后重试");
      return;
    }
    win.focus();
    onNotice(`已打开 ${PROVIDER_LABEL[provider]}，若已装扩展将自动填入并发送`);
    onClose();
  }

  async function launchManual(provider: ImageProvider) {
    recordSelections();
    try {
      await navigator.clipboard.writeText(prompt);
    } catch {
      /* ignore */
    }
    const target = buildProviderTarget(provider, prompt);
    const win = openSidePanel(target.url);
    if (!win) {
      onNotice("弹窗被浏览器拦截，请允许本站弹窗后重试（prompt 已复制）");
      return;
    }
    win.focus();
    onNotice(
      target.mode === "prefilled"
        ? `已打开 ${PROVIDER_LABEL[provider]}，prompt 已预填，按回车即可`
        : `内容较长，完整 prompt 已复制到剪贴板。在 ${PROVIDER_LABEL[provider]} 输入框直接 Cmd/Ctrl+V 粘贴后回车`,
    );
    onClose();
  }

  async function launchSearchEngine(provider: ImageProvider) {
    recordSelections();
    try {
      await navigator.clipboard.writeText(prompt);
    } catch {
      /* ignore */
    }
    const win = openSidePanel(buildSearchEngineUrl(provider, prompt));
    if (win) win.focus();
    onNotice(`已用搜索引擎模式打开 ${PROVIDER_LABEL[provider]}`);
  }

  async function copyTemplate(provider: ImageProvider) {
    try {
      await navigator.clipboard.writeText(SEARCH_ENGINE_TEMPLATE[provider]);
      onNotice(`已复制 ${PROVIDER_LABEL[provider]} 搜索引擎模板`);
    } catch {
      onNotice("复制失败，请手动选择");
    }
  }

  return (
    <div
      aria-modal="true"
      className="imagegen-overlay"
      onClick={onClose}
      role="dialog"
    >
      <div
        className="imagegen-dialog card card--panel stack"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="row imagegen-dialog__head">
          <h3>为本文一键生图</h3>
          <button
            aria-label="关闭"
            className="imagegen-dialog__close"
            onClick={onClose}
            type="button"
          >
            x
          </button>
        </div>

        <label className="imagegen-field">
          <span className="imagegen-field__label">
            视频逐字稿（默认填入全文，可编辑）
          </span>
          <textarea
            className="imagegen-textarea"
            onChange={(event) => setContext(event.target.value)}
            rows={5}
            value={context}
          />
        </label>

        <div className="row row--mobile-stack imagegen-row">
          <label className="imagegen-field imagegen-field--grow">
            <span className="imagegen-field__label">画面风格</span>
            <select
              className="imagegen-select"
              onChange={(event) => setStyleId(event.target.value)}
              value={styleId}
            >
              {STYLE_PRESETS.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {preset.label}
                </option>
              ))}
            </select>
            {recentStyles.length > 0 && (
              <div className="imagegen-recent">
                {recentStyles
                  .map((id) => STYLE_PRESETS.find((p) => p.id === id))
                  .filter((p): p is (typeof STYLE_PRESETS)[number] => Boolean(p))
                  .map((preset) => (
                    <button
                      className={`imagegen-recent__chip${
                        styleId === preset.id
                          ? " imagegen-recent__chip--active"
                          : ""
                      }`}
                      key={preset.id}
                      onClick={() => setStyleId(preset.id)}
                      type="button"
                    >
                      {preset.label}
                    </button>
                  ))}
              </div>
            )}
          </label>
          <label className="imagegen-field imagegen-field--grow">
            <span className="imagegen-field__label">画面比例</span>
            <select
              className="imagegen-select"
              onChange={(event) => setRatio(event.target.value)}
              value={ratio}
            >
              {RATIO_PRESETS.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {preset.label}
                </option>
              ))}
            </select>
            {recentRatios.length > 0 && (
              <div className="imagegen-recent">
                {recentRatios
                  .map((id) => RATIO_PRESETS.find((p) => p.id === id))
                  .filter((p): p is (typeof RATIO_PRESETS)[number] => Boolean(p))
                  .map((preset) => (
                    <button
                      className={`imagegen-recent__chip${
                        ratio === preset.id
                          ? " imagegen-recent__chip--active"
                          : ""
                      }`}
                      key={preset.id}
                      onClick={() => setRatio(preset.id)}
                      type="button"
                    >
                      {preset.label}
                    </button>
                  ))}
              </div>
            )}
          </label>
        </div>

        <label className="imagegen-field">
          <span className="imagegen-field__label">补充要求（选填）</span>
          <textarea
            className="imagegen-textarea"
            onChange={(event) => setExtra(event.target.value)}
            placeholder="例如：暖色调、有人物剪影、避免文字水印"
            rows={2}
            value={extra}
          />
        </label>

        <details className="imagegen-preview">
          <summary>预览将发送的 Prompt</summary>
          <pre className="imagegen-preview__body">{prompt}</pre>
        </details>

        <div className="imagegen-group">
          <div className="imagegen-group__title">
            自动生成 <span className="imagegen-tag">需装扩展 · 推荐</span>
          </div>
            <div className="row row--mobile-stack imagegen-actions">
            <Button onClick={() => launchAuto("chatgpt")}>
              自动发到 ChatGPT
            </Button>
            <Button onClick={() => launchAuto("gemini")} variant="secondary">
              自动发到 Gemini
            </Button>
          </div>
          <p className="imagegen-hint">
            打开官网后由扩展自动填入并发送，<b>无需回车、不受长度限制</b>。
            首次使用请先安装 <code>browser-extension/</code> 目录里的扩展。
          </p>
        </div>

        <div className="imagegen-group">
          <div className="imagegen-group__title">
            手动模式{" "}
            <span className="imagegen-tag imagegen-tag--quiet">无需扩展</span>
          </div>
          <div className="row row--mobile-stack imagegen-actions">
            <Button onClick={() => launchManual("chatgpt")} variant="secondary">
              去 ChatGPT
            </Button>
            <Button onClick={() => launchManual("gemini")} variant="secondary">
              去 Gemini
            </Button>
          </div>
                    <div
            className={`imagegen-status imagegen-status--${
              manualMode === "prefilled" ? "short" : "long"
            }`}
          >
            {manualMode === "prefilled" ? (
              <>
                <span className="imagegen-status__dot" />
                当前为<b>短内容</b>:点击后会<b>自动预填到输入框</b>,直接按回车即可发送。
              </>
            ) : (
              <>
                <span className="imagegen-status__dot" />
                当前为<b>长内容</b>:点击后会打开<b>空白输入框</b>,完整 prompt 已复制,<b>Cmd/Ctrl+V 粘贴</b>后回车。
              </>
            )}
          </div>
        </div>

        <div className="imagegen-group">
          <button
            className="imagegen-group__toggle"
            onClick={() => setShowSearchEngine((v) => !v)}
            type="button"
          >
            搜索引擎模式 {showSearchEngine ? "收起" : "展开"}
          </button>
          {showSearchEngine && (
            <div className="stack imagegen-search">
              <p className="imagegen-hint">
                把下面模板加到浏览器「自定义搜索引擎」中，部分浏览器(如 Vivaldi)
                通过地址栏触发时会<b>自动发送</b>。Chrome 仅预填。
              </p>
              <div className="imagegen-template-row">
                <code className="imagegen-template">
                  {SEARCH_ENGINE_TEMPLATE.chatgpt}
                </code>
                <Button
                  className="imagegen-copy"
                  onClick={() => copyTemplate("chatgpt")}
                  variant="quiet"
                >
                  复制
                </Button>
              </div>
              <div className="imagegen-template-row">
                <code className="imagegen-template">
                  {SEARCH_ENGINE_TEMPLATE.gemini}
                </code>
                <Button
                  className="imagegen-copy"
                  onClick={() => copyTemplate("gemini")}
                  variant="quiet"
                >
                  复制
                </Button>
              </div>
              <div className="row row--mobile-stack imagegen-actions">
                <Button
                  onClick={() => launchSearchEngine("chatgpt")}
                  variant="quiet"
                >
                  试用 ChatGPT
                </Button>
                <Button
                  onClick={() => launchSearchEngine("gemini")}
                  variant="quiet"
                >
                  试用 Gemini
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
