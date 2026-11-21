"""사장 Taekki: login, presence on the office map, and where she stands.

- On this Mac (127.0.0.1 / localhost) 「사장 로그인」 is a one-click local session
  (email from data/boss.json). No Google redirect needed.
- Optional Google OAuth: set HQ_BOSS_OAUTH=1 or open /login?oauth=1. Reuses okadmin's
  GOOGLE_OAUTH_CLIENT_ID / _SECRET. redirect_uri is always
  http://127.0.0.1:<PORT>/oauth/callback — add that URI in Google Cloud Console
  (okadmin only registered :8090).
- Without login starful-hq works as before. Login does not block any feature.
  While present, idle staff sit at their desks (see office.rest_plan).
"""

from __future__ import annotations
from app.paths import HQ_ROOT

import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from app import office
from app import snapshot
ROOT = HQ_ROOT  # starful-hq/ (data lives next to app/)
CONFIG_PATH = ROOT / "data" / "boss.json"
STATE_PATH = ROOT / "data" / "boss_state.json"
SECRET_PATH = ROOT / "data" / ".flask_secret"
DEFAULT_EMAILS = ["starful0418@gmail.com"]
NAME = "Taekki"
PRESENT_SEC = 90                       # shown while the boss page polled within this many seconds
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
_LOCK = threading.Lock()


def _okadmin_env() -> dict[str, str]:
    path = snapshot.WORK_ROOT / "okadmin" / ".env"
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def oauth_client() -> tuple[str, str]:
    env = _okadmin_env()
    cid = os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or env.get("GOOGLE_OAUTH_CLIENT_ID", "")
    sec = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET") or env.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    return cid, sec


def secret_key() -> str:
    """okadmin's FLASK_SECRET_KEY when there is one, else a key kept in data/."""
    key = os.environ.get("FLASK_SECRET_KEY") or _okadmin_env().get("FLASK_SECRET_KEY", "")
    if key:
        return key + ":starful-hq"
    try:
        return SECRET_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        key = secrets.token_hex(32)
        SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        SECRET_PATH.write_text(key, encoding="utf-8")
        return key


def config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    data = {"emails": DEFAULT_EMAILS, "name": NAME}
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def is_boss(email: str | None) -> bool:
    emails = {str(e).strip().lower() for e in config().get("emails") or []}
    return bool(email) and email.strip().lower() in emails


def default_boss_email() -> str:
    emails = [str(e).strip().lower() for e in (config().get("emails") or DEFAULT_EMAILS) if str(e).strip()]
    return emails[0] if emails else ""


def callback_uri(port: int | None = None) -> str:
    """Stable redirect URI — always 127.0.0.1 so it matches Google Console registration."""
    p = int(port if port is not None else os.environ.get("PORT", "8091"))
    return f"http://127.0.0.1:{p}/oauth/callback"


def auth_url(redirect_uri: str, state: str) -> str:
    cid, _ = oauth_client()
    q = {"client_id": cid, "redirect_uri": redirect_uri, "response_type": "code", "scope": "openid email profile",
         "state": state, "prompt": "select_account"}
    return AUTH_URL + "?" + urllib.parse.urlencode(q)


def email_from_code(code: str, redirect_uri: str) -> str:
    cid, sec = oauth_client()
    body = urllib.parse.urlencode({"code": code, "client_id": cid, "client_secret": sec,
                                   "redirect_uri": redirect_uri, "grant_type": "authorization_code"}).encode()
    with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body), timeout=20) as res:
        token = json.loads(res.read().decode()).get("access_token") or ""
    if not token:
        return ""
    req = urllib.request.Request(USERINFO_URL, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as res:
        return str(json.loads(res.read().decode()).get("email") or "").lower()


# ---------- presence + position on the map ----------

def _read() -> dict[str, Any]:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(data: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def heartbeat() -> None:
    with _LOCK:
        data = _read()
        data["seen"] = time.time()
        _write(data)


def leave() -> None:
    with _LOCK:
        data = _read()
        data["seen"] = 0
        data.pop("pos", None)
        _write(data)


def move(x: float, y: float, forced: bool = False) -> dict[str, Any]:
    with _LOCK:
        data = _read()
        data["pos"] = {"x": round(max(0.0, min(100.0, x)), 2), "y": round(max(0.0, min(100.0, y)), 2)}
        if forced:  # the server moved her (e.g. to the head of the table): every page follows
            data["forced"] = time.time()
        _write(data)
        return data


def state(me: bool = False) -> dict[str, Any]:
    data = _read()
    pos = data.get("pos") or dict(office.BOSS_DESK)
    present = me or (time.time() - float(data.get("seen") or 0)) < PRESENT_SEC
    at_desk = abs(pos["x"] - office.BOSS_DESK["x"]) < 0.6 and abs(pos["y"] - office.BOSS_DESK["y"]) < 0.6
    return {"present": present, "me": me, "name": config().get("name") or NAME, "pos": pos, "at_desk": at_desk,
            "forced": float(data.get("forced") or 0)}
