"""어필리에이트 숫자 자동 취득.

- 클릭: our own data — GA4 affiliate_* events + /go/ redirects (okadmin affiliate_clicks_report), month to date.
- 주문 · 보상: none of 楽天 · A8 · Agoda · Amazon has a public earnings API, so a browser with its own profile
  (data/affiliate_browser) opens each dashboard and Claude reads this month's numbers from the page text.
  The owner logs in once per platform in that browser (🔑 로그인 창); after that it runs by itself every
  few hours. When a session runs out the card says "로그인 필요" for that platform.
Numbers are stored through okadmin's own store (same file and history as 8090 /affiliate).
Needs Playwright on the Mac's Python (the card offers a one-time install).
"""

from __future__ import annotations
from app.paths import HQ_ROOT

import json
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = HQ_ROOT  # starful-hq/ (data lives next to app/)
PROFILE = ROOT / "data" / "affiliate_browser"
STATE_PATH = ROOT / "data" / "affiliate_sync.json"
LOG_PATH = ROOT / "data" / "logs" / "affiliate_sync.log"
EVERY_SEC = 6 * 3600
FIRST_AFTER_SEC = 120
PAGES = {   # each platform's dashboard (where this month's numbers are shown). A8 is no longer read or shown.
    "rakuten": "https://affiliate.rakuten.co.jp/",
    "agoda": "https://partners.agoda.com/dashboard",
    "amazon": "https://affiliate.amazon.co.jp/home",
}
LABELS = {"rakuten": "楽天", "a8": "A8.net", "agoda": "Agoda", "amazon": "Amazon"}
CLICK_KEY = {"rakuten": ("Rakuten",), "a8": ("A8", "A8/기타"), "agoda": ("Agoda",), "amazon": ("Amazon",)}   # okadmin click report names
_LOCK = threading.Lock()          # one browser on the profile at a time (login window or fetch)
_STATE_LOCK = threading.Lock()


# ---------- state ----------

def read_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _update(**changes: Any) -> dict[str, Any]:
    with _STATE_LOCK:
        data = read_state()
        for k, v in changes.items():
            if isinstance(v, dict) and isinstance(data.get(k), dict):
                data[k] = {**data[k], **v}
            else:
                data[k] = v
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(STATE_PATH)
        return data


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _log(line: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {line}\n")


def busy() -> bool:
    return _LOCK.locked()


# ---------- browser (Playwright) ----------

_READY: dict[str, Any] = {}
CHROME_APP = Path("/Applications/Google Chrome.app")


def use_chrome() -> bool:
    """The Mac's own Google Chrome (no separate Chromium download). Its own profile here, not your everyday one."""
    return CHROME_APP.exists()


def _launch_args() -> dict[str, Any]:
    return {"channel": "chrome"} if use_chrome() else {}


def browser_ready() -> bool:
    """Playwright is importable AND its Chromium is downloaded (pip alone leaves the browser missing:
    'Executable doesn't exist at …/ms-playwright/chromium_…'). Checked in a child process, cached 2 minutes."""
    if _READY and time.time() - _READY["at"] < 120:
        return _READY["ok"]
    try:
        import playwright  # noqa: F401
    except ImportError:
        _READY.update(ok=False, at=time.time(), why="module")
        return False
    if use_chrome():   # Google Chrome is installed: nothing else to download
        _READY.update(ok=True, at=time.time(), why="")
        return True
    code = ("import os\nfrom playwright.sync_api import sync_playwright\n"
            "p = sync_playwright().start()\nprint(os.path.exists(p.chromium.executable_path))\np.stop()")
    try:
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        ok = proc.stdout.strip().endswith("True")
    except Exception:  # noqa: BLE001
        ok = False
    _READY.update(ok=ok, at=time.time(), why="" if ok else "chromium")
    return ok


def missing_part() -> str:
    """'module' (Playwright not installed) or 'chromium' (installed, browser not downloaded) or ''."""
    return "" if browser_ready() else _READY.get("why", "module")


def install(wait: bool = False) -> bool:
    """One-time: Playwright + its Chromium into this Python (the one Starful HQ runs on)."""
    if read_state().get("installing"):
        return False

    def run() -> None:
        _update(installing=True, install_error="")
        try:
            cmds = [[sys.executable, "-m", "playwright", "install", "chromium"]]
            try:
                import playwright  # noqa: F401
            except ImportError:
                cmds.insert(0, [sys.executable, "-m", "pip", "install", "--break-system-packages", "playwright"])
            for cmd in cmds:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
                _log(f"$ {' '.join(cmd[1:])}\n{(proc.stdout or '')[-2000:]}{(proc.stderr or '')[-2000:]}")
                if proc.returncode != 0:
                    raise RuntimeError(((proc.stderr or proc.stdout or "").strip().splitlines() or ["설치 실패"])[-1][:200])
            _READY.clear()
            _update(installing=False, installed_at=_stamp())
        except Exception as exc:  # noqa: BLE001 - shown on the card
            _update(installing=False, install_error=str(exc)[:200])

    t = threading.Thread(target=run, name="affiliate-install", daemon=True)
    t.start()
    if wait:
        t.join()
    return True


def open_login() -> bool:
    """A visible browser window on the Mac with the four dashboards: the owner logs in, then closes it."""
    if not browser_ready() or busy():
        return False

    def run() -> None:
        with _LOCK:
            _update(login_open=True)
            try:
                from playwright.sync_api import sync_playwright

                PROFILE.mkdir(parents=True, exist_ok=True)
                with sync_playwright() as p:
                    ctx = p.chromium.launch_persistent_context(str(PROFILE), headless=False, viewport=None, **_launch_args())
                    pages = list(ctx.pages)
                    for i, url in enumerate(PAGES.values()):
                        page = pages[i] if i < len(pages) else ctx.new_page()
                        try:
                            page.goto(url, timeout=60000)
                        except Exception:  # noqa: BLE001 - the owner can still type the address
                            pass
                    closed = threading.Event()
                    ctx.on("close", lambda *_: closed.set())
                    closed.wait(timeout=30 * 60)   # 30 minutes to log in everywhere
                    try:
                        ctx.close()
                    except Exception:  # noqa: BLE001
                        pass
            except Exception as exc:  # noqa: BLE001
                _log("login window failed: " + traceback.format_exc())
                _update(fetch_error=f"로그인 창을 열지 못했어요: {str(exc)[:120]}")
            finally:
                _update(login_open=False)
        fetch_now()   # read the numbers right after logging in

    threading.Thread(target=run, name="affiliate-login", daemon=True).start()
    return True


def _page_texts() -> dict[str, dict[str, str]]:
    """platform → {url (where we ended up), text (visible page text)}."""
    from playwright.sync_api import sync_playwright

    out: dict[str, dict[str, str]] = {}
    PROFILE.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE), headless=True, viewport={"width": 1400, "height": 1000},
                                             **_launch_args())
        try:
            for pid, url in PAGES.items():
                page = ctx.new_page()
                try:
                    page.goto(url, timeout=60000, wait_until="domcontentloaded")
                    try:
                        page.wait_for_load_state("networkidle", timeout=20000)
                    except Exception:  # noqa: BLE001 - dashboards keep polling; text is there anyway
                        pass
                    page.wait_for_timeout(2500)
                    out[pid] = {"url": page.url, "text": page.inner_text("body")[:14000]}
                except Exception as exc:  # noqa: BLE001
                    out[pid] = {"url": url, "text": "", "error": str(exc)[:200]}
                finally:
                    page.close()
        finally:
            ctx.close()
    return out


_LOGIN_HINTS = ("login", "signin", "sign-in", "/ap/", "auth", "sso")


def _read_numbers(pid: str, page: dict[str, str], month: str) -> dict[str, Any]:
    """Claude reads this month's orders and commission off the dashboard text."""
    from app.work import workers
    if page.get("error"):
        return {"ok": False, "error": page["error"]}
    text = page.get("text") or ""
    if not text.strip():
        return {"ok": False, "error": "빈 화면"}
    workers._okadmin_import()
    from llm_claude import MODEL_LIGHT, claude_json

    prompt = f"""Below is the visible text of the {LABELS[pid]} affiliate dashboard ({page.get('url')}).
Find this month's ({month}) numbers for the publisher: number of orders/conversions (成果件数, 注文数, 発生件数, bookings, ordered items)
and the commission/earnings (報酬, 成果報酬, 発生報酬, 収益, commission). Prefer month-to-date totals for {month}.
If the page is a login / sign-in page, set logged_in false.
Return ONLY JSON: {{"logged_in": true, "found": true, "orders": 0, "commission": 0, "currency": "JPY|USD", "clicks": null, "note": "어디서 읽었는지 한국어 한 줄"}}
Use null for any number you cannot find. Never guess.

PAGE TEXT:
{text}"""
    data = claude_json(prompt, timeout_sec=120, model=MODEL_LIGHT) or {}
    looks_login = any(h in (page.get("url") or "").lower() for h in _LOGIN_HINTS)
    if data.get("logged_in") is False or (looks_login and not data.get("found")):
        return {"ok": False, "needs_login": True, "error": "로그인 필요"}
    if not data.get("found") or (data.get("orders") is None and data.get("commission") is None):
        return {"ok": False, "error": "숫자를 못 찾았어요", "note": str(data.get("note") or "")[:120]}
    return {"ok": True, "orders": data.get("orders"), "commission": data.get("commission"),
            "dash_clicks": data.get("clicks"), "note": str(data.get("note") or "")[:120]}


def _our_clicks(month_day: int) -> dict[str, int]:
    """Month-to-date clicks per platform from our own sites (GA4 affiliate events + /go/)."""
    from app.work import workers
    workers._okadmin_import()
    from affiliate_clicks_report import load_affiliate_clicks

    data = load_affiliate_clicks(days=max(1, month_day)) or {}
    by = data.get("by_platform") or {}
    if isinstance(by, list):   # [{platform, clicks}] in some versions
        by = {str(r.get("platform")): int(r.get("clicks") or 0) for r in by if isinstance(r, dict)}
    return {pid: sum(int(by.get(k) or 0) for k in keys) for pid, keys in CLICK_KEY.items()}


def fetch(wait_for_lock: bool = False) -> dict[str, Any]:
    """Read everything and store it. Returns the new state."""
    from app.money import affiliate
    if not _LOCK.acquire(blocking=wait_for_lock):
        return read_state()
    try:
        now = datetime.now()
        month = now.strftime("%Y-%m")
        _update(running=True, fetch_error="")
        result: dict[str, dict[str, Any]] = {}
        clicks: dict[str, int] = {}
        try:
            clicks = _our_clicks(now.day)
        except Exception as exc:  # noqa: BLE001 - dashboards can still be read
            _log("clicks failed: " + traceback.format_exc())
            _update(clicks_error=str(exc)[:160])
        else:
            _update(clicks_error="")
        pages: dict[str, dict[str, str]] = {}
        if browser_ready():
            try:
                pages = _page_texts()
            except Exception as exc:  # noqa: BLE001
                _log("browser failed: " + traceback.format_exc())
                _update(fetch_error=f"브라우저를 열지 못했어요: {str(exc)[:120]}")
        for pid in PAGES:
            if pid in pages:
                try:
                    result[pid] = _read_numbers(pid, pages[pid], month)
                except Exception as exc:  # noqa: BLE001
                    result[pid] = {"ok": False, "error": str(exc)[:160]}
            else:
                result[pid] = {"ok": False, "error": "브라우저 준비 전" if not browser_ready() else "못 읽었어요"}
        # store: our clicks always; orders/commission when the dashboard was read
        cur = {r["id"]: r for r in (affiliate.summary().get("rows") or [])} if affiliate.summary().get("month") == month else {}
        values = {}
        for pid in PAGES:
            old = cur.get(pid) or {}
            r = result[pid]
            values[pid] = {
                "clicks": clicks.get(pid, old.get("clicks", 0)) if clicks else old.get("clicks", 0),
                "orders": r["orders"] if r.get("ok") and r.get("orders") is not None else old.get("orders", 0),
                "commission": r["commission"] if r.get("ok") and r.get("commission") is not None else old.get("commission_raw", 0),
            }
        try:
            changed = affiliate.save(month, values)
        except Exception as exc:  # noqa: BLE001
            _log("save failed: " + traceback.format_exc())
            changed = 0
            _update(fetch_error=f"저장 못 했어요: {str(exc)[:120]}")
        stamp = _stamp()
        return _update(running=False, at=stamp, changed=changed,
                       platforms={pid: {**r, "at": stamp if r.get("ok") else (read_state().get("platforms", {}).get(pid, {}).get("at", ""))}
                                  for pid, r in result.items()})
    finally:
        _update(running=False)
        _LOCK.release()


def fetch_now() -> bool:
    if busy():
        return False
    threading.Thread(target=fetch, name="affiliate-fetch", daemon=True).start()
    return True


# ---------- every few hours ----------

def _loop() -> None:
    time.sleep(FIRST_AFTER_SEC)
    while True:
        try:
            last = read_state().get("at") or ""
            due = True
            if last:
                try:
                    due = (datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M")).total_seconds() >= EVERY_SEC
                except ValueError:
                    due = True
            if due and not busy():
                fetch()
        except Exception:  # noqa: BLE001
            _log("loop: " + traceback.format_exc())
        time.sleep(600)


_STARTED = False


def start_loop() -> None:
    global _STARTED
    if _STARTED:
        return
    _STARTED = True
    # a restart ends whatever was going on (and an old 'browser missing' error no longer applies)
    _update(running=False, login_open=False, installing=False, fetch_error="")
    threading.Thread(target=_loop, name="affiliate-sync", daemon=True).start()


def summary() -> dict[str, Any]:
    s = read_state()
    plats = s.get("platforms") or {}
    return {
        "ready": browser_ready(), "missing": missing_part(), "chrome": use_chrome(), "installing": bool(s.get("installing")), "install_error": s.get("install_error") or "",
        "running": bool(s.get("running")) or busy(), "login_open": bool(s.get("login_open")),
        "at": s.get("at") or "", "error": s.get("fetch_error") or "", "clicks_error": s.get("clicks_error") or "",
        "rows": [{"id": pid, "label": LABELS[pid], **(plats.get(pid) or {})} for pid in PAGES],
        "every_h": EVERY_SEC // 3600,
    }
