# 拾句：本地图文文章提取（含飞书）实现计划

> 关联调研：[local-article-extraction-technical-research.md](file:///Users/bytedance/Desktop/project/my-open-design-project/docs/local-article-extraction-technical-research.md)
> 目标：在现有视频管线之外，新增「图文文章」处理通道，覆盖通用网页 / 微信公众号 / 小红书 / **飞书文档**，复用现有 Ollama 总结、SQLite 持久化与导出。

## 1. Summary

新增 `backend/app/article.py` Article Adapter 模块，把"取得正文"封装成 `extract_article(url, source) -> ArticleResult`；在 `main.py` 中扩展平台识别为「视频 / 文章」两类，Worker 根据类型分派到不同管线。文章管线只跑 `resolving → fetching → extracting → summarizing → completed`，跳过下载/ASR。

支持的来源（按优先级）：

| 优先级 | 来源 | 主要技术 |
| --- | --- | --- |
| P0 | 通用网页 / 博客 | trafilatura（静态） |
| P0 | 微信公众号（`mp.weixin.qq.com`） | trafilatura + lxml 兜底 `#js_content` |
| P0 | 飞书文档 / Wiki / Docx（`*.feishu.cn`、`*.larkoffice.com`） | Playwright + 用户 Chrome cookies |
| P1 | 小红书（`xiaohongshu.com`、`xhslink.com`） | curl-cffi + JSON 解析（兜底 Playwright） |

前端在「提交」页接受文章链接（与视频共用输入框）；详情页结构、导出格式和历史记录复用现有组件，仅文案标签做适配。

## 2. Current State Analysis

- 后端入口 [main.py](file:///Users/bytedance/Desktop/project/my-open-design-project/backend/app/main.py)：
  - `SUPPORTED_HOSTS`（[L39-49](file:///Users/bytedance/Desktop/project/my-open-design-project/backend/app/main.py#L39-L49)）只含 3 个视频平台，`identify_platform` 直接报 `UNSUPPORTED_URL`。
  - `Worker.process` 走 `_process_demo` 或 `_process_real`，后者强依赖 yt-dlp + mlx-whisper（[main.py L580-767](file:///Users/bytedance/Desktop/project/my-open-design-project/backend/app/main.py#L580-L767)）。
  - 阶段常量 `STAGES` 与 `ACTIVE_STATUSES` 都假设视频流水线。
  - `build_export` 已基于 `result["transcript"]["plainText"]` + `summary`/`quotes` 拼装导出文件，与来源无关，可直接复用。
- 视频管线 [pipeline.py](file:///Users/bytedance/Desktop/project/my-open-design-project/backend/app/pipeline.py)：`generate_summary`、`generate_quotes`、`translate_*` 完全基于"逐字稿纯文本"，无视频专属耦合，适合复用。
- 前端 [api-types.ts](file:///Users/bytedance/Desktop/project/my-open-design-project/src/lib/api-types.ts) `Platform` 写死 3 个；[video-url.ts](file:///Users/bytedance/Desktop/project/my-open-design-project/src/lib/video-url.ts) 只识别视频；[submit/page.tsx](file:///Users/bytedance/Desktop/project/my-open-design-project/src/app/submit/page.tsx) 文案/提示只面向视频；[format.ts](file:///Users/bytedance/Desktop/project/my-open-design-project/src/lib/format.ts) 状态标签只覆盖视频阶段。
- `requirements.txt` 仅含 fastapi / uvicorn / mlx-whisper，没有任何 HTML 解析依赖。

## 3. Proposed Changes

### 3.1 新增 `backend/app/article.py`（核心新模块）

**职责**：根据 URL 域名识别来源；用合适策略抓取 HTML；提取标题/作者/正文/Markdown；兜底重试。

数据结构：

```python
@dataclass
class ArticleResult:
    source: str          # web / wechat / xiaohongshu / feishu
    source_url: str
    canonical_url: str
    title: str
    author: str | None
    published_at: str | None  # ISO 字符串，可空
    plain_text: str
    markdown: str
    word_count: int
```

主函数：`extract_article(url: str, source: str) -> ArticleResult`

各 source 实现：

1. **`_extract_web(url)`（trafilatura 默认）**
   - 用 `trafilatura.fetch_url` 拉静态 HTML
   - 失败或正文 < 200 字 → 调用 `_render_with_playwright(url)` 兜底
   - `trafilatura.extract(html, output_format="json", with_metadata=True)` 一次拿到正文 + 元数据
   - 同时再 `extract(html, output_format="markdown", with_metadata=False)` 拿 Markdown

2. **`_extract_wechat(url)`**
   - 走 `_extract_web` 主路径（trafilatura 对公众号正文良好）
   - 若失败：`requests.get(url, headers={...})` + `lxml` 解析 `#js_content` 文本
   - 元数据从 `<meta property="og:title">`、`<meta name="author">` 取

3. **`_extract_feishu(url)`** ⭐ 新增重点
   - 飞书文档完全 SPA 渲染，trafilatura 拿不到正文 → **直接走 Playwright**
   - 用 `playwright.sync_api`，启动 Chromium：
     - 优先尝试 `playwright.context.add_cookies(...)`，从用户 Chrome 读 cookies（参考 yt-dlp 现有的 `cookies-from-browser` 模式，用 `browser_cookie3` 库读 `feishu.cn` / `larkoffice.com` 域 cookies）
     - 若无 cookies，以无头方式访问（仅适用于公开分享链接）
   - `page.goto(url, wait_until="networkidle")`
   - 等待选择器：`page.wait_for_selector(".docx-page-block, .doc-content, .lark-doc-render", timeout=15000)`
   - 提取正文：优先 `page.inner_text(".docx-page-block")` / `.lark-docx-page-block`；标题 `page.title()` 或 H1 文本
   - 再把 `page.content()` 交给 trafilatura 兜底拿 Markdown
   - 若被重定向到登录页（URL 含 `/accounts/login` 或正文 < 100 字）→ 抛 `ArticleError("FEISHU_LOGIN_REQUIRED", "飞书文档需登录访问，请先在 Chrome 登录飞书并打开过该文档")`

4. **`_extract_xiaohongshu(url)`** （P1 简化版）
   - `curl_cffi.requests.get(url, impersonate="chrome")`
   - 正则提取 `window.__INITIAL_STATE__ = {...};` 的 JS 对象，`json.loads`
   - 从 `note.noteDetailMap.<id>.note` 取 `title/desc/tagList/time/user.nickname`
   - 失败兜底：Playwright 渲染后再 trafilatura
   - 任何依赖（curl-cffi / playwright）缺失时抛 `ArticleError("DEP_MISSING", ...)`

公共工具：
- `_render_with_playwright(url, wait_selector=None) -> str` 单点维护
- `_load_browser_cookies(domain) -> list[dict]` 用 `browser_cookie3` 读 Chrome cookies；缺包时返回 `[]`
- `ArticleError(code, message)` 自定义异常，向上转成 HTTPException

可用性检测：

```python
def article_capabilities() -> dict:
    import importlib.util
    return {
        "trafilatura": {"available": importlib.util.find_spec("trafilatura") is not None},
        "playwright": {"available": importlib.util.find_spec("playwright") is not None},
        "curlCffi": {"available": importlib.util.find_spec("curl_cffi") is not None},
        "browserCookie3": {"available": importlib.util.find_spec("browser_cookie3") is not None},
    }
```

### 3.2 修改 `backend/app/main.py`

**(a) 扩展 host 表与平台识别**：

```python
ARTICLE_HOSTS = {
    "mp.weixin.qq.com": "wechat",
    "www.xiaohongshu.com": "xiaohongshu",
    "xiaohongshu.com": "xiaohongshu",
    "xhslink.com": "xiaohongshu",
}
FEISHU_HOST_SUFFIXES = (".feishu.cn", ".larkoffice.com", ".feishu-pre.cn")

def identify_platform(raw_url: str) -> tuple[str, str]:
    """Return (kind, platform). kind ∈ {'video','article'}."""
    # 视频平台优先
    if host in SUPPORTED_HOSTS: return ("video", SUPPORTED_HOSTS[host])
    if host in ARTICLE_HOSTS:   return ("article", ARTICLE_HOSTS[host])
    if any(host.endswith(s) for s in FEISHU_HOST_SUFFIXES): return ("article", "feishu")
    # 其他 http(s) 域名 → 通用网页
    return ("article", "web")
```

并把 `identify_platform` 的现有调用点（`create_batch`）改为接收元组并把 `kind` 也写入 task 表。

**(b) `tasks` 表新增列 `kind TEXT NOT NULL DEFAULT 'video'`**：
- 在 `init_database` 的 schema 后追加 `ALTER TABLE tasks ADD COLUMN kind TEXT NOT NULL DEFAULT 'video'`（用 `try/except` 兼容已存在数据库）。
- `task_to_dict` 暴露 `kind`。

**(c) Worker 分派**：

```python
def process(self, task_id):
    task_kind = ...  # SELECT kind FROM tasks
    if PROCESSOR_MODE == "demo":
        return self._process_demo(task_id)
    if task_kind == "article":
        return self._process_article(task_id)
    return self._process_real(task_id)
```

`_process_article` 流程（新方法）：
1. 读 task → `url`, `platform`（wechat/feishu/xiaohongshu/web）
2. `notify("resolving", 0.1)` → `extract_article(url, platform)` 期间分别推送 `fetching` (0.4)、`extracting` (0.7)
3. 用 `pipeline.generate_summary(article.plain_text, article.title)` 与 `pipeline.generate_quotes(...)`
   - `generate_quotes` 需要 `SubtitleSegment`：把整篇文章按段落（`split('\n\n')` 或定长切分）伪造成无时间戳的片段，`start_ms=end_ms=0`，复用现有逻辑
4. 组装 `result` JSON，与视频一致结构，但 `transcript.source = "article_<platform>"`、`metadata.platformLabel` 用文章标签、`durationMs = 0`
5. 写 `results` / `transcripts` / `generated_contents` / 更新 task 与 batch counts（与 `_process_real` 同套逻辑）

**(d) 新增/复用阶段常量**：

```python
ARTICLE_ACTIVE_STATUSES = {"queued","resolving","fetching","extracting","summarizing"}
ACTIVE_STATUSES = ACTIVE_STATUSES | ARTICLE_ACTIVE_STATUSES  # 合并
```

`update_batch_counts` 的 SQL CASE 列表也加上 `'fetching','extracting'`。

**(e) `/api/capabilities`** 返回值新增 `article` 子对象，前端按需展示：

```python
"article": article_capabilities(),
"sources": ["douyin","bilibili","youtube","wechat","xiaohongshu","feishu","web"]
```

**(f) `normalize_url`** 增加飞书短链兼容（去掉无关 query）：保留现状即可，飞书链接通常是规范的 `/docx/<token>` 或 `/wiki/<token>`。

### 3.3 `backend/requirements.txt`

新增（trafilatura 必装；其他文章可选，按需提示用户安装）：

```
trafilatura>=1.12.0
lxml>=5.0
playwright>=1.45.0
browser-cookie3>=0.19.0
curl-cffi>=0.7.0
```

### 3.4 前端类型与工具

**`src/lib/api-types.ts`**：
- `Platform` 扩展为 `"bilibili" | "youtube" | "douyin" | "wechat" | "xiaohongshu" | "feishu" | "web"`
- `TaskStatus` 增加 `"fetching" | "extracting"`
- `Task` 增加 `kind: "video" | "article"`
- `TaskResult.metadata` 增加 `kind: "video" | "article"` 与可选 `publishedAt: string | null`

**`src/lib/format.ts`**：补全 platform/status 标签：

```ts
platformLabels = { ..., wechat: "微信公众号", xiaohongshu: "小红书", feishu: "飞书文档", web: "网页文章" }
statusLabels  = { ..., fetching: "正在抓取页面", extracting: "正在提取正文" }
```

**`src/lib/video-url.ts` → 重命名职责**（不改文件名以减少改动面）：
- 增加 `detectArticlePlatform(url)`：识别 wechat / xiaohongshu / feishu / web
- 增加 `extractSupportedSourceUrls(value)`：合并视频 + 文章；如果不是任何视频平台但是 http(s) 链接，归为 `web`
- 提交页改为调用新函数

**`src/app/submit/page.tsx`**：
- 输入框 placeholder 与 hero 文案改为 "粘贴视频或文章链接（抖音/Bilibili/YouTube/公众号/小红书/飞书文档/任意网页）"
- `LinkDetection` 显示文章平台名
- "本机能力" 卡片增加文章相关行：trafilatura、Playwright、cookies（按 capabilities 渲染）
- 提交逻辑改用 `extractSupportedSourceUrls`

**`src/app/detail/page.tsx`** 与 **`progress/page.tsx`**：标签改为读取 `metadata.kind`/`task.kind`，文章不显示时长（`durationMs` 为 0 时显示发布时间或 "图文文章"）。

### 3.5 启动脚本与依赖准备

`scripts/dev.sh` 末尾在真实模式下新增检查：

```sh
if [ "$MODE" = "real" ]; then
  # 检查 trafilatura
  $PYTHON -c "import trafilatura" 2>/dev/null || echo "ℹ️  未安装 trafilatura，文章提取将不可用：pip3 install trafilatura"
  # 检查 playwright + chromium
  $PYTHON -c "import playwright" 2>/dev/null || echo "ℹ️  未安装 playwright（飞书/动态网页兜底需要）：pip3 install playwright && python3 -m playwright install chromium"
fi
```

不强制安装，避免首次启动卡住。

## 4. Assumptions & Decisions

1. **复用 task / batch / results 表**：通过 `kind` 字段区分；不另起表，避免破坏现有历史记录与导出。
2. **Demo 模式不实现文章分支**：演示模式继续走 `_process_demo` 即可（任何 URL 都会出 demo 内容）。文章真实处理只在 `SHIJU_PROCESSOR_MODE=real` 启用。
3. **飞书登录态**：使用 `browser_cookie3` 从用户本机 Chrome 读 `feishu.cn`/`larkoffice.com` cookies，无需用户配置。读不到 cookies 时退回无登录访问，并在失败信息中提示用户先在 Chrome 登录飞书。不实现 OAuth，符合"本地、零配置"的产品基调。
4. **Quotes 适配**：文章无时间戳，把段落按 ~200 字切成"伪片段"复用 `generate_quotes`，输出 `startMs=endMs=0`、`sourceSegmentIds=[paragraph_index]`。详情页若 `durationMs=0` 则隐藏时间戳显示。
5. **小红书**：本期仅做最小可用版（curl-cffi + 静态 JSON 解析），不接入 XHS-Downloader 子进程，留作后续。
6. **图片**：本期统一只取文字与 Markdown，不做图片本地化（与调研文档 §7 一致）。
7. **导出**：现有 `build_export` 逻辑直接复用，文章场景下"逐字稿"段落就是文章正文。
8. **错误码**：新增 `ARTICLE_FETCH_FAILED`、`ARTICLE_EMPTY`、`FEISHU_LOGIN_REQUIRED`、`ARTICLE_DEP_MISSING`，统一通过 `error_code/error_message` 写入 task。
9. **进度阶段**：article 用 `resolving(0.1) → fetching(0.4) → extracting(0.7) → summarizing(0.92) → completed(1.0)`，与调研文档 §5.3 一致。
10. **TS Platform 类型扩展**：直接扩 union；其他引用点（switch/key map）通过 `format.ts` 的全量 record 覆盖，无需逐文件改。

## 5. File-Level Change List

| 文件 | 改动 |
| --- | --- |
| `backend/app/article.py` | 新建：Adapter 抽象 + 4 个来源实现 + capabilities |
| `backend/app/main.py` | host 表、`identify_platform` 返回元组、`tasks.kind` 列、Worker 分派、新增 `_process_article`、`ACTIVE_STATUSES`、`/api/capabilities` 字段 |
| `backend/app/pipeline.py` | 暴露 `generate_summary` / `generate_quotes` / `_check_ollama_available`（已是模块级，无需改） |
| `backend/requirements.txt` | 新增 trafilatura/lxml/playwright/browser-cookie3/curl-cffi |
| `scripts/dev.sh` | 真实模式启动时温和提示文章依赖 |
| `src/lib/api-types.ts` | `Platform`、`TaskStatus`、`Task.kind`、`TaskResult.metadata.kind/publishedAt` |
| `src/lib/format.ts` | platform/status 标签补全 |
| `src/lib/video-url.ts` | 增加 `detectArticlePlatform`、`extractSupportedSourceUrls` |
| `src/app/submit/page.tsx` | 文案、提示、能力卡、提交校验切换到通用源 |
| `src/app/progress/page.tsx` | 标签兼容 article 阶段，文章任务隐藏时长 |
| `src/app/detail/page.tsx` | 标签兼容文章；时长 0 时改显发布时间或"图文文章" |
| `src/app/history/page.tsx` | 平台筛选项扩展；时长列文章显示「-」 |

## 6. Verification Steps

1. **静态检查**：`npm run lint && npm run build` 通过。
2. **后端启动**：`SHIJU_PROCESSOR_MODE=real npm run dev:real`，访问 `/api/capabilities`，看到 `article.trafilatura/playwright` 状态。
3. **通用网页**：粘贴一篇博客（如 `https://overreacted.io/...`），应得到正文 + 总结 + 金句。
4. **公众号**：粘贴任意公开 `mp.weixin.qq.com/s/...`，验证标题/作者/正文落地。
5. **飞书**：先在 Chrome 登录飞书并打开任意自己有权限的文档；后端读 cookies 后 Playwright 访问应能拿到正文。失败时返回明确登录提示。
6. **小红书**：粘贴一条 `xhslink.com/...`，能拿到标题与正文文字（图片忽略）；依赖缺失时报 `ARTICLE_DEP_MISSING`。
7. **历史 / 详情 / 导出**：文章任务在历史列表显示「微信公众号 / 飞书文档 / 网页文章」，时长列「-」；详情页内容总结/金句/正文三栏正常；TXT/MD 导出可直接打开。
8. **回归**：原有 3 个视频平台流程不受影响；demo 模式所有链接（含文章 host）仍按视频 demo 输出。

## 7. Out of Scope

- 公众号 / 小红书 / 飞书的图片本地化与下载
- 飞书 OAuth 与企业租户专属 host 自动适配
- 小红书完整 XHS-Downloader 子进程接入
- DOCX / PDF 导出格式（与现状一致，仍占位）
