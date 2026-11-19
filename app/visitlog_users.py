"""Content-site visitlog → HQ「이용자 보기」(방문자 · 흐름). SiteKPI는 sitekpi_users 유지."""

from __future__ import annotations

import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from app import sites

TTL = 300
PERIODS = (7, 30, 90)
_CACHE: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
_LOCK = threading.Lock()


def derive(secret: str, site_id: str) -> str:
    base = (secret or "").strip()
    sid = (site_id or "").strip()
    if not base or not sid:
        return ""
    return hmac.new(base.encode(), f"visitlog-hq-api-v1:{sid}".encode(), "sha256").hexdigest()


def _token(site_id: str) -> str:
    from app import gsecrets
    explicit = (os.environ.get("VISITLOG_HQ_TOKEN") or "").strip()
    if explicit:
        return explicit
    # Per-site override, then shared visitlog secret, then SESSION-style
    for key in (f"VISITLOG_SECRET_{site_id.upper()}", "VISITLOG_SECRET", "SITEKPI_SESSION_SECRET"):
        val = (os.environ.get(key) or gsecrets.get(key) or "").strip()
        if val:
            return derive(val, site_id)
    return ""


def _base(site_id: str) -> str:
    env = (os.environ.get(f"VISITLOG_BASE_URL_{site_id.upper()}") or os.environ.get("VISITLOG_BASE_URL") or "").strip()
    if env:
        return env.rstrip("/")
    site = sites.SITES.get(site_id)
    if site and site.domain:
        return f"https://{site.domain}"
    return ""


def _get(base: str, days: int, token: str) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{base}/hq/api/users?days={days}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "starful-hq"},
    )
    with urllib.request.urlopen(req, timeout=12) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))


def forget(site_id: str | None = None) -> None:
    with _LOCK:
        if site_id is None:
            _CACHE.clear()
        else:
            for k in [k for k in _CACHE if k[0] == site_id]:
                del _CACHE[k]


def fetch(site_id: str, days: int = 7, fresh: bool = False) -> dict[str, Any]:
    days = days if days in PERIODS else 7
    sid = (site_id or "").strip()
    key = (sid, days)
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
    if hit and not fresh and now - hit[0] < TTL:
        return {**hit[1], "cached": True}

    base = _base(sid)
    if not base:
        return {"ok": False, "days": days, "kind": "visitlog", "error": "사이트 production URL이 없어요."}
    token = _token(sid)
    if not token:
        return {
            "ok": False, "days": days, "kind": "visitlog",
            "error": "VISITLOG_SECRET(또는 VISITLOG_HQ_TOKEN)을 읽지 못했어요.",
        }
    try:
        data = _get(base, days, token)
    except urllib.error.HTTPError as exc:
        msg = {
            401: "열쇠가 맞지 않아요 (사이트 VISITLOG_SECRET 확인).",
            404: "사이트에 아직 이용자 로그 창구가 없어요.",
        }.get(exc.code, f"HTTP {exc.code}")
        return {"ok": False, "days": days, "kind": "visitlog", "error": msg}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return {"ok": False, "days": days, "kind": "visitlog", "error": f"사이트에 닿지 않아요: {str(exc)[:80]}"}

    data = {**data, "ok": True, "kind": "visitlog", "survey_on": False}
    for f in data.get("funnel") or []:
        top = max((x.get("n") or 0) for x in data["funnel"]) or 1
        f["w"] = round((f.get("n") or 0) / top * 100)
    with _LOCK:
        _CACHE[key] = (now, data)
    return {**data, "cached": False}
