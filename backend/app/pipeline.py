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
    key_points: List[Dict[str, str]]
    action_items: List[str]


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


def _run_ytdlp(args: List[str], timeout: int = 120) -> subprocess.CompletedProcess:
    """Run yt-dlp with given args, raise on failure."""
    # Use python3 -m yt_dlp to ensure we use the same Python env as the backend,
    # avoiding SSL/urllib3 incompatibility with older Python user-site installs.
    import sys
    cookie_args = ["--cookies-from-browser", COOKIES_FROM_BROWSER] if COOKIES_FROM_BROWSER else []
    cmd = [sys.executable, "-m", "yt_dlp"] + cookie_args + args
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
        try:
            _run_ytdlp([
                "--write-subs",
                "--write-auto-subs",
                "--sub-langs", f"{language}.*,zh.*,en.*",
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
        return SubtitleResult(source=source, language=language, segments=segments)


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
        "-o", str(output_path),
        url,
    ], timeout=600)
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
        result = mlx_whisper.transcribe(
            str(audio_path),
            path_or_hf_repo=repo,
            language=language,
            word_timestamps=True,
        )
        segments = []
        for idx, seg in enumerate(result.get("segments") or []):
            segments.append(SubtitleSegment(
                index=idx,
                start_ms=int(seg["start"] * 1000),
                end_ms=int(seg["end"] * 1000),
                text=seg["text"].strip(),
            ))
        return SubtitleResult(source="local_asr", language=language, segments=segments)
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
        "--language", language,
        "--output_format", "json",
        "--output_dir", str(output_dir),
    ]
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
    return SubtitleResult(source="local_asr", language=language, segments=segments)


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


def generate_summary(transcript_text: str, title: str) -> SummaryResult:
    """Generate structured summary using Ollama."""
    # Truncate transcript if too long (roughly 4000 chars for context)
    max_chars = 6000
    if len(transcript_text) > max_chars:
        transcript_text = transcript_text[:max_chars] + "\n...(内容已截断)"

    system_prompt = (
        "你是一个视频内容分析助手。请根据逐字稿生成结构化摘要。"
        "所有内容必须基于原文，不要编造不存在的信息。"
        "请直接返回 JSON，不要包含其他文字。"
    )
    user_prompt = f"""视频标题：{title}

以下是视频逐字稿：
{transcript_text}

请生成以下 JSON 格式的总结：
{{
  "overview": "用 2-3 句话概括视频核心内容",
  "keyPoints": [
    {{"title": "要点标题", "content": "要点说明（1句话）"}}
  ],
  "actionItems": ["可执行的行动建议1", "行动建议2"]
}}

要求：
- keyPoints 提取 3-5 个核心观点
- actionItems 提取 2-4 个具体可执行的建议
- 所有内容必须来自原文"""

    response = _call_ollama(user_prompt, system=system_prompt)
    data = _extract_json_from_response(response)
    return SummaryResult(
        overview=data.get("overview", ""),
        key_points=data.get("keyPoints", []),
        action_items=data.get("actionItems", []),
    )


def generate_quotes(
    segments: List[SubtitleSegment], title: str
) -> List[QuoteResult]:
    """Extract notable quotes from transcript segments using Ollama."""
    # Build segment text with indices for reference
    segment_text = "\n".join(
        f"[{s.index}] ({s.start_ms // 1000}s-{s.end_ms // 1000}s) {s.text}"
        for s in segments
    )
    # Truncate if needed
    if len(segment_text) > 6000:
        segment_text = segment_text[:6000] + "\n...(已截断)"

    system_prompt = (
        "你是一个文案金句提取助手。请从逐字稿中找出最精彩、最有启发性的原话。"
        "金句必须是视频中实际说过的话或非常接近的改写。"
        "请直接返回 JSON 数组，不要包含其他文字。"
    )
    user_prompt = f"""视频标题：{title}

以下是带编号的逐字稿片段：
{segment_text}

请提取 3-5 条精彩金句，返回 JSON 数组：
[
  {{
    "text": "金句原文",
    "sourceSegmentIds": [片段编号],
    "isPolished": false
  }}
]

要求：
- 金句必须来自原文（isPolished: false）或仅做轻微润色（isPolished: true）
- sourceSegmentIds 为对应的片段编号数组
- 优先选择有洞见、有金句感的话"""

    response = _call_ollama(user_prompt, system=system_prompt)
    data = _extract_json_from_response(response)

    quotes = []
    for item in (data if isinstance(data, list) else []):
        seg_ids = item.get("sourceSegmentIds", [])
        # Find time range from referenced segments
        start_ms = 0
        end_ms = 0
        for seg in segments:
            if seg.index in seg_ids:
                if start_ms == 0 or seg.start_ms < start_ms:
                    start_ms = seg.start_ms
                if seg.end_ms > end_ms:
                    end_ms = seg.end_ms
        quotes.append(QuoteResult(
            text=item.get("text", ""),
            start_ms=start_ms,
            end_ms=end_ms,
            source_segment_ids=seg_ids,
            is_polished=item.get("isPolished", False),
        ))
    return quotes


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
    language = opts.get("language", "zh")
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
        subtitle = fetch_subtitles(url, platform, language)

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
        subtitle = transcribe_with_whisper(normalized_audio, model=asr_model, language=language)

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
            summary = generate_summary(subtitle.plain_text, metadata.title)
        except Exception as e:
            logger.warning("Summary generation failed: %s", e)
            summary = SummaryResult(
                overview="总结生成失败，请确保 Ollama 服务正常运行。",
                key_points=[],
                action_items=[],
            )
        try:
            quotes = generate_quotes(subtitle.segments, metadata.title)
        except Exception as e:
            logger.warning("Quote generation failed: %s", e)
    elif not ollama_available:
        summary = SummaryResult(
            overview="Ollama 服务未运行，跳过自动总结。请启动 Ollama 后重试。",
            key_points=[],
            action_items=[],
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
