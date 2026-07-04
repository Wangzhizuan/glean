# 拾句：本地图文文章（公众号 / 小红书 / 通用网页）提取技术调研

> 文档状态：技术调研 / 方案设计（待评审）
> 编写日期：2026-06-07
> 目标环境：MacBook Pro 16 英寸，Apple M3 Pro，36 GB 统一内存
> 关联文档：`docs/local-video-copy-backend-technical-design.md`（视频文案提取）

## 1. 文档目的

当前「拾句」已支持抖音、Bilibili、YouTube 三类**视频**链接，通过 yt-dlp + mlx-whisper + Ollama 在本机生成逐字稿、总结与金句。

本文调研把能力从"视频"扩展到"**图文文章**"，覆盖三类来源：

1. **微信公众号文章**（`mp.weixin.qq.com/s/...`）；
2. **小红书笔记**（`xiaohongshu.com/explore/...`、`xhslink.com/...`）；
3. **通用网页文章 / 博客**（任意新闻、博客、文档页面）。

最终目标：用户粘贴文章链接后，本机自动得到**标题、作者、发布时间、正文文案**，并复用现有的总结 / 金句 / 导出流程。

### 1.1 核心约束（与现有视频方案一致）

- 不调用付费抓取或解析 API；
- 文章正文、图片和生成结果默认只保存在本机；
- 优先使用开源软件和可本地运行的方案；
- 解析器可独立替换，平台规则变化时局部改动即可；
- 尊重版权与平台规则，仅用于个人学习、研究与备份。

## 2. 结论摘要

三类来源的**技术难度差异极大**，不能用同一套方案。推荐按来源分层：

| 来源 | 难度 | 推荐方案 | 是否需要登录态 |
| --- | --- | --- | --- |
| 通用网页 / 博客 / 新闻 | 低 | **trafilatura**（纯本地，无需浏览器） | 否 |
| 微信公众号文章 | 中 | trafilatura + Playwright 渲染兜底 | 单篇通常否 |
| 小红书笔记 | 高 | Playwright + curl-cffi，解析页面内嵌 JSON | 多数否，部分需 Cookie |

推荐的统一处理主流程：

```text
提交文章链接
  -> 识别来源（公众号 / 小红书 / 通用网页）
  -> 选择对应 Article Adapter 抓取 HTML（必要时浏览器渲染）
  -> 提取正文与元数据（trafilatura / 内嵌 JSON 解析）
  -> 清洗为纯文本 + Markdown
  -> 复用现有 Ollama 总结 / 金句
  -> SQLite 保存结果
  -> 前端查看、复制、导出
```

**核心判断**：

- 通用网页占绝大多数场景，trafilatura 一个库就能纯本地、离线、无浏览器解决，应作为默认引擎；
- 公众号正文藏在 `<div id="js_content">`，静态请求多数能拿到，少数动态渲染场景用 Playwright 兜底；
- 小红书反爬最强，正文在页面内嵌的 `window.__INITIAL_STATE__` JS 对象里，需要浏览器或 curl-cffi 模拟真实指纹，是投入产出比最低的一类，建议作为 P2 可选项。

## 3. 技术选型与开源依赖

### 3.1 通用网页正文提取：trafilatura（首选）

[trafilatura](https://github.com/adbar/trafilatura) 是目前综合表现最好的开源正文提取库之一，由柏林-勃兰登堡科学院开发，HuggingFace、IBM、微软研究院等均在使用。

选它的理由：

- **纯本地、纯 Python**，无需数据库、无需浏览器、可离线运行，完全符合本项目约束；
- 同时提取**正文 + 元数据**（标题、作者、日期、标签）+ 可选评论；
- 直接输出 **TXT / Markdown / JSON / XML**，与现有导出格式天然契合；
- 内部融合了 readability、jusText 等算法，在多个公开评测（ScrapingHub benchmark、Bevendorff 2023）中精度/召回平衡最好；
- 中文内容支持良好（评测语料含中文）。

安装与最简用法：

```bash
pip3 install trafilatura
```

```python
import trafilatura

downloaded = trafilatura.fetch_url(url)
# 纯文本
text = trafilatura.extract(downloaded)
# 带元数据的 Markdown（推荐）
md = trafilatura.extract(downloaded, output_format="markdown", with_metadata=True)
# 结构化 JSON（取 title/author/date/text）
data_json = trafilatura.extract(downloaded, output_format="json", with_metadata=True)
```

**备选库**（仅在 trafilatura 失败时作为兜底，不必都引入）：

| 库 | 特点 | 适用 |
| --- | --- | --- |
| [readability-lxml](https://github.com/buriy/python-readability) | Mozilla Readability 的 Python 实现，清理页面保留部分标记 | trafilatura 兜底 |
| [newspaper4k](https://github.com/AndyTheFactory/newspaper4k) | 偏新闻，多语言、附带 NLP | 新闻站点 |
| [goose3](https://github.com/goose3/goose3) | 精度高但召回偏低 | 特定站点 |

### 3.2 动态渲染兜底：Playwright（按需）

当静态 HTML 拿不到正文（JS 动态渲染、需要滚动加载）时，用浏览器渲染后再交给 trafilatura 解析。

```bash
pip3 install playwright
python3 -m playwright install chromium
```

```python
from playwright.sync_api import sync_playwright

def render_html(url: str, timeout_ms: int = 15000) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        html = page.content()
        browser.close()
        return html
```

代价：Chromium 体积约数百 MB，单次渲染比静态请求慢。因此**仅作兜底**，不作为默认路径。

### 3.3 反指纹请求：curl-cffi（小红书需要）

小红书等强反爬平台会校验 TLS / JA3 指纹，普通 `requests` 容易被拦。[curl-cffi](https://github.com/lexiforest/curl_cffi) 能模拟真实浏览器的 TLS 指纹。

```bash
pip3 install curl-cffi
```

```python
from curl_cffi import requests as cffi_requests

resp = cffi_requests.get(url, impersonate="chrome")
html = resp.text
```

## 4. 分来源实现方案

### 4.1 通用网页（P0，最高优先级）

直接 trafilatura 一步到位，必要时 Playwright 渲染兜底：

```text
fetch_url(静态) -> trafilatura.extract
  若正文为空或过短 -> Playwright 渲染 -> trafilatura.extract(html)
```

判定"过短"可设阈值（如正文 < 200 字视为失败，触发兜底）。这条路径覆盖新闻、博客、文档等绝大多数链接，无需登录态。

### 4.2 微信公众号文章（P1）

**正文位置**：公众号文章正文在 `<div id="js_content">`（少数模板为 `#js_article`）。多数情况下静态请求即可拿到完整正文 HTML，无需登录。

**元数据**：标题、作者、公众号名、发布时间可从页面 `<meta>` 标签或内嵌的 `window.cgiDataNew` JS 对象解析。

推荐策略（不引入登录态、不做批量爬取）：

```text
curl-cffi/requests 取 HTML
  -> 优先用 trafilatura 提取（对公众号正文效果良好）
  -> 失败时回退：lxml 定位 #js_content 文本
  -> 仍失败：Playwright 渲染后再解析
```

**边界与风险**：

- 阅读量、点赞、评论需要手机端登录态，**不在本方案范围**，仅取公开正文；
- 图片有防盗链（`Referer` 校验），如需本地化图片要在下载时带 `Referer: https://mp.weixin.qq.com`；本期可只取文字，图片留待后续；
- 严禁高频批量抓取他人公众号，仅支持用户主动粘贴的单篇链接。

参考开源项目（仅作思路借鉴，不直接依赖）：
- [wechat-article-to-markdown](https://github.com/jackwener/wechat-article-to-markdown)：单篇转 Markdown，含图片本地化；
- [wechat-article-exporter](https://github.com/wechat-article/wechat-article-exporter)：基于公众号后台 session 的导出器（属批量场景，本项目不采用）。

### 4.3 小红书笔记（P2，可选）

**难度最高**，反爬最严格。正文与元数据通常在页面内嵌的 `window.__INITIAL_STATE__` JS 对象中。

推荐策略：

```text
curl-cffi(impersonate=chrome) 取 HTML
  -> 正则/JS 解析提取 window.__INITIAL_STATE__
  -> 从中读取 note 的 title/desc/tag/time/author
  若被反爬拦截 -> Playwright 渲染（必要时注入浏览器 Cookie）
```

可借鉴的成熟开源项目：
- [XHS-Downloader](https://github.com/JoeanAmier/XHS-Downloader)：基于 httpx + curl-cffi，支持笔记正文、元数据、媒体下载，提供 CLI 与本地 REST API（`127.0.0.1:5556`），可作为独立子进程调用，避免自己维护反爬逻辑；
- [Spider_XHS / xhs](https://github.com/cv-cat/Spider_XHS)：封装 Web 端 API + 签名机制。

**建议**：小红书规则变化频繁、维护成本高，优先级最低。可先通过子进程调用 XHS-Downloader 的本地服务，把反爬维护成本外置，而不是在本项目内自研。

## 5. 与现有后端的集成设计

### 5.1 复用现状

现有视频流水线 `backend/app/pipeline.py` 的后半段（清洗 → Ollama 总结 / 金句 → 持久化）**与来源无关**，文章方案只需替换"取得纯文本"之前的环节。

视频管线产出 `SubtitleResult.plain_text`；文章管线只要同样产出一段 `plain_text` + 元数据，即可直接接入 `generate_summary` / 金句生成与 SQLite 存储，无需改动总结层。

### 5.2 新增 Article Adapter 抽象

建议在 `backend/app/` 下新增 `article.py`，与 `pipeline.py` 并列：

```python
@dataclass
class ArticleResult:
    source: str          # wechat / xiaohongshu / web
    title: str
    author: str
    published_at: str | None
    plain_text: str
    markdown: str
    word_count: int

def extract_article(url: str, source: str) -> ArticleResult: ...
```

来源识别可扩展现有 `SUPPORTED_HOSTS`（见 [main.py](file:///Users/bytedance/Desktop/project/glean/backend/app/main.py#L39)）：

```python
ARTICLE_HOSTS = {
    "mp.weixin.qq.com": "wechat",
    "xiaohongshu.com": "xiaohongshu",
    "www.xiaohongshu.com": "xiaohongshu",
    "xhslink.com": "xiaohongshu",
    # 其余 host 落到通用网页分支 "web"
}
```

### 5.3 处理阶段（沿用 SSE 进度模型）

文章无需 ASR，阶段更短：

```text
resolving(0.1) -> fetching(0.4) -> extracting(0.7)
  -> summarizing(0.92) -> completed(1.0)
```

可复用现有任务表与 SSE 推送，仅去掉 `downloading / extracting_audio / transcribing` 三个视频专有阶段。

### 5.4 能力探测扩展

仿照已修复的 `mlx_whisper_status()`（见 [main.py](file:///Users/bytedance/Desktop/project/glean/backend/app/main.py#L204)），在 `/api/capabilities` 增加：

```python
"trafilatura": {"available": importlib.util.find_spec("trafilatura") is not None},
"playwright": {"available": importlib.util.find_spec("playwright") is not None},
```

前端"本机能力"面板即可显示文章提取依赖是否就绪。

## 6. 依赖安装清单

```bash
# P0：通用网页（必装）
pip3 install trafilatura

# P1：渲染兜底（公众号/动态页按需）
pip3 install playwright
python3 -m playwright install chromium

# P2：小红书反指纹请求（可选）
pip3 install curl-cffi
```

`backend/requirements.txt` 建议把 trafilatura 列为必选，playwright / curl-cffi 列为可选附加。

## 7. 风险与边界

| 风险 | 说明 | 缓解 |
| --- | --- | --- |
| 平台反爬升级 | 小红书/公众号规则变化频繁 | Adapter 隔离，局部替换；小红书外置到 XHS-Downloader |
| 登录态内容 | 阅读量、评论、付费/关注限制内容 | 不在范围内，仅取公开正文 |
| 图片防盗链 | 公众号图片需 Referer | 本期先取文字，图片本地化留待后续 |
| 版权合规 | 抓取他人内容 | 仅限用户主动粘贴单篇、个人学习用途，不做批量爬取 |
| 渲染开销 | Playwright 体积大、速度慢 | 仅作兜底，默认走静态 + trafilatura |

## 8. 落地建议（分阶段）

1. **P0**：接入 trafilatura，支持通用网页文章 → 接现有总结/金句/导出，最小可用；
2. **P1**：增加公众号 Adapter（静态优先，Playwright 兜底）；
3. **P2**：评估小红书需求强度，再决定是否通过 XHS-Downloader 子进程接入。

该分层方案能让"通用网页 + 公众号"这两类高频、低风险场景快速落地，把高风险的小红书隔离为可选项，既满足"本地、开源、少依赖外部 API"的目标，又控制了维护成本。
