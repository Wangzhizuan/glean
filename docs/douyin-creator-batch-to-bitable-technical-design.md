# 拾句：抖音博主批量文案提取 + 飞书多维表格归档技术方案

> 文档状态：技术调研 / 方案评审（待批准后实施）
> 编写日期：2026-07-04
> 目标环境：MacBook Pro（Apple Silicon），本机运行，无付费 API
> 关联文档：`docs/local-video-copy-backend-technical-design.md`、`docs/local-article-extraction-technical-research.md`

## 1. 文档目的

在现有「拾句」单条链接提取能力之上，新增一个**博主级批量提取**能力：

> 用户给出一个抖音博主的**主页链接**（或博主名字），系统自动抓取该博主的视频列表（含标题、点赞/评论/收藏/转发等指标），逐条完成本地语音转写与内容整理，最后把每条视频的指标与文案**汇总写入一张飞书多维表格**。

本方案评估其**可行性**、给出**架构设计、数据模型、后端模块、飞书集成命令、前端模块与分期计划**，并**诚实标注唯一的高风险环节**（抖音列表抓取），供评审决定是否实施。

## 2. 结论摘要

**结论：可以做。** 除"抖音博主主页 → 全部视频列表 + 指标"这一环需要先做 POC 验证外，其余环节均可直接复用项目已有能力。

| 环节 | 现状 | 结论 |
| --- | --- | --- |
| 单条视频 → 逐字稿 / 总结 / 金句 | `backend/app/pipeline.py` 已完整实现（yt-dlp + FFmpeg + mlx-whisper + Ollama） | ✅ 直接复用 |
| 任务队列 / 进度 / SSE / 落库 | `backend/app/main.py` 已实现 SQLite + Worker + SSE | ✅ 扩展复用 |
| 本机浏览器登录态 + Playwright 渲染滚动 | `backend/app/article.py`（飞书文档抓取）已有成熟模式 | ✅ 复用模式 |
| 写入飞书多维表格 | 本机 `lark-cli` 已安装并登录，`base` 域支持建表 / 建字段 / 批量写记录 | ✅ 现成工具链 |
| **抖音主页 → 视频列表 + 点赞等指标** | 项目内无此能力，抖音反爬严格（`a_bogus` 签名 + HTTP 412） | ⚠️ **唯一攻关点，需先 POC** |
| 博主名字 → 搜索定位主页 | 无，抖音搜索接口签名更脆弱 | ⚠️ 高风险可选项 |

### 2.1 已验证的本机能力（实测）

| 依赖 | 路径 / 版本 | 用途 |
| --- | --- | --- |
| `yt-dlp` | `~/Library/Python/3.9/bin/yt-dlp`，支持 `Douyin` extractor、`--flat-playlist` | 列表兜底解析、单条音频下载 |
| `ffmpeg` | `/opt/homebrew/bin/ffmpeg` | 音频标准化（16kHz 单声道 PCM） |
| `deno` | `/opt/homebrew/bin/deno` | yt-dlp 的 JS 运行时 |
| `lark-cli` | 已登录，身份 `王志钻`（`ou_f6fd...`），`base` 域可用 | 飞书多维表格读写 |

> `lark-cli whoami` 显示 `tokenStatus: needs_refresh`，实施前需 `lark-cli auth login` 刷新一次登录态。

## 3. 用户已确认的决策

| 项 | 选择 | 对方案的影响 |
| --- | --- | --- |
| 输入方式 | **名字 + 链接都要** | 主链路只吃主页链接；名字搜索作为独立**高风险可选模块**（§5.5），失败时提示用户改用链接 |
| 抓取方式 | **Playwright 无头 + 本机 cookies** | 复用 `article.py` 的 `_load_browser_cookies` + `_render_with_playwright` 模式，后台静默运行 |
| 默认抓取量 | **最近 50 条（可调）** | `requested_limit` 默认 50，前端可改；先控制量级再放开 |
| 飞书表 | **程序自动新建一张** | 每个博主任务自动 `+base-create` 一张新多维表，字段按 §8.2 预建 |

## 4. 整体架构与数据流

```text
博主主页 URL / 博主名字
        │
   [0] (可选) 名字 → 搜索 → 主页 URL         ← 高风险，可跳过（§5.5）
        │
   [1] 列表发现 discovering                   ← 新增 creator.py
        │   Playwright 无头 + 本机 Chrome cookies
        │   打开主页 → 滚动懒加载 → 拦截 aweme/post XHR
        │   得到每条：aweme_id / 标题 / 点赞·评论·收藏·转发 / 时长 / 发布时间 / 封面
        ▼
   [2] 落库 creator_videos（先出列表给用户看）  ← 新增数据表
        │
   [3] 逐条转写 processing                     ← 复用 run_pipeline()
        │   每条视频建一个 task → 下载音频 → 转写 → Ollama 总结/金句
        │   完成后把 transcript/summary/quotes 回填 creator_videos
        ▼
   [4] 汇总同步 syncing                        ← 新增 feishu_bitable.py
        │   lark-cli base +base-create 新建多维表
        │   +table-create 建字段 → +record-batch-create 批量写记录
        ▼
   飞书多维表格：标题│链接│点赞│评论│收藏│转发│时长│发布时间│逐字稿│总结│金句
```

状态机（`creator_jobs.status`）：

```text
discovering → processing → syncing → completed
     │             │           │
     └─ failed     └─ 部分失败 → completed_with_errors
```

## 5. 关键难点：抖音博主列表抓取（唯一攻关点）

抖音主页的视频列表由前端 JS 异步加载，且接口带 `a_bogus`/`X-Bogus` 签名与 HTTP 412 风控。这是整个方案里**唯一有可能做不成**的环节，必须在 P0 用真实博主主页做 POC。

### 5.1 方案 A（主）：Playwright 拦截 XHR

复用 `article.py` 已跑通的"读本机 Chrome cookies + 无头渲染滚动"模式，额外**监听网络响应**：

```text
1. browser_cookie3 读取本机 Chrome 的 douyin.com cookies（复用 _load_browser_cookies）
2. Playwright 无头打开 https://www.douyin.com/user/<sec_uid>
3. page.on("response") 监听，匹配 URL 含 aweme/v1/web/aweme/post/ 的 JSON 响应
4. 循环滚动到底触发懒加载，直到 has_more=0 或达到 requested_limit
5. 从响应 aweme_list 逐条解析字段
```

抖音 `aweme/post` 响应里每条 `aweme` 的关键字段（**以实际响应为准，抖音随时可能改**）：

| 目标字段 | 抖音响应路径 | 说明 |
| --- | --- | --- |
| 视频ID | `aweme_id` | 用于拼 `douyin.com/video/{id}` |
| 标题/文案 | `desc` | 视频描述文字 |
| 点赞 | `statistics.digg_count` | |
| 评论 | `statistics.comment_count` | |
| 收藏 | `statistics.collect_count` | |
| 转发 | `statistics.share_count` | |
| 播放 | `statistics.play_count` | 常为 0（web 端不返回） |
| 时长 | `video.duration`（ms） | |
| 发布时间 | `create_time`（秒级时间戳） | |
| 封面 | `video.cover.url_list[0]` | |

> 拦截 XHR 拿 `statistics` 是获取指标**最可靠**的方式；DOM 上的点赞数是缩写文本（如 "1.2万"）需二次解析，不作为首选。

### 5.2 方案 B（兜底）：纯 yt-dlp

```bash
# 只取列表（不逐条深挖），拿到每条 video_id
yt-dlp --flat-playlist --dump-json "https://www.douyin.com/user/<sec_uid>"
# 再对每条 douyin.com/video/<id> 做 --dump-json 拿元数据
```

- 优点：实现简单，与现有 `resolve_and_fetch_metadata` 一致。
- 缺点：**点赞/收藏/转发等指标字段基本拿不到**（yt-dlp 抖音 extractor 不稳定返回 statistics），同样受 412 风控。
- 定位：方案 A 拿不到指标时，至少用它保证"列表 + 逐条转写"可用，指标列留空并如实标注。

### 5.3 风控与稳健性

- **cookies 必需**：抖音匿名访问主页极易 412，必须注入本机登录态（延续项目现有做法）。
- **限速**：滚动之间随机 `800–1500ms` 延时；逐条转写之间留间隔，避免短时高频。
- **断点续跑**：`creator_videos` 按 `(creator_job_id, aweme_id)` 唯一，重跑幂等；失败的条目可单独重试。
- **诚实失败**：若拦截不到 `aweme/post` 或返回验证码页，`creator_jobs` 置 `failed` 并给出可操作提示（"请在本机 Chrome 登录抖音并访问过该主页后重试"），**绝不伪造列表**（遵循项目硬约束）。

### 5.4 POC 验收标准（P0，实施前置）

用一个真实博主主页跑通并打印：**能稳定拿到 ≥1 页视频列表，且每条带 aweme_id + 标题 + 点赞数。** 达标才进入 P1，否则退回方案 B 或调整产品预期。

### 5.5 博主名字搜索（高风险可选模块）

用户选择"名字也要"。抖音综合搜索接口（`aweme/v1/web/general/search/`）签名比主页更复杂、更易失效。方案：

- Playwright 打开 `douyin.com/search/<关键词>?type=user`，解析用户结果第一条的主页链接，回落到 §5.1 主链路。
- **明确标注风险**：成功率低、维护成本高；失败时前端提示"未能通过名字定位博主，请直接粘贴主页链接"。
- 列为 **P4 可选**，不阻塞主链路交付。

## 6. 数据模型

新增两张表，转写复用现有 `tasks`/`results`/`transcripts`/`generated_contents`。

```sql
CREATE TABLE IF NOT EXISTS creator_jobs (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,              -- douyin
    input_type TEXT NOT NULL,            -- url | name
    creator_url TEXT,                    -- 主页链接（name 定位成功后回填）
    creator_name TEXT,                   -- 博主昵称
    creator_sec_uid TEXT,                -- 抖音 sec_uid
    requested_limit INTEGER NOT NULL DEFAULT 50,
    discovered_count INTEGER NOT NULL DEFAULT 0,
    completed_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,                -- discovering|processing|syncing|completed|completed_with_errors|failed
    bitable_app_token TEXT,
    bitable_table_id TEXT,
    bitable_url TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS creator_videos (
    id TEXT PRIMARY KEY,
    creator_job_id TEXT NOT NULL REFERENCES creator_jobs(id),
    aweme_id TEXT NOT NULL,
    video_url TEXT NOT NULL,
    title TEXT,
    duration_ms INTEGER,
    like_count INTEGER,
    comment_count INTEGER,
    collect_count INTEGER,
    share_count INTEGER,
    play_count INTEGER,
    cover_url TEXT,
    published_at TEXT,
    task_id TEXT REFERENCES tasks(id),   -- 关联的转写任务
    transcribe_status TEXT NOT NULL DEFAULT 'pending',  -- pending|queued|done|failed
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (creator_job_id, aweme_id)
);
```

设计要点：
- **抓取与转写解耦**：先把全量列表落 `creator_videos`（这步就能给前端出列表），再逐条建 `task` 转写，完成后回填。
- **突破 10 条上限**：现有 `POST /api/batches` 有 `max_length=10` 校验；博主批量走**新的内部路径**创建 task，不经过该 Pydantic 模型，因此不受限。
- **指标独立存储**：点赞等指标只属于抖音列表，存 `creator_videos`，不污染通用 `tasks` schema。

## 7. 后端模块与 API

### 7.1 新增 / 改动文件

| 文件 | 改动 | 职责 |
| --- | --- | --- |
| `backend/app/creator.py` | 新增 | 抖音主页/名字 → 视频列表 + 指标（Playwright 拦截 XHR，yt-dlp 兜底） |
| `backend/app/feishu_bitable.py` | 新增 | 封装 `lark-cli base` 建表 / 建字段 / 批量写记录 |
| `backend/app/main.py` | 扩展 | 新增 `creator_jobs`/`creator_videos` 建表；新增 API 路由；Worker 增加 discovery + sync 分支 |
| `backend/app/pipeline.py` | 不改 | 逐条转写直接复用 `run_pipeline()` |

### 7.2 API 设计（沿用现有 camelCase 响应风格）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/creator-jobs` | 入参 `{ input, inputType: "url"\|"name", limit }`，创建 job 并异步 discovering |
| `GET` | `/api/creator-jobs` | 博主任务列表 |
| `GET` | `/api/creator-jobs/{id}` | job 详情 + `creator_videos` 列表 |
| `POST` | `/api/creator-jobs/{id}/start` | 开始/继续批量转写（也可发现后自动触发） |
| `POST` | `/api/creator-jobs/{id}/sync-feishu` | 汇总写入飞书多维表，返回 `bitableUrl` |
| `POST` | `/api/creator-jobs/{id}/retry` | 重试失败条目 |
| `GET` | `/api/creator-jobs/{id}/events` | SSE 进度（复用现有 SSE 模式，按 job 聚合） |

### 7.3 Worker 扩展

现有单 Worker 轮询 `tasks`。新增：
- **discovery 分支**：轮询 `creator_jobs.status = 'discovering'`，调用 `creator.py` 抓列表落 `creator_videos`，随后为每条建 `task`（`status='queued'`）并置 job 为 `processing`。
- **转写**：现有 Worker 照常消费 `tasks` 队列（无需改动）。每条完成后回填 `creator_videos.transcribe_status='done'`。
- **sync 分支**：所有条目终态后（或用户手动触发），置 `syncing`，调用 `feishu_bitable.py` 写表，完成置 `completed`。

> 建议 ASR 仍限 **1 个 Worker**（Apple Silicon 单模型实例），避免 50 条并发拖垮内存，与现有约定一致。

## 8. 飞书多维表格集成

全部通过本机已登录的 `lark-cli base`（实测命令，非 API 直连），无需额外鉴权代码。

### 8.1 命令序列（真实参数，已用 `--help` 核对）

```bash
# 1) 新建多维表（返回 app_token / base_token 与首表）
lark-cli base +base-create --name "抖音博主-<昵称>-文案库" \
  --table-name "视频文案" \
  --fields '<字段JSON见 8.2>' --json

# 2) 如需在同一 base 追加表：
lark-cli base +table-create --base-token <app_token> \
  --name "视频文案" --fields '<字段JSON>' --json

# 3) 批量写记录（rows 顺序对应 fields）
lark-cli base +record-batch-create --base-token <app_token> \
  --table-id <table_id> \
  --json '{"fields":["视频标题","视频链接","点赞","评论","收藏","转发","时长(秒)","发布时间","逐字稿","内容总结","精彩金句"],"rows":[[...],[...]]}'
```

- `+base-create` 的 `--folder-token` 可指定落到某个云空间文件夹；不给则落到默认位置。
- `+record-batch-create` 单次上限约 100 行，50 条无需分页；未来放开全量时按 100 行/批切分。
- 所有写命令 Risk 等级为 `write`（非 high-risk-write，无需 `--yes`）。

### 8.2 字段设计

| 字段 | 飞书类型 | 来源 |
| --- | --- | --- |
| 视频标题 | 文本 | `creator_videos.title` |
| 视频链接 | 超链接 | `video_url` |
| 点赞 / 评论 / 收藏 / 转发 | 数字 | `statistics.*` |
| 时长(秒) | 数字 | `duration_ms/1000` |
| 发布时间 | 日期 | `published_at` |
| 逐字稿 | 多行文本 | `transcripts.readable_text` |
| 内容总结 | 多行文本 | `generated_contents(summary).overview/detailedSummary` |
| 精彩金句 | 多行文本 | `generated_contents(quotes)` 拼接 |

> **类型注意（诚实标注）**：`--fields` 里 `type` 字符串目前仅从 `--help` 确认了 `text` 与 `select`。`number`/`url`/`datetime` 的确切 type 名需在 P2 用 `lark-cli schema base.app_table_field.create` 或 `+field-list` 实测确认。**未确认前统一降级为 `text` 字段**（数字与时间以文本写入），保证写入必成，再逐步升级为强类型。

## 9. 前端新模块

### 9.1 首页入口（呼应用户截图诉求）

在首页 `SurfaceGrid`（`src/components/features/surface-grid.tsx`）下方，新增一个独立卡片区块「**博主批量提取**」，与现有 4 张 surface 卡片风格一致，点击进入 `/creator`。仅新增数据与文案，复用现有 `surface-card` 样式，不新造组件。

### 9.2 新页面 `/creator`

| 区域 | 复用组件 | 说明 |
| --- | --- | --- |
| 输入区 | `Input` + `Button` | 主页链接 / 博主名字二选一，条数可调（默认 50） |
| 能力提示 | `Card` + `Badge` | 复用 capabilities，提示"需本机 Chrome 登录抖音 / 飞书" |
| 视频列表 | 表格 + `Badge` | 抓取到的视频与指标，转写状态实时更新 |
| 进度 | `Progress` + SSE | 复用现有 SSE hook，按 job 聚合 |
| 同步飞书 | `Button` | 完成后一键写表，成功后给出多维表链接 |

统一走 `src/lib/api.ts` 新增的 `createCreatorJob` / `getCreatorJob` / `syncCreatorToFeishu` 等函数（同源 `127.0.0.1:8787/api`）。样式令牌沿用 `design-system.css`，业务样式进 `pages.css`。

## 10. 性能与耗时预估

- 单条抖音短视频（15s–3min）端到端 ≈ **下载(数秒) + FFmpeg(秒级) + mlx-whisper 转写(音频时长的 ~1/4~1/2) + Ollama 总结(30s–2min)**。
- **50 条粗估 1–2.5 小时**（取决于视频长度与 Ollama 模型）。列表发现本身分钟级。
- 磁盘：`audio.wav` 约 1.8MB/min，转写后按现有约定及时清理，避免堆积。

## 11. 风险与合规

| 风险 | 等级 | 缓解 |
| --- | --- | --- |
| 抖音列表抓取失效（签名/风控变化） | **高** | P0 POC 先验；方案 B 兜底；诚实失败不伪造 |
| 名字搜索不稳定 | 中 | 降级为可选，失败提示改用链接 |
| 大博主全量耗时/风控 | 中 | 默认 50 条、限速、断点续跑 |
| 飞书字段强类型写入失败 | 低 | 先全 `text` 降级，实测后升级 |
| 合规 | — | 仅抓公开数据、仅本机存储、不绕登录墙，延续现有产品边界 |

## 12. 分期实施计划

| 阶段 | 内容 | 交付/验收 |
| --- | --- | --- |
| **P0** | `creator.py` POC：真实博主主页 → 打印列表 + 指标 | 稳定拿到列表且每条带点赞数（§5.4） |
| **P1** | 建表 DDL + `creator_jobs` 落库 + 复用 pipeline 批量转写 | 一个博主 50 条能跑完并回填文案 |
| **P2** | `feishu_bitable.py` + 字段类型实测 + 汇总写表 | 生成一张可访问的多维表，指标+文案齐全 |
| **P3** | 前端 `/creator` 页面 + 首页入口模块 | 全流程 UI 可操作，SSE 进度可见 |
| **P4（可选）** | 博主名字搜索定位 | 名字输入可用，失败有兜底提示 |

## 13. 待确认事项

1. 多维表是否落到指定云空间文件夹（提供 `folder-token`）？默认落默认位置。
2. 同一博主重复抓取：覆盖旧表 / 每次新建 / 增量更新？（当前默认每次新建）
3. 转写是"发现后自动全量跑"还是"用户在列表勾选后再跑"？（影响 `/start` 交互）
4. 是否需要同时保留现有 TXT/MD/JSON 导出，还是仅飞书归档？

---

**一句话总结**：技术上可行，飞书写入与逐条转写都是现成能力；成败取决于抖音列表抓取的 POC，建议先做 §5.4 验证再进入完整实施。
