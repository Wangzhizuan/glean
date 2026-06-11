"""Article extraction adapters for Shiju.

Supports four sources:
- web: any HTTP(S) page → trafilatura (with Playwright fallback)
- wechat: mp.weixin.qq.com → trafilatura → lxml `#js_content` fallback
- feishu: *.feishu.cn / *.larkoffice.com → Playwright + browser cookies
- xiaohongshu: xiaohongshu.com / xhslink.com → curl-cffi + INITIAL_STATE JSON

All adapters return a uniform :class:`ArticleResult` for downstream summary/quote
generation. Heavy dependencies (playwright/curl-cffi/browser-cookie3) are only
imported on demand so the backend still boots without them installed.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger("shiju.article")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

MIN_BODY_CHARS = 200


# ---------------------------------------------------------------------------
# Data classes & errors
# ---------------------------------------------------------------------------


@dataclass
class ArticleResult:
    source: str
    source_url: str
    canonical_url: str
    title: str
    author: Optional[str]
    published_at: Optional[str]
    plain_text: str
    markdown: str
    word_count: int


class ArticleError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Capability detection
# ---------------------------------------------------------------------------


def article_capabilities() -> Dict[str, Dict[str, bool]]:
    return {
        "trafilatura": {"available": importlib.util.find_spec("trafilatura") is not None},
        "playwright": {"available": importlib.util.find_spec("playwright") is not None},
        "curlCffi": {"available": importlib.util.find_spec("curl_cffi") is not None},
        "browserCookie3": {"available": importlib.util.find_spec("browser_cookie3") is not None},
        "lxml": {"available": importlib.util.find_spec("lxml") is not None},
        "larkCli": {"available": _find_lark_cli() is not None},
    }


def feishu_readiness() -> Dict[str, Any]:
    """Report whether the box is configured to read Feishu docs at all.

    The frontend uses this to surface a clear warning before the user submits
    a Feishu URL, instead of letting the task fail silently. Both fields can
    independently make Feishu work; we only consider the platform "ready" if
    at least one is usable.

    - ``larkCli``: an authenticated ``lark-cli``/``lark`` binary on PATH.
    - ``browserCookies``: at least one Feishu/Lark cookie is reachable from
      the local Chrome cookie store via ``browser_cookie3``.
    """
    lark_cli_ok = _find_lark_cli() is not None
    cookie_count = 0
    cookie_error: Optional[str] = None
    if importlib.util.find_spec("browser_cookie3") is not None:
        try:
            cookie_count = len(
                _load_browser_cookies(["feishu.cn", "larkoffice.com", "feishu-pre.cn"])
            )
        except Exception as e:  # noqa: BLE001
            cookie_error = str(e)[:200]
    cookies_ok = cookie_count > 0
    ready = lark_cli_ok or cookies_ok
    if ready:
        message = None
    elif lark_cli_ok is False and cookies_ok is False:
        message = (
            "未检测到 lark-cli，也未在本机 Chrome 找到飞书登录态，"
            "飞书文档将无法识别。请二选一：\n"
            "1) `npm i -g @larksuite/cli && lark-cli auth login`；\n"
            "2) 在本机 Chrome 登录飞书并访问过该文档。"
        )
    else:
        message = None
    return {
        "ready": ready,
        "larkCli": {"available": lark_cli_ok},
        "browserCookies": {
            "available": cookies_ok,
            "count": cookie_count,
            "error": cookie_error,
        },
        "message": message,
    }


def _find_lark_cli() -> Optional[str]:
    """Locate any installed Lark CLI binary.

    Different distributions expose the CLI as ``lark-cli`` (npm) or ``lark``
    (Go binary). Returns the first absolute path found, or ``None`` if neither
    is on PATH.
    """
    for name in ("lark-cli", "lark"):
        path = shutil.which(name)
        if path:
            return path
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fetch_static(url: str, timeout: int = 20) -> Optional[str]:
    """Plain HTTP fetch with browser-like headers. Returns None on failure."""
    try:
        req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
    except (urllib.error.URLError, OSError, ValueError) as e:
        logger.warning("Static fetch failed for %s: %s", url, e)
        return None


def _load_browser_cookies(domain_keywords: List[str]) -> List[Dict[str, Any]]:
    """Read cookies from the user's local Chrome for the given domain keywords.

    Returns an empty list if `browser_cookie3` is not installed or the cookie
    store is locked. The caller treats an empty list as "no cookies"; the
    adapter can then proceed unauthenticated.
    """
    if importlib.util.find_spec("browser_cookie3") is None:
        return []
    try:
        import browser_cookie3  # type: ignore
        jar = browser_cookie3.chrome()
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to read Chrome cookies: %s", e)
        return []

    cookies: List[Dict[str, Any]] = []
    for cookie in jar:
        host = (cookie.domain or "").lstrip(".")
        if not any(keyword in host for keyword in domain_keywords):
            continue
        cookies.append({
            "name": cookie.name,
            "value": cookie.value or "",
            "domain": cookie.domain,
            "path": cookie.path or "/",
            "secure": bool(cookie.secure),
            "httpOnly": bool(getattr(cookie, "_rest", {}).get("HttpOnly", False)),
            "expires": int(cookie.expires) if cookie.expires else -1,
        })
    return cookies


def _render_with_playwright(
    url: str,
    wait_selector: Optional[str] = None,
    cookies: Optional[List[Dict[str, Any]]] = None,
    timeout_ms: int = 20000,
    scroll_for_lazy: bool = False,
) -> Dict[str, str]:
    """Render a URL with headless Chromium and return {html, text, title, final_url}.

    When ``scroll_for_lazy`` is true, the page is scrolled to the bottom in
    increments so that virtualized content (e.g. Feishu Docs blocks) is
    materialised before we extract HTML/text.
    """
    if importlib.util.find_spec("playwright") is None:
        raise ArticleError(
            "ARTICLE_DEP_MISSING",
            "未安装 Playwright。请运行：pip3 install playwright && python3 -m playwright install chromium",
        )

    from playwright.sync_api import sync_playwright  # type: ignore

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                user_agent=DEFAULT_HEADERS["User-Agent"],
                locale="zh-CN",
            )
            if cookies:
                try:
                    context.add_cookies(cookies)
                except Exception as e:  # noqa: BLE001
                    logger.warning("add_cookies failed: %s", e)
            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=timeout_ms)
                except Exception:  # noqa: BLE001
                    pass
            if scroll_for_lazy:
                try:
                    last_height = 0
                    for _ in range(40):
                        page.evaluate(
                            "() => window.scrollTo(0, document.body.scrollHeight)"
                        )
                        page.wait_for_timeout(400)
                        height = page.evaluate("() => document.body.scrollHeight")
                        if height == last_height:
                            break
                        last_height = height
                except Exception as e:  # noqa: BLE001
                    logger.warning("scroll for lazy load failed: %s", e)
            html = page.content()
            try:
                text = page.inner_text("body") or ""
            except Exception:  # noqa: BLE001
                text = ""
            title = page.title() or ""
            final_url = page.url or url
            return {"html": html, "text": text, "title": title, "final_url": final_url}
        finally:
            browser.close()


def _trafilatura_extract(html: str, url: str) -> Dict[str, Any]:
    """Run trafilatura on rendered HTML. Returns {title, author, date, text, markdown}."""
    if importlib.util.find_spec("trafilatura") is None:
        raise ArticleError(
            "ARTICLE_DEP_MISSING",
            "未安装 trafilatura。请运行：pip3 install trafilatura",
        )

    import trafilatura  # type: ignore

    json_text = trafilatura.extract(
        html,
        url=url,
        output_format="json",
        with_metadata=True,
        favor_recall=True,
    ) or ""
    md = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        favor_recall=True,
    ) or ""
    parsed: Dict[str, Any] = {}
    if json_text:
        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError:
            parsed = {}
    return {
        "title": (parsed.get("title") or "").strip(),
        "author": (parsed.get("author") or "").strip() or None,
        "date": parsed.get("date") or None,
        "text": (parsed.get("text") or "").strip(),
        "markdown": md.strip(),
    }


def _word_count(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


def _extract_web(url: str) -> ArticleResult:
    html = _fetch_static(url)
    extracted: Dict[str, Any] = {}
    if html:
        extracted = _trafilatura_extract(html, url)

    if not extracted.get("text") or _word_count(extracted["text"]) < MIN_BODY_CHARS:
        # Fallback to Playwright rendering
        try:
            rendered = _render_with_playwright(url)
            extracted = _trafilatura_extract(rendered["html"], url)
            if (not extracted.get("title")) and rendered.get("title"):
                extracted["title"] = rendered["title"]
            if not extracted.get("text"):
                extracted["text"] = rendered.get("text", "")
        except ArticleError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("Playwright fallback failed for %s: %s", url, e)

    text = (extracted.get("text") or "").strip()
    if not text:
        raise ArticleError("ARTICLE_EMPTY", "未能从页面提取到正文，可能页面需要登录或不是文章页。")

    return ArticleResult(
        source="web",
        source_url=url,
        canonical_url=url,
        title=extracted.get("title") or "未命名网页",
        author=extracted.get("author"),
        published_at=extracted.get("date"),
        plain_text=text,
        markdown=extracted.get("markdown") or text,
        word_count=_word_count(text),
    )


def _extract_wechat(url: str) -> ArticleResult:
    html = _fetch_static(url)
    extracted: Dict[str, Any] = {}
    if html:
        extracted = _trafilatura_extract(html, url)

    if html and (not extracted.get("text") or _word_count(extracted["text"]) < MIN_BODY_CHARS):
        # Fallback: parse #js_content directly
        if importlib.util.find_spec("lxml") is not None:
            try:
                from lxml import html as lxml_html  # type: ignore
                tree = lxml_html.fromstring(html)
                node = tree.cssselect("#js_content") or tree.cssselect("#js_article")
                if node:
                    text_content = node[0].text_content().strip()
                    if text_content and _word_count(text_content) >= MIN_BODY_CHARS:
                        title = ""
                        title_node = tree.cssselect("h1#activity-name")
                        if title_node:
                            title = title_node[0].text_content().strip()
                        author = ""
                        author_node = tree.cssselect("#js_name")
                        if author_node:
                            author = author_node[0].text_content().strip()
                        extracted = {
                            "title": title or extracted.get("title") or "微信公众号文章",
                            "author": author or extracted.get("author"),
                            "date": extracted.get("date"),
                            "text": text_content,
                            "markdown": text_content,
                        }
            except Exception as e:  # noqa: BLE001
                logger.warning("wechat lxml fallback failed: %s", e)

    text = (extracted.get("text") or "").strip()
    if not text:
        raise ArticleError(
            "ARTICLE_EMPTY",
            "未能提取公众号正文。该文章可能需要登录或已被删除。",
        )

    return ArticleResult(
        source="wechat",
        source_url=url,
        canonical_url=url,
        title=extracted.get("title") or "微信公众号文章",
        author=extracted.get("author"),
        published_at=extracted.get("date"),
        plain_text=text,
        markdown=extracted.get("markdown") or text,
        word_count=_word_count(text),
    )


def _fetch_feishu_with_lark_cli(url: str) -> Optional[Dict[str, Any]]:
    """Fetch a Feishu/Lark doc via the official ``lark-cli`` (or ``lark``) binary.

    Returns a dict with ``title``/``markdown``/``text``/``canonical_url`` on
    success, or ``None`` if no Lark CLI is available, the user is not
    authenticated, or the target document cannot be accessed.

    The npm-distributed ``@larksuite/cli`` exposes ``lark-cli docs +fetch``,
    while the Go-binary ``lark`` distribution uses ``lark doc fetch``. Both
    are tried in turn. We capture stdout as JSON when possible; otherwise we
    accept raw markdown directly.
    """
    binary = _find_lark_cli()
    if not binary:
        return None

    binary_name = Path(binary).name

    candidate_argv: List[List[str]] = []
    if binary_name == "lark-cli":
        candidate_argv.append([
            binary, "docs", "+fetch",
            "--api-version", "v2",
            "--doc", url,
            "--doc-format", "markdown",
            "--format", "json",
        ])
        candidate_argv.append([
            binary, "doc", "fetch",
            "--url", url,
            "--format", "markdown",
        ])
    else:
        # Go binary `lark`
        candidate_argv.append([binary, "doc", "fetch", "--url", url, "--format", "markdown"])
        candidate_argv.append([binary, "docs", "+fetch", "--doc", url, "--doc-format", "markdown", "--format", "json"])

    for argv in candidate_argv:
        try:
            result = subprocess.run(
                argv, capture_output=True, text=True, timeout=60,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning("lark CLI %s timed out / failed: %s", argv[:3], e)
            continue
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            logger.warning("lark CLI %s failed: %s", argv[:3], stderr[:300])
            continue

        raw = (result.stdout or "").strip()
        if not raw:
            continue

        # Try JSON first.
        data: Any = None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", raw)
            if match:
                try:
                    data = json.loads(match.group(0))
                except json.JSONDecodeError:
                    data = None

        markdown = ""
        title = ""
        if isinstance(data, dict):
            payload = data.get("data") if isinstance(data.get("data"), dict) else data
            if isinstance(payload, dict):
                markdown = (
                    payload.get("content")
                    or payload.get("markdown")
                    or payload.get("body")
                    or ""
                )
                title = (
                    payload.get("title")
                    or (payload.get("document") or {}).get("title", "")
                    or ""
                )

        if not markdown:
            # Accept raw markdown stdout as a fallback.
            looks_markdown = ("\n" in raw and (raw.lstrip().startswith("#") or len(raw) > 200))
            if looks_markdown:
                markdown = raw
            else:
                continue

        text = re.sub(r"`{1,3}[^`]*`{1,3}", "", markdown)
        text = re.sub(r"!?\[[^\]]*\]\([^)]*\)", "", text)
        text = re.sub(r"[#*_>`-]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if _word_count(text) < 40:
            continue
        return {
            "title": (title or "").strip() or "飞书文档",
            "markdown": markdown.strip(),
            "text": text,
        }

    return None


def _extract_feishu(url: str) -> ArticleResult:
    # Strategy 1: try the official lark-cli (uses authenticated user/bot token).
    via_cli = _fetch_feishu_with_lark_cli(url)
    if via_cli and _word_count(via_cli["text"]) >= 40:
        return ArticleResult(
            source="feishu",
            source_url=url,
            canonical_url=url,
            title=via_cli["title"],
            author=None,
            published_at=None,
            plain_text=via_cli["text"],
            markdown=via_cli["markdown"],
            word_count=_word_count(via_cli["text"]),
        )

    # Strategy 2: Playwright fallback with browser cookies (no lark-cli login).
    if importlib.util.find_spec("playwright") is None:
        raise ArticleError(
            "ARTICLE_DEP_MISSING",
            "飞书文档需要 lark-cli（已登录）或 Playwright。"
            "请安装其一：npm i -g @larksuite/cli && lark-cli auth login，"
            "或 pip3 install playwright && python3 -m playwright install chromium。",
        )

    cookies = _load_browser_cookies(["feishu.cn", "larkoffice.com", "feishu-pre.cn"])
    wait_selector = ".docx-page-block, .doc-render, .lark-docx-page-block, .docx-block"
    try:
        rendered = _render_with_playwright(
            url,
            wait_selector=wait_selector,
            cookies=cookies,
            timeout_ms=30000,
            scroll_for_lazy=True,
        )
    except ArticleError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ArticleError("ARTICLE_FETCH_FAILED", f"飞书文档加载失败：{e}")

    final_url = rendered.get("final_url") or url
    text_body = (rendered.get("text") or "").strip()
    title = (rendered.get("title") or "").strip()

    if "/accounts/login" in final_url or "passport." in final_url:
        raise ArticleError(
            "FEISHU_LOGIN_REQUIRED",
            "飞书文档需要登录。请先在 Chrome 浏览器登录飞书并打开过该文档，"
            "或运行 `lark-cli auth login` 后重试。",
        )

    extracted: Dict[str, Any] = {}
    try:
        extracted = _trafilatura_extract(rendered["html"], url)
    except ArticleError:
        extracted = {}

    # Prefer trafilatura when it returns a substantial body; otherwise fall
    # back to the visible body text rendered by Playwright (it covers Feishu
    # Docs blocks that trafilatura's heuristics tend to skip).
    candidate_texts = [
        (extracted.get("text") or "").strip(),
        text_body,
    ]
    final_text = max(candidate_texts, key=_word_count)

    if _word_count(final_text) < 40:
        raise ArticleError(
            "FEISHU_LOGIN_REQUIRED",
            "未拿到飞书文档正文。请运行 `lark-cli auth login` 登录飞书，"
            "或确认你对该文档有访问权限。",
        )

    # Clean up the visible-body text (it includes UI chrome); trim leading
    # navigation lines if present.
    final_text = re.sub(r"\n{3,}", "\n\n", final_text).strip()

    final_title = extracted.get("title") or title or "飞书文档"
    # Strip the trailing " - 飞书云文档" that the browser tab title carries.
    final_title = re.sub(r"\s*[-|·]\s*飞书.*$", "", final_title).strip() or "飞书文档"

    return ArticleResult(
        source="feishu",
        source_url=url,
        canonical_url=final_url,
        title=final_title,
        author=extracted.get("author"),
        published_at=extracted.get("date"),
        plain_text=final_text,
        markdown=extracted.get("markdown") or final_text,
        word_count=_word_count(final_text),
    )


def _extract_xiaohongshu(url: str) -> ArticleResult:
    if importlib.util.find_spec("curl_cffi") is None:
        raise ArticleError(
            "ARTICLE_DEP_MISSING",
            "小红书提取需要 curl-cffi。请运行：pip3 install curl-cffi",
        )

    from curl_cffi import requests as cffi_requests  # type: ignore

    html = ""
    try:
        resp = cffi_requests.get(url, impersonate="chrome", timeout=20)
        html = resp.text or ""
    except Exception as e:  # noqa: BLE001
        logger.warning("curl-cffi request failed: %s", e)

    title = ""
    author = ""
    text = ""
    published_at: Optional[str] = None

    if html:
        match = re.search(
            r"window\.__INITIAL_STATE__\s*=\s*(\{.+?\})\s*</script>",
            html,
            re.DOTALL,
        )
        if match:
            try:
                state = json.loads(match.group(1).replace("undefined", "null"))
                note_map = (
                    state.get("note", {})
                    .get("noteDetailMap", {})
                )
                for entry in note_map.values():
                    note = entry.get("note") or {}
                    if not note:
                        continue
                    title = note.get("title") or title
                    text = note.get("desc") or text
                    user = note.get("user") or {}
                    author = user.get("nickname") or author
                    if note.get("time"):
                        try:
                            from datetime import datetime, timezone
                            published_at = datetime.fromtimestamp(
                                int(note["time"]) / 1000, tz=timezone.utc
                            ).isoformat()
                        except Exception:  # noqa: BLE001
                            pass
                    tag_list = note.get("tagList") or []
                    if tag_list:
                        tags = " ".join(f"#{t.get('name', '')}" for t in tag_list)
                        text = f"{text}\n\n{tags}".strip()
                    if text:
                        break
            except (json.JSONDecodeError, AttributeError) as e:
                logger.warning("xhs JSON parse failed: %s", e)

    if _word_count(text) < 20:
        # Fallback: render with playwright
        try:
            rendered = _render_with_playwright(url, wait_selector="#noteContainer, .note-content")
            extracted = _trafilatura_extract(rendered["html"], url)
            text = extracted.get("text") or rendered.get("text") or text
            title = extracted.get("title") or rendered.get("title") or title
            author = extracted.get("author") or author
        except ArticleError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("xhs playwright fallback failed: %s", e)

    text = (text or "").strip()
    if not text:
        raise ArticleError(
            "ARTICLE_EMPTY",
            "未能提取小红书笔记正文，可能笔记需要登录或已被删除。",
        )

    return ArticleResult(
        source="xiaohongshu",
        source_url=url,
        canonical_url=url,
        title=title or "小红书笔记",
        author=author or None,
        published_at=published_at,
        plain_text=text,
        markdown=text,
        word_count=_word_count(text),
    )


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


def extract_article(url: str, source: str) -> ArticleResult:
    """Dispatch to the matching adapter."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ArticleError("UNSUPPORTED_URL", "只允许 http 或 https 链接")

    if source == "wechat":
        return _extract_wechat(url)
    if source == "feishu":
        return _extract_feishu(url)
    if source == "xiaohongshu":
        return _extract_xiaohongshu(url)
    return _extract_web(url)
