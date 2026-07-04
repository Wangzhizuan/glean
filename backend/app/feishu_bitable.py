"""Feishu Bitable (多维表格) sync for Glean creator jobs.

Wraps the locally-authenticated ``lark-cli base`` shortcuts to:
  1. create a new Base with a pre-built schema (``+base-create``),
  2. resolve the created table id (``+table-list``),
  3. batch-write creator video rows (``+record-batch-create``).

Field types were validated against a real Base (see technical design §8):
  - hyperlink / cover -> ``text`` + ``style.type: url``
  - counts / duration  -> ``number`` + ``style.precision: 0``
  - publish time       -> ``datetime`` (value string ``YYYY-MM-DD HH:mm:ss``)

We shell out to ``lark-cli`` rather than calling the OpenAPI directly so the
existing local login state is reused with no extra auth code.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger("glean.feishu_bitable")

# Column order is the single source of truth for both schema and rows.
COLUMNS: List[str] = [
    "视频标题",
    "标签",
    "视频链接",
    "封面",
    "点赞",
    "评论",
    "收藏",
    "转发",
    "时长(秒)",
    "发布时间",
    "逐字稿",
    "内容总结",
    "精彩金句",
]

_FIELD_SCHEMA: List[Dict[str, Any]] = [
    {"type": "text", "name": "视频标题"},
    {"type": "text", "name": "标签"},
    {"type": "text", "name": "视频链接", "style": {"type": "url"}},
    {"type": "text", "name": "封面", "style": {"type": "url"}},
    {"type": "number", "name": "点赞", "style": {"type": "plain", "precision": 0}},
    {"type": "number", "name": "评论", "style": {"type": "plain", "precision": 0}},
    {"type": "number", "name": "收藏", "style": {"type": "plain", "precision": 0}},
    {"type": "number", "name": "转发", "style": {"type": "plain", "precision": 0}},
    {"type": "number", "name": "时长(秒)", "style": {"type": "plain", "precision": 0}},
    {"type": "datetime", "name": "发布时间", "style": {"format": "yyyy-MM-dd"}},
    {"type": "text", "name": "逐字稿"},
    {"type": "text", "name": "内容总结"},
    {"type": "text", "name": "精彩金句"},
]

_BATCH_SIZE = 100


class FeishuError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class BitableTarget:
    base_token: str
    table_id: str
    url: str


def _find_lark_cli() -> Optional[str]:
    for name in ("lark-cli", "lark"):
        path = shutil.which(name)
        if path:
            return path
    return None


def feishu_readiness() -> Dict[str, Any]:
    """Report whether ``lark-cli`` is available for Bitable writes."""
    binary = _find_lark_cli()
    if not binary:
        return {
            "ready": False,
            "larkCli": {"available": False},
            "message": (
                "未检测到 lark-cli，无法写入飞书多维表格。请安装并登录："
                "npm i -g @larksuite/cli && lark-cli auth login。"
            ),
        }
    return {"ready": True, "larkCli": {"available": True}, "message": None}


def _run_lark(argv: List[str], timeout: int = 90) -> Dict[str, Any]:
    binary = _find_lark_cli()
    if not binary:
        raise FeishuError(
            "FEISHU_CLI_MISSING",
            "未检测到 lark-cli。请安装并登录：npm i -g @larksuite/cli && lark-cli auth login。",
        )
    cmd = [binary] + argv + ["--format", "json"]
    logger.info("Running: %s ... (%d args)", " ".join(cmd[:4]), len(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        raise FeishuError("FEISHU_CLI_FAILED", f"lark-cli 调用失败：{e}")
    raw = (result.stdout or "").strip()
    if not raw:
        raise FeishuError(
            "FEISHU_CLI_FAILED",
            f"lark-cli 无输出（returncode={result.returncode}）：{(result.stderr or '')[:200]}",
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise FeishuError("FEISHU_CLI_FAILED", f"lark-cli 输出非 JSON：{raw[:200]}")
    if not data.get("ok"):
        err = data.get("error") or {}
        msg = err.get("message") or "未知错误"
        if "auth" in str(err).lower() or "token" in str(err).lower():
            raise FeishuError(
                "FEISHU_AUTH_REQUIRED",
                f"飞书登录态失效，请运行 lark-cli auth login 后重试。原始错误：{msg}",
            )
        raise FeishuError("FEISHU_CLI_FAILED", f"飞书命令失败：{msg}")
    return data


def create_base(name: str, folder_token: Optional[str] = None) -> BitableTarget:
    """Create a new Bitable with the creator video schema; return its identifiers."""
    argv = [
        "base", "+base-create",
        "--name", name,
        "--table-name", "视频文案",
        "--fields", json.dumps(_FIELD_SCHEMA, ensure_ascii=False),
    ]
    if folder_token:
        argv += ["--folder-token", folder_token]
    data = _run_lark(argv)
    base = (data.get("data") or {}).get("base") or {}
    base_token = base.get("base_token")
    url = base.get("url") or ""
    if not base_token:
        raise FeishuError("FEISHU_CREATE_FAILED", "建表成功但未返回 base_token。")

    table_id = _resolve_table_id(base_token, "视频文案")
    return BitableTarget(base_token=base_token, table_id=table_id, url=url)


def _resolve_table_id(base_token: str, table_name: str) -> str:
    data = _run_lark(["base", "+table-list", "--base-token", base_token])
    tables = (data.get("data") or {}).get("tables") or []
    for table in tables:
        if table.get("name") == table_name:
            return table.get("id") or table.get("table_id") or ""
    # Fall back to the first table if the name lookup fails.
    if tables:
        return tables[0].get("id") or tables[0].get("table_id") or ""
    raise FeishuError("FEISHU_TABLE_NOT_FOUND", "未能在新建的多维表中找到数据表。")


def _row_from_video(video: Dict[str, Any]) -> List[Any]:
    """Build a row (aligned to COLUMNS) from a creator_videos dict.

    Empty cover/text stays as None (empty cell). Numbers pass through as-is.
    """
    def num(value: Any) -> Any:
        return value if isinstance(value, (int, float)) else None

    duration_ms = video.get("duration_ms") or 0
    duration_s = round(duration_ms / 1000) if duration_ms else None
    return [
        video.get("title") or None,
        video.get("tags") or None,
        video.get("video_url") or None,
        video.get("cover_url") or None,
        num(video.get("like_count")),
        num(video.get("comment_count")),
        num(video.get("collect_count")),
        num(video.get("share_count")),
        duration_s,
        video.get("published_at") or None,
        video.get("transcript") or None,
        video.get("summary") or None,
        video.get("quotes") or None,
    ]


def write_records(target: BitableTarget, videos: List[Dict[str, Any]]) -> int:
    """Batch-write creator video rows into the Bitable. Returns rows written."""
    if not videos:
        return 0
    total = 0
    for offset in range(0, len(videos), _BATCH_SIZE):
        chunk = videos[offset:offset + _BATCH_SIZE]
        payload = {
            "fields": COLUMNS,
            "rows": [_row_from_video(v) for v in chunk],
        }
        _run_lark([
            "base", "+record-batch-create",
            "--base-token", target.base_token,
            "--table-id", target.table_id,
            "--json", json.dumps(payload, ensure_ascii=False),
        ], timeout=120)
        total += len(chunk)
    return total


def sync_creator_videos(
    base_name: str,
    videos: List[Dict[str, Any]],
    folder_token: Optional[str] = None,
) -> BitableTarget:
    """Create a Bitable and write all creator videos into it."""
    target = create_base(base_name, folder_token=folder_token)
    write_records(target, videos)
    return target
