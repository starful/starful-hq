"""실행 큐: 회의 제안·팀 할 일을 담아 두고, 자리·한도가 날 때 순서대로 시작한다.

회의 탭「회의 결과」·팀 상세가 실행 입구다. 리포트 팝업은 쓰지 않는다.
사이트별 파일: data[/sites/<id>]/work_queue.json, prefs.json (queue_paused 등).
검색 고칠 목록(GSC 저CTR·저노출)은 항상 켠다.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app import sites
LOOP_SEC = 60
KICK_DELAY = 4.0
KEEP_DONE = 40
_LOCK = threading.Lock()
_STARTED = False

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_HELD = "held"  # e.g. open PR blocks feature work
# Restarted mid-way: content work is simply tried again; code/PR work waits for a person (could double a PR).
RETRY_ON_RESTART = ("write", "search", "polish", "analyze", "research", "images", "ba_ja", "propose", "queue")
_SKIP_WORDS = ("이미 쓴", "건너뜀", "고칠 글", "다듬을 글", "비어")   # old refusals without a code


def classify_refusal(code: str, why: str) -> str:
    """jobs reason code (+ old wording as fallback) → done | held | failed | wait."""
    from app.office import jobs
    if code == "skip" or (not code and any(w in why for w in _SKIP_WORDS)):
        return "done"
    if code in jobs.HOLD_CODES or (not code and "PR" in why and "머지" in why):
        return "held"
    if code == "never":
        return "failed"
    return "wait"


def _after_refusal(item: dict[str, Any], why: str, code: str) -> None:
    """Put a refused queue row where it belongs and keep the reason on it."""
    kind = classify_refusal(code, why)
    why = (why or "").strip()[:200]
    if kind == "done":
        _mark(item["id"], status=STATUS_DONE, finished_at=_stamp(), error=why)
    elif kind == "held":
        _mark(item["id"], status=STATUS_HELD, error=why, hold=code or "held")
    elif kind == "failed":
        _mark(item["id"], status=STATUS_FAILED, finished_at=_stamp(), error=why)
    elif why:
        _mark(item["id"], error=why, wait=code or "busy")


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _queue_path(site_id: str | None = None) -> Path:
    return sites.data_path("work_queue.json", site_id)


def _prefs_path(site_id: str | None = None) -> Path:
    return sites.data_path("prefs.json", site_id)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def prefs(site_id: str | None = None) -> dict[str, Any]:
    data = _read_json(_prefs_path(site_id))
    return {
        "search_backlog": bool(data.get("search_backlog", True)),
        "queue_paused": bool(data.get("queue_paused", False)),
    }


def set_prefs(**changes: Any) -> dict[str, Any]:
    with _LOCK:
        data = _read_json(_prefs_path())
        for k, v in changes.items():
            if k in ("search_backlog", "queue_paused"):
                data[k] = bool(v)
        _write_json(_prefs_path(), data)
        return prefs()


def search_backlog_on(site_id: str | None = None) -> bool:
    """Always on: GSC low-CTR / low-impression list is always the search work queue."""
    return True


def clear_search_targets() -> None:
    """Empty the GSC fix list for the current site (does not delete posts)."""
    from app import snapshot
    empty = {
        "cache_day": "",
        "cached_at": _stamp(),
        "gsc": {
            "low_ctr": [],
            "low_ctr_count": 0,
            "low_impression": [],
            "low_impression_count": 0,
            "page_rows": [],
        },
    }
    path = Path(snapshot.TARGETS_PATH)
    _write_json(path, empty)


def _state(site_id: str | None = None) -> dict[str, Any]:
    data = _read_json(_queue_path(site_id))
    items = data.get("items") if isinstance(data.get("items"), list) else []
    return {"items": [x for x in items if isinstance(x, dict)], "paused": bool(data.get("paused", False))}


def _save(state: dict[str, Any], site_id: str | None = None) -> None:
    items = state.get("items") or []
    # keep recent finished items; drop older ones
    live, done = [], []
    for x in items:
        if x.get("status") in (STATUS_DONE, STATUS_FAILED):
            done.append(x)
        else:
            live.append(x)
    state["items"] = live + done[-KEEP_DONE:]
    state["paused"] = bool(state.get("paused", False))
    _write_json(_queue_path(site_id), state)


def list_items(site_id: str | None = None, include_done: bool = True) -> list[dict[str, Any]]:
    items = _state(site_id)["items"]
    if include_done:
        return items
    return [x for x in items if x.get("status") in (STATUS_QUEUED, STATUS_RUNNING, STATUS_HELD)]


def summary(site_id: str | None = None) -> dict[str, int | bool]:
    items = _state(site_id)["items"]
    p = prefs(site_id)
    counts = {STATUS_QUEUED: 0, STATUS_RUNNING: 0, STATUS_DONE: 0, STATUS_FAILED: 0, STATUS_HELD: 0}
    for x in items:
        st = x.get("status")
        if st in counts:
            counts[st] += 1
    return {
        **counts,
        "paused": p["queue_paused"] or _state(site_id).get("paused", False),
        "search_backlog": p["search_backlog"],
        "pending": counts[STATUS_QUEUED] + counts[STATUS_HELD] + counts[STATUS_RUNNING],
    }


def pending_tasks(site_id: str | None = None) -> dict[str, str]:
    """task id → live queue status (running > held > queued). Used so the map/할 일 button
    doesn't offer ▶ again while the same work is already in the queue."""
    rank = {STATUS_RUNNING: 3, STATUS_HELD: 2, STATUS_QUEUED: 1}
    out: dict[str, str] = {}
    for x in list_items(site_id, include_done=False):
        task = str(x.get("task") or "")
        st = str(x.get("status") or "")
        if not task or st not in rank:
            continue
        if rank[st] > rank.get(out.get(task, ""), 0):
            out[task] = st
    return out


HOLD_LABEL = {   # why a row is held, in a few words (2026-11-23: was always 「PR 머지 후」)
    "held": "보류 중 (PR 머지 후)",
    "person": "보류 · 사람 확인 필요",
    "frozen": "보류 · 콘텐츠 동결 중",
    "nobody": "보류 · 층 소등 중",
    "restart": "보류 · 재시작으로 끊김 (지금 시작)",
}


def pending_label(status: str, hold: str = "") -> str:
    if status == "held":
        return HOLD_LABEL.get(hold or "held", HOLD_LABEL["held"])
    return {"queued": "큐에 담김 · 곧 시작", "running": "진행 중…"}.get(status, "큐에 있음")


def pending_labels(site_id: str | None = None) -> dict[str, str]:
    """task id → label of its live queue row (with the real hold reason)."""
    rank = {STATUS_RUNNING: 3, STATUS_HELD: 2, STATUS_QUEUED: 1}
    best: dict[str, dict[str, Any]] = {}
    for x in list_items(site_id, include_done=False):
        task, st = str(x.get("task") or ""), str(x.get("status") or "")
        if task and st in rank and rank[st] > rank.get(str((best.get(task) or {}).get("status") or ""), 0):
            best[task] = x
    return {t: pending_label(str(x.get("status")), str(x.get("hold") or "")) for t, x in best.items()}


def _site_has_live_job(site_id: str) -> bool:
    """True if a non-meeting job is actually running for this site right now."""
    from app.office import jobs
    with jobs._LOCK:
        for j in jobs.JOBS.values():
            if not j.get("running"):
                continue
            if (j.get("site") or sites.DEFAULT_SITE) != site_id:
                continue
            if str(j.get("task") or "").startswith("meeting"):
                continue
            return True
    return False


def _reopen_idea(x: dict[str, Any]) -> None:
    """A meeting row that will run again: clear the idea's 「started」 so the queue can start it."""
    if x.get("source") != "meeting" or not x.get("pid"):
        return
    try:
        from app.work import planning
        prop = planning.read(str(x["pid"]))
        ideas = list(prop.get("ideas") or [])
        i = int(x.get("idx") if x.get("idx") is not None else -1)
        if prop.get("id") and 0 <= i < len(ideas) and ideas[i].get("started"):
            ideas[i] = {k: v for k, v in ideas[i].items() if k != "started"}
            planning.update(prop["id"], ideas=ideas)
    except Exception as exc:  # noqa: BLE001
        print(f"workqueue reopen idea: {exc}")


def sweep_zombies(site_id: str | None = None) -> int:
    """Mark queue rows stuck as running with no live job → failed.
    Server restart kills the finish watcher; without this, 한눈에·버튼이 영원히「진행 중」."""
    sid = site_id or sites.current_id()
    if _site_has_live_job(sid):
        return 0
    n = 0
    with _LOCK:
        state = _state(sid)
        for x in state["items"]:
            if x.get("status") != STATUS_RUNNING:
                continue
            task = str(x.get("task") or "")
            if task in RETRY_ON_RESTART and int(x.get("retries") or 0) < 1:
                x["status"] = STATUS_QUEUED          # content work: try once more by itself
                x["retries"] = int(x.get("retries") or 0) + 1
                x["error"] = "HQ 재시작으로 끊겨서 다시 대기"
                _reopen_idea(x)
            else:
                x["status"] = STATUS_HELD            # code / PR work: a person decides
                x["hold"] = "restart"
                x["error"] = "HQ 재시작으로 끊겼어요 · 확인 후 「지금 시작」"
                _reopen_idea(x)
            n += 1
        if n:
            _save(state, sid)
    return n


def _pulse_row(x: dict[str, Any]) -> dict[str, str]:
    """Compact queue row for the 한눈에 popover."""
    return {
        "id": str(x.get("id") or ""),
        "title": str(x.get("title") or x.get("label") or x.get("task") or "")[:160],
        "who": str(x.get("who") or ""),
        "task": str(x.get("task") or ""),
        "label": str(x.get("label") or ""),
        "status": str(x.get("status") or ""),
        "error": str(x.get("error") or "")[:160],
        "source": str(x.get("source") or ""),
    }


def site_pulse(site_id: str | None = None) -> dict[str, Any]:
    """한눈에 상태 칸: live job · 대기 · 보류 · 멈춤(좀비) · 쉼.
    Does not clear zombies — sweep_zombies (loop/start) does that so「멈춤」이 한 번은 보이게."""
    sid = site_id or sites.current_id()
    live = _site_has_live_job(sid)
    items = list_items(sid, include_done=False)
    queued = [x for x in items if x.get("status") == STATUS_QUEUED]
    held = [x for x in items if x.get("status") == STATUS_HELD]
    running = [x for x in items if x.get("status") == STATUS_RUNNING]
    stuck = [] if live else list(running)
    # Prefer what's most actionable; still include the rest so the popover is complete.
    if stuck:
        show = stuck + queued + held
    elif live or running:
        show = running + queued + held
    else:
        show = queued + held
    tip = " · ".join(
        t for t in (str(x.get("title") or x.get("label") or x.get("task") or "") for x in show[:3]) if t
    )
    rows = [_pulse_row(x) for x in show[:12]]
    base = {
        "detail": tip,
        "entries": rows,  # not "items" — Jinja dict.items collides in templates
        "n_running": len(running),
        "n_queued": len(queued),
        "n_held": len(held),
        "n_stuck": len(stuck),
    }

    if live:
        return {**base, "kind": "work", "label": "작업중", "detail": tip or "작업 중"}
    if stuck:
        return {**base, "kind": "stuck", "label": "멈춤", "detail": tip or "잡이 끊긴 큐가 있어요"}
    if queued:
        return {**base, "kind": "queued", "label": f"대기 {len(queued)}", "detail": tip or "큐에 담김"}
    if held:
        return {**base, "kind": "held", "label": f"보류 {len(held)}", "detail": tip or "PR 머지 후"}
    return {**base, "kind": "idle", "label": "", "detail": "",
            "n_running": 0, "n_queued": 0, "n_held": 0, "n_stuck": 0, "entries": []}


def _dedupe_key(item: dict[str, Any]) -> str:
    src = item.get("source") or ""
    if src == "meeting":
        return f"meeting:{(item.get('pid') or '')}:{(item.get('idx'))}"
    if src == "task":
        return f"task:{(item.get('task') or '')}"
    if src == "call":
        c = item.get("call") or {}
        return f"call:{item.get('task')}:{c.get('fn')}:{json.dumps([c.get('args'), c.get('items')], ensure_ascii=False, sort_keys=True)}"
    return f"{src}:{item.get('title') or item.get('id')}"


def enqueue(item: dict[str, Any], site_id: str | None = None) -> dict[str, Any] | None:
    """Add one work item. Returns the stored item, or None if already queued/running."""
    sid = site_id or sites.current_id()
    with _LOCK:
        state = _state(sid)
        key = _dedupe_key(item)
        for x in state["items"]:
            if _dedupe_key(x) == key and x.get("status") in (STATUS_QUEUED, STATUS_RUNNING, STATUS_HELD):
                return None
        row = {
            "id": str(uuid.uuid4())[:8],
            "at": _stamp(),
            "status": STATUS_QUEUED,
            "title": str(item.get("title") or "")[:200],
            "source": str(item.get("source") or "manual"),
            "task": str(item.get("task") or ""),
            "who": str(item.get("who") or ""),
            "site": str(item.get("site") or sid),
            "pid": str(item.get("pid") or ""),
            "idx": item.get("idx"),
            "action": item.get("action") if isinstance(item.get("action"), dict) else {},
            "label": str(item.get("label") or "")[:120],
            "priority": int(item.get("priority") or 0),
            "error": "",
            "started_at": "",
            "finished_at": "",
        }
        if isinstance(item.get("call"), dict):
            row["call"] = item["call"]
        if item.get("hold"):
            row["status"] = STATUS_HELD
            row["error"] = str(item.get("hold_reason") or "보류")[:200]
        state["items"].append(row)
        _save(state, sid)
        return row


def enqueue_meeting(pid: str, site_id: str | None = None) -> int:
    """Put every runnable idea from a meeting into the queue. Returns how many were added."""
    from app.work import actions
    from app.work import planning
    prop = planning.read(pid)
    if not prop.get("meeting"):
        return 0
    ideas = prop.get("ideas") or []
    n = 0
    for idx, idea in enumerate(ideas):
        if idea.get("started") or idea.get("excluded"):
            continue
        act = idea.get("action") if isinstance(idea.get("action"), dict) else {}
        kind = act.get("type") or "none"
        if kind == "none" or not actions.ACTION_LABEL.get(kind):
            continue
        sid = idea.get("site") if idea.get("site") in sites.SITES else (site_id or sites.current_id())
        hold_reason = actions.pr_hold_reason(kind, sid)
        hold = bool(hold_reason)
        row = enqueue(
            {
                "source": "meeting",
                "pid": prop.get("id") or pid,
                "idx": idx,
                "title": idea.get("title") or "",
                "who": idea.get("full") or idea.get("name") or idea.get("who") or "",
                "site": sid,
                "task": kind,
                "action": act,
                "label": actions.ACTION_LABEL.get(kind, ""),
                "hold": hold,
                "hold_reason": hold_reason,
            },
            site_id=sid,
        )
        if row:
            n += 1
            kick(sid)
    return n


def enqueue_task(task: str, label: str = "") -> dict[str, Any] | None:
    """Queue a status-tab waiting task (issue / review / deploy / write / search)."""
    from app.work import actions
    hold_why = actions.pr_hold_reason(task)
    row = enqueue(
        {
            "source": "task",
            "task": task,
            "title": label or task,
            "label": label or task,
            "site": sites.current_id(),
            "hold": bool(hold_why),
            "hold_reason": hold_why,
        }
    )
    if row:
        kick(sites.current_id())
    return row


def set_paused(paused: bool) -> None:
    with _LOCK:
        state = _state()
        state["paused"] = bool(paused)
        _save(state)
    set_prefs(queue_paused=bool(paused))
    if not paused:
        kick(sites.current_id())


def _mark(item_id: str, **changes: Any) -> None:
    with _LOCK:
        state = _state()
        for x in state["items"]:
            if x.get("id") == item_id:
                x.update(changes)
                break
        _save(state)


def _release_held() -> None:
    """Open-PR holds (feature/search/polish) → queued again when the PR is gone/merged."""
    from app import office
    from app.work import actions
    with _LOCK:
        state = _state()
        flow = office.read_flow()
        open_pr = bool(flow.get("pr")) and not flow.get("merged")
        changed = False
        from app.work import seo_guard
        from app.office import staffing
        sid = sites.current_id()
        for x in state["items"]:
            if x.get("status") != STATUS_HELD:
                continue
            task = str(x.get("task") or "")
            code = str(x.get("hold") or "held")
            free = {
                "held": (task in actions.PR_HOLD_TASKS or code == "held") and not open_pr,
                "frozen": not seo_guard.frozen(sid),
                "nobody": sites.current().office not in staffing.closed_offices(),
                "person": not flow.get("needs_person") or not open_pr,   # PR merged/closed: nothing left to decide
                "restart": False,   # a person presses 「지금 시작」
            }.get(code, task in actions.PR_HOLD_TASKS and not open_pr)
            if task in ("review", "fix", "fix_code", "push") and not open_pr:
                x["status"], x["finished_at"] = STATUS_DONE, _stamp()   # the PR it waited on is gone
                x["error"] = "PR이 머지·종료돼서 정리"
                x.pop("hold", None)
                changed = True
                continue
            if free:
                x["status"] = STATUS_QUEUED
                x["error"] = ""
                x.pop("hold", None)
                changed = True
        if changed:
            _save(state)


def _start_item(item: dict[str, Any]) -> bool:
    """Try to start one queued item. Returns True if work was handed out."""
    from app.work import actions
    from app.office import jobs
    from app.work import planning
    sid = item.get("site") or sites.current_id()
    with sites.use(sid), jobs.no_queue():
        jobs.reset_request()
        source = item.get("source")
        if source == "call":
            return _start_call(item)
        if source == "meeting":
            pid = item.get("pid") or ""
            idx = int(item.get("idx") if item.get("idx") is not None else -1)
            prop = planning.read(pid)
            if not prop or not prop.get("meeting"):
                _mark(item["id"], status=STATUS_FAILED,
                      error=f"회의 결과 파일이 없어요 ({pid or '?'})", finished_at=_stamp())
                return False
            ideas = prop.get("ideas") or []
            if not (0 <= idx < len(ideas)):
                _mark(item["id"], status=STATUS_FAILED,
                      error=f"제안 #{idx}을 찾지 못했어요 (회의 {pid})", finished_at=_stamp())
                return False
            if ideas[idx].get("started"):
                _mark(item["id"], status=STATUS_DONE, finished_at=_stamp(), error="")
                return False
            who, what = actions.run_idea(ideas[idx])
            if not who:
                res = jobs.request_result()
                why = (res.get("why") or what or "").strip()
                code = res.get("code") or ""
                if classify_refusal(code, why) == "done":
                    ideas[idx]["started"] = f"{_stamp()[:16]} · {why}"
                    planning.update(prop["id"], ideas=ideas)
                _after_refusal(item, "" if why == "검색 글 고치기" else why, code)
                return False
            ideas[idx]["started"] = f"{_stamp()[:16]} · {what}"
            planning.update(prop["id"], ideas=ideas)
            _mark(item["id"], status=STATUS_RUNNING, started_at=_stamp(), label=what, error="")
            # job runs async; mark done when? We mark running now; a short follow-up marks done
            # after kick delay if the idea already has started (job accepted).
            threading.Thread(target=_finish_when_idle, args=(item["id"], sid), daemon=True).start()
            return True
        if source == "task":
            from app.work import actions
            task = item.get("task") or ""
            hold_why = actions.pr_hold_reason(task, sid)
            if hold_why:
                _mark(item["id"], status=STATUS_HELD, error=hold_why[:200])
                return False
            who = None
            if task == "ba_ja":
                from app.work import workers as workers_mod
                paths = item.get("paths") if isinstance(item.get("paths"), list) else None
                who = jobs.start_task("ba_ja", item.get("title") or "BA 일본어화 시작",
                                     workers_mod.translate_ba_ja, paths)
            elif task in actions.ACTIONS and task not in ("write", "search", "issue", "review", "deploy"):
                # panel action ids (research, polish, analyze, optimize, …)
                who = actions.ACTIONS[task]["run"]()
            else:
                who = actions.run_waiting(task)
            if not who:
                res = jobs.request_result()
                _after_refusal(item, res.get("why") or "", res.get("code") or "")
                return False
            _mark(item["id"], status=STATUS_RUNNING, started_at=_stamp(), error="")
            threading.Thread(target=_finish_when_idle, args=(item["id"], sid), daemon=True).start()
            return True
    return False


def _start_call(item: dict[str, Any]) -> bool:
    """A start parked by jobs._park: call the same function with the same arguments."""
    import importlib
    from app.office import jobs
    c = item.get("call") or {}
    try:
        mod, qual = str(c.get("fn") or "").split(":", 1)
        fn: Any = importlib.import_module(mod)
        for part in qual.split("."):
            fn = getattr(fn, part)
    except Exception as exc:  # noqa: BLE001
        _mark(item["id"], status=STATUS_FAILED, finished_at=_stamp(), error=f"다시 부를 수 없어요 ({c.get('fn')}: {exc})"[:200])
        return False
    task, line, args = str(item.get("task") or ""), str(c.get("line") or item.get("title") or "시작"), list(c.get("args") or [])
    if c.get("items"):
        started = jobs.start_task_split(task, list(c["items"]), lambda n: line.replace("{n}", str(n)), fn, *args)
        who = bool(started)
    else:
        who = jobs.start_task(task, line, fn, *args)
    if not who:
        res = jobs.request_result()
        _after_refusal(item, res.get("why") or "", res.get("code") or "")
        return False
    _mark(item["id"], status=STATUS_RUNNING, started_at=_stamp(), error="")
    threading.Thread(target=_finish_when_idle, args=(item["id"], item.get("site") or sites.current_id()), daemon=True).start()
    return True


def requeue(item_id: str) -> None:
    """Held row → queued again (a person said go)."""
    with _LOCK:
        state = _state()
        for x in state["items"]:
            if str(x.get("id") or "") == item_id:
                x["status"], x["error"] = STATUS_QUEUED, ""
                x.pop("hold", None)
        _save(state)


def _finish_when_idle(item_id: str, site_id: str) -> None:
    """After the started job(s) for this site settle, mark the queue row done."""
    from app.office import jobs
    time.sleep(2)
    for _ in range(180):  # up to ~15 min
        busy = False
        with jobs._LOCK:
            for j in jobs.JOBS.values():
                if j.get("running") and (j.get("site") or sites.DEFAULT_SITE) == site_id \
                        and not str(j.get("task") or "").startswith("meeting"):
                    busy = True
                    break
        if not busy:
            _mark(item_id, status=STATUS_DONE, finished_at=_stamp())
            kick(site_id)
            return
        time.sleep(5)
    _mark(item_id, status=STATUS_DONE, finished_at=_stamp(), error="시간 초과 · 잡 상태를 확인하세요")


def next_item() -> bool:
    """Start at most one queued item for the current site. Returns True if started."""
    if prefs()["queue_paused"] or _state().get("paused"):
        return False
    from app import tools as tools_mod
    blocked, why = tools_mod.claude_blocked()
    if blocked:
        try:
            tools_mod.halt_claude_limit(why)
        except Exception as exc:  # noqa: BLE001
            print(f"workqueue claude halt: {exc}")
        return False
    _release_held()
    with _LOCK:
        state = _state()
        pending = [x for x in state["items"] if x.get("status") == STATUS_QUEUED]
    pending.sort(key=lambda x: -int(x.get("priority") or 0))   # stable: same priority keeps queue order
    for item in pending:
        if _start_item(item):
            return True
    return False


def start_now(item_id: str, site_id: str | None = None) -> dict[str, Any]:
    """Start one queued item immediately (한눈에 팝업 「지금 시작»)."""
    from app.office import jobs
    from app import tools as tools_mod

    sid = site_id or sites.current_id()
    blocked, why = tools_mod.claude_blocked()
    if blocked:
        try:
            tools_mod.halt_claude_limit(why)
        except Exception as exc:  # noqa: BLE001
            print(f"workqueue start_now claude halt: {exc}")
        return {"ok": False, "msg": why or "Claude 한도예요 · 풀린 뒤 다시 시작해 주세요"}
    if prefs(sid)["queue_paused"] or _state(sid).get("paused"):
        return {"ok": False, "msg": "실행 큐가 멈춰 있어요 · 다시 켠 뒤 시도해 주세요"}
    with _LOCK:
        state = _state(sid)
        item = next((x for x in state["items"] if str(x.get("id") or "") == str(item_id)), None)
        if not item:
            return {"ok": False, "msg": "큐에서 찾지 못했어요"}
        st = str(item.get("status") or "")
        if st == STATUS_RUNNING:
            return {"ok": False, "msg": "이미 진행 중이에요"}
        if st == STATUS_HELD and item.get("hold") == "restart":
            item["status"], item["error"] = STATUS_QUEUED, ""   # a person said go
            item.pop("hold", None)
            _save(state, sid)
            st = STATUS_QUEUED
        if st == STATUS_HELD:
            return {"ok": False, "msg": item.get("error") or "보류 중이에요 · PR 머지 후 자동으로 빠져요"}
        if st != STATUS_QUEUED:
            return {"ok": False, "msg": f"시작할 수 없어요 ({st or '?'})"}
        row = dict(item)
    title = str(row.get("title") or row.get("label") or row.get("task") or "일")[:60]
    with sites.use(sid):
        jobs.reset_request()
        if _start_item(row):
            return {"ok": True, "msg": f"시작했어요 · {title}", "started": True}
        why = (jobs.request_result().get("why") or "").strip()
        # _start_item may have moved the row (held / done / failed) or left a tip in error
        after = next((x for x in list_items(sid, include_done=True) if str(x.get("id") or "") == str(item_id)), None)
        tip = ((after or {}).get("error") or why or "").strip()
        if after and after.get("status") == STATUS_HELD:
            return {"ok": False, "msg": tip or "보류로 옮겼어요 · PR 머지 후"}
        if after and after.get("status") == STATUS_DONE:
            return {"ok": True, "msg": tip or "할 일이 없어 끝냈어요", "started": False}
        if after and after.get("status") == STATUS_FAILED:
            return {"ok": False, "msg": tip or "시작하지 못했어요"}
        if tip and ("자리" in tip or "한도" in tip or "하는 중" in tip or "모두 일" in tip):
            return {"ok": False, "msg": tip}
        if tip:
            return {"ok": False, "msg": tip}
        return {"ok": False, "msg": "지금은 시작할 수 없어요 · 자리·한도를 확인해 주세요"}


def drain_now(site_id: str | None = None) -> dict[str, Any]:
    """Start the next queued item for a site right away (no kick delay)."""
    from app import tools as tools_mod

    sid = site_id or sites.current_id()
    blocked, why = tools_mod.claude_blocked()
    if blocked:
        try:
            tools_mod.halt_claude_limit(why)
        except Exception as exc:  # noqa: BLE001
            print(f"workqueue drain_now claude halt: {exc}")
        return {"ok": False, "msg": why or "Claude 한도예요 · 풀린 뒤 다시 시작해 주세요"}
    if prefs(sid)["queue_paused"] or _state(sid).get("paused"):
        return {"ok": False, "msg": "실행 큐가 멈춰 있어요"}
    with sites.use(sid):
        if next_item():
            return {"ok": True, "msg": "대기 일을 시작했어요", "started": True}
    n = sum(1 for x in list_items(sid, include_done=False) if x.get("status") == STATUS_QUEUED)
    if n:
        return {"ok": False, "msg": f"대기 {n}건이 남아 있어요 · 자리·한도를 확인해 주세요"}
    return {"ok": False, "msg": "시작할 대기가 없어요"}


def kick(site_id: str) -> None:
    def later() -> None:
        time.sleep(KICK_DELAY)
        try:
            with sites.use(site_id):
                next_item()
        except Exception as exc:  # noqa: BLE001
            print(f"workqueue {site_id}: {exc}")

    threading.Thread(target=later, name=f"workqueue-{site_id}", daemon=True).start()


def _loop() -> None:
    time.sleep(45)
    while True:
        try:
            from app import tools as tools_mod
            tools_mod.resume_after_halt()
        except Exception as exc:  # noqa: BLE001
            print(f"workqueue resume: {exc}")
        for s in sites.enabled():
            try:
                with sites.use(s.id):
                    sweep_zombies(s.id)
                    next_item()
            except Exception as exc:  # noqa: BLE001
                print(f"workqueue loop {s.id}: {exc}")
        time.sleep(LOOP_SEC)


def start_loop() -> None:
    global _STARTED
    if _STARTED:
        return
    _STARTED = True
    for s in sites.enabled():
        try:
            with sites.use(s.id):
                sweep_zombies(s.id)
        except Exception as exc:  # noqa: BLE001
            print(f"workqueue sweep {s.id}: {exc}")
    threading.Thread(target=_loop, name="workqueue", daemon=True).start()
