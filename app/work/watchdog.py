"""HQ 자가 점검 (2026-11-25): HQ가 사람 없이도 멈추지 않고, 같은 실패에 토큰을 태우지 않게.

1. 루프 생존: 실행 큐·자동 진행 루프는 매 바퀴 beat()를 남긴다. 10분 넘게 소식이 없으면 새로 띄운다.
   (11/24 밤: 루프가 아예 시작되지 않아 아무 일도 안 돌던 사고)
2. 같은 실패 반복 차단: 같은 사이트·같은 일이 같은 이유로 24시간 안에 3번 실패하면 6시간 동안
   그 일을 맡기지 않는다(거부는 Claude를 부르기 전이라 토큰을 안 씀). 그 일이 한 번 성공하면 풀린다.
   (krcampus 배포 3연속 실패 · okpy PR 만들기 5연속 실패 · SiteKPI 기능 만들기 3연속 실패)
3. 상태 한 줄: 한눈에 맨 위에 「점검 정상」 또는 문제 목록 — 사람이 봐야 할 것만.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from app import sites

PATH = sites.ROOT / "data" / "watchdog.json"
REPEAT_N = 3                 # same failure this many times …
REPEAT_WINDOW_H = 24         # … within this many hours …
BLOCK_H = 6                  # … blocks that work for this long
STALE_SEC = 10 * 60          # a loop silent this long is restarted
CHECK_SEC = 120

_LOCK = threading.Lock()
_BEATS: dict[str, float] = {}
_LOOPS: dict[str, Callable[[], None]] = {}
_STARTED_AT = time.time()
_STARTED = False


# ---------- storage ----------

def _read() -> dict[str, Any]:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict[str, Any]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(PATH)


def _now() -> datetime:
    return datetime.now()


def _fmt(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M")


# ---------- 1. loops ----------

def register(name: str, loop: Callable[[], None]) -> None:
    """A background loop that beat()s every round and can be started again."""
    _LOOPS[name] = loop
    _BEATS.setdefault(name, time.time())


def beat(name: str) -> None:
    _BEATS[name] = time.time()


def _revive_loops() -> list[str]:
    out = []
    for name, loop in list(_LOOPS.items()):
        if time.time() - _BEATS.get(name, 0) > STALE_SEC:
            _BEATS[name] = time.time()
            threading.Thread(target=loop, name=f"{name}-revived", daemon=True).start()
            out.append(name)
    return out


# ---------- 2. repeated failures ----------

def _reason(line: str) -> str:
    """The part of a failure line that stays the same between tries (paths, numbers, times dropped)."""
    s = re.sub(r"^못 했어요\.?\s*", "", str(line or ""))
    s = re.sub(r"\(로그:.*?\)|/[\w./-]+|\d+", "#", s)
    return s.strip()[:80]


def record(site: str, task: str, ok: bool, line: str = "") -> str:
    """Called by jobs when a job ends. Returns a block message when this failure starts a block."""
    if not site or not task or task.startswith("meeting"):
        return ""
    key = f"{site}:{task}"
    with _LOCK:
        data = _read()
        fails = data.setdefault("fails", {})
        blocks = data.setdefault("blocks", {})
        if ok:
            a = fails.pop(key, None)
            b = blocks.pop(key, None)
            changed = a is not None or b is not None
            if changed:
                _write(data)
            return ""
        cut = _fmt(_now() - timedelta(hours=REPEAT_WINDOW_H))
        rows = [r for r in fails.get(key, []) if r.get("at", "") >= cut]
        why = _reason(line)
        rows.append({"at": _fmt(_now()), "why": why, "line": str(line)[:240]})
        fails[key] = rows[-10:]
        same = [r for r in rows if r.get("why") == why]
        msg = ""
        if len(same) >= REPEAT_N and key not in blocks:
            until = _fmt(_now() + timedelta(hours=BLOCK_H))
            blocks[key] = {"until": until, "line": str(line)[:240], "n": len(same)}
            msg = (f"같은 이유로 {len(same)}번 실패해서 {until[11:]}까지 이 일을 쉬어요 "
                   f"(토큰 절약) · {str(line)[:120]}")
        _write(data)
    return msg


def blocked(site: str, task: str) -> dict[str, Any] | None:
    """An active block for this site's task, or None."""
    key = f"{site}:{task}"
    with _LOCK:
        data = _read()
        rec = (data.get("blocks") or {}).get(key)
        if not rec:
            return None
        if str(rec.get("until") or "") <= _fmt(_now()):
            data["blocks"].pop(key, None)
            _write(data)
            return None
        return rec


def unblock(site: str, task: str) -> None:
    """A person pressed the button for this work: give it another try."""
    with _LOCK:
        data = _read()
        changed = (data.get("blocks") or {}).pop(f"{site}:{task}", None) is not None
        changed = (data.get("fails") or {}).pop(f"{site}:{task}", None) is not None or changed
        if changed:
            _write(data)


# ---------- 3. status ----------

def status() -> dict[str, Any]:
    """{'ok': bool, 'items': [{'site','label','text'}]} for the 한눈에 line."""
    items = []
    now = _fmt(_now())
    for key, rec in sorted((_read().get("blocks") or {}).items()):
        if str(rec.get("until") or "") <= now:
            continue
        sid, task = key.split(":", 1)
        label = sites.SITES[sid].label if sid in sites.SITES else sid
        items.append({"site": sid, "label": label, "task": task,
                      "text": f"{task} 쉬는 중 ~{str(rec.get('until'))[11:]} · {str(rec.get('line') or '')[:120]}"})
    if time.time() - _STARTED_AT > STALE_SEC:
        for name in _LOOPS:
            age = time.time() - _BEATS.get(name, 0)
            if age > STALE_SEC:
                items.append({"site": "", "label": "HQ", "task": name, "text": f"{name} 루프가 {int(age // 60)}분째 소식 없음"})
    return {"ok": not items, "items": items}


# ---------- loop ----------

def _loop() -> None:
    time.sleep(CHECK_SEC)
    while True:
        try:
            revived = _revive_loops()
            if revived:
                from app import office
                office.append_event(who="HQ 점검", dept="시스템", state="wait", site="",
                                    text=f"멈춘 루프를 다시 띄웠어요 ({', '.join(revived)})")
        except Exception as exc:  # noqa: BLE001
            print(f"watchdog: {exc}")
        time.sleep(CHECK_SEC)


def start_loop() -> None:
    global _STARTED
    if _STARTED:
        return
    _STARTED = True
    threading.Thread(target=_loop, name="watchdog", daemon=True).start()
