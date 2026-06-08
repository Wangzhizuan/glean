/**
 * 拾句 · Gemini 自动发送 content script
 *
 * Gemini 官网原生不支持 ?q= 预填，这里同样走 URL hash 传 prompt：
 *   gemini.google.com/app#shiju=<encoded>
 * content script 读取后填入 Gemini 的富文本输入框（rich-textarea / Quill），
 * 等发送按钮可用再点击。
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
    // Gemini 输入框：rich-textarea 内的 contenteditable / Quill 编辑器。
    return (
      document.querySelector("rich-textarea .ql-editor") ||
      document.querySelector('div[contenteditable="true"][role="textbox"]') ||
      document.querySelector("rich-textarea div[contenteditable='true']")
    );
  }

  function findSendButton() {
    return (
      document.querySelector('button[aria-label*="Send"]') ||
      document.querySelector('button[aria-label*="发送"]') ||
      document.querySelector('button.send-button') ||
      document.querySelector('button[mattooltip*="Send"]')
    );
  }

  function setEditableText(el, text) {
    el.focus();
    el.innerHTML = "";
    const lines = text.split("\n");
    lines.forEach((line) => {
      const p = document.createElement("p");
      p.textContent = line.length ? line : "​";
      el.appendChild(p);
    });
    el.dispatchEvent(new InputEvent("input", { bubbles: true }));
  }

  async function run() {
    const prompt = readPromptFromHash();
    if (!prompt) return;
    clearHash();

    const input = await waitFor(findInput, 15000);
    if (!input) {
      console.warn("[拾句] 未找到 Gemini 输入框");
      return;
    }

    setEditableText(input, prompt);

    const btn = await waitFor(() => {
      const b = findSendButton();
      return b && !b.disabled && b.getAttribute("aria-disabled") !== "true"
        ? b
        : null;
    }, 8000);

    if (btn) {
      btn.click();
      console.log("[拾句] 已自动发送 Gemini prompt");
    } else {
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
  window.addEventListener("hashchange", run);
})();
