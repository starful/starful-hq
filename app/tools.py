"""Are the AI / deploy tools ready? Same checks okadmin (8090) uses, nothing new.

- Claude CLI : okadmin llm_claude.claude_bin() + claude_cli_ready()  (`claude auth status`)
- gh         : okadmin github_ops.gh_available()                    (`gh auth status`)
- gcloud     : on PATH (deploy.sh needs it), active account
- Gemini key : okadmin merge_pipeline_env() view of okpy/.env + okadmin/.env (Imagen only)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app import snapshot
from app.paths import DATA
_CACHE: dict[str, Any] = {"at": 0.0, "rows": []}
_LOCK = threading.Lock()
TTL = 120
_HALT_PATH = DATA / "claude_halt.json"
_HALT_LOCK = threading.Lock()


def _okadmin_import() -> None:
    import sys

    path = str(snapshot.OKADMIN_ROOT)
    if path not in sys.path:
        sys.path.insert(0, path)


def claude_blocked() -> tuple[bool, str]:
    """True when Claude 5h/weekly session is at the limit (level=over or ≥100%)."""
    try:
        from app.money import costs
        info = costs.claude_limits()
    except Exception:  # noqa: BLE001
        return False, ""
    for w in info.get("windows") or []:
        if not isinstance(w, dict):
            continue
        pct = float(w.get("percent") or 0)
        level = str(w.get("level") or "")
        if level == "over" or pct >= 99.5:
            label = str(w.get("label") or "세션")
            when = str(w.get("resets_in") or w.get("resets") or "").strip()
            tip = f"{when} 후" if when else "한도 풀린 뒤"
            return True, f"Claude {label} 한도 · {tip} 다시"
    return False, ""


def is_claude_limit_error(err: Any) -> bool:
    low = str(err or "").lower()
    return any(x in low for x in (
        "session limit", "usage limit", "rate limit", "사용 한도", "session/usage limit",
        "hit your session limit", "you've hit your",
    ))


def halt_claude_limit(reason: str = "") -> str:
    """Stop autoship on every floor + pause every site queue until a person turns them back on."""
    from app import sites
    from app.work import autoship, workqueue

    blocked, why = claude_blocked()
    msg = reason or why or "Claude 사용 한도"
    with _HALT_LOCK:
        DATA.mkdir(parents=True, exist_ok=True)
        payload = {
            "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "reason": msg[:240],
            "autoship_off": True,
            "queues_paused": True,
        }
        _HALT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for oid in sites.OFFICES:
        try:
            autoship.set_enabled(False, office_id=oid)
        except Exception as exc:  # noqa: BLE001
            print(f"halt autoship {oid}: {exc}")
    for s in sites.enabled():
        try:
            with sites.use(s.id):
                workqueue.set_paused(True)
        except Exception as exc:  # noqa: BLE001
            print(f"halt queue {s.id}: {exc}")
    with _LOCK:
        _CACHE["at"] = 0.0
    return f"Claude 한도로 자동 진행·실행 큐를 멈췄어요 · {msg}"


def clear_claude_halt() -> None:
    try:
        _HALT_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def claude_halt_info() -> dict[str, Any] | None:
    try:
        data = json.loads(_HALT_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _claude() -> dict[str, Any]:
    _okadmin_import()
    from llm_claude import claude_bin, claude_cli_ready

    bin_path = claude_bin()
    found = Path(bin_path).is_file() or bool(shutil.which(bin_path))
    if not found:
        return {"ok": False, "detail": f"claude 명령을 못 찾았어요 ({bin_path})",
                "fix": "CLAUDE_BIN을 okadmin/.env에 적거나 PATH에 claude를 두세요"}
    if not claude_cli_ready():
        return {"ok": False, "detail": "로그인 안 됨", "fix": "아래 'Claude 로그인' 버튼", "login": True}
    blocked, why = claude_blocked()
    if blocked:
        return {
            "ok": False,
            "detail": why,
            "fix": "층 메뉴에서 자동 진행·실행 큐를 한도 풀린 뒤 다시 켜 주세요",
            "limit": True,
        }
    return {"ok": True, "detail": "구독 로그인됨"}


def _gh() -> dict[str, Any]:
    _okadmin_import()
    from github_ops import gh_available

    info = gh_available()
    if info.get("logged_in"):
        return {"ok": True, "detail": f"{info.get('user') or ''} 로그인됨".strip()}
    if "not found" in str(info.get("error") or ""):
        return {"ok": False, "detail": "gh 명령이 없어요", "fix": "터미널: brew install gh"}
    return {"ok": False, "detail": "로그인 안 됨", "fix": "터미널: gh auth login"}


def _gcloud() -> dict[str, Any]:
    path = shutil.which("gcloud")
    if not path:
        return {"ok": False, "detail": "gcloud 명령이 없어요", "fix": "Google Cloud SDK 설치 후 ./start.sh 다시 실행"}
    try:
        out = subprocess.run([path, "config", "get-value", "account"], capture_output=True, text=True, timeout=20)
        account = (out.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        account = ""
    if not account or account == "(unset)":
        return {"ok": False, "detail": "로그인 계정 없음", "fix": "터미널: gcloud auth login"}
    return {"ok": True, "detail": account}


def _gemini() -> dict[str, Any]:
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        try:
            _okadmin_import()
            from pipeline_limits import merge_pipeline_env

            key = (merge_pipeline_env(snapshot.OKPY_ROOT).get("GEMINI_API_KEY") or "").strip()
        except Exception:  # noqa: BLE001
            key = ""
    if not key:
        return {"ok": False, "detail": "키 없음 (이미지 그리기에만 필요)", "fix": "okadmin/.env 또는 okpy/.env에 GEMINI_API_KEY"}
    return {"ok": True, "detail": f"키 있음 (…{key[-4:]})"}


CHECKS = (
    ("claude", "Claude CLI", "글·검색·이슈·리뷰", _claude),
    ("gh", "GitHub (gh)", "이슈·리뷰·머지", _gh),
    ("gcloud", "gcloud", "올리기", _gcloud),
    ("gemini", "Gemini 키", "이미지·표지", _gemini),
)


def status(refresh: bool = False) -> list[dict[str, Any]]:
    with _LOCK:
        if not refresh and _CACHE["rows"] and time.time() - _CACHE["at"] < TTL:
            return list(_CACHE["rows"])
    rows = []
    for key, name, used_by, check in CHECKS:
        try:
            info = check()
        except Exception as exc:  # noqa: BLE001
            info = {"ok": False, "detail": f"확인 실패: {str(exc)[:120]}"}
        rows.append({"id": key, "name": name, "used_by": used_by, **info})
    with _LOCK:
        _CACHE.update(at=time.time(), rows=rows)
    return list(rows)


def need(*keys: str) -> None:
    """Raise a plain message when a tool this job needs isn't ready (checked fresh)."""
    if "claude" in keys:
        blocked, why = claude_blocked()
        if blocked:
            halt_claude_limit(why)
            raise RuntimeError(f"Claude CLI: {why}. 자동 진행·큐를 멈춰 두었어요")
    by_id = {r["id"]: r for r in status(refresh=True)}
    for key in keys:
        row = by_id.get(key) or {}
        if not row.get("ok"):
            raise RuntimeError(f"{row.get('name', key)}: {row.get('detail', '준비 안 됨')}. {row.get('fix', '')}".strip())


def claude_login() -> dict[str, Any]:
    _okadmin_import()
    from llm_claude import start_claude_login

    result = start_claude_login()
    with _LOCK:
        _CACHE["at"] = 0.0
    return result
