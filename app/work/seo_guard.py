"""SEO guard: stop changes that can sink a site in Google Search.

Added 2026-11-21 after okonsen and okcaddie lost almost all search impressions in
early November. Automatic merges had rewritten titles and meta on many pages that
already ranked, moved page dates forward without changing the text, and merged
PRs whose review still listed required fixes.

Three parts:
- check_pr(): looks at a PR's diff before any merge from HQ and lists blockers.
- frozen() / freeze(): a frozen site takes no content changes from HQ.
- watch_traffic(): once a day, compares each site's search impressions; a big drop
  freezes the site, turns its floor's 자동 진행 off and posts an alert.

Limits can be changed in data/seo_guard.json (see DEFAULTS).
"""

from __future__ import annotations

import json
import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app import sites
from app.paths import HQ_ROOT

CONFIG_PATH = HQ_ROOT / "data" / "seo_guard.json"
FREEZE_PATH = HQ_ROOT / "data" / "seo_freeze.json"
WATCH_PATH = HQ_ROOT / "data" / "seo_watch.json"
ALERTS_PATH = HQ_ROOT / "data" / "seo_alerts.json"   # open alerts shown as a banner on every HQ page
_LOCK = threading.Lock()

DEFAULTS: dict[str, Any] = {
    "max_existing_pages": 5,        # existing pages one PR may change
    "max_title_changes": 3,         # existing pages whose title / seo_title / description may change
    "drop_ratio": 0.6,              # last 7 days below 60% of the previous 4 weeks' daily average = drop
    "min_daily_impressions": 50,    # ignore sites with less search traffic than this
    "freeze_days": 30,
    "lag_days": 3,                  # newest GSC days still filling in: not compared
}
DATE_KEYS = ("date", "updated", "updated_at", "last_updated", "lastupdated", "lastmod", "modified", "published")
TITLE_KEYS = ("title", "seo_title", "seo_description", "description", "meta_description")
CONTENT_EXT = (".md", ".markdown", ".mdx")
BINARY_DB = re.compile(r"\.(sqlite3?|db)(-journal)?$", re.I)
INDEXING_API = re.compile(r"indexing\.googleapis\.com|urlNotifications", re.I)


def config() -> dict[str, Any]:
    try:
        return {**DEFAULTS, **json.loads(CONFIG_PATH.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


# ---------- freeze ----------

def frozen(site_id: str | None = None) -> dict[str, Any] | None:
    """The freeze record when the site is frozen today, else None."""
    sid = site_id or sites.current_id()
    rec = _read(FREEZE_PATH).get(sid)
    if not isinstance(rec, dict):
        return None
    until = str(rec.get("until") or "")
    if until and until < date.today().isoformat():
        return None
    return rec


def freeze(site_id: str, reason: str, days: int | None = None) -> dict[str, Any]:
    days = int(days or config()["freeze_days"])
    rec = {"until": (date.today() + timedelta(days=days)).isoformat(), "reason": reason,
           "since": datetime.now().strftime("%Y-%m-%d %H:%M")}
    with _LOCK:
        data = _read(FREEZE_PATH)
        prev = data.get(site_id) if isinstance(data.get(site_id), dict) else {}
        if str(prev.get("until") or "") > rec["until"]:
            rec["until"] = prev["until"]   # never shorten an existing freeze
        data[site_id] = rec
        _write(FREEZE_PATH, data)
    return rec


# ---------- PR check ----------

def _front(text: str) -> dict[str, str]:
    """Front matter keys → raw values (no YAML parser needed for what we compare)."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    block = text[3:end] if end > 0 else ""
    out: dict[str, str] = {}
    for line in block.splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if m:
            out[m.group(1).lower()] = m.group(2).strip().strip("'\"")
    return out


def _body(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            text = text[end + 4:]
    return re.sub(r"\s+", " ", text).strip()


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")[:8]


def check_diff(git, base: str, head: str) -> list[str]:
    """Blockers for the change base...head. git(*args) runs git in the site repo."""
    cfg = config()
    status = git("diff", "--name-status", f"{base}...{head}").stdout.splitlines()
    rows = [ln.split("\t") for ln in status if "\t" in ln]
    blockers: list[str] = []

    added_db = [r[-1] for r in rows if r[0].startswith("A") and BINARY_DB.search(r[-1])]
    if added_db:
        blockers.append(f"DB 파일이 커밋에 들어 있어요 ({', '.join(added_db[:3])})")

    patch = git("diff", f"{base}...{head}").stdout
    if any(INDEXING_API.search(ln) for ln in patch.splitlines() if ln.startswith("+") and not ln.startswith("+++")):
        blockers.append("Indexing API 호출이 추가됐어요 (일반 페이지에 쓰면 사이트 전체가 위험해요)")

    modified = [r[-1] for r in rows if r[0].startswith("M") and r[-1].lower().endswith(CONTENT_EXT)]
    if len(modified) > cfg["max_existing_pages"]:
        blockers.append(f"기존 페이지 {len(modified)}개를 한 번에 바꿔요 (한도 {cfg['max_existing_pages']}개)")

    date_only, date_later, titled = [], [], []
    for path in modified:
        old = git("show", f"{base}:{path}").stdout
        new = git("show", f"{head}:{path}").stdout
        fo, fn = _front(old), _front(new)
        same_body = _body(old) == _body(new)
        changed = {k for k in set(fo) | set(fn) if fo.get(k) != fn.get(k)}
        if changed & set(TITLE_KEYS):
            titled.append(path)
        dates = changed & set(DATE_KEYS)
        if dates and same_body and changed <= set(DATE_KEYS):
            date_only.append(path)
        elif any(_digits(fn.get(k, "")) > _digits(fo.get(k, "")) and fo.get(k) for k in dates) and same_body:
            date_later.append(path)
    if date_only:
        blockers.append(f"본문은 그대로이고 날짜만 바꾼 페이지가 {len(date_only)}개 있어요 ({date_only[0]} 등)")
    if date_later:
        blockers.append(f"본문 변경 없이 날짜를 뒤로 민 페이지가 {len(date_later)}개 있어요 ({date_later[0]} 등)")
    if len(titled) > cfg["max_title_changes"]:
        blockers.append(f"기존 페이지 {len(titled)}개의 제목·설명을 바꿔요 (한도 {cfg['max_title_changes']}개)")

    rec = frozen()
    touches_content = any(r[-1].lower().endswith(CONTENT_EXT) for r in rows)
    if rec and touches_content:
        blockers.append(f"이 사이트는 {rec.get('until')}까지 콘텐츠 변경 동결이에요 ({rec.get('reason')})")
    return blockers


def check_branch(git, branch: str) -> list[str]:
    base = "main" if git("rev-parse", "--verify", "--quiet", "main").returncode == 0 else "master"
    git("fetch", "--quiet", "origin", branch)
    head = f"origin/{branch}" if git("rev-parse", "--verify", "--quiet", f"origin/{branch}").returncode == 0 else branch
    return check_diff(git, base, head)


# ---------- traffic watch ----------

def _impressions(gsc_url: str, days: int) -> int | None:
    from analytics_api import fetch_gsc_pages   # okadmin
    res = fetch_gsc_pages(gsc_url, days=days) or {}
    if res.get("error"):
        return None
    return sum(int(r.get("impressions") or 0) for r in res.get("rows") or [])


def watch_traffic(force: bool = False) -> list[str]:
    """Once a day: freeze and alert any site whose search impressions dropped hard."""
    today = date.today().isoformat()
    state = _read(WATCH_PATH)
    if state.get("checked") == today and not force:
        return []
    cfg = config()
    alerts: list[str] = []
    try:
        from app import ga4_users
        ga4_users._okadmin()
        from analytics_api import site_analytics_config
    except Exception as exc:  # noqa: BLE001
        print(f"seo_guard watch: {exc}")
        return []
    results: dict[str, Any] = {}
    for s in sites.enabled():
        gsc_url = str((site_analytics_config(s.oid) or {}).get("gsc_site_url") or "").strip()
        if not gsc_url:
            continue
        # GSC's newest 2-3 days are still filling in: leave them out, or every site looks like it dropped.
        lag, recent = int(cfg.get("lag_days", 3)), 7
        d_lag, d_recent, d_all = (_impressions(gsc_url, lag), _impressions(gsc_url, lag + recent),
                                  _impressions(gsc_url, lag + recent + 28))
        if d_lag is None or d_recent is None or d_all is None:
            continue
        now = max(d_recent - d_lag, 0) / recent
        before = max(d_all - d_recent, 0) / 28
        results[s.id] = {"now": round(now), "before": round(before)}
        if before >= cfg["min_daily_impressions"] and now < before * cfg["drop_ratio"]:
            text = (f"검색 노출 급락: 최근 7일 하루 {round(now)}회 · 그전 4주 하루 {round(before)}회. "
                    f"콘텐츠 변경을 동결하고 자동 진행을 껐어요. Search Console을 확인해 주세요")
            freeze(s.id, f"노출 급락 감지 {today}")
            try:
                from app.work import autoship
                autoship.set_enabled(False, s.id)
            except Exception as exc:  # noqa: BLE001
                print(f"seo_guard autoship off {s.id}: {exc}")
            try:
                from app import office
                with sites.use(s.id):
                    office.append_event(who="SEO 감시", dept="경보", text=text, state="wait", site=s.id)
            except Exception as exc:  # noqa: BLE001
                print(f"seo_guard event {s.id}: {exc}")
            raise_alert(s.id, text)
            alerts.append(f"{s.id}: {text}")
    with _LOCK:
        _write(WATCH_PATH, {"checked": today, "sites": results, "alerts": alerts})
    return alerts


# ---------- alerts people cannot miss ----------

def raise_alert(site_id: str, text: str) -> None:
    """Keep the alert until someone acknowledges it, and tell the Mac right away."""
    with _LOCK:
        data = _read(ALERTS_PATH)
        prev = data.get(site_id) if isinstance(data.get(site_id), dict) else {}
        data[site_id] = {"text": text, "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                         "first_at": prev.get("first_at") or datetime.now().strftime("%Y-%m-%d %H:%M"),
                         "acked": False}
        _write(ALERTS_PATH, data)


def active_alerts() -> list[dict[str, Any]]:
    data = _read(ALERTS_PATH)
    return [{"site": sid, **rec} for sid, rec in sorted(data.items())
            if isinstance(rec, dict) and not rec.get("acked")]


def ack(site_id: str) -> None:
    with _LOCK:
        data = _read(ALERTS_PATH)
        if isinstance(data.get(site_id), dict):
            data[site_id]["acked"] = True
            data[site_id]["acked_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            _write(ALERTS_PATH, data)


def notify_mac(title: str, text: str) -> None:
    """macOS Notification Center with a sound (HQ runs on the owner's Mac)."""
    import subprocess
    esc = lambda v: str(v).replace("\\", "\\\\").replace('"', '\\"')[:220]
    try:
        subprocess.run(["osascript", "-e",
                        f'display notification "{esc(text)}" with title "Starful HQ" subtitle "{esc(title)}" sound name "Sosumi"'],
                       capture_output=True, timeout=10)
    except Exception as exc:  # noqa: BLE001
        print(f"seo_guard notify: {exc}")
