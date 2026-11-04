"""Starful HQ application package.

Layout (read this first):
  app/routes.py     — HTTP (Flask)
  app/sites.py      — sites.yaml registry
  app/office/       — virtual office, people, meetings
  app/work/         — hand-off, queue, agents, ship
  app/content/      — write / images per site type
  app/money/        — affiliate, GCP cost, HQ click usage
  app/snapshot.py   — saved site numbers (JSON)
  data/             — runtime files (not source)
"""

from __future__ import annotations

from app.paths import DATA, HQ_ROOT

__all__ = ["HQ_ROOT", "DATA", "create_app"]


def create_app():
    """Flask app used by `python -m app` and tests."""
    from app.routes import app

    return app
