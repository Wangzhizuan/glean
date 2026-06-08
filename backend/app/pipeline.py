"""Real processing pipeline for Shiju.

Implements: platform adapters (yt-dlp), FFmpeg audio normalization,
mlx-whisper ASR, and Ollama summarization/quote extraction.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("shiju.pipeline")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = Path(os.getenv("SHIJU_DATA_DIR", Path(__file__).resolve().parents[2] / ".data")).expanduser()
TASKS_DIR = DATA_DIR / "tasks"
OLLAMA_HOST = os.getenv("SHIJU_OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("SHIJU_OLLAMA_MODEL", "qwen2.5:7b")
WHISPER_MODEL = os.getenv("SHIJU_WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
# Browser to extract cookies from. Set to "chrome", "firefox", "safari", etc.
# Needed for Bilibili/Douyin anti-scraping (HTTP 412).
COOKIES_FROM_BROWSER = os.getenv("SHIJU_COOKIES_BROWSER", "chrome")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class VideoMetadata:
    platform: str
    source_url: str
    canonical_url: str
    source_id: str
    title: str
    author: str
    duration_ms: int
    cover_url: Optional[str] = None
    published_at: Optional[str] = None


@dataclass
class SubtitleSegment:
    index: int
    start_ms: int
    end_ms: int
    text: str


@dataclass
class SubtitleResult:
    source: str  # platform_manual, platform_auto, local_asr
    language: str
    segments: List[SubtitleSegment] = field(default_factory=list)

    @property
    def plain_text(self) -> str:
        return "".join(seg.text for seg in self.segments)

    @property
    def word_count(self) -> int:
        return len(self.plain_text)


@dataclass
class SummaryResult:
    overview: str
    core_thesis: str
    detailed_summary: str
    key_points: List[Dict[str, str]]
    content_structure: List[Dict[str, str]]
    action_items: List[str]
    target_audience: List[str]
    terms: List[Dict[str, str]]
    conclusions: List[str]


@dataclass
class QuoteResult:
    text: str
    start_ms: int
    end_ms: int
    source_segment_ids: List[int]
    is_polished: bool


# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------


def check_command(name: str) -> bool:
    return shutil.which(name) is not None


def check_dependencies() -> Dict[str, bool]:
    return {
        "ffmpeg": check_command("ffmpeg"),
        "yt-dlp": check_command("yt-dlp"),
    }


# ---------------------------------------------------------------------------
# Platform Adapter: yt-dlp based (Bilibili, YouTube, Douyin)
# ---------------------------------------------------------------------------


def _is_youtube_url(url: str) -> bool:
    return bool(re.search(r"(?:youtube\.com|youtu\.be)", url, re.IGNORECASE))


def _has_deno() -> bool:
    """Detect if a JS runtime usable by yt-dlp's EJS solver is available."""
    return shutil.which("deno") is not None


def _youtube_extractor_args() -> List[str]:
    """Workaround for YouTube n-sig JavaScript challenge.

    Strategy:
    - If Deno is installed, the default web client can solve the n-sig
      challenge, so we don't override the player_client.
    - Otherwise we fall back to alternative player clients that don't
      require JS solving. tv_simply now needs a GVS PO Token, so we
      prefer android/web_safari which still return progressive audio.
    """
    if _has_deno():
        return []
    return [
        "--extractor-args",
        "youtube:player_client=android,web_safari,mweb;skip=hls,dash",
    ]


def _run_ytdlp(args: List[str], timeout: int = 120, url: Optional[str] = None) -> subprocess.CompletedProcess:
    """Run yt-dlp with given args, raise on failure."""
    # Use python3 -m yt_dlp to ensure we use the same Python env as the backend,
    # avoiding SSL/urllib3 incompatibility with older Python user-site installs.
    import sys
    target_url = url or (args[-1] if args else "")
    is_youtube = _is_youtube_url(target_url)
    # YouTube alt player clients (tv_simply/ios/mweb) don't accept browser cookies
    # and would otherwise be skipped, falling back to the broken web client.
    # Drop cookies for YouTube; keep them for Bilibili/Douyin which need them.
    cookie_args = (
        []
        if is_youtube
        else (["--cookies-from-browser", COOKIES_FROM_BROWSER] if COOKIES_FROM_BROWSER else [])
    )
    extractor_args = _youtube_extractor_args() if is_youtube else []
    cmd = [sys.executable, "-m", "yt_dlp"] + cookie_args + extractor_args + args
    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        # Filter out urllib3 warnings from stderr
        stderr = "\n".join(
            line for line in result.stderr.splitlines()
            if "NotOpenSSLWarning" not in line and "urllib3" not in line
        )
        if stderr.strip():
            logger.error("yt-dlp failed: %s", stderr[:500])
            # Provide a more actionable hint for the YouTube n-sig issue.
            if "n challenge" in stderr or "Requested format is not available" in stderr or "PO Token" in stderr:
                deno_hint = (
                    "" if _has_deno()
                    else "建议安装 Deno 作为 JS 运行时：brew install deno；"
                )
                raise RuntimeError(
                    "yt-dlp 无法获取 YouTube 音频格式（n-sig 解码失败或缺少 PO Token）。"
                    f"请先升级 yt-dlp：pip3 install -U yt-dlp；{deno_hint}"
                    "完成后请重试。原始错误：" + stderr[:200]
                )
            raise RuntimeError(f"yt-dlp error: {stderr[:200]}")
    return result


def resolve_and_fetch_metadata(url: str, platform: str) -> VideoMetadata:
    """Use yt-dlp --dump-json to get video metadata without downloading."""
    result = _run_ytdlp([
        "--dump-json",
        "--no-download",
        "--no-playlist",
        url,
    ], timeout=60)
    info = json.loads(result.stdout)
    duration_s = info.get("duration") or 0
    return VideoMetadata(
        platform=platform,
        source_url=url,
        canonical_url=info.get("webpage_url") or url,
        source_id=info.get("id") or "",
        title=info.get("title") or info.get("fulltitle") or "未知标题",
        author=info.get("uploader") or info.get("channel") or "未知作者",
        duration_ms=int(duration_s * 1000),
        cover_url=info.get("thumbnail"),
        published_at=info.get("upload_date"),
    )


def fetch_subtitles(url: str, platform: str, language: str = "zh") -> Optional[SubtitleResult]:
    """Try to fetch existing platform subtitles via yt-dlp.

    Returns None if no subtitle is available.
    """
    with tempfile.TemporaryDirectory(prefix="shiju_sub_") as tmp_dir:
        output_template = str(Path(tmp_dir) / "sub")
        # Try to write subtitle only (no download)
        subtitle_languages = "zh.*,zh-Hans.*,zh-Hant.*,en.*" if language == "auto" else f"{language}.*,zh.*,en.*"
        try:
            _run_ytdlp([
                "--write-subs",
                "--write-auto-subs",
                "--sub-langs", subtitle_languages,
                "--sub-format", "json3/srv3/vtt/srt/best",
                "--skip-download",
                "--no-playlist",
                "-o", output_template,
                url,
            ], timeout=60)
        except RuntimeError:
            return None

        # Find any subtitle file
        sub_files = list(Path(tmp_dir).glob("sub.*"))
        if not sub_files:
            return None

        # Parse the subtitle file
        sub_file = sub_files[0]
        segments = _parse_subtitle_file(sub_file)
        if not segments:
            return None

        source = "platform_auto" if "auto" in sub_file.name.lower() else "platform_manual"
        detected_language = _subtitle_language_from_filename(sub_file.name, language)
        return SubtitleResult(source=source, language=detected_language, segments=segments)


def _subtitle_language_from_filename(filename: str, fallback: str) -> str:
    lowered = filename.lower()
    if any(marker in lowered for marker in (".zh", "zh-", "zh_")):
        return "zh"
    if any(marker in lowered for marker in (".en", "en-", "en_")):
        return "en"
    return fallback if fallback != "auto" else "unknown"


def _parse_subtitle_file(path: Path) -> List[SubtitleSegment]:
    """Parse VTT/SRT/JSON3 subtitle file into segments."""
    content = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()

    if suffix == ".json3" or suffix == ".json":
        return _parse_json3_subtitle(content)
    elif suffix in (".vtt", ".srt"):
        return _parse_srt_vtt(content)
    return []


def _parse_json3_subtitle(content: str) -> List[SubtitleSegment]:
    """Parse YouTube json3 format."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return []

    segments = []
    events = data.get("events") or []
    idx = 0
    for event in events:
        start_ms = event.get("tStartMs", 0)
        duration_ms = event.get("dDurationMs", 0)
        segs = event.get("segs") or []
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if text and text != "\n":
            segments.append(SubtitleSegment(
                index=idx,
                start_ms=start_ms,
                end_ms=start_ms + duration_ms,
                text=text,
            ))
            idx += 1
    return segments


def _parse_srt_vtt(content: str) -> List[SubtitleSegment]:
    """Parse SRT/VTT format into segments."""
    # Remove VTT header
    content = re.sub(r"^WEBVTT.*?\n\n", "", content, flags=re.DOTALL)
    # Match timestamp blocks
    pattern = re.compile(
        r"(\d{1,2}:)?(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*"
        r"(\d{1,2}:)?(\d{2}):(\d{2})[.,](\d{3})\s*\n(.*?)(?:\n\n|\Z)",
        re.DOTALL,
    )
    segments = []
    for idx, m in enumerate(pattern.finditer(content)):
        h1 = int(m.group(1).rstrip(":")) if m.group(1) else 0
        m1 = int(m.group(2))
        s1 = int(m.group(3))
        ms1 = int(m.group(4))
        h2 = int(m.group(5).rstrip(":")) if m.group(5) else 0
        m2 = int(m.group(6))
        s2 = int(m.group(7))
        ms2 = int(m.group(8))
        start = (h1 * 3600 + m1 * 60 + s1) * 1000 + ms1
        end = (h2 * 3600 + m2 * 60 + s2) * 1000 + ms2
        text = re.sub(r"<[^>]+>", "", m.group(9)).strip()
        text = re.sub(r"\d+\n?$", "", text).strip()
        if text:
            segments.append(SubtitleSegment(index=idx, start_ms=start, end_ms=end, text=text))
    return segments


def download_audio(url: str, task_dir: Path) -> Path:
    """Download audio using yt-dlp, output as best audio format."""
    task_dir.mkdir(parents=True, exist_ok=True)
    output_path = task_dir / "source_audio.%(ext)s"
    _run_ytdlp([
        "--extract-audio",
        "--audio-format", "best",
        "--audio-quality", "0",
        "--no-playlist",
        # Explicit format fallback chain: prefer m4a/mp3 progressive,
        # then any bestaudio, then any best stream. This avoids the
        # "Requested format is not available" error when only certain
        # player_clients return playable streams.
        "-f", "bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio/best",
        "-o", str(output_path),
        url,
    ], timeout=600, url=url)
    # Find the downloaded audio file
    audio_files = list(task_dir.glob("source_audio.*"))
    if not audio_files:
        raise RuntimeError("yt-dlp 未能下载音频文件")
    return audio_files[0]


# ---------------------------------------------------------------------------
# FFmpeg: Audio normalization
# ---------------------------------------------------------------------------


def normalize_audio(input_path: Path, output_path: Path) -> Path:
    """Convert audio to 16kHz mono PCM WAV for Whisper."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(output_path),
    ]
    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg 音频标准化失败: {result.stderr[:200]}")
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("FFmpeg 输出文件为空")
    return output_path


def get_audio_duration_ms(audio_path: Path) -> int:
    """Get audio duration in milliseconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "json",
        str(audio_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return 0
    try:
        data = json.loads(result.stdout)
        duration = float(data["format"]["duration"])
        return int(duration * 1000)
    except (json.JSONDecodeError, KeyError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# mlx-whisper ASR
# ---------------------------------------------------------------------------


def transcribe_with_whisper(audio_path: Path, model: str = WHISPER_MODEL, language: str = "zh") -> SubtitleResult:
    """Transcribe audio using mlx-whisper.

    Tries to import mlx_whisper directly; falls back to whisper CLI if unavailable.
    """
    try:
        import mlx_whisper  # type: ignore
        # Normalize model name to a full HF repo path. The frontend may pass a
        # short alias like "large-v3-turbo"; map it to mlx-community/whisper-*.
        repo = model if "/" in model else f"mlx-community/whisper-{model}"
        logger.info("Using mlx_whisper with repo=%s", repo)
        transcribe_options: Dict[str, Any] = {
            "path_or_hf_repo": repo,
            "word_timestamps": True,
        }
        if language and language != "auto":
            transcribe_options["language"] = language
        result = mlx_whisper.transcribe(str(audio_path), **transcribe_options)
        segments = []
        for idx, seg in enumerate(result.get("segments") or []):
            segments.append(SubtitleSegment(
                index=idx,
                start_ms=int(seg["start"] * 1000),
                end_ms=int(seg["end"] * 1000),
                text=seg["text"].strip(),
            ))
        detected_language = result.get("language") or (
            language if language != "auto" else "unknown"
        )
        return SubtitleResult(
            source="local_asr",
            language=detected_language,
            segments=segments,
        )
    except ImportError:
        logger.warning("mlx_whisper not installed, trying whisper CLI fallback")
        return _transcribe_with_cli(audio_path, model, language)


def _transcribe_with_cli(audio_path: Path, model: str, language: str) -> SubtitleResult:
    """Fallback: use whisper CLI (openai-whisper or whisper.cpp)."""
    # Try openai-whisper CLI
    whisper_cmd = shutil.which("whisper")
    if not whisper_cmd:
        raise RuntimeError(
            "未安装 mlx-whisper 或 whisper CLI。请运行: pip install mlx-whisper"
        )

    output_dir = audio_path.parent / "whisper_output"
    output_dir.mkdir(exist_ok=True)
    cmd = [
        whisper_cmd,
        str(audio_path),
        "--model", model.replace("-mlx", ""),
        "--output_format", "json",
        "--output_dir", str(output_dir),
    ]
    if language and language != "auto":
        cmd.extend(["--language", language])
    logger.info("Running whisper CLI: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        raise RuntimeError(f"Whisper CLI 转写失败: {result.stderr[:200]}")

    # Parse output JSON
    json_files = list(output_dir.glob("*.json"))
    if not json_files:
        raise RuntimeError("Whisper 未生成输出文件")

    data = json.loads(json_files[0].read_text())
    segments = []
    for idx, seg in enumerate(data.get("segments") or []):
        segments.append(SubtitleSegment(
            index=idx,
            start_ms=int(seg["start"] * 1000),
            end_ms=int(seg["end"] * 1000),
            text=seg["text"].strip(),
        ))
    detected_language = data.get("language") or (
        language if language != "auto" else "unknown"
    )
    return SubtitleResult(
        source="local_asr",
        language=detected_language,
        segments=segments,
    )


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------


def clean_transcript(subtitle: SubtitleResult) -> SubtitleResult:
    """Basic text cleaning: merge very short segments, normalize whitespace."""
    if not subtitle.segments:
        return subtitle

    cleaned = []
    for seg in subtitle.segments:
        text = seg.text.strip()
        # Normalize unicode whitespace
        text = re.sub(r"\s+", " ", text)
        if not text:
            continue
        # Merge with previous if < 1 second and could be continuation
        if (cleaned
                and seg.start_ms - cleaned[-1].end_ms < 100
                and len(cleaned[-1].text) < 10):
            cleaned[-1] = SubtitleSegment(
                index=cleaned[-1].index,
                start_ms=cleaned[-1].start_ms,
                end_ms=seg.end_ms,
                text=cleaned[-1].text + text,
            )
        else:
            cleaned.append(SubtitleSegment(
                index=len(cleaned),
                start_ms=seg.start_ms,
                end_ms=seg.end_ms,
                text=text,
            ))

    return SubtitleResult(
        source=subtitle.source,
        language=subtitle.language,
        segments=cleaned,
    )


# ---------------------------------------------------------------------------
# Ollama: Local LLM for summarization and quotes
# ---------------------------------------------------------------------------


def _call_ollama(prompt: str, system: str = "", model: str = OLLAMA_MODEL) -> str:
    """Call Ollama HTTP API for chat completion."""
    import urllib.request
    import urllib.error

    url = f"{OLLAMA_HOST}/api/chat"
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.3},
    }).encode()

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
            return data.get("message", {}).get("content", "")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Ollama 服务不可用: {e}")


def _extract_json_from_response(text: str) -> Any:
    """Extract JSON object/array from LLM response that might contain markdown."""
    # Try direct parse
    text = text.strip()
    if text.startswith("{") or text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    # Try to find JSON in code blocks
    match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # Try to find first { ... } or [ ... ]
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = text.find(start_char)
        end = text.rfind(end_char)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    raise RuntimeError(f"无法从 LLM 响应中提取 JSON: {text[:200]}")


def _contains_mostly_chinese(text: str) -> bool:
    letters = re.findall(r"[A-Za-z\u4e00-\u9fff]", text)
    if not letters:
        return True
    chinese = re.findall(r"[\u4e00-\u9fff]", text)
    return len(chinese) / len(letters) >= 0.35


def translate_segments_to_chinese(
    subtitle: SubtitleResult,
) -> SubtitleResult:
    """Translate non-Chinese transcript segments while preserving timestamps."""
    if subtitle.language.startswith("zh") or _contains_mostly_chinese(subtitle.plain_text):
        return subtitle

    translated: List[SubtitleSegment] = []
    chunk_size = 36
    for offset in range(0, len(subtitle.segments), chunk_size):
        chunk = subtitle.segments[offset:offset + chunk_size]
        payload = [{"index": segment.index, "text": segment.text} for segment in chunk]
        prompt = f"""请把下面的视频逐字稿片段准确翻译成简体中文。

要求：
- 保留原意、语气、专有名词和技术术语，不要总结或删减
- 每个输入 index 必须返回且只能返回一次
- 只返回 JSON 数组，不要返回解释

输入：
{json.dumps(payload, ensure_ascii=False)}

输出格式：
[{{"index": 0, "text": "中文翻译"}}]"""
        response = _call_ollama(
            prompt,
            system="你是专业的视频字幕翻译员，负责把非中文逐字稿忠实翻译成简体中文。",
        )
        data = _extract_json_from_response(response)
        translated_by_index = {
            int(item["index"]): str(item["text"]).strip()
            for item in data
            if isinstance(item, dict) and "index" in item and "text" in item
        }
        for segment in chunk:
            translated.append(SubtitleSegment(
                index=segment.index,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                text=translated_by_index.get(segment.index, segment.text),
            ))

    return SubtitleResult(
        source=f"{subtitle.source}_translated",
        language="zh",
        segments=translated,
    )


def translate_title_to_chinese(title: str) -> str:
    if _contains_mostly_chinese(title):
        return title
    response = _call_ollama(
        f"把下面的视频标题准确翻译成简体中文，只返回翻译后的标题：\n{title}",
        system="你是专业的视频标题翻译员。保留人名、产品名和专有名词，不要添加解释。",
    )
    translated = response.strip().strip('"“”')
    return translated or title


def _chunk_text(text: str, max_chars: int = 9000) -> List[str]:
    if len(text) <= max_chars:
        return [text]
    chunks = []
    for start in range(0, len(text), max_chars):
        chunks.append(text[start:start + max_chars])
    return chunks


def generate_summary(transcript_text: str, title: str) -> SummaryResult:
    """Generate structured summary using Ollama."""
    chunks = _chunk_text(transcript_text)
    source_text = transcript_text
    if len(chunks) > 1:
        chunk_summaries = []
        for index, chunk in enumerate(chunks, start=1):
            chunk_prompt = f"""这是视频逐字稿的第 {index}/{len(chunks)} 部分：
{chunk}

请用简体中文提取这部分的事实、论点、案例、术语、结论和可执行建议。
保留具体细节，不要泛泛而谈。返回 JSON：
{{
  "summary": "详细分段摘要",
  "keyPoints": ["要点"],
  "examples": ["案例或论据"],
  "terms": [{{"term": "术语", "explanation": "解释"}}],
  "actionItems": ["行动建议"]
}}"""
            chunk_response = _call_ollama(
                chunk_prompt,
                system="你是严谨的中文内容分析师。所有输出使用简体中文，且只能依据逐字稿。",
            )
            chunk_summaries.append(_extract_json_from_response(chunk_response))
        source_text = json.dumps(chunk_summaries, ensure_ascii=False)

    system_prompt = (
        "你是资深中文内容研究员和知识编辑。请根据逐字稿生成信息密度高、"
        "层次清晰、可用于复习和行动的深度总结。无论原视频是什么语言，"
        "所有输出都必须使用自然、准确的简体中文。所有内容必须基于原文，"
        "不要编造不存在的信息，也不要用空泛套话凑数。"
        "请直接返回 JSON，不要包含其他文字。"
    )
    user_prompt = f"""视频标题：{title}

以下是视频逐字稿或各分段的结构化摘要：
{source_text}

请生成以下 JSON 格式的总结：
{{
  "overview": "用 4-6 句话概括视频讨论范围、核心问题、主要结论和价值",
  "coreThesis": "用一段话说明视频最核心的主张与论证逻辑",
  "detailedSummary": "不少于 500 字的详细中文总结，按视频推进顺序覆盖重要观点、论据、案例、分歧和结论",
  "keyPoints": [
    {{"title": "要点标题", "content": "2-4 句话说明观点、依据、案例及实际影响"}}
  ],
  "contentStructure": [
    {{"section": "内容阶段或主题", "summary": "这一部分讲了什么，以及它与前后内容的关系"}}
  ],
  "actionItems": ["具体、可执行、可检查的行动建议"],
  "targetAudience": ["最适合观看这段视频的人群及原因"],
  "terms": [{{"term": "术语或专有名词", "explanation": "结合视频语境的中文解释"}}],
  "conclusions": ["视频明确得出的结论或值得保留的判断"]
}}

要求：
- keyPoints 提取 6-10 个核心观点，按重要程度排序
- contentStructure 提取 4-10 个内容阶段
- actionItems 提取 4-8 个具体建议
- terms 提取 3-10 个重要术语；没有则返回空数组
- conclusions 提取 3-8 条
- detailedSummary 必须保留人物、产品、数字、案例和因果关系
- 不要重复同一句话，不要添加原文没有的事实"""

    response = _call_ollama(user_prompt, system=system_prompt)
    data = _extract_json_from_response(response)
    return SummaryResult(
        overview=data.get("overview", ""),
        core_thesis=data.get("coreThesis", ""),
        detailed_summary=data.get("detailedSummary", ""),
        key_points=data.get("keyPoints", []),
        content_structure=data.get("contentStructure", []),
        action_items=data.get("actionItems", []),
        target_audience=data.get("targetAudience", []),
        terms=data.get("terms", []),
        conclusions=data.get("conclusions", []),
    )


def generate_quotes(
    segments: List[SubtitleSegment], title: str
) -> List[QuoteResult]:
    """Extract notable quotes from transcript segments using Ollama."""
    segment_groups: List[List[SubtitleSegment]] = []
    current_group: List[SubtitleSegment] = []
    current_chars = 0
    for segment in segments:
        if current_group and current_chars + len(segment.text) > 9000:
            segment_groups.append(current_group)
            current_group = []
            current_chars = 0
        current_group.append(segment)
        current_chars += len(segment.text)
    if current_group:
        segment_groups.append(current_group)

    candidates: List[QuoteResult] = []
    for group_index, group in enumerate(segment_groups, start=1):
        segment_text = "\n".join(
            f"[{s.index}] ({s.start_ms // 1000}s-{s.end_ms // 1000}s) {s.text}"
            for s in group
        )
        target_count = "12-20" if len(segment_groups) == 1 else "3-6"
        system_prompt = (
            "你是专业的中文内容编辑。请从逐字稿中提取观点完整、表达有力、"
            "可脱离上下文理解的精彩原话。无论原视频是什么语言，输出必须是简体中文。"
            "金句必须能回溯到指定片段，不得凭空创作。只返回 JSON 数组。"
        )
        user_prompt = f"""视频标题：{title}
这是第 {group_index}/{len(segment_groups)} 组带编号逐字稿：
{segment_text}

请提取 {target_count} 条精彩金句，返回 JSON 数组：
[
  {{
    "text": "简体中文金句",
    "sourceSegmentIds": [片段编号],
    "isPolished": false
  }}
]

要求：
- 覆盖核心论点、方法、判断、警示、案例结论等不同类型
- 原句完整可读时保持原话（isPolished: false）
- 翻译或为增强中文可读性做轻微改写时标记 isPolished: true
- sourceSegmentIds 为对应的片段编号数组
- 不要选择重复含义的句子，不要只选短口号"""

        response = _call_ollama(user_prompt, system=system_prompt)
        data = _extract_json_from_response(response)
        for item in (data if isinstance(data, list) else []):
            seg_ids = item.get("sourceSegmentIds", [])
            referenced = [seg for seg in group if seg.index in seg_ids]
            if not referenced or not item.get("text"):
                continue
            candidates.append(QuoteResult(
                text=item["text"].strip(),
                start_ms=min(seg.start_ms for seg in referenced),
                end_ms=max(seg.end_ms for seg in referenced),
                source_segment_ids=seg_ids,
                is_polished=item.get("isPolished", False),
            ))

    unique: List[QuoteResult] = []
    seen = set()
    for quote in candidates:
        normalized = re.sub(r"[\s，。！？、,.!?\"“”']", "", quote.text)
        if len(normalized) < 8 or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(quote)
    if len(unique) < 12:
        used_segment_ids = {
            segment_id
            for quote in unique
            for segment_id in quote.source_segment_ids
        }
        for segment in segments:
            normalized = re.sub(r"[\s，。！？、,.!?\"“”']", "", segment.text)
            if (
                len(unique) >= 12
                or segment.index in used_segment_ids
                or len(normalized) < 12
                or normalized in seen
            ):
                continue
            seen.add(normalized)
            unique.append(QuoteResult(
                text=segment.text,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                source_segment_ids=[segment.index],
                is_polished=False,
            ))
    return unique[:20]


# ---------------------------------------------------------------------------
# Full pipeline orchestration
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    metadata: VideoMetadata
    subtitle: SubtitleResult
    summary: Optional[SummaryResult] = None
    quotes: List[QuoteResult] = field(default_factory=list)


def run_pipeline(
    url: str,
    platform: str,
    task_id: str,
    on_stage: Any = None,  # callback(stage: str, progress: float)
    options: Optional[Dict[str, Any]] = None,
) -> PipelineResult:
    """Execute the full processing pipeline for a single URL.

    Stages: resolving -> fetching_subtitle -> downloading -> extracting_audio
            -> transcribing -> normalizing -> summarizing -> completed
    """
    opts = options or {}
    source_language = opts.get("sourceLanguage") or opts.get("language", "auto")
    output_language = opts.get("outputLanguage", "zh")
    subtitle_policy = opts.get("subtitlePolicy", "prefer_platform")
    asr_model = opts.get("asrModel", WHISPER_MODEL)

    task_dir = TASKS_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    def notify(stage: str, progress: float):
        if on_stage:
            on_stage(stage, progress)

    # Stage 1: Resolve URL and fetch metadata
    notify("resolving", 0.05)
    metadata = resolve_and_fetch_metadata(url, platform)

    # Stage 2: Try to fetch platform subtitles
    notify("fetching_subtitle", 0.10)
    subtitle: Optional[SubtitleResult] = None
    if subtitle_policy in ("prefer_platform", "platform_only"):
        subtitle = fetch_subtitles(url, platform, source_language)

    # Stage 3-4: If no subtitle, download audio and extract
    if subtitle is None:
        notify("downloading", 0.30)
        audio_source = download_audio(url, task_dir)

        notify("extracting_audio", 0.40)
        normalized_audio = task_dir / "audio.wav"
        normalize_audio(audio_source, normalized_audio)

        # Update duration from actual audio if metadata was 0
        if metadata.duration_ms == 0:
            metadata.duration_ms = get_audio_duration_ms(normalized_audio)

        # Stage 5: Transcribe
        notify("transcribing", 0.60)
        subtitle = transcribe_with_whisper(
            normalized_audio,
            model=asr_model,
            language=source_language,
        )

        # Cleanup source audio to save disk
        if audio_source.exists() and audio_source != normalized_audio:
            audio_source.unlink(missing_ok=True)
    else:
        # Skip download/extract/transcribe stages - jump progress
        notify("downloading", 0.30)
        notify("extracting_audio", 0.40)
        notify("transcribing", 0.60)

    # Stage 6: Clean text
    notify("normalizing", 0.85)
    subtitle = clean_transcript(subtitle)

    # Stage 7: Summarize and extract quotes
    notify("summarizing", 0.92)
    summary: Optional[SummaryResult] = None
    quotes: List[QuoteResult] = []

    # Check if Ollama is available
    ollama_available = _check_ollama_available()
    if ollama_available and subtitle.plain_text:
        try:
            if output_language == "zh":
                subtitle = translate_segments_to_chinese(subtitle)
                metadata.title = translate_title_to_chinese(metadata.title)
            summary = generate_summary(subtitle.plain_text, metadata.title)
        except Exception as e:
            logger.warning("Summary generation failed: %s", e)
            summary = SummaryResult(
                overview="总结生成失败，请确保 Ollama 服务正常运行。",
                core_thesis="",
                detailed_summary="",
                key_points=[],
                content_structure=[],
                action_items=[],
                target_audience=[],
                terms=[],
                conclusions=[],
            )
        try:
            quotes = generate_quotes(subtitle.segments, metadata.title)
        except Exception as e:
            logger.warning("Quote generation failed: %s", e)
    elif not ollama_available:
        summary = SummaryResult(
            overview="Ollama 服务未运行，跳过自动总结。请启动 Ollama 后重试。",
            core_thesis="",
            detailed_summary="",
            key_points=[],
            content_structure=[],
            action_items=[],
            target_audience=[],
            terms=[],
            conclusions=[],
        )

    return PipelineResult(
        metadata=metadata,
        subtitle=subtitle,
        summary=summary,
        quotes=quotes,
    )


def _check_ollama_available() -> bool:
    """Check if Ollama service is reachable."""
    import urllib.request
    import urllib.error

    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags")
        with urllib.request.urlopen(req, timeout=5):
            return True
    except (urllib.error.URLError, OSError):
        return False
