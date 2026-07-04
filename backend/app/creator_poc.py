"""P0 POC: Douyin creator homepage -> video list + metrics.

Standalone probe (NOT wired into the app yet). Validates whether we can
reliably harvest a creator's video list with like/comment/collect/share
metrics by driving headless Chromium with the local Chrome login state and
intercepting the `aweme/post` XHR responses.

Run:
    /opt/homebrew/bin/python3.13 backend/app/creator_poc.py "<douyin user homepage url>" [limit]

Success criteria (§5.4): >=1 page of videos, each with aweme_id + title + like count.
"""

from __future__ import annotations

import json
import random
import re
import sys
import time
from typing import Any, Dict, List, Optional


DOUYIN_COOKIE_DOMAINS = ["douyin.com"]
POST_API_MARKER = "aweme/v1/web/aweme/post/"


def load_chrome_cookies() -> List[Dict[str, Any]]:
    """Read Douyin cookies from local Chrome, shaped for Playwright add_cookies."""
    import browser_cookie3  # type: ignore

    jar = browser_cookie3.chrome(domain_name="douyin.com")
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


def parse_aweme(aweme: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the target fields from one aweme entry."""
    stats = aweme.get("statistics") or {}
    video = aweme.get("video") or {}
    cover = (video.get("cover") or {}).get("url_list") or []
    aweme_id = aweme.get("aweme_id") or ""
    return {
        "aweme_id": aweme_id,
        "video_url": f"https://www.douyin.com/video/{aweme_id}" if aweme_id else "",
        "title": (aweme.get("desc") or "").strip(),
        "like_count": stats.get("digg_count"),
        "comment_count": stats.get("comment_count"),
        "collect_count": stats.get("collect_count"),
        "share_count": stats.get("share_count"),
        "play_count": stats.get("play_count"),
        "duration_ms": video.get("duration"),
        "create_time": aweme.get("create_time"),
        "cover_url": cover[0] if cover else None,
    }


def harvest(url: str, limit: int = 50, headless: bool = True) -> Dict[str, Any]:
    from playwright.sync_api import sync_playwright  # type: ignore

    cookies = load_chrome_cookies()
    captured: List[Dict[str, Any]] = []
    raw_responses = 0
    seen_ids = set()
    saw_captcha = False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="zh-CN",
                viewport={"width": 1280, "height": 900},
            )
            if cookies:
                try:
                    context.add_cookies(cookies)
                except Exception as e:  # noqa: BLE001
                    print(f"[warn] add_cookies failed: {e}", file=sys.stderr)

            page = context.new_page()

            def on_response(response):  # noqa: ANN001
                nonlocal raw_responses
                if POST_API_MARKER not in response.url:
                    return
                try:
                    data = response.json()
                except Exception:  # noqa: BLE001
                    return
                raw_responses += 1
                for aweme in data.get("aweme_list") or []:
                    parsed = parse_aweme(aweme)
                    if parsed["aweme_id"] and parsed["aweme_id"] not in seen_ids:
                        seen_ids.add(parsed["aweme_id"])
                        captured.append(parsed)

            page.on("response", on_response)

            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            # Let the first post batch fire.
            page.wait_for_timeout(3000)

            final_url = page.url
            if "verify" in final_url or "captcha" in final_url:
                saw_captcha = True

            # Scroll to trigger lazy loading until we hit the limit or stop growing.
            stagnant_rounds = 0
            for _ in range(40):
                if len(captured) >= limit:
                    break
                before = len(captured)
                page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(random.randint(900, 1600))
                after = len(captured)
                if after == before:
                    stagnant_rounds += 1
                    if stagnant_rounds >= 4:
                        break
                else:
                    stagnant_rounds = 0

            page_title = page.title()
            body_snippet = ""
            try:
                body_snippet = (page.inner_text("body") or "")[:200]
            except Exception:  # noqa: BLE001
                pass
            return {
                "final_url": final_url,
                "page_title": page_title,
                "cookie_count": len(cookies),
                "raw_post_responses": raw_responses,
                "captured_count": len(captured),
                "saw_captcha": saw_captcha or ("验证" in body_snippet),
                "videos": captured[:limit],
                "body_snippet": body_snippet,
            }
        finally:
            browser.close()


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: creator_poc.py <douyin user url> [limit] [--headed]")
        sys.exit(2)
    url = sys.argv[1]
    limit = 50
    headless = True
    for arg in sys.argv[2:]:
        if arg == "--headed":
            headless = False
        elif arg.isdigit():
            limit = int(arg)

    started = time.time()
    result = harvest(url, limit=limit, headless=headless)
    elapsed = round(time.time() - started, 1)

    print("=" * 60)
    print(f"final_url      : {result['final_url']}")
    print(f"page_title     : {result['page_title']}")
    print(f"cookie_count   : {result['cookie_count']}")
    print(f"post_responses : {result['raw_post_responses']}")
    print(f"captured       : {result['captured_count']}  (elapsed {elapsed}s)")
    print(f"saw_captcha    : {result['saw_captcha']}")
    print("-" * 60)
    for i, v in enumerate(result["videos"][:10], 1):
        print(
            f"{i:2}. like={v['like_count']!s:>7} cmt={v['comment_count']!s:>6} "
            f"col={v['collect_count']!s:>6} shr={v['share_count']!s:>6} "
            f"| {v['aweme_id']} | {v['title'][:30]}"
        )
    if result["captured_count"] > 10:
        print(f"    ... and {result['captured_count'] - 10} more")
    print("=" * 60)

    # Field completeness across all captured videos (for Bitable schema).
    vids = result["videos"]
    if vids:
        def pct(key: str) -> str:
            n = sum(1 for v in vids if v.get(key) not in (None, "", 0))
            return f"{n}/{len(vids)}"
        print("field completeness (non-empty):")
        for key in [
            "like_count", "comment_count", "collect_count", "share_count",
            "duration_ms", "create_time", "cover_url",
        ]:
            print(f"  {key:14}: {pct(key)}")
        print("-" * 60)

    # Dump raw JSON for P1 reference.
    try:
        from pathlib import Path
        out = Path(__file__).resolve().parents[2] / ".data" / "creator_poc_dump.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"raw dump saved: {out}")
        print("-" * 60)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] dump failed: {e}")

    # Verdict against §5.4 acceptance.
    have_metrics = any(v["like_count"] is not None for v in result["videos"])
    have_titles = any(v["title"] for v in result["videos"])
    if result["captured_count"] >= 1 and have_metrics and have_titles:
        print("VERDICT: PASS ✅  (list + title + like metrics captured)")
    elif result["captured_count"] >= 1:
        print("VERDICT: PARTIAL ⚠️  (list captured but metrics/titles incomplete)")
    else:
        print("VERDICT: FAIL ❌  (no videos captured; check login/captcha)")


if __name__ == "__main__":
    main()
