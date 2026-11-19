"""URL별 숫자 — 사이트의 페이지마다 GA4(조회·사용자·세션) + GSC(검색 클릭·노출·CTR·순위)를 한 표로.

「이용자 보기」 dialog의 「URL별 숫자」 탭이 부른다. 모든 사이트 공통(visitlog 여부와 무관).
GA4 pagePath와 GSC page URL은 경로(쿼리·끝 / 제외)로 맞춘다.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from app import sites

PERIODS = (7, 30, 90)
TTL = 300
MAX_ROWS = 500
JST = timezone(timedelta(hours=9))
_CACHE: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
_LOCK = threading.Lock()


def path_key(url: str) -> str:
    """'https://x.com/a/b/?q=1' · '/a/b' · '/a/b/' → '/a/b'. Home → '/'."""
    raw = str(url or "").strip()
    path = urlsplit(raw).path if "://" in raw else raw.split("?", 1)[0].split("#", 1)[0]
    path = path or "/"
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/"


def merge_rows(ga4_rows: list[dict[str, Any]], gsc_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per path. Sorted by 조회수, then 검색 클릭."""
    by: dict[str, dict[str, Any]] = {}

    def row(key: str) -> dict[str, Any]:
        return by.setdefault(key, {"path": key, "views": 0, "users": 0, "sessions": 0,
                                   "clicks": 0, "impressions": 0, "_pos_w": 0.0})

    for r in ga4_rows or []:
        x = row(path_key(r.get("page") or "/"))
        x["views"] += int(r.get("pageviews") or 0)
        x["users"] += int(r.get("users") or 0)
        x["sessions"] += int(r.get("sessions") or 0)
    for r in gsc_rows or []:
        x = row(path_key(r.get("page") or "/"))
        imp = int(r.get("impressions") or 0)
        x["clicks"] += int(r.get("clicks") or 0)
        x["impressions"] += imp
        x["_pos_w"] += float(r.get("position") or 0) * imp
    out = []
    for x in by.values():
        imp = x["impressions"]
        x["ctr"] = round(x["clicks"] / imp * 100, 1) if imp else None
        x["position"] = round(x.pop("_pos_w") / imp, 1) if imp else None
        out.append(x)
    out.sort(key=lambda x: (-x["views"], -x["clicks"], -x["impressions"], x["path"]))
    return out


def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    t = {k: sum(int(r.get(k) or 0) for r in rows) for k in ("views", "users", "sessions", "clicks", "impressions")}
    t["ctr"] = round(t["clicks"] / t["impressions"] * 100, 1) if t["impressions"] else None
    return t


def fetch(site_id: str, days: int = 7, fresh: bool = False) -> dict[str, Any]:
    days = days if days in PERIODS else 7
    sid = (site_id or "").strip()
    key = (sid, days)
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
    if hit and not fresh and now - hit[0] < TTL:
        return {**hit[1], "cached": True}

    site = sites.SITES.get(sid)
    base = {"ok": False, "days": days, "site_id": sid, "rows": [], "total_rows": 0}
    if not site:
        return {**base, "error": "사이트를 찾지 못했어요."}
    try:
        from app import ga4_users
        ga4_users._okadmin()
        from analytics_api import fetch_ga4_pages, fetch_gsc_pages, site_analytics_config
    except Exception as exc:  # noqa: BLE001 - shown in the dialog
        return {**base, "error": f"분석 도구를 불러오지 못했어요: {str(exc)[:80]}"}

    cfg = site_analytics_config(site.oid) or {}
    prop = str(cfg.get("ga4_property_id") or "").strip()
    gsc_url = str(cfg.get("gsc_site_url") or "").strip()
    if not prop and not gsc_url:
        return {**base, "error": f"{site.label}에 GA4 · GSC 설정이 없어요 (sites.yaml analytics)."}

    ga4: dict[str, Any] = {}
    gsc: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_ga4 = pool.submit(fetch_ga4_pages, prop, days=days) if prop else None
        f_gsc = pool.submit(fetch_gsc_pages, gsc_url, days=days) if gsc_url else None
        for name, fut in (("ga4", f_ga4), ("gsc", f_gsc)):
            if not fut:
                continue
            try:
                res = fut.result() or {}
            except Exception as exc:  # noqa: BLE001
                res = {"error": str(exc)[:100]}
            if name == "ga4":
                ga4 = res
            else:
                gsc = res

    warn = []
    if ga4.get("error"):
        warn.append(f"GA4: {str(ga4['error'])[:80]}")
    if gsc.get("error"):
        warn.append(f"GSC: {str(gsc['error'])[:80]}")
    rows = merge_rows(ga4.get("rows") or [], gsc.get("rows") or [])
    if not rows and warn:
        return {**base, "error": " · ".join(warn)}

    data = {
        "ok": True,
        "days": days,
        "site_id": sid,
        "site_label": site.label,
        "domain": site.domain,
        "generated_at": datetime.now(JST).strftime("%m-%d %H:%M"),
        "total_rows": len(rows),
        "rows": rows[:MAX_ROWS],
        "totals": _totals(rows),
        "has_ga4": bool(prop and not ga4.get("error")),
        "has_gsc": bool(gsc_url and not gsc.get("error")),
        "gsc_cap": len(gsc.get("rows") or []) >= 500,
        "warn": " · ".join(warn),
    }
    with _LOCK:
        _CACHE[key] = (now, data)
    return {**data, "cached": False}
