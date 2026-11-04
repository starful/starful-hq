"""API keys from GCP Secret Manager — the same source okadmin uses (okadmin/scripts/fetch_secrets.sh).

get("GOOGLE_PLACES_API_KEY") → the latest version of that secret in project starful-258005, read with the Mac's
`gcloud` login and kept in memory for an hour. When gcloud can't be reached it falls back to okadmin/.env (which
fetch_secrets.sh wrote from the same secrets). Site .env files can hold old keys; these win over them:
workers._okpy_env() passes them to every site script (python-dotenv never overrides a variable already set).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

PROJECT = os.environ.get("GCP_PROJECT_ID", "starful-258005")
TTL = 3600
# secrets site scripts need (Places / Maps photos and lookups).
# Campus kits may also ask for their maps_key by name via get(); keep the common ones here for script_env().
SCRIPT_KEYS = (
    "GOOGLE_PLACES_API_KEY",
    "KRCAMPUS_GOOGLE_MAPS_API_KEY",
    "JPCAMPUS_GOOGLE_MAPS_API_KEY",
    "JPFUN_GOOGLE_MAPS_API_KEY",
    "OKRAMEN_GOOGLE_MAPS_API_KEY",
    "OKONSEN_GOOGLE_MAPS_API_KEY",
)
_CACHE: dict[str, tuple[float, str, str]] = {}  # name → (at, value, source)
_LOCK = threading.Lock()


def _gcloud() -> str:
    for cand in (shutil.which("gcloud"), "/opt/homebrew/bin/gcloud", "/usr/local/bin/gcloud",
                 str(Path.home() / "google-cloud-sdk" / "bin" / "gcloud"), "/opt/homebrew/share/google-cloud-sdk/bin/gcloud"):
        if cand and Path(cand).is_file():
            return cand
    return ""


def _okadmin_env(name: str) -> str:
    from app import snapshot
    try:
        for line in (Path(snapshot.OKADMIN_ROOT) / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


def _from_gcloud(name: str) -> str:
    gc = _gcloud()
    if not gc:
        return ""
    try:
        r = subprocess.run(
            [gc, "secrets", "versions", "access", "latest", f"--secret={name}", f"--project={PROJECT}"],
            capture_output=True, text=True, timeout=30, check=False,
            env={**os.environ, "CLOUDSDK_CORE_DISABLE_PROMPTS": "1"},
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def get(name: str) -> str:
    """Latest secret value ('' when there is none anywhere)."""
    with _LOCK:
        hit = _CACHE.get(name)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
    value, src = "", ""
    sm = _from_gcloud(name)
    if sm:
        value, src = sm, "Secret Manager"
    else:
        env = _okadmin_env(name)
        if env:
            value, src = env, "okadmin .env"
    with _LOCK:
        _CACHE[name] = (time.time(), value, src)
    return value


def source(name: str) -> str:
    """Where a key came from, for messages: 'Secret Manager' / 'okadmin .env' / ''."""
    with _LOCK:
        hit = _CACHE.get(name)
        if hit and time.time() - hit[0] < TTL:
            return hit[2] if hit[1] else ""
    get(name)
    with _LOCK:
        hit = _CACHE.get(name)
        return (hit[2] if hit and hit[1] else "")


def script_env() -> dict[str, str]:
    out = {k: v for k in SCRIPT_KEYS if (v := get(k))}
    # current campus kit may name a maps key not in SCRIPT_KEYS (e.g. GOOGLE_MAPS_API_KEY)
    try:
        from app import sites
        if sites.current().content == "campus_guides":
            from app.content import jpcontent
            mk = jpcontent.kit().maps_key
            if mk and mk not in out and (v := get(mk)):
                out[mk] = v
    except Exception:  # noqa: BLE001 - script_env must never break workers
        pass
    return out


def forget() -> None:
    with _LOCK:
        _CACHE.clear()
