"""다른 사이트 「이용자 보기」— GA4 요약 (설문·방문자별 흐름 없음).

HQ는 로컬 전용이라 공개 사이트 비콘을 받을 수 없음. GA4 property가 있는 사이트는
활성 사용자 · 많이 본 페이지 · 유입 채널을 SiteKPI 팝업과 비슷한 틀로 보여 준다.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from typing import Any

from app import sites

PERIODS = (7, 30, 90)
TTL = 300
JST = timezone(timedelta(hours=9))
_CACHE: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
_LOCK = threading.Lock()


def forget(site_id: str | None = None) -> None:
    with _LOCK:
        if site_id is None:
            _CACHE.clear()
        else:
            for k in [k for k in _CACHE if k[0] == site_id]:
                del _CACHE[k]


def _okadmin() -> None:
    from app.work import marketing
    marketing._okadmin_import()
    marketing._ensure_ga4_creds()


def _bar_rows(rows: list[dict[str, Any]], *, n_key: str = "n", limit: int = 12) -> list[dict[str, Any]]:
    top = max((int(r.get(n_key) or 0) for r in rows[:limit]), default=1) or 1
    out = []
    for r in rows[:limit]:
        n = int(r.get(n_key) or 0)
        out.append({**r, "w": round(n / top * 100)})
    return out


def _home_paths(domain: str) -> set[str]:
    return {"/", "/index.html", "/index", "/ja", "/en", "/ko", "/ja/", "/en/", "/ko/"}


def _funnel_from_pages(pages: list[dict[str, Any]], *, domain: str = "") -> tuple[list[dict[str, Any]], str]:
    """Rough path funnel: 홈 → 콘텐츠 → 깊은 페이지 (페이지별 사용자 수 · 중복 가능)."""
    homes = _home_paths(domain)
    home_u = content_u = deep_u = 0
    home_pv = content_pv = deep_pv = 0
    for r in pages:
        path = str(r.get("page") or "") or "/"
        users = int(r.get("users") or 0)
        pvs = int(r.get("pageviews") or 0)
        depth = len([p for p in path.strip("/").split("/") if p])
        if path.rstrip("/") in {h.rstrip("/") for h in homes} or path in homes:
            home_u += users
            home_pv += pvs
        elif depth <= 1:
            content_u += users
            content_pv += pvs
        else:
            deep_u += users
            deep_pv += pvs
    base = max(home_u, content_u, deep_u, 1)
    pct = lambda n: f"{round(n / base * 100)}%" if base else "—"
    funnel = [
        {"label": "홈", "n": home_u, "pct": pct(home_u), "note": f"조회 {home_pv:,}"},
        {"label": "콘텐츠", "n": content_u, "pct": pct(content_u), "note": f"조회 {content_pv:,}"},
        {"label": "깊은 페이지", "n": deep_u, "pct": pct(deep_u), "note": f"조회 {deep_pv:,}"},
    ]
    stuck = ""
    if home_u and content_u < home_u:
        stuck = f"가장 많이 멈추는 곳: 홈 → 콘텐츠 (홈 {home_u}명 · 콘텐츠 {content_u}명 · 페이지별 합이라 겹칠 수 있어요)"
    elif content_u and deep_u < content_u // 2:
        stuck = f"콘텐츠에서 더 깊게 안 가는 편이에요 (콘텐츠 {content_u} · 깊은 페이지 {deep_u})"
    return funnel, stuck


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
    if not site:
        return {"ok": False, "days": days, "kind": "ga4", "error": "사이트를 찾지 못했어요."}

    try:
        _okadmin()
        from analytics_api import fetch_ga4_channels, fetch_ga4_pages, fetch_ga4_summary, site_analytics_config
    except Exception as exc:
        return {"ok": False, "days": days, "kind": "ga4", "error": f"분석 도구를 불러오지 못했어요: {str(exc)[:80]}"}

    prop = (site_analytics_config(site.oid).get("ga4_property_id") or "").strip()
    if not prop:
        return {
            "ok": False, "days": days, "kind": "ga4",
            "error": f"{site.label}에 GA4 property가 없어요 (sites.yaml analytics.ga4_property_id).",
        }

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            fut_sum = pool.submit(fetch_ga4_summary, prop, days=days)
            fut_pages = pool.submit(fetch_ga4_pages, prop, days=days)
            fut_ch = pool.submit(fetch_ga4_channels, prop, days=days)
            summary = fut_sum.result() or {}
            pages_raw = fut_pages.result() or {}
            ch_raw = fut_ch.result() or {}
    except Exception as exc:
        return {"ok": False, "days": days, "kind": "ga4", "error": f"GA4를 읽지 못했어요: {str(exc)[:100]}"}

    err = str(summary.get("error") or pages_raw.get("error") or ch_raw.get("error") or "")
    if err and not (summary.get("totals") or pages_raw.get("rows")):
        return {"ok": False, "days": days, "kind": "ga4", "error": err[:160]}

    totals = summary.get("totals") if isinstance(summary.get("totals"), dict) else {}
    # fetch_ga4_summary shape: may put totals at top or under period — support both
    if not totals and isinstance(summary.get("period"), dict):
        totals = summary["period"]
    users = int(totals.get("users") or totals.get("activeUsers") or 0)
    sessions = int(totals.get("sessions") or 0)
    pageviews = int(totals.get("pageviews") or totals.get("screenPageViews") or 0)
    new_users = int(totals.get("new_users") or totals.get("newUsers") or 0)
    # Daily rows fallback for totals
    if not users and isinstance(summary.get("rows"), list):
        for r in summary["rows"]:
            if not isinstance(r, dict):
                continue
            users += int(r.get("users") or r.get("activeUsers") or 0)
            sessions += int(r.get("sessions") or 0)
            pageviews += int(r.get("pageviews") or r.get("screenPageViews") or 0)
            new_users += int(r.get("new_users") or r.get("newUsers") or 0)

    pages = []
    for r in pages_raw.get("rows") or []:
        if not isinstance(r, dict):
            continue
        pages.append({
            "label": str(r.get("page") or "/")[:80],
            "n": int(r.get("users") or 0),
            "pageviews": int(r.get("pageviews") or 0),
            "sessions": int(r.get("sessions") or 0),
            "page": r.get("page") or "/",
            "users": int(r.get("users") or 0),
        })
    funnel, stuck = _funnel_from_pages(pages, domain=site.domain)
    for f in funnel:
        top = max((x["n"] for x in funnel), default=1) or 1
        f["w"] = round(f["n"] / top * 100)

    channels = []
    for r in ch_raw.get("rows") or []:
        if not isinstance(r, dict):
            continue
        channels.append({
            "label": str(r.get("channel") or "Direct")[:40],
            "n": int(r.get("users") or 0),
            "sessions": int(r.get("sessions") or 0),
        })

    generated = datetime.now(JST).strftime("%m-%d %H:%M")
    data = {
        "ok": True,
        "kind": "ga4",
        "survey_on": False,
        "days": days,
        "site_id": sid,
        "site_label": site.label,
        "generated_at": generated,
        "kpis": [
            {"label": "활성 사용자", "value": users, "unit": "명", "note": f"신규 {new_users:,}명"},
            {"label": "세션", "value": sessions, "unit": "회", "note": f"1인당 {sessions / users:.1f}" if users else ""},
            {"label": "조회수", "value": pageviews, "unit": "회", "note": f"1인당 {pageviews / users:.1f}" if users else ""},
            {"label": "페이지 수", "value": len(pages), "unit": "개", "note": "조회된 경로"},
        ],
        "funnel": funnel,
        "stuck": stuck,
        "pages": _bar_rows(pages, n_key="n"),
        "channels": _bar_rows(channels, n_key="n"),
        "note": "GA4 집계예요. 방문자별 흐름·설문은 SiteKPI처럼 자체 로그가 있을 때 붙일 수 있어요.",
    }
    with _LOCK:
        _CACHE[key] = (now, data)
    return {**data, "cached": False}
