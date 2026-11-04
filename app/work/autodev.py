"""자동 공통화 · 소스정리 (개발팀 외국인 3명: 리암 · 프리야 · 웨이).

A background loop looks every CHECK_SEC whether some cleanup is worth doing, and only then hands it
to the free member of the dev team:
- only when the code in that area changed since the last automatic pass (nothing changed → skip),
- at most once a day per area,
- never while a PR is open, someone already refactors, or uncommitted code is lying around.
Claude is told to change nothing when there is nothing worth fixing. Results go through the normal
PR → review flow (devwork.refactor hands off to 기획팀 for the PR, 다은 reviews).
"""

from __future__ import annotations
from app.paths import HQ_ROOT

import json
import threading
import time
from pathlib import Path
from typing import Any

from app.work import devwork
from app.office import jobs
from app import office
from app import sites
from app import snapshot
ROOT = HQ_ROOT  # starful-hq/ (data lives next to app/)
STATE_PATH = sites.data("auto_refactor.json")   # per site
CHECK_SEC = 15 * 60
FIRST_CHECK_SEC = 3 * 60
MIN_GAP_SEC = 24 * 3600
PLAN = (("optimize", "all"),)   # 최적화(소스·주석 정리)만 자동. 공통화는 위험해서 사람이 따로 맡긴다
AREA_PATHS = {"back": ["app/*.py", "app/**/*.py", "scripts", "run.py"],
              "front": ["app/templates", "app/static/css", "app/static/js"],
              "all": ["app", "scripts", "run.py"]}
AUTO_ASK = ("자동 점검이다. 최근 바뀐 코드 위주로 꼭 필요한 곳만 고쳐라. 고칠 가치가 있는 곳이 없으면 "
            "아무 파일도 바꾸지 말고 마지막 줄에 '고칠 곳 없음'이라고만 써라.")
_LOCK = threading.Lock()
_STARTED = False


def read_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(data: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def is_on() -> bool:
    # okpy: on unless switched off. A newly added site starts off: turn it on once its flow is trusted
    return read_state().get("on", sites.current_id() == sites.DEFAULT_SITE)


def set_on(on: bool) -> None:
    with _LOCK:
        data = read_state()
        data["on"] = bool(on)
        _write(data)


def _head() -> str:
    return devwork._git("rev-parse", "HEAD").stdout.strip()


def changed_since(base: str, area: str) -> bool:
    """Did code in this area change since `base`? No base yet: yes (first pass)."""
    if not base:
        return True
    proc = devwork._git("diff", "--name-only", f"{base}..HEAD", "--", *AREA_PATHS[area])
    if proc.returncode != 0:
        return True
    return bool(proc.stdout.strip())


def next_job(state: dict[str, Any], now: float | None = None) -> tuple[str, str] | None:
    now = time.time() if now is None else now
    last = state.get("last") or {}
    for kind, area in PLAN:
        rec = last.get(f"{kind}:{area}") or {}
        if now - float(rec.get("at") or 0) < MIN_GAP_SEC:
            continue
        if not changed_since(str(rec.get("head") or ""), area):
            continue
        return kind, area
    return None


def _record(kind: str, area: str, result: str) -> None:
    with _LOCK:
        data = read_state()
        data.setdefault("last", {})[f"{kind}:{area}"] = {"at": time.time(), "head": _head(),
                                                          "when": time.strftime("%Y-%m-%d %H:%M"), "result": result[:200]}
        _write(data)


def _auto(work, kind: str, area: str) -> str:
    try:
        out = devwork.optimize(work, AUTO_ASK, True) if kind == "optimize" else devwork.refactor(work, kind, area, AUTO_ASK, True)
    except Exception as exc:
        _record(kind, area, f"실패: {exc}")
        raise
    _record(kind, area, out)
    return "자동 " + out


def blocked() -> str:
    """Why an automatic pass can't start now ('' = it can)."""
    flow = office.read_flow()
    if flow.get("pr") and not flow.get("merged"):
        return "열린 PR이 있어요"
    if snapshot.owner_wip():
        return "사장님이 커밋하지 않은 작업 파일이 있어요"
    groups = jobs.busy_groups()
    for g in ("refactor", "pr", "deploy", "meeting"):
        if g in groups:
            return f"{office.staff_by_role(groups[g])['name']}이(가) 다른 일을 하는 중"
    return ""


def tick() -> str:
    if not is_on():
        return "꺼짐"
    why = blocked()
    if why:
        return why
    pick = next_job(read_state())
    if not pick:
        return "할 일 없음 (바뀐 코드 없음)"
    kind, area = pick
    label = devwork.KINDS[kind][0]
    who = jobs.start_task("optimize" if kind == "optimize" else devwork.area_task(area), f"자동 {label} 시작", _auto, kind, area)
    return f"{office.staff_by_role(who)['name']} 시작" if who else "맡을 사람이 없어요"


def _loop() -> None:
    time.sleep(FIRST_CHECK_SEC)
    while True:
        try:
            for site in sites.enabled():   # each site's repo on its own
                with sites.use(site.id):
                    with _LOCK:
                        data = read_state()
                        data["checked"] = time.strftime("%Y-%m-%d %H:%M")
                        _write(data)
                    msg = tick()
                    with _LOCK:
                        data = read_state()
                        data["note"] = msg
                        _write(data)
        except Exception as exc:  # noqa: BLE001 - the loop must keep running
            print(f"autodev: {exc}")
        time.sleep(CHECK_SEC)


def start_loop() -> None:
    global _STARTED
    if _STARTED:
        return
    _STARTED = True
    threading.Thread(target=_loop, name="autodev", daemon=True).start()


def summary() -> dict[str, Any]:
    st = read_state()
    last = st.get("last") or {}
    rows = []
    for kind, area in PLAN:
        rec = last.get(f"{kind}:{area}") or {}
        rows.append({"label": devwork.KINDS[kind][0] + (" (소스·주석 정리)" if kind == "optimize" else f" · {devwork.AREAS[area][0]}"),
                     "who": "개발팀 2명" if kind == "optimize" else "개발팀",
                     "when": rec.get("when") or "아직 안 함", "result": rec.get("result") or ""})
    return {"on": st.get("on", True), "checked": st.get("checked") or "", "note": st.get("note") or "", "rows": rows}
