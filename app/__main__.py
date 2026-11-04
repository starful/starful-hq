"""python -m app  →  start Starful HQ on PORT (default 8091)."""

from __future__ import annotations

import os

from app.routes import app

PORT = int(os.environ.get("PORT", "8091"))

if __name__ == "__main__":
    # use_reloader: .py edits restart the server (a running job is cut off when that happens).
    app.run(host="127.0.0.1", port=PORT, threaded=True, use_reloader=True, reloader_type="stat")
