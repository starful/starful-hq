"""YouTube Data API v3 helpers for Starful HQ candidate search."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# Search pool size, then keep top N by view count
SEARCH_POOL = 15
TOP_BY_VIEWS = 6


class YouTubeApiError(RuntimeError):
    pass


def youtube_api_key() -> str:
    return (os.environ.get("YOUTUBE_API_KEY") or "").strip()


def _request_headers() -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "starful-hq/1.0"}
    # If the key is restricted to HTTP referrers, set YOUTUBE_API_REFERER
    referer = (os.environ.get("YOUTUBE_API_REFERER") or "").strip()
    if referer:
        headers["Referer"] = referer
    return headers


def _api_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    key = youtube_api_key()
    if not key:
        raise YouTubeApiError("YOUTUBE_API_KEY is not set")
    params = {**params, "key": key}
    url = f"https://www.googleapis.com/youtube/v3/{path}?" + urlencode(params)
    req = Request(url, headers=_request_headers())
    try:
        with urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        detail = ""
        try:
            if isinstance(exc, HTTPError) and exc.fp is not None:
                raw = exc.read().decode("utf-8", errors="replace")
                try:
                    err_json = json.loads(raw)
                    detail = (err_json.get("error") or {}).get("message") or raw[:300]
                    reason = ""
                    for d in (err_json.get("error") or {}).get("details") or []:
                        if d.get("reason"):
                            reason = d["reason"]
                            break
                    if reason == "API_KEY_HTTP_REFERRER_BLOCKED":
                        detail = (
                            "API key blocks empty Referer. In Google Cloud → Credentials, "
                            "set Application restriction to None (or IP), or set "
                            "YOUTUBE_API_REFERER to an allowed origin."
                        )
                except json.JSONDecodeError:
                    detail = raw[:300]
        except Exception:
            detail = ""
        msg = f"YouTube API failed: {exc}"
        if detail:
            msg = f"{msg} — {detail}"
        raise YouTubeApiError(msg) from exc

    if payload.get("error"):
        err = payload["error"]
        raise YouTubeApiError(err.get("message") or str(err))
    return payload


def format_view_count(n: int) -> str:
    """Human-readable view count (e.g. 12.3万 / 1.2M / 840)."""
    if n < 0:
        n = 0
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}億".replace(".0億", "億")
    if n >= 10_000:
        v = n / 10_000
        return f"{v:.1f}万".replace(".0万", "万")
    return f"{n:,}"


def _video_statistics(video_ids: list[str]) -> dict[str, dict[str, Any]]:
    """video_id → {view_count, like_count}."""
    ids = [v for v in video_ids if v]
    if not ids:
        return {}
    payload = _api_get(
        "videos",
        {
            "part": "statistics",
            "id": ",".join(ids[:50]),
        },
    )
    out: dict[str, dict[str, Any]] = {}
    for item in payload.get("items") or []:
        vid = (item.get("id") or "").strip()
        st = item.get("statistics") or {}
        if not vid:
            continue
        try:
            views = int(st.get("viewCount") or 0)
        except (TypeError, ValueError):
            views = 0
        try:
            likes = int(st.get("likeCount") or 0)
        except (TypeError, ValueError):
            likes = 0
        out[vid] = {"view_count": views, "like_count": likes}
    return out


def search_videos(
    query: str,
    *,
    max_results: int = TOP_BY_VIEWS,
    relevance_language: str = "ja",
    region_code: str = "JP",
    search_pool: int = SEARCH_POOL,
) -> list[dict[str, Any]]:
    """Search, enrich with view counts, return top N by views."""
    q = (query or "").strip()
    if not q:
        raise YouTubeApiError("empty query")

    top_n = max(1, min(int(max_results), 15))
    pool = max(top_n, min(int(search_pool), 25))

    payload = _api_get(
        "search",
        {
            "part": "snippet",
            "type": "video",
            "q": q,
            "maxResults": pool,
            "relevanceLanguage": relevance_language,
            "regionCode": region_code,
            "safeSearch": "none",
        },
    )

    rows: list[dict[str, Any]] = []
    for item in payload.get("items") or []:
        vid = ((item.get("id") or {}).get("videoId") or "").strip()
        sn = item.get("snippet") or {}
        if not vid:
            continue
        thumbs = sn.get("thumbnails") or {}
        thumb = (
            (thumbs.get("medium") or {}).get("url")
            or (thumbs.get("default") or {}).get("url")
            or ""
        )
        rows.append(
            {
                "video_id": vid,
                "title": (sn.get("title") or "").strip(),
                "channel": (sn.get("channelTitle") or "").strip(),
                "published_at": (sn.get("publishedAt") or "").strip(),
                "thumbnail": thumb,
                "url": f"https://www.youtube.com/watch?v={vid}",
            }
        )

    stats = _video_statistics([r["video_id"] for r in rows])
    for row in rows:
        st = stats.get(row["video_id"]) or {}
        views = int(st.get("view_count") or 0)
        likes = int(st.get("like_count") or 0)
        row["view_count"] = views
        row["like_count"] = likes
        row["views_label"] = format_view_count(views)

    rows.sort(key=lambda r: int(r.get("view_count") or 0), reverse=True)
    return rows[:top_n]
