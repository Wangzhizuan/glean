from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import quote, urlparse, parse_qs

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("SHIJU_DATA_DIR", ROOT_DIR / ".data")).expanduser()
DB_PATH = DATA_DIR / "shiju.db"
PROCESSOR_MODE = os.getenv("SHIJU_PROCESSOR_MODE", "demo")
TERMINAL_STATUSES = {"completed", "cancelled", "failed"}
ACTIVE_STATUSES = {
    "queued",
    "resolving",
    "fetching_subtitle",
    "downloading",
    "extracting_audio",
    "transcribing",
    "normalizing",
    "summarizing",
    # Article-only stages
    "fetching",
    "extracting",
}
SUPPORTED_HOSTS = {
    "bilibili.com": "bilibili",
    "www.bilibili.com": "bilibili",
    "b23.tv": "bilibili",
    "youtube.com": "youtube",
    "www.youtube.com": "youtube",
    "youtu.be": "youtube",
    "douyin.com": "douyin",
    "www.douyin.com": "douyin",
    "v.douyin.com": "douyin",
}
ARTICLE_HOSTS = {
    "mp.weixin.qq.com": "wechat",
    "xiaohongshu.com": "xiaohongshu",
    "www.xiaohongshu.com": "xiaohongshu",
    "xhslink.com": "xiaohongshu",
}
FEISHU_HOST_SUFFIXES = (".feishu.cn", ".larkoffice.com", ".feishu-pre.cn")
PLATFORM_LABELS = {
    "bilibili": "Bilibili",
    "youtube": "YouTube",
    "douyin": "抖音",
    "wechat": "微信公众号",
    "xiaohongshu": "小红书",
    "feishu": "飞书文档",
    "web": "网页文章",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def init_database() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as connection:
        connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;

            CREATE TABLE IF NOT EXISTS batches (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                task_count INTEGER NOT NULL,
                completed_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL REFERENCES batches(id),
                source_url TEXT NOT NULL,
                canonical_url TEXT,
                platform TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'video',
                source_id TEXT,
                title TEXT,
                author TEXT,
                duration_ms INTEGER,
                status TEXT NOT NULL,
                stage_progress REAL NOT NULL DEFAULT 0,
                overall_progress REAL NOT NULL DEFAULT 0,
                options_json TEXT NOT NULL,
                error_code TEXT,
                error_message TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS results (
                task_id TEXT PRIMARY KEY REFERENCES tasks(id),
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS transcripts (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(id),
                source TEXT NOT NULL,
                language TEXT NOT NULL DEFAULT 'zh',
                raw_text TEXT,
                readable_text TEXT,
                segments_json TEXT,
                word_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS generated_contents (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(id),
                type TEXT NOT NULL,
                model TEXT,
                prompt_version TEXT,
                content_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(id),
                kind TEXT NOT NULL,
                path TEXT NOT NULL,
                size_bytes INTEGER,
                sha256 TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_batch ON tasks(batch_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);
            CREATE INDEX IF NOT EXISTS idx_transcripts_task ON transcripts(task_id);
            CREATE INDEX IF NOT EXISTS idx_generated_contents_task ON generated_contents(task_id);
            CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id);
            """
        )
        connection.execute(
            """
            UPDATE tasks
            SET status = 'queued', stage_progress = 0, updated_at = ?
            WHERE status NOT IN ('completed', 'cancelled', 'failed', 'paused')
            """,
            (utc_now(),),
        )
        # Backward-compat: ensure the `kind` column exists on databases that
        # were created before article support was added.
        try:
            connection.execute("ALTER TABLE tasks ADD COLUMN kind TEXT NOT NULL DEFAULT 'video'")
        except sqlite3.OperationalError:
            pass


class OutputOptions(BaseModel):
    transcript: bool = True
    summary: bool = True
    quotes: bool = True


class ProcessingOptions(BaseModel):
    language: str = "auto"
    sourceLanguage: str = "auto"
    outputLanguage: str = "zh"
    subtitlePolicy: str = "prefer_platform"
    asrModel: str = "large-v3-turbo"
    useBrowserCookies: bool = False
    browser: Optional[str] = None
    enableOcr: bool = False


class BatchCreate(BaseModel):
    urls: List[str] = Field(min_length=1, max_length=10)
    outputs: OutputOptions = Field(default_factory=OutputOptions)
    options: ProcessingOptions = Field(default_factory=ProcessingOptions)


def normalize_url(raw_url: str) -> str:
    """Normalize platform-specific URL variants into yt-dlp compatible forms."""
    parsed = urlparse(raw_url)
    host = (parsed.hostname or "").lower()
    # 抖音精选页 jingxuan?modal_id=xxx → video/xxx
    if host in {"www.douyin.com", "douyin.com"} and "modal_id" in (parsed.query or ""):
        qs = parse_qs(parsed.query)
        modal_ids = qs.get("modal_id", [])
        if modal_ids and modal_ids[0].isdigit():
            return f"https://www.douyin.com/video/{modal_ids[0]}"
    return raw_url


def identify_platform(raw_url: str) -> tuple[str, str]:
    """Return ``(kind, platform)`` where ``kind`` is ``"video"`` or ``"article"``."""
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(400, detail={"code": "UNSUPPORTED_URL", "message": "只允许 http 或 https 链接"})
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(400, detail={"code": "UNSUPPORTED_URL", "message": "不允许本机或内网链接"})
    if host in SUPPORTED_HOSTS:
        return ("video", SUPPORTED_HOSTS[host])
    if host in ARTICLE_HOSTS:
        return ("article", ARTICLE_HOSTS[host])
    if any(host.endswith(suffix) for suffix in FEISHU_HOST_SUFFIXES):
        return ("article", "feishu")
    # Generic web articles fall through.
    return ("article", "web")
    # TODO(security): resolve DNS and validate every redirect target before the
    # real downloader follows it, blocking private/link-local IP ranges.


def dependency_status(command: str) -> Dict[str, Any]:
    path = shutil.which(command)
    return {"available": bool(path), "path": path}


def mlx_whisper_status() -> Dict[str, Any]:
    """Detect whether the mlx_whisper Python module is importable and whether
    the default ASR model is already cached locally."""
    import importlib.util

    available = importlib.util.find_spec("mlx_whisper") is not None
    model_ready = False
    if available:
        model_dir = (
            Path.home()
            / ".cache/huggingface/hub/models--mlx-community--whisper-large-v3-turbo"
        )
        model_ready = model_dir.exists()
    return {"available": available, "modelReady": model_ready}


def ollama_status() -> Dict[str, Any]:
    """Detect Ollama by pinging its local HTTP API rather than relying on a CLI
    binary on PATH (the official .app does not install a `ollama` shim)."""
    from .pipeline import _check_ollama_available

    return {"available": _check_ollama_available()}


def task_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "batchId": row["batch_id"],
        "kind": row["kind"] if "kind" in row.keys() else "video",
        "platform": row["platform"],
        "sourceUrl": row["source_url"],
        "canonicalUrl": row["canonical_url"],
        "title": row["title"] or "等待解析视频信息",
        "author": row["author"],
        "durationMs": row["duration_ms"],
        "status": row["status"],
        "stageProgress": row["stage_progress"],
        "overallProgress": row["overall_progress"],
        "estimatedRemainingSeconds": None,
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "completedAt": row["completed_at"],
        "error": (
            {"code": row["error_code"], "message": row["error_message"]}
            if row["error_code"]
            else None
        ),
    }


def update_batch_counts(connection: sqlite3.Connection, batch_id: str) -> None:
    counts = connection.execute(
        """
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
          SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
          SUM(CASE WHEN status IN ('queued','resolving','fetching_subtitle','downloading',
            'extracting_audio','transcribing','normalizing','summarizing',
            'fetching','extracting') THEN 1 ELSE 0 END) AS active,
          SUM(CASE WHEN status = 'paused' THEN 1 ELSE 0 END) AS paused
        FROM tasks WHERE batch_id = ?
        """,
        (batch_id,),
    ).fetchone()
    if counts["active"]:
        status = "processing"
    elif counts["paused"]:
        status = "paused"
    elif counts["failed"] and counts["completed"] + counts["failed"] == counts["total"]:
        status = "completed_with_errors"
    else:
        status = "completed"
    connection.execute(
        """
        UPDATE batches
        SET status = ?, completed_count = ?, failed_count = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, counts["completed"], counts["failed"], utc_now(), batch_id),
    )


DEMO_CONTENT = {
    "overview": "这段视频系统讨论了如何把零散的信息输入转化为可长期积累、能够在真实任务中调用的个人知识系统。内容从无目标收藏的问题出发，解释了问题驱动输入、主动转述和真实调用三者之间的关系。视频强调，知识管理的核心不是工具或收藏数量，而是能否形成从问题到行动的闭环。最终目标是让笔记成为写作、决策和沟通时可以复用的思考资产。",
    "coreThesis": "个人知识系统的价值不在于存储更多资料，而在于围绕真实问题完成输入、理解、压缩和调用的循环，并让每一次输入最终影响行动。",
    "detailedSummary": "视频首先指出，许多人每天接触大量信息，却很少真正留下可调用的理解，原因通常不是输入不足，而是输入前没有明确问题。没有问题约束的收藏会不断堆积，最终形成缺少索引和使用场景的资料仓库。随后，视频提出主动转述是从保存信息到理解信息的关键步骤：看完内容后，应当用自己的语言写出结论、依据以及它可能改变的行动。最后，视频将知识系统放回真实任务中检验，强调笔记只有在文章、方案、讨论和决策中被再次调用，才会真正转化为能力。整套方法可以概括为问题、输入、转述、调用四个连续环节。",
    "keyPoints": [
        {
            "title": "先提出问题，再开始输入",
            "content": "带着具体问题阅读或观看，可以减少无目标的信息消费。",
        },
        {
            "title": "用自己的语言完成压缩",
            "content": "摘抄只是保存，转述才代表理解。",
        },
        {
            "title": "让笔记进入真实任务",
            "content": "知识需要在写作、决策或讨论中被调用，才能转化为能力。",
        },
        {
            "title": "收藏不等于理解",
            "content": "资料数量只代表保存行为，只有经过筛选、转述和关联，信息才会变成个人理解。",
        },
        {
            "title": "建立最小学习闭环",
            "content": "从问题出发，完成输入、转述和调用，比搭建复杂工具体系更重要。",
        },
        {
            "title": "用输出检验输入质量",
            "content": "能否在文章、方案或讨论中复用一个观点，是判断输入是否真正有效的标准。",
        },
    ],
    "contentStructure": [
        {"section": "问题提出", "summary": "解释信息摄入很多但有效积累很少的常见困境。"},
        {"section": "问题驱动输入", "summary": "说明先确定问题如何减少无效收藏。"},
        {"section": "主动转述", "summary": "介绍用自己的语言压缩结论、依据和行动的方法。"},
        {"section": "真实任务调用", "summary": "讨论笔记如何在写作、决策和沟通中转化为能力。"},
        {"section": "闭环总结", "summary": "将方法归纳为问题、输入、转述和调用四个环节。"},
    ],
    "actionItems": [
        "选择一个本周正在解决的问题",
        "每天只收集三条直接相关的材料",
        "周末整理成一页主题笔记并在输出中引用",
        "每次收藏前写下这条信息要解决的具体问题",
        "看完内容后用三句话记录结论、依据和下一步行动",
        "每周删除或归档没有明确使用场景的收藏",
    ],
    "targetAudience": [
        "收藏很多资料但很少回看的知识工作者",
        "希望改善阅读、学习和笔记流程的内容创作者",
        "需要在写作、方案和决策中快速调用资料的人",
    ],
    "terms": [
        {"term": "问题驱动输入", "explanation": "先明确需要解决的问题，再有选择地获取信息。"},
        {"term": "主动转述", "explanation": "不用原文摘抄代替理解，而是用自己的语言重新表达。"},
        {"term": "知识调用", "explanation": "在真实任务中找到并使用过去积累的观点和材料。"},
    ],
    "conclusions": [
        "收藏数量不能代表知识积累质量。",
        "转述是信息从外部资料变成个人理解的关键步骤。",
        "知识只有进入真实行动和输出，才能转化为长期能力。",
        "简单而持续的闭环优于复杂但不使用的工具系统。",
    ],
}

DEMO_SEGMENTS = [
    (0, 14000, "我们每天都会看到很多信息，但真正能留下来的非常少。"),
    (14000, 42000, "问题往往不是输入不够，而是输入之前没有明确自己想解决什么。"),
    (42000, 76000, "收藏夹更像一个没有索引的仓库，你需要把内容放回一个具体问题里。"),
    (76000, 112000, "看完一段内容后，用三句话写出结论、依据，以及它会改变哪个行动。"),
    (112000, 146000, "笔记系统的价值，是在写文章、做方案和讨论时找到已经思考过的东西。"),
    (146000, 178000, "最小的闭环是问题、输入、转述和调用，只要循环起来，知识系统就会生长。"),
]


def build_demo_result(task: sqlite3.Row) -> Dict[str, Any]:
    segments = [
        {
            "index": index,
            "startMs": start,
            "endMs": end,
            "text": text,
        }
        for index, (start, end, text) in enumerate(DEMO_SEGMENTS)
    ]
    plain_text = "".join(segment["text"] for segment in segments)
    return {
        "taskId": task["id"],
        "metadata": {
            "kind": task["kind"] if "kind" in task.keys() else "video",
            "platform": task["platform"],
            "platformLabel": PLATFORM_LABELS.get(task["platform"], task["platform"]),
            "title": task["title"],
            "author": task["author"],
            "durationMs": task["duration_ms"],
            "publishedAt": None,
            "generatedAt": utc_now(),
            "sourceUrl": task["source_url"],
        },
        "transcript": {
            "source": "demo_local",
            "language": "zh",
            "wordCount": len(plain_text),
            "plainText": plain_text,
            "segments": segments,
        },
        "summary": DEMO_CONTENT,
        "quotes": [
            {
                "text": "收藏只是把信息留下，转述才是把理解留下。",
                "startMs": 42000,
                "endMs": 76000,
                "sourceSegmentIds": [2],
                "isPolished": True,
            },
            {
                "text": "知识系统的价值，在于需要时能否被调用。",
                "startMs": 112000,
                "endMs": 146000,
                "sourceSegmentIds": [4],
                "isPolished": True,
            },
            {
                "text": "先有问题，再有输入；先有转述，再有积累。",
                "startMs": 14000,
                "endMs": 42000,
                "sourceSegmentIds": [1],
                "isPolished": True,
            },
            {
                "text": "输入之前没有问题，输入之后通常也不会留下答案。",
                "startMs": 14000,
                "endMs": 42000,
                "sourceSegmentIds": [1],
                "isPolished": True,
            },
            {
                "text": "收藏夹更像没有索引的仓库，而不是已经掌握的知识。",
                "startMs": 42000,
                "endMs": 76000,
                "sourceSegmentIds": [2],
                "isPolished": True,
            },
            {
                "text": "摘抄完成的是保存，转述完成的才是理解。",
                "startMs": 76000,
                "endMs": 112000,
                "sourceSegmentIds": [3],
                "isPolished": True,
            },
            {
                "text": "每次输入都应该回答三个问题：结论是什么，依据是什么，行动会怎样改变。",
                "startMs": 76000,
                "endMs": 112000,
                "sourceSegmentIds": [3],
                "isPolished": True,
            },
            {
                "text": "笔记不是为了看起来完整，而是为了需要时能够被找到。",
                "startMs": 112000,
                "endMs": 146000,
                "sourceSegmentIds": [4],
                "isPolished": True,
            },
            {
                "text": "知识系统最终要接受真实任务的检验。",
                "startMs": 112000,
                "endMs": 146000,
                "sourceSegmentIds": [4],
                "isPolished": True,
            },
            {
                "text": "能在写作、决策和讨论中被调用的内容，才真正属于你。",
                "startMs": 112000,
                "endMs": 146000,
                "sourceSegmentIds": [4],
                "isPolished": True,
            },
            {
                "text": "工具不会自动形成知识，循环才会。",
                "startMs": 146000,
                "endMs": 178000,
                "sourceSegmentIds": [5],
                "isPolished": True,
            },
            {
                "text": "最小的学习闭环，是让一个观点改变下一次行动。",
                "startMs": 146000,
                "endMs": 178000,
                "sourceSegmentIds": [5],
                "isPolished": True,
            },
            {
                "text": "问题决定输入的方向，调用决定积累的价值。",
                "startMs": 146000,
                "endMs": 178000,
                "sourceSegmentIds": [5],
                "isPolished": True,
            },
            {
                "text": "简单但持续运行的系统，胜过复杂却从不使用的系统。",
                "startMs": 146000,
                "endMs": 178000,
                "sourceSegmentIds": [5],
                "isPolished": True,
            },
        ],
        "processor": {
            "mode": "demo",
            "notice": "当前结果由本地演示处理器生成，未下载或识别真实视频。",
        },
    }


STAGES = [
    ("resolving", 0.05, 0.35),
    ("fetching_subtitle", 0.10, 0.45),
    ("downloading", 0.30, 0.55),
    ("extracting_audio", 0.40, 0.35),
    ("transcribing", 0.80, 1.0),
    ("normalizing", 0.85, 0.35),
    ("summarizing", 1.0, 0.75),
]


class Worker:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self.run, daemon=True, name="shiju-worker")
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)

    def run(self) -> None:
        while not self.stop_event.is_set():
            task = self.claim_next_task()
            if not task:
                self.stop_event.wait(0.4)
                continue
            try:
                self.process(task["id"])
            except Exception as error:
                self.fail_task(task["id"], "PROCESSOR_FAILED", str(error))

    def claim_next_task(self) -> Optional[sqlite3.Row]:
        with connect() as connection:
            return connection.execute(
                "SELECT * FROM tasks WHERE status = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()

    def current_status(self, task_id: str) -> Optional[str]:
        with connect() as connection:
            row = connection.execute(
                "SELECT status FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            return row["status"] if row else None

    def wait_if_paused(self, task_id: str) -> bool:
        while not self.stop_event.is_set():
            status = self.current_status(task_id)
            if status == "cancelled":
                return False
            if status != "paused":
                return True
            time.sleep(0.3)
        return False

    def process(self, task_id: str) -> None:
        if PROCESSOR_MODE == "demo":
            self._process_demo(task_id)
            return
        # Real mode: dispatch by task kind
        with connect() as connection:
            row = connection.execute(
                "SELECT kind FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            kind = row["kind"] if row else "video"
        if kind == "article":
            self._process_article(task_id)
        else:
            self._process_real(task_id)

    def _process_real(self, task_id: str) -> None:
        """Real processing using yt-dlp, FFmpeg, mlx-whisper, and Ollama."""
        from .pipeline import run_pipeline, PipelineResult

        with connect() as connection:
            task = connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if not task:
                return
            batch_id = task["batch_id"]
            url = task["source_url"]
            platform = task["platform"]
            options = json.loads(task["options_json"]).get("options", {})

        def on_stage(stage: str, progress: float):
            if not self.wait_if_paused(task_id):
                raise RuntimeError("CANCELLED")
            with connect() as conn:
                current = conn.execute(
                    "SELECT status FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
                if current and current["status"] == "cancelled":
                    raise RuntimeError("CANCELLED")
                conn.execute(
                    """
                    UPDATE tasks SET status = ?, stage_progress = 0.5,
                      overall_progress = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (stage, progress, utc_now(), task_id),
                )
                conn.execute(
                    "UPDATE batches SET updated_at = ? WHERE id = ?",
                    (utc_now(), batch_id),
                )

        try:
            pipeline_result: PipelineResult = run_pipeline(
                url=url,
                platform=platform,
                task_id=task_id,
                on_stage=on_stage,
                options=options,
            )
        except RuntimeError as e:
            if "CANCELLED" in str(e):
                return
            raise

        # Build result JSON matching the API schema
        meta = pipeline_result.metadata
        sub = pipeline_result.subtitle
        segments_data = [
            {
                "index": s.index,
                "startMs": s.start_ms,
                "endMs": s.end_ms,
                "text": s.text,
            }
            for s in sub.segments
        ]
        summary_data = {}
        if pipeline_result.summary:
            summary_data = {
                "overview": pipeline_result.summary.overview,
                "coreThesis": pipeline_result.summary.core_thesis,
                "detailedSummary": pipeline_result.summary.detailed_summary,
                "keyPoints": pipeline_result.summary.key_points,
                "contentStructure": pipeline_result.summary.content_structure,
                "actionItems": pipeline_result.summary.action_items,
                "targetAudience": pipeline_result.summary.target_audience,
                "terms": pipeline_result.summary.terms,
                "conclusions": pipeline_result.summary.conclusions,
            }
        quotes_data = [
            {
                "text": q.text,
                "startMs": q.start_ms,
                "endMs": q.end_ms,
                "sourceSegmentIds": q.source_segment_ids,
                "isPolished": q.is_polished,
            }
            for q in pipeline_result.quotes
        ]

        platform_names = PLATFORM_LABELS
        result = {
            "taskId": task_id,
            "metadata": {
                "kind": "video",
                "platform": meta.platform,
                "platformLabel": platform_names.get(meta.platform, meta.platform),
                "title": meta.title,
                "author": meta.author,
                "durationMs": meta.duration_ms,
                "publishedAt": meta.published_at,
                "generatedAt": utc_now(),
                "sourceUrl": meta.source_url,
            },
            "transcript": {
                "source": sub.source,
                "language": sub.language,
                "wordCount": sub.word_count,
                "plainText": sub.plain_text,
                "segments": segments_data,
            },
            "summary": summary_data,
            "quotes": quotes_data,
            "processor": {
                "mode": "real",
                "notice": None,
            },
        }

        with connect() as connection:
            # Save result
            connection.execute(
                "INSERT OR REPLACE INTO results(task_id, result_json, created_at) VALUES (?, ?, ?)",
                (task_id, json.dumps(result, ensure_ascii=False), utc_now()),
            )
            # Save transcript record
            connection.execute(
                """
                INSERT OR REPLACE INTO transcripts(id, task_id, source, language, raw_text,
                  readable_text, segments_json, word_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    make_id("tr"),
                    task_id,
                    sub.source,
                    sub.language,
                    sub.plain_text,
                    sub.plain_text,
                    json.dumps(segments_data, ensure_ascii=False),
                    sub.word_count,
                    utc_now(),
                ),
            )
            # Save generated contents
            if summary_data:
                connection.execute(
                    """
                    INSERT INTO generated_contents(id, task_id, type, model, prompt_version,
                      content_json, created_at)
                    VALUES (?, ?, 'summary', ?, 'v2-rich-zh', ?, ?)
                    """,
                    (
                        make_id("gc"),
                        task_id,
                        os.getenv("SHIJU_OLLAMA_MODEL", "qwen2.5:7b"),
                        json.dumps(summary_data, ensure_ascii=False),
                        utc_now(),
                    ),
                )
            if quotes_data:
                connection.execute(
                    """
                    INSERT INTO generated_contents(id, task_id, type, model, prompt_version,
                      content_json, created_at)
                    VALUES (?, ?, 'quotes', ?, 'v2-12-plus-zh', ?, ?)
                    """,
                    (
                        make_id("gc"),
                        task_id,
                        os.getenv("SHIJU_OLLAMA_MODEL", "qwen2.5:7b"),
                        json.dumps(quotes_data, ensure_ascii=False),
                        utc_now(),
                    ),
                )
            # Update task as completed
            connection.execute(
                """
                UPDATE tasks SET status = 'completed', stage_progress = 1,
                  overall_progress = 1, title = ?, author = ?, duration_ms = ?,
                  canonical_url = ?, updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    meta.title,
                    meta.author,
                    meta.duration_ms,
                    meta.canonical_url,
                    utc_now(),
                    utc_now(),
                    task_id,
                ),
            )
            update_batch_counts(connection, batch_id)

    def _process_article(self, task_id: str) -> None:
        """Process an article task: fetch HTML → extract → summarize via Ollama."""
        from .article import (
            ArticleError,
            ArticleResult,
            extract_article,
        )
        from .pipeline import (
            SubtitleSegment,
            _check_ollama_available,
            generate_quotes,
            generate_summary,
        )

        with connect() as connection:
            task = connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if not task:
                return
            batch_id = task["batch_id"]
            url = task["source_url"]
            platform = task["platform"]

        def push(stage: str, progress: float) -> None:
            if not self.wait_if_paused(task_id):
                raise RuntimeError("CANCELLED")
            with connect() as conn:
                current = conn.execute(
                    "SELECT status FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
                if current and current["status"] == "cancelled":
                    raise RuntimeError("CANCELLED")
                conn.execute(
                    """
                    UPDATE tasks SET status = ?, stage_progress = 0.5,
                      overall_progress = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (stage, progress, utc_now(), task_id),
                )
                conn.execute(
                    "UPDATE batches SET updated_at = ? WHERE id = ?",
                    (utc_now(), batch_id),
                )

        try:
            push("resolving", 0.10)
            push("fetching", 0.40)
            try:
                article: ArticleResult = extract_article(url, platform)
            except ArticleError as e:
                self.fail_task(task_id, e.code, e.message)
                return
            push("extracting", 0.70)
        except RuntimeError as e:
            if "CANCELLED" in str(e):
                return
            raise

        # Build pseudo segments from paragraphs so generate_quotes can work.
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", article.plain_text) if p.strip()]
        if not paragraphs:
            paragraphs = [article.plain_text]
        # Split overly long paragraphs into ~200-char chunks.
        segments: List[SubtitleSegment] = []
        idx = 0
        for paragraph in paragraphs:
            if len(paragraph) <= 240:
                segments.append(SubtitleSegment(index=idx, start_ms=0, end_ms=0, text=paragraph))
                idx += 1
                continue
            for offset in range(0, len(paragraph), 200):
                chunk = paragraph[offset:offset + 200]
                segments.append(SubtitleSegment(index=idx, start_ms=0, end_ms=0, text=chunk))
                idx += 1

        # Summarize via Ollama (best-effort)
        push("summarizing", 0.92)
        summary_data: Dict[str, Any] = {}
        quotes_data: List[Dict[str, Any]] = []
        ollama_ok = _check_ollama_available()
        if ollama_ok and article.plain_text.strip():
            try:
                summary = generate_summary(article.plain_text, article.title)
                summary_data = {
                    "overview": summary.overview,
                    "coreThesis": summary.core_thesis,
                    "detailedSummary": summary.detailed_summary,
                    "keyPoints": summary.key_points,
                    "contentStructure": summary.content_structure,
                    "actionItems": summary.action_items,
                    "targetAudience": summary.target_audience,
                    "terms": summary.terms,
                    "conclusions": summary.conclusions,
                }
            except Exception as e:  # noqa: BLE001
                logger = __import__("logging").getLogger("shiju.article")
                logger.warning("article summary failed: %s", e)
            try:
                quotes = generate_quotes(segments, article.title)
                quotes_data = [
                    {
                        "text": q.text,
                        "startMs": 0,
                        "endMs": 0,
                        "sourceSegmentIds": q.source_segment_ids,
                        "isPolished": q.is_polished,
                    }
                    for q in quotes
                ]
            except Exception as e:  # noqa: BLE001
                logger = __import__("logging").getLogger("shiju.article")
                logger.warning("article quotes failed: %s", e)
        elif not ollama_ok:
            summary_data = {
                "overview": "Ollama 服务未运行，跳过自动总结。请启动 Ollama 后重试。",
                "coreThesis": "",
                "detailedSummary": "",
                "keyPoints": [],
                "contentStructure": [],
                "actionItems": [],
                "targetAudience": [],
                "terms": [],
                "conclusions": [],
            }

        segments_data = [
            {"index": s.index, "startMs": 0, "endMs": 0, "text": s.text}
            for s in segments
        ]
        result = {
            "taskId": task_id,
            "metadata": {
                "kind": "article",
                "platform": platform,
                "platformLabel": PLATFORM_LABELS.get(platform, platform),
                "title": article.title,
                "author": article.author,
                "durationMs": 0,
                "publishedAt": article.published_at,
                "generatedAt": utc_now(),
                "sourceUrl": article.source_url,
            },
            "transcript": {
                "source": f"article_{platform}",
                "language": "zh",
                "wordCount": article.word_count,
                "plainText": article.plain_text,
                "segments": segments_data,
            },
            "summary": summary_data,
            "quotes": quotes_data,
            "processor": {
                "mode": "real",
                "notice": None,
            },
        }

        with connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO results(task_id, result_json, created_at) VALUES (?, ?, ?)",
                (task_id, json.dumps(result, ensure_ascii=False), utc_now()),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO transcripts(id, task_id, source, language, raw_text,
                  readable_text, segments_json, word_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    make_id("tr"),
                    task_id,
                    f"article_{platform}",
                    "zh",
                    article.plain_text,
                    article.markdown,
                    json.dumps(segments_data, ensure_ascii=False),
                    article.word_count,
                    utc_now(),
                ),
            )
            if summary_data:
                connection.execute(
                    """
                    INSERT INTO generated_contents(id, task_id, type, model, prompt_version,
                      content_json, created_at)
                    VALUES (?, ?, 'summary', ?, 'v2-rich-zh', ?, ?)
                    """,
                    (
                        make_id("gc"),
                        task_id,
                        os.getenv("SHIJU_OLLAMA_MODEL", "qwen2.5:7b"),
                        json.dumps(summary_data, ensure_ascii=False),
                        utc_now(),
                    ),
                )
            if quotes_data:
                connection.execute(
                    """
                    INSERT INTO generated_contents(id, task_id, type, model, prompt_version,
                      content_json, created_at)
                    VALUES (?, ?, 'quotes', ?, 'v2-12-plus-zh', ?, ?)
                    """,
                    (
                        make_id("gc"),
                        task_id,
                        os.getenv("SHIJU_OLLAMA_MODEL", "qwen2.5:7b"),
                        json.dumps(quotes_data, ensure_ascii=False),
                        utc_now(),
                    ),
                )
            connection.execute(
                """
                UPDATE tasks SET status = 'completed', stage_progress = 1,
                  overall_progress = 1, title = ?, author = ?, duration_ms = 0,
                  canonical_url = ?, updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    article.title,
                    article.author,
                    article.canonical_url,
                    utc_now(),
                    utc_now(),
                    task_id,
                ),
            )
            update_batch_counts(connection, batch_id)

    def _process_demo(self, task_id: str) -> None:
        """Demo processing with simulated delays."""
        for stage, overall_progress, duration in STAGES:
            if not self.wait_if_paused(task_id):
                return
            with connect() as connection:
                task = connection.execute(
                    "SELECT * FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
                if not task or task["status"] == "cancelled":
                    return
                title = task["title"]
                duration_ms = task["duration_ms"]
                if stage == "resolving":
                    platform_label = PLATFORM_LABELS.get(task["platform"], task["platform"])
                    title = f"{platform_label} 文案提取示例"
                    duration_ms = 178000
                connection.execute(
                    """
                    UPDATE tasks SET status = ?, stage_progress = 0.5,
                      overall_progress = ?, title = ?, author = ?, duration_ms = ?,
                      canonical_url = source_url, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        stage,
                        overall_progress,
                        title,
                        "本地演示",
                        duration_ms,
                        utc_now(),
                        task_id,
                    ),
                )
                update_batch_counts(connection, task["batch_id"])
            self.stop_event.wait(duration)

        with connect() as connection:
            task = connection.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if not task or task["status"] == "cancelled":
                return
            result = build_demo_result(task)
            connection.execute(
                "INSERT OR REPLACE INTO results(task_id, result_json, created_at) VALUES (?, ?, ?)",
                (task_id, json.dumps(result, ensure_ascii=False), utc_now()),
            )
            connection.execute(
                """
                UPDATE tasks SET status = 'completed', stage_progress = 1,
                  overall_progress = 1, updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (utc_now(), utc_now(), task_id),
            )
            update_batch_counts(connection, task["batch_id"])

    def fail_task(self, task_id: str, code: str, message: str) -> None:
        with connect() as connection:
            task = connection.execute(
                "SELECT batch_id FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if not task:
                return
            connection.execute(
                """
                UPDATE tasks SET status = 'failed', error_code = ?,
                  error_message = ?, updated_at = ? WHERE id = ?
                """,
                (code, message, utc_now(), task_id),
            )
            update_batch_counts(connection, task["batch_id"])


worker = Worker()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_database()
    worker.start()
    yield
    worker.stop()


app = FastAPI(title="拾句本地服务", version="0.1.0", lifespan=lifespan)
# TODO(security): add a random per-install session token before distributing
# this service beyond local development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"status": "ready", "processorMode": PROCESSOR_MODE, "database": str(DB_PATH)}


@app.get("/api/capabilities")
def capabilities() -> Dict[str, Any]:
    from .article import article_capabilities

    dependencies = {
        "ffmpeg": dependency_status("ffmpeg"),
        "ytDlp": dependency_status("yt-dlp"),
        "mlxWhisper": mlx_whisper_status(),
        "ollama": ollama_status(),
    }
    article = article_capabilities()
    real_ready = all(
        [
            dependencies["ffmpeg"]["available"],
            dependencies["ytDlp"]["available"],
            dependencies["mlxWhisper"]["available"],
            dependencies["ollama"]["available"],
        ]
    )
    return {
        "status": "ready" if PROCESSOR_MODE == "demo" or real_ready else "needs_setup",
        "processorMode": PROCESSOR_MODE,
        "dependencies": dependencies,
        "article": article,
        "platforms": ["douyin", "bilibili", "youtube"],
        "sources": ["douyin", "bilibili", "youtube", "wechat", "xiaohongshu", "feishu", "web"],
        "notice": (
            "演示处理器已启用，可跑通任务、进度、结果与导出；不会读取真实视频。"
            if PROCESSOR_MODE == "demo"
            else None
        ),
    }


@app.post("/api/batches", status_code=201)
def create_batch(payload: BatchCreate) -> Dict[str, Any]:
    urls = [normalize_url(url.strip()) for url in payload.urls if url.strip()]
    if not 1 <= len(urls) <= 10:
        raise HTTPException(400, detail={"code": "INVALID_BATCH_SIZE", "message": "单批需要 1-10 条链接"})
    classifications = [identify_platform(url) for url in urls]
    batch_id = make_id("bat")
    task_ids: List[str] = []
    created_at = utc_now()
    options = json.dumps(
        {"outputs": payload.outputs.model_dump(), "options": payload.options.model_dump()},
        ensure_ascii=False,
    )
    with connect() as connection:
        connection.execute(
            "INSERT INTO batches VALUES (?, 'processing', ?, 0, 0, ?, ?)",
            (batch_id, len(urls), created_at, created_at),
        )
        for url, (kind, platform) in zip(urls, classifications):
            task_id = make_id("tsk")
            task_ids.append(task_id)
            connection.execute(
                """
                INSERT INTO tasks(
                  id, batch_id, source_url, platform, kind, status, options_json,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (task_id, batch_id, url, platform, kind, options, created_at, created_at),
            )
    return {"batchId": batch_id, "taskIds": task_ids, "createdAt": created_at}


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str) -> Dict[str, Any]:
    with connect() as connection:
        batch = connection.execute(
            "SELECT * FROM batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if not batch:
            raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "批次不存在"})
        tasks = connection.execute(
            "SELECT * FROM tasks WHERE batch_id = ? ORDER BY created_at", (batch_id,)
        ).fetchall()
    return {
        "id": batch["id"],
        "status": batch["status"],
        "taskCount": batch["task_count"],
        "completedCount": batch["completed_count"],
        "failedCount": batch["failed_count"],
        "createdAt": batch["created_at"],
        "updatedAt": batch["updated_at"],
        "tasks": [task_to_dict(task) for task in tasks],
    }


@app.get("/api/tasks")
def list_tasks(
    status: Optional[str] = None,
    platform: Optional[str] = None,
    query: Optional[str] = None,
) -> Dict[str, Any]:
    clauses: List[str] = []
    parameters: List[Any] = []
    if status:
        clauses.append("status = ?")
        parameters.append(status)
    if platform:
        clauses.append("platform = ?")
        parameters.append(platform)
    if query:
        clauses.append("(LOWER(title) LIKE ? OR LOWER(platform) LIKE ?)")
        value = f"%{query.lower()}%"
        parameters.extend([value, value])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT 200",
            parameters,
        ).fetchall()
    return {"items": [task_to_dict(row) for row in rows], "total": len(rows)}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> Dict[str, Any]:
    with connect() as connection:
        task = connection.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
    if not task:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "任务不存在"})
    return task_to_dict(task)


@app.get("/api/tasks/{task_id}/result")
def get_result(task_id: str) -> Dict[str, Any]:
    with connect() as connection:
        row = connection.execute(
            "SELECT result_json FROM results WHERE task_id = ?", (task_id,)
        ).fetchone()
    if not row:
        raise HTTPException(409, detail={"code": "RESULT_NOT_READY", "message": "文案尚未生成完成"})
    return json.loads(row["result_json"])


def control_task(task_id: str, action: Literal["pause", "resume", "cancel", "retry"]) -> Dict[str, Any]:
    with connect() as connection:
        task = connection.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not task:
            raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "任务不存在"})
        status = task["status"]
        if action == "pause" and status in ACTIVE_STATUSES:
            next_status = "paused"
        elif action == "resume" and status == "paused":
            next_status = "queued"
        elif action == "cancel" and status not in TERMINAL_STATUSES:
            next_status = "cancelled"
        elif action == "retry" and status in {"failed", "cancelled"}:
            next_status = "queued"
        else:
            next_status = status
        connection.execute(
            """
            UPDATE tasks SET status = ?, error_code = NULL, error_message = NULL,
              attempt_count = attempt_count + ?, updated_at = ? WHERE id = ?
            """,
            (next_status, 1 if action == "retry" else 0, utc_now(), task_id),
        )
        update_batch_counts(connection, task["batch_id"])
        updated = connection.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
    return task_to_dict(updated)


@app.post("/api/tasks/{task_id}/{action}")
def task_action(
    task_id: str, action: Literal["pause", "resume", "cancel", "retry"]
) -> Dict[str, Any]:
    return control_task(task_id, action)


class DeleteTasksRequest(BaseModel):
    task_ids: List[str]


@app.post("/api/tasks/delete")
def delete_tasks(body: DeleteTasksRequest) -> Dict[str, Any]:
    if not body.task_ids:
        raise HTTPException(400, detail={"code": "BAD_REQUEST", "message": "task_ids 不能为空"})
    placeholders = ",".join("?" * len(body.task_ids))
    with connect() as connection:
        # 删除关联数据文件
        rows = connection.execute(
            f"SELECT id FROM tasks WHERE id IN ({placeholders})", body.task_ids
        ).fetchall()
        for row in rows:
            task_dir = DATA_DIR / "tasks" / row["id"]
            if task_dir.exists():
                shutil.rmtree(task_dir, ignore_errors=True)
        connection.execute(
            f"DELETE FROM tasks WHERE id IN ({placeholders})", body.task_ids
        )
    return {"deleted": len(rows)}


@app.post("/api/batches/{batch_id}/{action}")
def batch_action(batch_id: str, action: Literal["pause", "resume"]) -> Dict[str, Any]:
    with connect() as connection:
        tasks = connection.execute(
            "SELECT id FROM tasks WHERE batch_id = ?", (batch_id,)
        ).fetchall()
    if not tasks:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "批次不存在"})
    for task in tasks:
        control_task(task["id"], action)
    return get_batch(batch_id)


def format_timestamp(milliseconds: int) -> str:
    total_seconds, ms = divmod(milliseconds, 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{ms:03}"


def build_export(result: Dict[str, Any], export_format: str) -> tuple[str, str, str]:
    title = result["metadata"]["title"]
    transcript = result["transcript"]
    summary = result["summary"]
    quotes = result["quotes"]
    core_thesis = summary.get("coreThesis")
    detailed_summary = summary.get("detailedSummary")
    content_structure = summary.get("contentStructure", [])
    action_items = summary.get("actionItems", [])
    terms = summary.get("terms", [])
    conclusions = summary.get("conclusions", [])
    target_audience = summary.get("targetAudience", [])
    safe_name = "".join(character for character in title if character not in '/\\:*?"<>|')[:80]
    if export_format == "json":
        return f"{safe_name}.json", "application/json", json.dumps(result, ensure_ascii=False, indent=2)
    if export_format == "md":
        points = "\n".join(
            f"- **{point['title']}**：{point['content']}" for point in summary["keyPoints"]
        )
        quote_lines = "\n".join(f"> {quote['text']}" for quote in quotes)
        sections = [
            f"# {title}",
            f"## 内容总结\n\n{summary['overview']}",
            f"## 核心主张\n\n{core_thesis}" if core_thesis else "",
            f"## 详细总结\n\n{detailed_summary}" if detailed_summary else "",
            f"## 关键观点\n\n{points}",
            "## 内容结构\n\n"
            + "\n".join(
                f"- **{item['section']}**：{item['summary']}" for item in content_structure
            )
            if content_structure
            else "",
            "## 行动建议\n\n" + "\n".join(f"- {item}" for item in action_items)
            if action_items
            else "",
            "## 关键术语\n\n"
            + "\n".join(f"- **{item['term']}**：{item['explanation']}" for item in terms)
            if terms
            else "",
            "## 主要结论\n\n" + "\n".join(f"- {item}" for item in conclusions)
            if conclusions
            else "",
            "## 适合人群\n\n" + "\n".join(f"- {item}" for item in target_audience)
            if target_audience
            else "",
            f"## 精彩金句\n\n{quote_lines}",
            f"## 逐字稿\n\n{transcript['plainText']}",
        ]
        content = "\n\n".join(section for section in sections if section) + "\n"
        return f"{safe_name}.md", "text/markdown; charset=utf-8", content
    # TODO(export): add a real DOCX artifact using python-docx after the core
    # local media pipeline is available.
    sections = [
        title,
        f"内容总结\n{summary['overview']}",
        f"核心主张\n{core_thesis}" if core_thesis else "",
        f"详细总结\n{detailed_summary}" if detailed_summary else "",
        "关键观点\n"
        + "\n".join(
            f"- {point['title']}：{point['content']}" for point in summary["keyPoints"]
        ),
        "内容结构\n"
        + "\n".join(
            f"- {item['section']}：{item['summary']}" for item in content_structure
        )
        if content_structure
        else "",
        "行动建议\n" + "\n".join(f"- {item}" for item in action_items)
        if action_items
        else "",
        "关键术语\n"
        + "\n".join(f"- {item['term']}：{item['explanation']}" for item in terms)
        if terms
        else "",
        "主要结论\n" + "\n".join(f"- {item}" for item in conclusions)
        if conclusions
        else "",
        "适合人群\n" + "\n".join(f"- {item}" for item in target_audience)
        if target_audience
        else "",
        "精彩金句\n" + "\n".join(f"- {quote['text']}" for quote in quotes),
        f"逐字稿\n{transcript['plainText']}",
    ]
    content = "\n\n".join(section for section in sections if section) + "\n"
    return f"{safe_name}.txt", "text/plain; charset=utf-8", content


@app.get("/api/tasks/{task_id}/export")
def export_task(task_id: str, format: str = Query("txt", pattern="^(txt|md|json)$")):
    result = get_result(task_id)
    filename, media_type, content = build_export(result, format)
    return Response(
        content=content.encode("utf-8"),
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f"attachment; filename=shiju-export.{format}; "
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )


@app.get("/api/events")
async def events(batchId: str):
    async def stream():
        previous_hash = ""
        while True:
            payload = get_batch(batchId)
            serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            current_hash = hashlib.sha256(serialized.encode()).hexdigest()
            if current_hash != previous_hash:
                previous_hash = current_hash
                yield f"event: batch.updated\ndata: {serialized}\n\n"
            if payload["status"] in {"completed", "completed_with_errors"}:
                yield "event: batch.finished\ndata: {}\n\n"
                break
            await asyncio.sleep(0.7)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
