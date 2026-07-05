"""Douyin creator harvesting for Glean.

Given a creator homepage URL (or a creator name), harvest the creator's
video list together with engagement metrics by driving headless Chromium
with the local Chrome login state and intercepting the ``aweme/post`` XHR
responses.

This module is import-light: Playwright and browser_cookie3 are only
imported on demand so the backend still boots without them installed.

Validated by ``creator_poc.py`` against a real creator homepage: list +
title + like/comment/collect/share/duration/publish/cover all captured.
"""

from __future__ import annotations

import importlib.util
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger("glean.creator")

DOUYIN_COOKIE_DOMAINS = ["douyin.com"]
POST_API_MARKER = "aweme/v1/web/aweme/post/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class CreatorError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class CreatorVideo:
    aweme_id: str
    video_url: str
    title: str
    like_count: Optional[int] = None
    comment_count: Optional[int] = None
    collect_count: Optional[int] = None
    share_count: Optional[int] = None
    play_count: Optional[int] = None
    duration_ms: Optional[int] = None
    published_at: Optional[str] = None  # ISO date string
    cover_url: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    # 图文帖（一张/多张图片 + 文字），没有可播放的音视频流。
    # 这类内容的文案就是 desc，直接复用即可，不需要走 yt-dlp/whisper。
    is_image_post: bool = False


@dataclass
class CreatorHarvest:
    creator_name: str
    creator_sec_uid: str
    creator_url: str
    videos: List[CreatorVideo] = field(default_factory=list)
    saw_captcha: bool = False


# ---------------------------------------------------------------------------
# Capability & readiness
# ---------------------------------------------------------------------------


def creator_capabilities() -> Dict[str, Any]:
    """Report whether Douyin creator harvesting is usable on this machine."""
    playwright_ok = importlib.util.find_spec("playwright") is not None
    cookie_module = importlib.util.find_spec("browser_cookie3") is not None
    cookie_count = 0
    cookie_error: Optional[str] = None
    if cookie_module:
        try:
            cookie_count = len(_load_chrome_cookies())
        except Exception as e:  # noqa: BLE001
            cookie_error = str(e)[:200]
    ready = playwright_ok and cookie_count > 0
    message: Optional[str] = None
    if not playwright_ok:
        message = (
            "未安装 Playwright。请运行："
            "pip3 install playwright && python3 -m playwright install chromium"
        )
    elif cookie_count == 0:
        message = (
            "未在本机 Chrome 找到抖音登录态。"
            "请先在 Chrome 登录抖音并访问过目标博主主页后重试。"
        )
    return {
        "ready": ready,
        "playwright": {"available": playwright_ok},
        "browserCookies": {
            "available": cookie_count > 0,
            "count": cookie_count,
            "error": cookie_error,
        },
        "message": message,
    }


# ---------------------------------------------------------------------------
# URL / input helpers
# ---------------------------------------------------------------------------


def is_douyin_user_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return "douyin.com" in host and "/user/" in (parsed.path or "")


def extract_sec_uid(url: str) -> str:
    match = re.search(r"/user/([A-Za-z0-9_-]+)", url)
    return match.group(1) if match else ""


# ---------------------------------------------------------------------------
# Cookies
# ---------------------------------------------------------------------------


def _load_chrome_cookies() -> List[Dict[str, Any]]:
    """Read Douyin cookies from local Chrome, shaped for Playwright."""
    if importlib.util.find_spec("browser_cookie3") is None:
        return []
    import browser_cookie3  # type: ignore

    try:
        jar = browser_cookie3.chrome(domain_name="douyin.com")
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to read Chrome cookies: %s", e)
        return []

    cookies: List[Dict[str, Any]] = []
    for cookie in jar:
        host = (cookie.domain or "").lstrip(".")
        if not any(keyword in host for keyword in DOUYIN_COOKIE_DOMAINS):
            continue
        cookies.append(
            {
                "name": cookie.name,
                "value": cookie.value or "",
                "domain": cookie.domain,
                "path": cookie.path or "/",
                "secure": bool(cookie.secure),
                "httpOnly": bool(getattr(cookie, "_rest", {}).get("HttpOnly", False)),
                "expires": int(cookie.expires) if cookie.expires else -1,
            }
        )
    return cookies


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _extract_tags(aweme: Dict[str, Any], desc: str) -> List[str]:
    """Extract hashtag names from an aweme.

    Prefer Douyin's structured ``text_extra`` (each entry carries a
    ``hashtag_name``); fall back to a regex over ``desc`` for older payloads.
    """
    tags: List[str] = []
    for extra in aweme.get("text_extra") or []:
        name = (extra.get("hashtag_name") or "").strip()
        if name and name not in tags:
            tags.append(name)
    if not tags and desc:
        for match in re.findall(r"#([^\s#@]+)", desc):
            tag = match.strip()
            if tag and tag not in tags:
                tags.append(tag)
    return tags


def _parse_aweme(aweme: Dict[str, Any]) -> Optional[CreatorVideo]:
    aweme_id = aweme.get("aweme_id") or ""
    if not aweme_id:
        return None
    stats = aweme.get("statistics") or {}
    video = aweme.get("video") or {}
    cover = (video.get("cover") or {}).get("url_list") or []
    desc = (aweme.get("desc") or "").strip()
    # 图文帖判定：抖音图文有 images 列表，且 aweme_type 常为 68。
    # 这类内容没有可播放的音视频流，标题/正文就写在 desc 里。
    images = aweme.get("images") or []
    is_image_post = bool(images) or aweme.get("aweme_type") == 68
    # 图文帖没有视频封面时，回退到第一张图片作为封面。
    cover_url = cover[0] if cover else None
    if not cover_url and images:
        image_urls = (images[0].get("url_list") if isinstance(images[0], dict) else None) or []
        cover_url = image_urls[0] if image_urls else None
    published_at: Optional[str] = None
    create_time = aweme.get("create_time")
    if create_time:
        try:
            from datetime import datetime, timezone

            published_at = datetime.fromtimestamp(
                int(create_time), tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError, OverflowError):
            published_at = None
    return CreatorVideo(
        aweme_id=aweme_id,
        video_url=f"https://www.douyin.com/video/{aweme_id}",
        title=desc,
        like_count=stats.get("digg_count"),
        comment_count=stats.get("comment_count"),
        collect_count=stats.get("collect_count"),
        share_count=stats.get("share_count"),
        play_count=stats.get("play_count"),
        duration_ms=video.get("duration"),
        published_at=published_at,
        cover_url=cover_url,
        tags=_extract_tags(aweme, desc),
        is_image_post=is_image_post,
    )


# ---------------------------------------------------------------------------
# Harvest
# ---------------------------------------------------------------------------


def harvest_creator(
    url: str,
    limit: int = 50,
    on_progress: Any = None,  # callback(discovered: int)
) -> CreatorHarvest:
    """Harvest a Douyin creator's videos + metrics from their homepage URL.

    Raises :class:`CreatorError` on dependency/login/captcha failures so the
    caller can fail the job honestly instead of fabricating a list.
    """
    if importlib.util.find_spec("playwright") is None:
        raise CreatorError(
            "CREATOR_DEP_MISSING",
            "未安装 Playwright。请运行："
            "pip3 install playwright && python3 -m playwright install chromium",
        )

    cookies = _load_chrome_cookies()
    if not cookies:
        raise CreatorError(
            "CREATOR_LOGIN_REQUIRED",
            "未在本机 Chrome 找到抖音登录态。"
            "请先在 Chrome 登录抖音并访问过目标博主主页后重试。",
        )

    from playwright.sync_api import sync_playwright  # type: ignore

    captured: List[CreatorVideo] = []
    seen_ids: set[str] = set()
    saw_captcha = False
    creator_name = ""
    sec_uid = extract_sec_uid(url)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                user_agent=USER_AGENT,
                locale="zh-CN",
                viewport={"width": 1280, "height": 900},
            )
            try:
                context.add_cookies(cookies)
            except Exception as e:  # noqa: BLE001
                logger.warning("add_cookies failed: %s", e)

            page = context.new_page()

            def on_response(response):  # noqa: ANN001
                if POST_API_MARKER not in response.url:
                    return
                try:
                    data = response.json()
                except Exception:  # noqa: BLE001
                    return
                for aweme in data.get("aweme_list") or []:
                    parsed = _parse_aweme(aweme)
                    if parsed and parsed.aweme_id not in seen_ids:
                        seen_ids.add(parsed.aweme_id)
                        captured.append(parsed)
                if on_progress:
                    try:
                        on_progress(len(captured))
                    except Exception:  # noqa: BLE001
                        pass

            page.on("response", on_response)

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:  # noqa: BLE001
                raise CreatorError("CREATOR_FETCH_FAILED", f"打开博主主页失败：{e}")
            page.wait_for_timeout(3000)

            final_url = page.url or url
            if "verify" in final_url or "captcha" in final_url:
                saw_captcha = True

            # Creator name from page title (e.g. "凯莉彭的抖音 - 抖音").
            # The title populates asynchronously after hydration, so poll it
            # for a short while instead of reading it immediately.
            creator_name = ""
            for _ in range(10):
                page_title = page.title() or ""
                name_match = re.match(r"(.+?)的抖音", page_title)
                if name_match:
                    creator_name = name_match.group(1).strip()
                    break
                page.wait_for_timeout(500)

            # Scroll to trigger lazy loading.
            stagnant_rounds = 0
            for _ in range(60):
                if len(captured) >= limit:
                    break
                before = len(captured)
                try:
                    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                except Exception:  # noqa: BLE001
                    break
                page.wait_for_timeout(random.randint(900, 1600))
                after = len(captured)
                if after == before:
                    stagnant_rounds += 1
                    if stagnant_rounds >= 5:
                        break
                else:
                    stagnant_rounds = 0

            if not captured:
                body_snippet = ""
                try:
                    body_snippet = (page.inner_text("body") or "")[:200]
                except Exception:  # noqa: BLE001
                    pass
                if saw_captcha or "验证" in body_snippet:
                    raise CreatorError(
                        "CREATOR_CAPTCHA",
                        "抖音触发了验证码/风控。请在本机 Chrome 登录抖音、"
                        "手动访问该博主主页通过验证后重试。",
                    )
                raise CreatorError(
                    "CREATOR_EMPTY",
                    "未能从该主页抓到任何视频。请确认链接为博主主页、"
                    "本机 Chrome 已登录抖音并访问过该主页后重试。",
                )

            return CreatorHarvest(
                creator_name=creator_name or "抖音博主",
                creator_sec_uid=sec_uid,
                creator_url=final_url,
                videos=captured[:limit],
                saw_captcha=saw_captcha,
            )
        finally:
            browser.close()


def resolve_creator_url_by_name(name: str) -> str:
    """Resolve a creator name to a homepage URL via Douyin user search.

    High-risk / best-effort (§5.5): Douyin search is more fragile than the
    homepage feed. Raises :class:`CreatorError` if no user match is found so
    the frontend can prompt the user to paste a homepage URL instead.
    """
    if importlib.util.find_spec("playwright") is None:
        raise CreatorError(
            "CREATOR_DEP_MISSING",
            "未安装 Playwright，无法按名字搜索博主。请直接粘贴博主主页链接。",
        )
    cookies = _load_chrome_cookies()
    if not cookies:
        raise CreatorError(
            "CREATOR_LOGIN_REQUIRED",
            "未在本机 Chrome 找到抖音登录态，无法按名字搜索。请直接粘贴博主主页链接。",
        )

    from urllib.parse import quote

    from playwright.sync_api import sync_playwright  # type: ignore

    search_url = f"https://www.douyin.com/search/{quote(name)}?type=user"
    resolved: Optional[str] = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(user_agent=USER_AGENT, locale="zh-CN")
            try:
                context.add_cookies(cookies)
            except Exception:  # noqa: BLE001
                pass
            page = context.new_page()
            try:
                page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:  # noqa: BLE001
                raise CreatorError("CREATOR_FETCH_FAILED", f"打开搜索页失败：{e}")
            page.wait_for_timeout(3500)
            try:
                hrefs = page.eval_on_selector_all(
                    "a[href*='/user/']",
                    "els => els.map(e => e.getAttribute('href'))",
                )
            except Exception:  # noqa: BLE001
                hrefs = []
            for href in hrefs:
                if href and "/user/" in href:
                    resolved = href if href.startswith("http") else f"https://www.douyin.com{href}"
                    break
        finally:
            browser.close()

    if not resolved:
        raise CreatorError(
            "CREATOR_NAME_UNRESOLVED",
            f"未能通过名字「{name}」定位到博主。请直接粘贴该博主的主页链接。",
        )
    return resolved
