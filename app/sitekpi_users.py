"""👥 SiteKPI 이용자 — 현황 ss-actions 「이용자 · 설문 보기」→ dialog.

Reads sitekpi.net's operator API (GET /hq/api/users?days=7|30|90, SiteKPI repo sitekpi/visitlog.py). The Bearer key
is the same HMAC SiteKPI makes from its session secret: Secret Manager SITEKPI_SESSION_SECRET via gsecrets (the
Mac's gcloud login). SITEKPI_HQ_TOKEN (env or secret) wins when set — mirrors SiteKPI's HQ_API_TOKEN.
Five-minute in-memory cache per period; ↻ in the popup skips it. Status page keeps only the button (no summary block).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

BASE = os.environ.get("SITEKPI_BASE_URL", "https://sitekpi.net").rstrip("/")
SECRET = "SITEKPI_HQ_TOKEN"
BASE_SECRET = "SITEKPI_SESSION_SECRET"
TTL = 300
PERIODS = (7, 30, 90)
_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_LOCK = threading.Lock()


def derive(session_secret: str) -> str:
    """Same as SiteKPI visitlog.hq_token(): HMAC-SHA256(session secret, "sitekpi-hq-api-v1")."""
    base = (session_secret or "").strip()
    return hmac.new(base.encode(), b"sitekpi-hq-api-v1", hashlib.sha256).hexdigest() if base else ""


def _token() -> str:
    from app import gsecrets
    explicit = (os.environ.get(SECRET) or "").strip()
    return explicit or derive(gsecrets.get(BASE_SECRET))


def _get(days: int, token: str) -> dict[str, Any]:
    req = urllib.request.Request(f"{BASE}/hq/api/users?days={days}",
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                                          "User-Agent": "starful-hq"})
    with urllib.request.urlopen(req, timeout=12) as r:  # noqa: S310 — fixed https host
        return json.loads(r.read().decode("utf-8"))


def fetch(days: int = 7, fresh: bool = False) -> dict[str, Any]:
    """The API's JSON plus ``ok``/``error``/``cached``. Never raises (the block shows the error)."""
    days = days if days in PERIODS else 7
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(days)
    if hit and not fresh and now - hit[0] < TTL:
        return {**hit[1], "cached": True}
    token = _token()
    if not token:
        return {"ok": False, "days": days, "error": f"Secret Manager의 {BASE_SECRET}를 읽지 못했어요 (Mac의 gcloud 로그인 확인)."}
    try:
        data = _get(days, token)
    except urllib.error.HTTPError as exc:
        msg = {401: "열쇠가 맞지 않아요 (SiteKPI의 SESSION_SECRET이 바뀌었나요?).",
               404: "sitekpi.net에 아직 이 창구가 없어요 (이용자 로그 배포 전)."}.get(exc.code, f"HTTP {exc.code}")
        return {"ok": False, "days": days, "error": msg}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return {"ok": False, "days": days, "error": f"sitekpi.net에 닿지 않아요: {str(exc)[:80]}"}
    data = {**data, "ok": True}
    for f in data.get("funnel") or []:  # bar widths vs the first step
        top = max((x.get("n") or 0) for x in data["funnel"]) or 1
        f["w"] = round((f.get("n") or 0) / top * 100)
    for q in data.get("survey") or []:
        top = max([r.get("n") or 0 for r in q.get("rows") or []] or [1]) or 1
        for r in q.get("rows") or []:
            r["w"] = round((r.get("n") or 0) / top * 100)
    with _LOCK:
        _CACHE[days] = (now, data)
    return {**data, "cached": False}


def forget() -> None:
    with _LOCK:
        _CACHE.clear()
