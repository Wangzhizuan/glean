# 拾句 · 一键生图助手（浏览器扩展 / 方案①）

让「拾句」网页点一下，就能在 ChatGPT / Gemini 官网**自动填入并发送**生图 prompt。
全程在浏览器内操作，复用你已登录的会员账号，**不调用任何 API、不产生费用**。

## 工作原理

1. 网页侧把完整 prompt 经 `encodeURIComponent` 放进目标平台 URL 的 **hash** 部分：
   - `https://chatgpt.com/#shiju=<prompt>`
   - `https://gemini.google.com/app#shiju=<prompt>`
   - hash 不会发送到服务器，因此**不受 HTTP 431 请求头长度限制**（彻底解决长文本问题）。
2. 本扩展的 content script 在对应官网页面读取 hash，等输入框出现 → 填入 → 自动点「发送」。

## 安装步骤（Chrome / Edge）

1. 打开 `chrome://extensions`
2. 右上角打开「开发者模式」
3. 点「加载已解压的扩展程序」，选择本目录 `browser-extension/`
4. 确认扩展已启用即可

> 安装后回到「拾句」详情页，点弹窗里的 **「⚡ 自动生成」** 按钮即可。

## 注意

- GPT / Gemini 改版时，发送按钮的选择器可能失效，届时更新 `content-*.js` 里的 `findSendButton()` 即可。
- 扩展仅在 `chatgpt.com` / `gemini.google.com` 注入，不收集任何数据。
