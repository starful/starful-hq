"""python -m app  →  start Starful HQ on PORT (default 8091)."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

# The background loops (queue · autoship · autodev · affsync) start in app.routes only when this is set:
# it used to come from the werkzeug reloader, which HQ no longer uses (see _reload_when_idle).
os.environ["WERKZEUG_RUN_MAIN"] = "true"

from app.routes import app  # noqa: E402

PORT = int(os.environ.get("PORT", "8091"))
WATCH = [Path(__file__).resolve().parent, Path(os.environ.get("WORK_ROOT", "/opt/work")) / "okadmin"]


def _mtimes() -> dict[str, float]:
    out: dict[str, float] = {}
    for root in WATCH:
        files = root.rglob("*.py") if root.name == "app" else root.glob("*.py")
        for p in files:
            try:
                out[str(p)] = p.stat().st_mtime
            except OSError:
                pass
    return out


def _busy() -> int:
    from app.office import jobs
    with jobs._LOCK:
        return sum(1 for j in jobs.JOBS.values() if j.get("running"))


def _reload_when_idle() -> None:
    """Code changed → restart HQ, but only once no job is running (2026-11-24).

    The werkzeug reloader restarted at once and cut off running jobs (a SiteKPI feature build died
    half-way and left its edits uncommitted). Here the new code waits for the jobs to finish.
    """
    base = _mtimes()
    told = False
    while True:
        time.sleep(3)
        now = _mtimes()
        if now == base:
            continue
        n = _busy()
        if n:
            if not told:
                print(f"code changed · waiting for {n} running job(s) before restarting", flush=True)
                try:
                    from app import office
                    office.append_event(who="HQ", dept="시스템", text=f"HQ 코드가 바뀌었어요 · 진행 중인 일 {n}개가 끝나면 다시 켜요",
                                        state="wait", site="")
                except Exception:  # noqa: BLE001
                    pass
                told = True
            continue
        time.sleep(1)   # let an editor finish writing
        print("code changed · no job running · restarting HQ", flush=True)
        os.execv(sys.executable, [sys.executable, "-m", "app"])


if __name__ == "__main__":
    threading.Thread(target=_reload_when_idle, name="idle-reload", daemon=True).start()
    app.run(host="127.0.0.1", port=PORT, threaded=True, use_reloader=False)
