"""HQ click / action usage — local JSON so we can drop unused UI and polish frequent paths.

Server POSTs are counted in after_request. UI clicks post to /usage/track with data-track.
"""

from __future__ import annotations
from app.paths import HQ_ROOT

import json
import threading
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app import sites
ROOT = HQ_ROOT  # starful-hq/ (data lives next to app/)
PATH = ROOT / "data" / "hq_usage.json"
_LOCK = threading.Lock()
KEEP_DAYS = 60

# Friendly labels for the usage card (endpoint / event id → short Korean)
LABELS = {
    "post:site_run": "할 일 → 큐",
    "post:site_meeting": "회의 시작",
    "post:site_action": "팀 액션",
    "post:site_ship_review": "리뷰·머지",
    "post:site_ship_issue": "이슈·PR",
    "post:site_ship_merge": "머지",
    "post:site_ship_push": "푸시",
    "post:site_ship_deploy": "배포",
    "post:site_write": "글 쓰기",
    "post:site_write_research": "트렌드 리서치",
    "post:site_search": "검색 고치기",
    "post:site_search_analyze": "GA4·GSC 분석",
    "post:site_polish": "다듬기",
    "post:refresh": "새로고침",
    "ui:floor": "층 전환",
    "ui:tab": "팀 탭",
    "ui:drawer": "상세 드로어",
    "ui:todo": "할 일 버튼",
    "ui:meet": "회의 버튼",
    "ui:glance": "한눈에 숫자",
    "ui:queue-pulse": "한눈에 큐",
    "ui:queue-start": "큐 지금 시작",
    "ui:queue-start-all": "큐 대기 실행",
}


def _read() -> dict[str, Any]:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(data: dict[str, Any]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(PATH)


def track(event: str, *, site: str = "", detail: str = "") -> None:
    """Increment today's count for event (and optional site)."""
    event = str(event or "").strip()[:80]
    if not event:
        return
    day = date.today().isoformat()
    site = (site or sites.current_id() or "")[:40]
    detail = str(detail or "").strip()[:80]
    key = event if not detail else f"{event}:{detail}"
    with _LOCK:
        data = _read()
        days = data.get("days") if isinstance(data.get("days"), dict) else {}
        bucket = days.get(day) if isinstance(days.get(day), dict) else {}
        events = bucket.get("events") if isinstance(bucket.get("events"), dict) else {}
        events[key] = int(events.get(key) or 0) + 1
        sites_b = bucket.get("sites") if isinstance(bucket.get("sites"), dict) else {}
        if site:
            sites_b[site] = int(sites_b.get(site) or 0) + 1
        bucket["events"] = events
        bucket["sites"] = sites_b
        days[day] = bucket
        # drop old days
        cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
        days = {d: v for d, v in days.items() if d >= cutoff}
        data["days"] = days
        data["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _write(data)


def label_of(event_key: str) -> str:
    base = event_key.split(":", 2)
    if event_key.startswith("post:site_run:"):
        return f"할 일·{event_key.rsplit(':', 1)[-1]}"
    if event_key.startswith("post:site_action:"):
        return f"액션·{event_key.rsplit(':', 1)[-1]}"
    if event_key.startswith("ui:tab:"):
        return f"탭·{event_key.rsplit(':', 1)[-1]}"
    if event_key.startswith("ui:meet:"):
        return f"회의·{event_key.rsplit(':', 1)[-1]}"
    if event_key.startswith("ui:todo:"):
        return f"할일·{event_key.rsplit(':', 1)[-1]}"
    if len(base) >= 2:
        head = ":".join(base[:2]) if event_key.startswith("post:") or event_key.startswith("ui:") else base[0]
        if head in LABELS:
            rest = event_key[len(head) + 1:]
            return LABELS[head] + (f"·{rest}" if rest else "")
    return LABELS.get(event_key, event_key)


def summary(days: int = 14, top_n: int = 12) -> dict[str, Any]:
    """Roll up the last N days for a small HQ card."""
    with _LOCK:
        data = _read()
    day_map = data.get("days") if isinstance(data.get("days"), dict) else {}
    cutoff = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
    ev = Counter()
    site_c = Counter()
    for d, bucket in day_map.items():
        if d < cutoff or not isinstance(bucket, dict):
            continue
        for k, n in (bucket.get("events") or {}).items():
            ev[str(k)] += int(n or 0)
        for s, n in (bucket.get("sites") or {}).items():
            site_c[str(s)] += int(n or 0)
    ranked = ev.most_common()
    top = [{"id": k, "label": label_of(k), "n": n} for k, n in ranked[:top_n]]
    # "low" = seen but rare (help decide what to hide); skip zero (never seen)
    low = [{"id": k, "label": label_of(k), "n": n} for k, n in ranked[-8:] if n <= 2] if len(ranked) > 8 else []
    low = list(reversed(low))
    return {
        "days": days,
        "total": sum(ev.values()),
        "updated": data.get("updated") or "",
        "top": top,
        "low": low,
        "sites": [{"id": s, "n": n} for s, n in site_c.most_common(8)],
    }
