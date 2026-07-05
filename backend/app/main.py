from __future__ import annotations

import asyncio
import hashlib
import json
import logging
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
DATA_DIR = Path(os.getenv("GLEAN_DATA_DIR", ROOT_DIR / ".data")).expanduser()
DB_PATH = DATA_DIR / "glean.db"
PROCESSOR_MODE = os.getenv("GLEAN_PROCESSOR_MODE", "demo")
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
    # 小宇宙播客 (yt-dlp 的 generic 抽取器可识别 m4a 媒体地址)
    "xiaoyuzhoufm.com": "xiaoyuzhou",
    "www.xiaoyuzhoufm.com": "xiaoyuzhou",
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
    "xiaoyuzhou": "小宇宙",
    "wechat": "微信公众号",
    "xiaohongshu": "小红书",
    "feishu": "飞书文档",
    "web": "网页文章",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _parse_tags(tags_json: Optional[str]) -> List[str]:
    if not tags_json:
        return []
    try:
        data = json.loads(tags_json)
        return [str(tag) for tag in data if str(tag).strip()]
    except (json.JSONDecodeError, TypeError):
        return []


def _strip_hashtags(text: str) -> str:
    """Remove #hashtags from a title for a cleaner Bitable 标题 cell."""
    cleaned = re.sub(r"#[^\s#@]+", "", text)
    return re.sub(r"\s+", " ", cleaned).strip() or text


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

            CREATE TABLE IF NOT EXISTS creator_jobs (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL DEFAULT 'douyin',
                input_type TEXT NOT NULL,
                input_value TEXT NOT NULL,
                creator_url TEXT,
                creator_name TEXT,
                creator_sec_uid TEXT,
                requested_limit INTEGER NOT NULL DEFAULT 50,
                discovered_count INTEGER NOT NULL DEFAULT 0,
                completed_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
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
                tags_json TEXT,
                task_id TEXT REFERENCES tasks(id),
                transcribe_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (creator_job_id, aweme_id)
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_batch ON tasks(batch_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);
            CREATE INDEX IF NOT EXISTS idx_transcripts_task ON transcripts(task_id);
            CREATE INDEX IF NOT EXISTS idx_generated_contents_task ON generated_contents(task_id);
            CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id);
            CREATE INDEX IF NOT EXISTS idx_creator_videos_job ON creator_videos(creator_job_id);
            CREATE INDEX IF NOT EXISTS idx_creator_jobs_status ON creator_jobs(status);
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
        # Backward-compat: creator_videos.tags_json added after initial release.
        try:
            connection.execute("ALTER TABLE creator_videos ADD COLUMN tags_json TEXT")
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


class CreatorJobCreate(BaseModel):
    input: str = Field(min_length=1)
    inputType: Literal["url", "name"] = "url"
    limit: int = Field(default=50, ge=1, le=200)


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


DEMO_NOTICE = (
    "演示模式：未真正下载或识别该链接。请用 `npm run dev:real` 启动真实处理模式后重试。"
)


def build_demo_result(task: sqlite3.Row) -> Dict[str, Any]:
    """Return a clearly-labelled placeholder so the UI can be exercised without
    real ASR/LLM dependencies. The content here intentionally avoids
    fabricating any details about the source URL.
    """
    kind = task["kind"] if "kind" in task.keys() else "video"
    platform = task["platform"]
    platform_label = PLATFORM_LABELS.get(platform, platform)
    placeholder_text = (
        f"演示模式占位：未识别 {platform_label} 链接的真实内容。\n"
        "若要查看真实的逐字稿或正文，请先停止当前服务，"
        "改用 `npm run dev:real` 启动真实处理模式（需安装 ffmpeg、yt-dlp、"
        "mlx-whisper 与 Ollama）。"
    )
    segments = [
        {
            "index": 0,
            "startMs": 0,
            "endMs": 0,
            "text": placeholder_text,
        }
    ]
    summary = {
        "overview": DEMO_NOTICE,
        "coreThesis": "",
        "detailedSummary": placeholder_text,
        "keyPoints": [],
        "contentStructure": [],
        "actionItems": [],
        "targetAudience": [],
        "terms": [],
        "conclusions": [],
    }
    return {
        "taskId": task["id"],
        "metadata": {
            "kind": kind,
            "platform": platform,
            "platformLabel": platform_label,
            "title": task["title"] or f"{platform_label} 演示占位（未识别真实内容）",
            "author": task["author"],
            "durationMs": task["duration_ms"] or 0,
            "publishedAt": None,
            "generatedAt": utc_now(),
            "sourceUrl": task["source_url"],
        },
        "transcript": {
            "source": "demo_placeholder",
            "language": "zh",
            "wordCount": len(placeholder_text),
            "plainText": placeholder_text,
            "segments": segments,
        },
        "summary": summary,
        "quotes": [],
        "processor": {
            "mode": "demo",
            "notice": DEMO_NOTICE,
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
        self.thread = threading.Thread(target=self.run, daemon=True, name="glean-worker")
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)

    def run(self) -> None:
        while not self.stop_event.is_set():
            # Creator jobs take priority for their discovery/sync phases, but
            # the per-video transcription still flows through the tasks queue.
            creator_job = self.claim_next_creator_job()
            if creator_job:
                try:
                    self.process_creator_job(creator_job["id"], creator_job["status"])
                except Exception as error:  # noqa: BLE001
                    self.fail_creator_job(creator_job["id"], "CREATOR_FAILED", str(error))
                continue

            task = self.claim_next_task()
            if not task:
                self.stop_event.wait(0.4)
                continue
            try:
                self.process(task["id"])
            except Exception as error:
                self.fail_task(task["id"], "PROCESSOR_FAILED", str(error))
            finally:
                self.backfill_creator_video(task["id"])

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
        elif kind == "image_text":
            self._process_image_text(task_id)
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
                        os.getenv("GLEAN_OLLAMA_MODEL", "qwen2.5:7b"),
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
                        os.getenv("GLEAN_OLLAMA_MODEL", "qwen2.5:7b"),
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
                logger = __import__("logging").getLogger("glean.article")
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
                logger = __import__("logging").getLogger("glean.article")
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
                        os.getenv("GLEAN_OLLAMA_MODEL", "qwen2.5:7b"),
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
                        os.getenv("GLEAN_OLLAMA_MODEL", "qwen2.5:7b"),
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

    def _process_image_text(self, task_id: str) -> None:
        """Process a Douyin image-text post (图文帖).

        These posts have no audio/video stream, so yt-dlp/whisper cannot apply.
        The caption text was already harvested into ``tasks.title``; we treat it
        as the body, optionally summarize via Ollama, and mark the task done —
        instead of failing it like a broken video download.
        """
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
            body_text = (task["title"] or "").strip()

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
            push("extracting", 0.40)
        except RuntimeError as e:
            if "CANCELLED" in str(e):
                return
            raise

        if not body_text:
            # 图文帖也没有任何文字（纯图片、无文案）：如实标记失败，说明原因。
            self.fail_task(
                task_id,
                "IMAGE_POST_NO_TEXT",
                "这是一条图文帖，但没有可提取的文字文案（纯图片，无标题/正文）。",
            )
            return

        # 标题取正文首行/首句，正文全文作为文案。
        title = re.split(r"[。！？\n]", body_text, maxsplit=1)[0].strip()[:60] or body_text[:60]

        segments: List[SubtitleSegment] = []
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", body_text) if p.strip()] or [body_text]
        idx = 0
        for paragraph in paragraphs:
            if len(paragraph) <= 240:
                segments.append(SubtitleSegment(index=idx, start_ms=0, end_ms=0, text=paragraph))
                idx += 1
                continue
            for offset in range(0, len(paragraph), 200):
                segments.append(
                    SubtitleSegment(index=idx, start_ms=0, end_ms=0, text=paragraph[offset:offset + 200])
                )
                idx += 1

        push("summarizing", 0.92)
        summary_data: Dict[str, Any] = {}
        quotes_data: List[Dict[str, Any]] = []
        ollama_ok = _check_ollama_available()
        if ollama_ok:
            try:
                summary = generate_summary(body_text, title)
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
                logging.getLogger("glean.creator").warning("image-text summary failed: %s", e)
            try:
                quotes = generate_quotes(segments, title)
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
                logging.getLogger("glean.creator").warning("image-text quotes failed: %s", e)

        word_count = len(re.sub(r"\s+", "", body_text))
        segments_data = [
            {"index": s.index, "startMs": 0, "endMs": 0, "text": s.text} for s in segments
        ]
        result = {
            "taskId": task_id,
            "metadata": {
                "kind": "image_text",
                "platform": "douyin",
                "platformLabel": PLATFORM_LABELS.get("douyin", "douyin"),
                "title": title,
                "author": None,
                "durationMs": 0,
                "publishedAt": None,
                "generatedAt": utc_now(),
                "sourceUrl": url,
            },
            "transcript": {
                "source": "douyin_image_text",
                "language": "zh",
                "wordCount": word_count,
                "plainText": body_text,
                "segments": segments_data,
            },
            "summary": summary_data,
            "quotes": quotes_data,
            "processor": {"mode": "real", "notice": "图文帖：文案取自帖子正文，无音视频转写。"},
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
                    "douyin_image_text",
                    "zh",
                    body_text,
                    body_text,
                    json.dumps(segments_data, ensure_ascii=False),
                    word_count,
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
                        os.getenv("GLEAN_OLLAMA_MODEL", "qwen2.5:7b"),
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
                        os.getenv("GLEAN_OLLAMA_MODEL", "qwen2.5:7b"),
                        json.dumps(quotes_data, ensure_ascii=False),
                        utc_now(),
                    ),
                )
            connection.execute(
                """
                UPDATE tasks SET status = 'completed', stage_progress = 1,
                  overall_progress = 1, duration_ms = 0,
                  canonical_url = source_url, updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (utc_now(), utc_now(), task_id),
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

    # ------------------------------------------------------------------
    # Creator jobs
    # ------------------------------------------------------------------

    def claim_next_creator_job(self) -> Optional[sqlite3.Row]:
        """Pick a creator job that needs the worker: discovery or sync."""
        with connect() as connection:
            return connection.execute(
                """
                SELECT id, status FROM creator_jobs
                WHERE status IN ('discovering', 'syncing')
                ORDER BY created_at LIMIT 1
                """
            ).fetchone()

    def process_creator_job(self, job_id: str, status: str) -> None:
        if PROCESSOR_MODE == "demo":
            # Demo mode never harvests real creators; fail honestly.
            self.fail_creator_job(
                job_id,
                "DEMO_MODE",
                "演示模式不支持博主批量抓取，请用 npm run dev:real 启动真实模式。",
            )
            return
        if status == "discovering":
            self._discover_creator(job_id)
        elif status == "syncing":
            self._sync_creator(job_id)

    def _discover_creator(self, job_id: str) -> None:
        """Harvest the creator's video list and enqueue transcription tasks."""
        from .creator import CreatorError, harvest_creator, resolve_creator_url_by_name

        with connect() as connection:
            job = connection.execute(
                "SELECT * FROM creator_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if not job:
                return
            input_type = job["input_type"]
            input_value = job["input_value"]
            limit = job["requested_limit"]
            options_json = json.dumps(
                {"outputs": OutputOptions().model_dump(), "options": ProcessingOptions().model_dump()},
                ensure_ascii=False,
            )

        try:
            creator_url = job["creator_url"] or input_value
            if input_type == "name" and not job["creator_url"]:
                creator_url = resolve_creator_url_by_name(input_value)

            def on_progress(count: int) -> None:
                with connect() as conn:
                    conn.execute(
                        "UPDATE creator_jobs SET discovered_count = ?, updated_at = ? WHERE id = ?",
                        (count, utc_now(), job_id),
                    )

            harvest = harvest_creator(creator_url, limit=limit, on_progress=on_progress)
        except CreatorError as e:
            self.fail_creator_job(job_id, e.code, e.message)
            return

        # If the user cancelled while we were harvesting, stop before enqueuing
        # any transcription tasks so we don't spend local resources.
        with connect() as connection:
            current = connection.execute(
                "SELECT status FROM creator_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if current and current["status"] == "cancelled":
                return

        created_at = utc_now()
        with connect() as connection:
            connection.execute(
                """
                UPDATE creator_jobs SET creator_url = ?, creator_name = ?,
                  creator_sec_uid = ?, discovered_count = ?, status = 'processing',
                  updated_at = ? WHERE id = ?
                """,
                (
                    harvest.creator_url,
                    harvest.creator_name,
                    harvest.creator_sec_uid,
                    len(harvest.videos),
                    created_at,
                    job_id,
                ),
            )
            for video in harvest.videos:
                task_id = make_id("tsk")
                # 图文帖没有音视频流，走纯文案管线（kind=image_text），
                # 并把已抓到的文案直接写进 task.title 供后续处理复用。
                task_kind = "image_text" if video.is_image_post else "video"
                connection.execute(
                    """
                    INSERT INTO tasks(
                      id, batch_id, source_url, platform, kind, status, title,
                      options_json, created_at, updated_at
                    ) VALUES (?, ?, ?, 'douyin', ?, 'queued', ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        job_id,
                        video.video_url,
                        task_kind,
                        video.title,
                        options_json,
                        created_at,
                        created_at,
                    ),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO creator_videos(
                      id, creator_job_id, aweme_id, video_url, title, duration_ms,
                      like_count, comment_count, collect_count, share_count, play_count,
                      cover_url, published_at, tags_json, task_id, transcribe_status,
                      created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                    """,
                    (
                        make_id("cvd"),
                        job_id,
                        video.aweme_id,
                        video.video_url,
                        video.title,
                        video.duration_ms,
                        video.like_count,
                        video.comment_count,
                        video.collect_count,
                        video.share_count,
                        video.play_count,
                        video.cover_url,
                        video.published_at,
                        json.dumps(video.tags, ensure_ascii=False),
                        task_id,
                        created_at,
                        created_at,
                    ),
                )

    def backfill_creator_video(self, task_id: str) -> None:
        """After a task terminates, copy its result into the creator_videos row."""
        with connect() as connection:
            video = connection.execute(
                "SELECT id, creator_job_id FROM creator_videos WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if not video:
                return
            task = connection.execute(
                "SELECT status, title, duration_ms FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if not task:
                return
            status = task["status"]
            if status not in ("completed", "failed", "cancelled"):
                return
            new_status = "done" if status == "completed" else "failed"
            connection.execute(
                """
                UPDATE creator_videos SET transcribe_status = ?, title = COALESCE(?, title),
                  duration_ms = COALESCE(?, duration_ms), updated_at = ?
                WHERE id = ?
                """,
                (new_status, task["title"], task["duration_ms"], utc_now(), video["id"]),
            )
            job_id = video["creator_job_id"]
            self._recount_creator_job(connection, job_id)

    def _recount_creator_job(self, connection: sqlite3.Connection, job_id: str) -> None:
        counts = connection.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN transcribe_status = 'done' THEN 1 ELSE 0 END) AS done,
              SUM(CASE WHEN transcribe_status = 'failed' THEN 1 ELSE 0 END) AS failed,
              SUM(CASE WHEN transcribe_status IN ('pending','queued') THEN 1 ELSE 0 END) AS pending
            FROM creator_videos WHERE creator_job_id = ?
            """,
            (job_id,),
        ).fetchone()
        job = connection.execute(
            "SELECT status FROM creator_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not job:
            return
        # Never resurrect a job the user has cancelled (or one that already failed).
        if job["status"] in ("cancelled", "failed"):
            return
        next_status = job["status"]
        # When all videos reached a terminal state, mark the transcription phase
        # as done. The user then triggers Feishu sync explicitly.
        if job["status"] == "processing" and counts["pending"] == 0 and counts["total"] > 0:
            next_status = "transcribed"
        connection.execute(
            """
            UPDATE creator_jobs SET completed_count = ?, failed_count = ?,
              status = ?, updated_at = ? WHERE id = ?
            """,
            (counts["done"] or 0, counts["failed"] or 0, next_status, utc_now(), job_id),
        )

    def _sync_creator(self, job_id: str) -> None:
        """Write all completed creator videos into a new Feishu Bitable."""
        from .feishu_bitable import FeishuError, sync_creator_videos

        with connect() as connection:
            job = connection.execute(
                "SELECT * FROM creator_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if not job:
                return
            videos = connection.execute(
                "SELECT * FROM creator_videos WHERE creator_job_id = ? ORDER BY created_at",
                (job_id,),
            ).fetchall()

        payload: List[Dict[str, Any]] = []
        for video in videos:
            transcript_text, summary_text, quotes_text = self._creator_video_texts(
                video["task_id"]
            )
            tags = _parse_tags(video["tags_json"] if "tags_json" in video.keys() else None)
            clean_title = _strip_hashtags(video["title"]) if video["title"] else video["title"]
            payload.append(
                {
                    "title": clean_title,
                    "tags": " ".join(f"#{t}" for t in tags) if tags else None,
                    "video_url": video["video_url"],
                    "cover_url": video["cover_url"],
                    "like_count": video["like_count"],
                    "comment_count": video["comment_count"],
                    "collect_count": video["collect_count"],
                    "share_count": video["share_count"],
                    "duration_ms": video["duration_ms"],
                    "published_at": video["published_at"],
                    "transcript": transcript_text,
                    "summary": summary_text,
                    "quotes": quotes_text,
                }
            )

        base_name = f"抖音博主-{job['creator_name'] or job['creator_sec_uid'] or '未命名'}-文案库"
        try:
            target = sync_creator_videos(base_name, payload)
        except FeishuError as e:
            self.fail_creator_job(job_id, e.code, e.message)
            return

        with connect() as connection:
            connection.execute(
                """
                UPDATE creator_jobs SET status = 'completed', bitable_url = ?,
                  error_code = NULL, error_message = NULL, updated_at = ? WHERE id = ?
                """,
                (target.url, utc_now(), job_id),
            )

    def _creator_video_texts(self, task_id: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Pull transcript / summary / quotes text for one video's task."""
        if not task_id:
            return None, None, None
        with connect() as connection:
            tr = connection.execute(
                "SELECT readable_text FROM transcripts WHERE task_id = ?", (task_id,)
            ).fetchone()
            summary_row = connection.execute(
                "SELECT content_json FROM generated_contents WHERE task_id = ? AND type = 'summary' ORDER BY created_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            quotes_row = connection.execute(
                "SELECT content_json FROM generated_contents WHERE task_id = ? AND type = 'quotes' ORDER BY created_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        transcript_text = tr["readable_text"] if tr else None
        summary_text = None
        if summary_row:
            try:
                data = json.loads(summary_row["content_json"])
                summary_text = data.get("detailedSummary") or data.get("overview") or None
            except (json.JSONDecodeError, AttributeError):
                summary_text = None
        quotes_text = None
        if quotes_row:
            try:
                data = json.loads(quotes_row["content_json"])
                quotes_text = "\n".join(f"「{q.get('text', '')}」" for q in data if q.get("text"))
            except (json.JSONDecodeError, AttributeError, TypeError):
                quotes_text = None
        return transcript_text, summary_text, quotes_text

    def fail_creator_job(self, job_id: str, code: str, message: str) -> None:
        with connect() as connection:
            connection.execute(
                """
                UPDATE creator_jobs SET status = 'failed', error_code = ?,
                  error_message = ?, updated_at = ? WHERE id = ?
                """,
                (code, message, utc_now(), job_id),
            )


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
    from .article import article_capabilities, feishu_readiness

    dependencies = {
        "ffmpeg": dependency_status("ffmpeg"),
        "ytDlp": dependency_status("yt-dlp"),
        "mlxWhisper": mlx_whisper_status(),
        "ollama": ollama_status(),
    }
    article = article_capabilities()
    feishu = feishu_readiness()
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
        "feishu": feishu,
        "platforms": ["douyin", "bilibili", "youtube", "xiaoyuzhou"],
        "sources": ["douyin", "bilibili", "youtube", "xiaoyuzhou", "wechat", "xiaohongshu", "feishu", "web"],
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
                f"attachment; filename=glean-export.{format}; "
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


# ---------------------------------------------------------------------------
# Creator jobs (Douyin creator batch -> Feishu Bitable)
# ---------------------------------------------------------------------------

CREATOR_TERMINAL = {"completed", "failed", "cancelled"}


def creator_job_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "platform": row["platform"],
        "inputType": row["input_type"],
        "inputValue": row["input_value"],
        "creatorUrl": row["creator_url"],
        "creatorName": row["creator_name"],
        "creatorSecUid": row["creator_sec_uid"],
        "requestedLimit": row["requested_limit"],
        "discoveredCount": row["discovered_count"],
        "completedCount": row["completed_count"],
        "failedCount": row["failed_count"],
        "status": row["status"],
        "bitableUrl": row["bitable_url"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "error": (
            {"code": row["error_code"], "message": row["error_message"]}
            if row["error_code"]
            else None
        ),
    }


def creator_video_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    # Reflect the live task status while a video is still being transcribed so
    # the UI can show "识别中" instead of a stale "排队中".
    stored = row["transcribe_status"]
    task_status = row["task_status"] if "task_status" in row.keys() else None
    display_status = stored
    if stored in ("pending", "queued") and task_status:
        if task_status in ACTIVE_STATUSES and task_status != "queued":
            display_status = "processing"
    keys = row.keys()
    task_error_code = row["task_error_code"] if "task_error_code" in keys else None
    task_error_message = row["task_error_message"] if "task_error_message" in keys else None
    task_kind = row["task_kind"] if "task_kind" in keys else None
    return {
        "id": row["id"],
        "awemeId": row["aweme_id"],
        "videoUrl": row["video_url"],
        "title": row["title"],
        "durationMs": row["duration_ms"],
        "likeCount": row["like_count"],
        "commentCount": row["comment_count"],
        "collectCount": row["collect_count"],
        "shareCount": row["share_count"],
        "playCount": row["play_count"],
        "coverUrl": row["cover_url"],
        "publishedAt": row["published_at"],
        "tags": _parse_tags(row["tags_json"] if "tags_json" in keys else None),
        "taskId": row["task_id"],
        "transcribeStatus": display_status,
        "kind": task_kind,
        "error": (
            {"code": task_error_code, "message": task_error_message}
            if task_error_code
            else None
        ),
    }


def get_creator_job_payload(job_id: str) -> Dict[str, Any]:
    with connect() as connection:
        job = connection.execute(
            "SELECT * FROM creator_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not job:
            raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "博主任务不存在"})
        videos = connection.execute(
            """
            SELECT cv.*, t.status AS task_status,
                   t.kind AS task_kind,
                   t.error_code AS task_error_code,
                   t.error_message AS task_error_message
            FROM creator_videos cv
            LEFT JOIN tasks t ON t.id = cv.task_id
            WHERE cv.creator_job_id = ? ORDER BY cv.created_at
            """,
            (job_id,),
        ).fetchall()
    payload = creator_job_to_dict(job)
    payload["videos"] = [creator_video_to_dict(video) for video in videos]
    return payload


@app.get("/api/creator/capabilities")
def creator_capabilities_endpoint() -> Dict[str, Any]:
    from .creator import creator_capabilities
    from .feishu_bitable import feishu_readiness

    return {
        "processorMode": PROCESSOR_MODE,
        "harvest": creator_capabilities(),
        "feishu": feishu_readiness(),
    }


@app.post("/api/creator-jobs", status_code=201)
def create_creator_job(payload: CreatorJobCreate) -> Dict[str, Any]:
    from .creator import is_douyin_user_url

    value = payload.input.strip()
    creator_url: Optional[str] = None
    if payload.inputType == "url":
        if not is_douyin_user_url(value):
            raise HTTPException(
                400,
                detail={
                    "code": "INVALID_CREATOR_URL",
                    "message": "请粘贴抖音博主主页链接（形如 douyin.com/user/XXX）。",
                },
            )
        creator_url = value

    job_id = make_id("crt")
    created_at = utc_now()
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO creator_jobs(
              id, platform, input_type, input_value, creator_url, requested_limit,
              status, created_at, updated_at
            ) VALUES (?, 'douyin', ?, ?, ?, ?, 'discovering', ?, ?)
            """,
            (job_id, payload.inputType, value, creator_url, payload.limit, created_at, created_at),
        )
    return get_creator_job_payload(job_id)


@app.get("/api/creator-jobs")
def list_creator_jobs() -> Dict[str, Any]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT id FROM creator_jobs ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
    items = [get_creator_job_payload(row["id"]) for row in rows]
    return {"items": items, "total": len(items)}


@app.get("/api/creator-jobs/{job_id}")
def get_creator_job(job_id: str) -> Dict[str, Any]:
    return get_creator_job_payload(job_id)


@app.post("/api/creator-jobs/{job_id}/sync-feishu")
def sync_creator_job(job_id: str) -> Dict[str, Any]:
    with connect() as connection:
        job = connection.execute(
            "SELECT status FROM creator_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not job:
            raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "博主任务不存在"})
        if job["status"] in ("discovering", "processing"):
            raise HTTPException(
                409,
                detail={"code": "NOT_READY", "message": "视频还在抓取或转写中，请等待完成后再同步。"},
            )
        connection.execute(
            "UPDATE creator_jobs SET status = 'syncing', error_code = NULL, error_message = NULL, updated_at = ? WHERE id = ?",
            (utc_now(), job_id),
        )
    return get_creator_job_payload(job_id)


@app.post("/api/creator-jobs/{job_id}/retry")
def retry_creator_job(job_id: str) -> Dict[str, Any]:
    """Retry a failed job: re-discover if no videos yet, else re-run failed videos."""
    with connect() as connection:
        job = connection.execute(
            "SELECT * FROM creator_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not job:
            raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "博主任务不存在"})
        video_count = connection.execute(
            "SELECT COUNT(*) AS n FROM creator_videos WHERE creator_job_id = ?", (job_id,)
        ).fetchone()["n"]
        if video_count == 0:
            connection.execute(
                "UPDATE creator_jobs SET status = 'discovering', error_code = NULL, error_message = NULL, updated_at = ? WHERE id = ?",
                (utc_now(), job_id),
            )
        else:
            # Re-queue failed videos' tasks. Reclassify likely 图文帖
            # (no duration + has caption text) to the text-only pipeline so
            # they stop failing on a video download that can never succeed.
            failed_videos = connection.execute(
                """
                SELECT id, task_id, duration_ms, title
                FROM creator_videos
                WHERE creator_job_id = ? AND transcribe_status = 'failed'
                """,
                (job_id,),
            ).fetchall()
            for video in failed_videos:
                if video["task_id"]:
                    has_text = bool((video["title"] or "").strip())
                    is_image_post = has_text and not video["duration_ms"]
                    new_kind = "image_text" if is_image_post else "video"
                    connection.execute(
                        """
                        UPDATE tasks SET status = 'queued', kind = ?,
                          error_code = NULL, error_message = NULL, updated_at = ?
                        WHERE id = ?
                        """,
                        (new_kind, utc_now(), video["task_id"]),
                    )
                    connection.execute(
                        "UPDATE creator_videos SET transcribe_status = 'queued', updated_at = ? WHERE task_id = ?",
                        (utc_now(), video["task_id"]),
                    )
            connection.execute(
                "UPDATE creator_jobs SET status = 'processing', error_code = NULL, error_message = NULL, updated_at = ? WHERE id = ?",
                (utc_now(), job_id),
            )
    return get_creator_job_payload(job_id)


@app.post("/api/creator-jobs/{job_id}/cancel")
def cancel_creator_job(job_id: str) -> Dict[str, Any]:
    """Stop a running creator job: cancel all non-terminal child tasks and mark
    the job cancelled so the worker stops spending local resources on it."""
    with connect() as connection:
        job = connection.execute(
            "SELECT status FROM creator_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not job:
            raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "博主任务不存在"})
        if job["status"] in ("completed", "cancelled"):
            return get_creator_job_payload(job_id)
        # Cancel every child task that hasn't reached a terminal state. The
        # pipeline's on_stage callback checks for 'cancelled' and aborts.
        connection.execute(
            f"""
            UPDATE tasks SET status = 'cancelled', updated_at = ?
            WHERE batch_id = ? AND status NOT IN ({','.join('?' for _ in TERMINAL_STATUSES)})
            """,
            (utc_now(), job_id, *TERMINAL_STATUSES),
        )
        # Mark pending/queued videos as cancelled for a truthful UI.
        connection.execute(
            """
            UPDATE creator_videos SET transcribe_status = 'cancelled', updated_at = ?
            WHERE creator_job_id = ? AND transcribe_status IN ('pending','queued')
            """,
            (utc_now(), job_id),
        )
        connection.execute(
            "UPDATE creator_jobs SET status = 'cancelled', updated_at = ? WHERE id = ?",
            (utc_now(), job_id),
        )
    return get_creator_job_payload(job_id)


@app.get("/api/creator-events")
async def creator_events(jobId: str):
    async def stream():
        previous_hash = ""
        while True:
            try:
                payload = get_creator_job_payload(jobId)
            except HTTPException:
                break
            serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            current_hash = hashlib.sha256(serialized.encode()).hexdigest()
            if current_hash != previous_hash:
                previous_hash = current_hash
                yield f"event: creator.updated\ndata: {serialized}\n\n"
            if payload["status"] in CREATOR_TERMINAL:
                yield "event: creator.finished\ndata: {}\n\n"
                break
            await asyncio.sleep(0.8)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
