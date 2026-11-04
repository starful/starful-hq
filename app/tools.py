"""Are the AI / deploy tools ready? Same checks okadmin (8090) uses, nothing new.

- Claude CLI : okadmin llm_claude.claude_bin() + claude_cli_ready()  (`claude auth status`)
- gh         : okadmin github_ops.gh_available()                    (`gh auth status`)
- gcloud     : on PATH (deploy.sh needs it), active account
- Gemini key : okadmin merge_pipeline_env() view of okpy/.env + okadmin/.env (Imagen only)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from app import snapshot
_CACHE: dict[str, Any] = {"at": 0.0, "rows": []}
_LOCK = threading.Lock()
TTL = 120


def _okadmin_import() -> None:
    import sys

    path = str(snapshot.OKADMIN_ROOT)
    if path not in sys.path:
        sys.path.insert(0, path)


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
