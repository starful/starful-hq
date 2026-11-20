"""What a meeting should know about each site before it proposes anything.

Three blocks per site, each left out when it cannot be read:
- 기획서: the plan HTML in the Google Drive folder (AI/<site>/<site> 기획서.html), tags stripped, capped per site.
- 페이지별 성적 (30일): url_stats.fetch — 조회수 상위 10 + 노출 많고 CTR 낮은 10.
- 최근 회의 결정과 결과 (2주): meeting proposals (planning) for the site + the work queue's status / failure reason,
  so a meeting does not propose again what just failed (「이미 있는 가이드예요」).
"""

from __future__ import annotations

import html
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app import sites

DRIVE_AI = Path(os.environ.get(
    "HQ_PLAN_DIR",
    str(Path.home() / "Library" / "CloudStorage" / "GoogleDrive-starful0418@gmail.com" / "내 드라이브" / "AI")))
PLAN_CHARS = 8000        # per site
STATS_DAYS = 30
TOP_N = 10
LOW_CTR_MIN_IMPRESSIONS = 50
RECENT_DAYS = 14
RECENT_MAX = 15          # lines per site
_STATUS_KO = {"queued": "대기", "running": "실행 중", "done": "완료", "failed": "실패", "held": "보류"}


# --- 기획서 -------------------------------------------------------------------

def plan_path(site: sites.Site) -> Path | None:
    """AI/<site>/<site> 기획서.html — by id first, then by label (「Starful HQ」 style folders)."""
    for name in (site.id, site.label):
        if not name:
            continue
        p = DRIVE_AI / name / f"{name} 기획서.html"
        try:
            if p.is_file():
                return p
        except OSError:   # the Drive folder may be offline
            return None
    return None


_DROP = re.compile(r"<(script|style|head|iframe|svg|nav)\b.*?</\1\s*>", re.S | re.I)
_BLOCK = re.compile(r"</?(h[1-6]|p|li|tr|div|section|figure|table|ul|ol|br)\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")


def html_text(raw: str) -> str:
    """Readable text of an HTML document: no scripts, styles, embedded screens or menus; one line per block."""
    text = _DROP.sub(" ", raw or "")
    text = re.sub(r"<(td|th)\b[^>]*>", " | ", text, flags=re.I)
    text = _BLOCK.sub("\n", text)
    text = html.unescape(_TAG.sub("", text))
    lines = [re.sub(r"[ \t ]+", " ", ln).strip(" |") for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def plan_text(site: sites.Site, limit: int = PLAN_CHARS) -> str:
    p = plan_path(site)
    if p is None:
        return ""
    try:
        text = html_text(p.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""
    return text if len(text) <= limit else text[:limit].rsplit("\n", 1)[0] + "\n…(이하 생략)"


# --- 페이지별 성적 -----------------------------------------------------------------

def _row_line(r: dict[str, Any]) -> str:
    name = (r.get("title") or r.get("path") or "")[:70]
    ctr = "-" if r.get("ctr") is None else f"{r['ctr']}%"
    pos = "-" if r.get("position") is None else r["position"]
    return (f"  - {name} ({r.get('path', '')}) 조회 {r.get('views', 0)} · 클릭 {r.get('clicks', 0)} · "
            f"노출 {r.get('impressions', 0)} · CTR {ctr} · 순위 {pos}\n")


def pick_rows(rows: list[dict[str, Any]], n: int = TOP_N) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(조회수 상위 n, 노출 많고 CTR 낮은 n). Low CTR = below the site's own CTR, with enough impressions."""
    top = sorted(rows, key=lambda r: (-int(r.get("views") or 0), -int(r.get("clicks") or 0)))[:n]
    seen = [r for r in rows if int(r.get("impressions") or 0) >= LOW_CTR_MIN_IMPRESSIONS and r.get("ctr") is not None]
    clicks = sum(int(r.get("clicks") or 0) for r in seen)
    imps = sum(int(r.get("impressions") or 0) for r in seen)
    avg = clicks / imps * 100 if imps else 0.0
    low = [r for r in seen if float(r["ctr"]) < avg] if avg else seen
    low = sorted(low, key=lambda r: (-int(r.get("impressions") or 0), float(r["ctr"])))[:n]
    return top, low


def stats_text(site_id: str) -> str:
    try:
        from app import url_stats
        data = url_stats.fetch(site_id, STATS_DAYS)
    except Exception:  # noqa: BLE001 - GA4/GSC trouble: leave the block out
        return ""
    rows = data.get("rows") or []
    if not data.get("ok") or not rows:
        return ""
    top, low = pick_rows(rows)
    out = f"조회수 상위 {len(top)}:\n" + "".join(_row_line(r) for r in top)
    if low:
        out += f"노출 많고 CTR 낮은 {len(low)} (제목·설명 고칠 후보):\n" + "".join(_row_line(r) for r in low)
    return out


# --- 최근 회의 결정과 결과 -----------------------------------------------------------

def _when(pid: str) -> datetime | None:
    try:
        return datetime.strptime(str(pid)[:17], "%Y-%m-%d_%H%M%S")
    except ValueError:
        return None


def recent_text(site_id: str, days: int = RECENT_DAYS, now: datetime | None = None) -> str:
    """One line per idea for this site from meetings of the last `days`: title · 실행 상태 · 실패 이유."""
    from app.work import planning, workqueue
    since = (now or datetime.now()) - timedelta(days=days)
    try:
        with sites.use(site_id):
            metas = planning.history(limit=60)
            props = [(m["id"], planning.read(m["id"])) for m in metas if (_when(m["id"]) or since) > since]
            queue = workqueue.list_items(site_id)
    except Exception:  # noqa: BLE001 - no history is fine
        return ""
    by_idea = {(str(x.get("pid") or ""), x.get("idx")): x for x in queue if x.get("source") == "meeting"}
    lines: list[str] = []
    for pid, prop in props:
        for idx, idea in enumerate(prop.get("ideas") or []):
            if str(idea.get("site") or site_id) != site_id or not idea.get("title"):
                continue
            q = by_idea.get((pid, idx))
            kind = ((idea.get("action") or {}).get("type") or "none")
            if q:
                state = _STATUS_KO.get(str(q.get("status") or ""), str(q.get("status") or ""))
                err = str(q.get("error") or "").strip()
                state += f" ({err[:80]})" if err and q.get("status") in ("failed", "held") else ""
            else:
                state = "사람이 할 일" if kind == "none" else "실행 안 함"
            lines.append(f"  - {pid[:10]} {str(idea['title'])[:80]} [{kind}] → {state}\n")
    # queue rows from elsewhere (팀 할 일) that failed: also worth knowing
    for x in queue:
        if x.get("source") != "meeting" and x.get("status") == "failed" and (_when(str(x.get("at") or "").replace(" ", "_").replace(":", "")) or since) > since:
            lines.append(f"  - {str(x.get('at'))[:10]} {str(x.get('title') or x.get('task'))[:80]} → 실패 ({str(x.get('error') or '')[:80]})\n")
    return "".join(lines[:RECENT_MAX])


# --- 회의 프롬프트 블록 ------------------------------------------------------------

def _one(site: sites.Site) -> str:
    parts = []
    plan = plan_text(site)
    if plan:
        parts.append(f"[기획서 — 목적·독자·하지 말 것·우선순위. 제안은 이 기획서에 맞아야 한다]\n{plan}\n")
    stats = stats_text(site.id)
    if stats:
        parts.append(f"[페이지별 성적 · 최근 {STATS_DAYS}일]\n{stats}")
    recent = recent_text(site.id)
    if recent:
        parts.append(f"[최근 {RECENT_DAYS}일 회의 결정과 결과]\n{recent}")
    return f"### {site.id}\n" + "\n".join(parts) if parts else ""


def block(site_list: list[sites.Site]) -> str:
    """Prompt block for _ask_all. Sites are read in parallel; empty when nothing could be read."""
    if not site_list:
        return ""
    with ThreadPoolExecutor(max_workers=min(4, len(site_list))) as pool:
        parts = [p for p in pool.map(_one, site_list) if p]
    if not parts:
        return ""
    return ("사이트 브리핑 (기획서 · 페이지별 성적 · 최근 회의 결과). 이미 실패했거나 「이미 있는」 이유로 막힌 제안은 다시 내지 말고, "
            "노출 많고 CTR 낮은 페이지는 search 후보로 보라:\n" + "\n".join(parts) + "\n")
