"""Starful HQ paths. Code lives in app/; runtime data stays at the repo root."""

from __future__ import annotations

from pathlib import Path

# …/starful-hq/app/paths.py → …/starful-hq
HQ_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent
DATA = HQ_ROOT / "data"
