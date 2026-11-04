"""자동 진행: changes → PR → review → merge → deploy, without anyone pressing a button. The last step is a person
looking at the live site (사이트 확인).

After every job of a site ends (jobs.run → kick) and every few minutes (start_loop), next_step() looks at where the
site stands and hands the next piece of work to the right team — one step at a time, only when nobody is still
working on that site (so a batch of writers ends up in one PR):
  uncommitted changes, no open PR          → 기획팀 PR 만들기 (issue_pr; the owner's own WIP still blocks it)
  open PR, not reviewed                    → 개발팀 리뷰 (review_and_merge; merges by itself when it passes)
  open PR, reviewed OK, not merged         → 개발팀 머지
  open PR, review asks for fixes           → 마케팀 / 개발팀 고치기 (up to workers.MAX_FIX_ROUNDS, then it stops for a person)
  merged, not deployed                     → 개발팀 배포
  deployed                                 → waits for 사이트 확인 (a person presses "확인했어요")

On/off is per office (floor): data/autoship.json → offices.<id>.on. 사이트 확인(checked_at) stays per site.
"""

from __future__ import annotations
from app.paths import HQ_ROOT

import json
import threading
import time
from pathlib import Path
from typing import Any

from app import sites
ROOT = HQ_ROOT  # starful-hq/ (data lives next to app/)
STATE_PATH = ROOT / "data" / "autoship.json"
LOOP_SEC = 180
KICK_DELAY = 4.0
_LOCK = threading.Lock()


def _office_id(site_id: str | None = None) -> str:
    sid = site_id or sites.current_id()
    try:
        return sites.SITES[sid].office
    except KeyError:
        return sites.current().office


def _read_raw() -> dict[str, Any]:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _migrate(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize to {offices: {oid: {on}}, sites: {sid: {checked_at}}}."""
    if isinstance(raw.get("offices"), dict) and isinstance(raw.get("sites"), dict):
        return {"offices": dict(raw["offices"]), "sites": dict(raw["sites"])}

    offices: dict[str, Any] = {}
    site_checked: dict[str, Any] = {}
    legacy_on: dict[str, list[bool]] = {}

    # Already partially migrated
    if isinstance(raw.get("offices"), dict):
        for oid, blk in raw["offices"].items():
            if isinstance(blk, dict):
                offices[str(oid)] = {"on": bool(blk.get("on", True))}

    for key, blk in raw.items():
        if key in ("offices", "sites") or not isinstance(blk, dict):
            continue
        if key in sites.OFFICES:
            offices.setdefault(key, {})["on"] = bool(blk.get("on", True))
            continue
        if key in sites.SITES:
            if "checked_at" in blk and blk["checked_at"]:
                site_checked[key] = {"checked_at": str(blk["checked_at"])}
            if "on" in blk:
                oid = sites.SITES[key].office
                legacy_on.setdefault(oid, []).append(bool(blk["on"]))

    if isinstance(raw.get("sites"), dict):
        for sid, blk in raw["sites"].items():
            if isinstance(blk, dict) and blk.get("checked_at"):
                site_checked[str(sid)] = {"checked_at": str(blk["checked_at"])}

    for oid in sites.OFFICES:
        if oid in offices:
            continue
        votes = legacy_on.get(oid) or []
        # Floor on unless every known site vote was off
        offices[oid] = {"on": True if not votes else any(votes)}

    return {"offices": offices, "sites": site_checked}


def _read() -> dict[str, Any]:
    return _migrate(_read_raw())


def _write(data: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "offices": {oid: {"on": bool((blk or {}).get("on", True))} for oid, blk in (data.get("offices") or {}).items()},
        "sites": {
            sid: {"checked_at": str((blk or {}).get("checked_at") or "")}
            for sid, blk in (data.get("sites") or {}).items()
            if (blk or {}).get("checked_at")
        },
    }
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE_PATH)


def enabled(site_id: str | None = None, *, office_id: str | None = None) -> bool:
    oid = office_id or _office_id(site_id)
    blk = (_read().get("offices") or {}).get(oid) or {}
    return bool(blk.get("on", True))


def set_enabled(on: bool, site_id: str | None = None, *, office_id: str | None = None) -> str:
    """Turn autoship on/off for a floor. Returns the office id that was changed."""
    oid = office_id or _office_id(site_id)
    with _LOCK:
        data = _read()
        data.setdefault("offices", {}).setdefault(oid, {})["on"] = bool(on)
        _write(data)
    return oid


def office_states() -> dict[str, bool]:
    """office_id → on/off for every known floor."""
    data = _read()
    out: dict[str, bool] = {}
    for oid in sites.OFFICES:
        blk = (data.get("offices") or {}).get(oid) or {}
        out[oid] = bool(blk.get("on", True))
    return out


def mark_checked(site_id: str | None = None) -> str:
    from app import snapshot
    sid = site_id or sites.current_id()
    with _LOCK:
        data = _read()
        data.setdefault("sites", {}).setdefault(sid, {})["checked_at"] = snapshot.now_stamp()
        _write(data)
    return data["sites"][sid]["checked_at"]


def checked_at(site_id: str | None = None) -> str:
    sid = site_id or sites.current_id()
    return str(((_read().get("sites") or {}).get(sid) or {}).get("checked_at") or "")


def _site_busy(sid: str) -> list[str]:
    from app.office import jobs
    with jobs._LOCK:
        return [r for r, j in jobs.JOBS.items() if j.get("running") and (j.get("site") or sites.DEFAULT_SITE) == sid
                and not str(j.get("task") or "").startswith("meeting")]


def _dirty() -> bool:
    from app import snapshot
    files = snapshot._git_all_dirty()
    return bool(files) and not snapshot.owner_wip(files)


def next_step() -> str | None:
    """Hand out the next step for the current site. Returns the person it went to (or None)."""
    from app.office import jobs
    from app import office
    from app.work import workers
    site = sites.current()
    if not site.enabled or not enabled(site.id) or _site_busy(site.id):
        return None
    flow = office.read_flow()
    open_pr = bool(flow.get("pr")) and not flow.get("merged")
    if open_pr:
        if not flow.get("reviewed"):
            return jobs.start_task("review", f"PR #{flow['pr'].get('number')} 리뷰 시작 (자동)", workers.review_and_merge)
        if flow.get("verdict_ok"):
            return jobs.start_task("review", f"PR #{flow['pr'].get('number')} 머지 시작 (자동)", workers.review_and_merge, True)
        if int(flow.get("review_round") or 0) >= workers.MAX_FIX_ROUNDS:
            return None   # the review keeps blocking it: a person decides
        who, _kind = workers.start_fix_for_review()
        return who
    if flow.get("merged") and not flow.get("deployed"):
        return jobs.start_task("deploy", "사이트 올리기 시작 (자동)", workers.deploy)
    if _dirty():
        return jobs.start_task("issue", "바뀐 내용 PR 만들기 시작 (자동)", workers.issue_pr, "")
    return None


def kick(site_id: str) -> None:
    """A job of this site just ended: look for the next step a moment later (after its hand-offs started)."""
    def later() -> None:
        time.sleep(KICK_DELAY)
        try:
            with sites.use(site_id):
                next_step()
        except Exception as exc:  # noqa: BLE001 - never break the job that ended
            print(f"autoship {site_id}: {exc}")

    threading.Thread(target=later, name=f"autoship-{site_id}", daemon=True).start()


def stage(snap_git: dict[str, Any] | None = None) -> dict[str, Any]:
    """Where the current site stands, for the 반영 card: {key, label, detail, needs_person, url, auto, floor_*}."""
    from app import office
    from app.work import workers
    site = sites.current()
    off = sites.office_of(site.id)
    flow = office.read_flow()
    pr = flow.get("pr") if isinstance(flow.get("pr"), dict) else None
    open_pr = bool(pr) and not flow.get("merged")
    git = snap_git or {}
    url = f"https://{site.domain}" if site.domain else ""
    busy = _site_busy(site.id)
    out: dict[str, Any] = {
        "auto": enabled(site.id),
        "office": off.id,
        "floor_label": off.floor_label,
        "url": url,
        "busy": len(busy),
        "checked_at": checked_at(site.id),
        "deployed_at": str(flow.get("deployed_at") or ""),
        "pr": pr,
    }
    if open_pr and flow.get("reviewed") and not flow.get("verdict_ok") and int(flow.get("review_round") or 0) >= workers.MAX_FIX_ROUNDS:
        out.update(key="stuck", label=f"PR #{pr.get('number')} 리뷰가 계속 막아요", detail=(flow.get("review") or "")[:200], needs_person=True)
    elif open_pr:
        step = "머지" if flow.get("verdict_ok") else ("고치는 중" if flow.get("reviewed") else "리뷰")
        out.update(key="pr", label=f"PR #{pr.get('number')} {step}", detail="개발팀이 검토해서 통과하면 머지·배포까지 해요", needs_person=False)
    elif flow.get("merged") and not flow.get("deployed"):
        out.update(key="deploy", label="머지됨 · 사이트 올리는 차례", detail="개발팀이 Cloud Build로 올려요", needs_person=False)
    elif git.get("dirty"):
        detail = (f"{off.floor_label} 자동 진행이 꺼져 있어요 · 아래 버튼으로 PR을 만들거나 자동 진행을 켜 주세요" if not out["auto"]
                  else ("진행 중인 일이 끝나면 PR을 만들어요" if busy else "곧 PR을 만들어요"))
        out.update(key="changes", label=f"올릴 변경 {git.get('file_count') or 0}개", detail=detail, needs_person=not out["auto"])
    elif out["deployed_at"] and out["deployed_at"] > out["checked_at"]:
        out.update(key="check", label=f"올렸어요 · {out['deployed_at'][5:16]}", detail="사이트를 열어 보고 괜찮으면 확인을 눌러 주세요", needs_person=True)
    else:
        out.update(key="idle", label="올릴 변경 없음", detail=(f"마지막 확인 {out['checked_at'][5:16]}" if out["checked_at"] else ""), needs_person=False)
    return out


def _loop() -> None:
    time.sleep(60)
    while True:
        states = office_states()
        for s in sites.enabled():
            if not states.get(s.office, True):
                continue
            try:
                with sites.use(s.id):
                    next_step()
            except Exception as exc:  # noqa: BLE001
                print(f"autoship loop {s.id}: {exc}")
        time.sleep(LOOP_SEC)


_STARTED = False


def start_loop() -> None:
    global _STARTED
    if _STARTED:
        return
    _STARTED = True
    threading.Thread(target=_loop, name="autoship", daemon=True).start()
