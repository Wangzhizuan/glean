/**
 * 拾句 · ChatGPT 自动发送 content script
 *
 * 触发约定（与网页侧 image-bridge.ts 保持一致）：
 *   1) 网页把完整 prompt 写入「拾句」自己域名下不可行（跨域 localStorage 不通），
 *      因此改为把 prompt 通过 URL hash 传递：chatgpt.com/?#shiju=<encoded>
 *      —— hash 不会发到服务器，绕开了 HTTP 431 的请求头长度限制。
 *   2) content script 读取 hash 里的 prompt，等输入框出现，填入并点发送。
 */
(function () {
  "use strict";

  const FLAG = "shiju=";

  function readPromptFromHash() {
    const hash = location.hash || "";
    const idx = hash.indexOf(FLAG);
    if (idx === -1) return null;
    try {
      return decodeURIComponent(hash.slice(idx + FLAG.length));
    } catch (e) {
      return null;
    }
  }

  function clearHash() {
    // 发送后清掉 hash，避免刷新重复发送。
    history.replaceState(null, "", location.pathname + location.search);
  }

  function waitFor(selectorFn, timeoutMs) {
    return new Promise((resolve) => {
      const start = Date.now();
      const tick = () => {
        const el = selectorFn();
        if (el) return resolve(el);
        if (Date.now() - start > timeoutMs) return resolve(null);
        requestAnimationFrame(tick);
      };
      tick();
    });
  }

  function findInput() {
    // ChatGPT 输入框：优先 ProseMirror 富文本，其次 textarea。
    return (
      document.querySelector("div.ProseMirror#prompt-textarea") ||
      document.querySelector("#prompt-textarea") ||
      document.querySelector('textarea[data-id]') ||
      document.querySelector("main textarea")
    );
  }

  function findSendButton() {
    return (
      document.querySelector('button[data-testid="send-button"]') ||
      document.querySelector('button[aria-label*="Send"]') ||
      document.querySelector('button[aria-label*="发送"]')
    );
  }

  function setProseMirrorText(el, text) {
    el.focus();
    // ProseMirror 需要用 paste/input 事件触发，直接赋 textContent 也能显示。
    el.innerHTML = "";
    const lines = text.split("\n");
    lines.forEach((line) => {
      const p = document.createElement("p");
      p.textContent = line.length ? line : "";
      el.appendChild(p);
    });
    el.dispatchEvent(new InputEvent("input", { bubbles: true }));
  }

  function setTextareaText(el, text) {
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype,
      "value"
    ).set;
    setter.call(el, text);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }

  async function run() {
    const prompt = readPromptFromHash();
    if (!prompt) return;
    clearHash();

    const input = await waitFor(findInput, 15000);
    if (!input) {
      console.warn("[拾句] 未找到 ChatGPT 输入框");
      return;
    }

    if (input.classList.contains("ProseMirror")) {
      setProseMirrorText(input, prompt);
    } else {
      setTextareaText(input, prompt);
    }

    // 等发送按钮变为可用再点击。
    const btn = await waitFor(() => {
      const b = findSendButton();
      return b && !b.disabled ? b : null;
    }, 8000);

    if (btn) {
      btn.click();
      console.log("[拾句] 已自动发送 ChatGPT prompt");
    } else {
      // 兜底：模拟回车
      input.dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "Enter",
          code: "Enter",
          bubbles: true,
        })
      );
    }
  }

  run();
  // SPA 路由变化时也尝试一次。
  window.addEventListener("hashchange", run);
})();
