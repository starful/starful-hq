"""안 쓰는 이미지 정리: image files no page, template, script or data file uses, in the site folder and its GCS copy.

"Used" is decided conservatively (a file is kept when in doubt):
  1. a page uses it: snapshot._image_posts() (names in posts + the place/campus naming rules, shop_en.md ↔ shop.jpg)
  2. its file name appears in any text file of the site repo (templates, css, js, json data, py, csv queues, sitemaps …)
     or of another site repo under WORK_ROOT (family sites linking each other's images)
  3. its name stem (6+ chars) appears in this site's text files — code that builds f"{slug}.jpg" still finds it
  4. favicons, logos, default / placeholder / og images are never touched
Deleted files: the local file (git keeps it, so a PR shows the deletions) and the same-named object in GCS (only that
exact object — not okadmin's sibling cleanup, which would also remove other extensions).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app import sites
from app import snapshot
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}
TEXT_EXT = {".html", ".htm", ".jinja", ".j2", ".css", ".scss", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".json", ".py", ".md",
            ".yaml", ".yml", ".xml", ".txt", ".csv", ".toml", ".webmanifest", ".svg", ".sh", ".cfg", ".ini"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache", "tmp", ".bak", "_to_delete", ".next", "dist", "build"}
PROTECT = re.compile(r"^(favicon|apple-touch|android-chrome|mstile|safari-pinned|logo|default|placeholder|no[-_]?image|og[-_]|ogp|icon|"
                     r"site[-_]|brand|hero|banner|share|card[-_]|social)", re.I)
MAX_TEXT = 8 * 1024 * 1024
_CORPUS: dict[str, str] = {}


def _corpus(repo: Path, skip: Path | None = None) -> str:
    key = f"{repo}|{skip}"
    if key in _CORPUS:
        return _CORPUS[key]
    parts: list[str] = []
    skip_r = skip.resolve() if skip else None
    for path in repo.rglob("*"):
        if path.suffix.lower() not in TEXT_EXT or not path.is_file():
            continue
        rel = path.relative_to(repo).parts
        if any(p in SKIP_DIRS for p in rel):
            continue
        if skip_r is not None and skip_r in path.resolve().parents:
            continue
        try:
            if path.stat().st_size > MAX_TEXT:
                continue
            parts.append(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    _CORPUS[key] = "\n".join(parts)
    return _CORPUS[key]


def _others(site_id: str) -> str:
    """Text of the other site repos (templates, code, data): images one site borrows from another."""
    out = []
    for s in sites.SITES.values():
        if s.id != site_id and s.repo.is_dir():
            out.append(_corpus(s.repo))
    return "\n".join(out)


def find_unused(site_id: str) -> dict[str, Any]:
    """{site, images, unused: [names], kept_by_text: n} for one site (read only)."""
    with sites.use(site_id):
        site = sites.current()
        root = Path(snapshot.IMAGES_ROOT)
        if not root.is_dir():
            return {"site": site_id, "images": 0, "unused": [], "kept_by_text": 0}
        files = sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT and not p.name.startswith("."))
        used = set(snapshot._image_posts())
        own = _corpus(site.repo, root)
        others = _others(site_id)
        unused, kept = [], 0
        for p in files:
            if p.name in used or PROTECT.match(p.name):
                continue
            if p.name in own or p.name in others or (len(p.stem) >= 6 and p.stem in own):
                kept += 1
                continue
            unused.append(p.name)
        return {"site": site_id, "images": len(files), "unused": unused, "kept_by_text": kept}


def _delete_gcs(names: list[str]) -> tuple[int, list[str]]:
    from app.content import gcsimage
    key = gcsimage.site_key()
    if not key or not names:
        return 0, []
    from config import gcs_sites

    cfg = gcs_sites()[key]
    bucket = gcsimage._client().bucket(cfg["bucket"])
    prefix = cfg.get("prefix") or ""
    gone, errors = 0, []
    for name in names:
        try:
            blob = bucket.blob(f"{prefix}{name}")
            if blob.exists():
                blob.delete()
                gone += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {str(exc)[:100]}")
    return gone, errors


def clean(work) -> str:
    """Job (current site): delete unused images locally and in GCS. A backup copy goes to data/image_backups/cleanup."""
    import shutil

    from app.work import workers
    site_id = sites.current_id()
    _CORPUS.clear()   # read every file fresh: a page written since the last run must keep its image
    work.say("안 쓰는 이미지를 찾는 중 (글·템플릿·데이터·다른 사이트까지)")
    found = find_unused(site_id)
    names = found["unused"]
    if not names:
        return f"안 쓰는 이미지가 없어요 (이미지 {found['images']}개 모두 쓰여요)"
    root = Path(snapshot.IMAGES_ROOT)
    keep = workers.BACKUP_DIR / "cleanup" / site_id
    keep.mkdir(parents=True, exist_ok=True)
    work.say(f"안 쓰는 이미지 {len(names)}개 지우는 중")
    gone_gcs, errors = _delete_gcs(names)
    for name in names:
        path = root / name
        if path.is_file():
            shutil.copy2(path, keep / name)
            path.unlink()
    snapshot.refresh_snapshot()
    msg = f"안 쓰는 이미지 {len(names)}개를 지웠어요" + (f" · GCS에서도 {gone_gcs}개" if gone_gcs else "")
    if errors:
        msg += f" · GCS {len(errors)}개는 못 지웠어요 ({errors[0]})"
    return msg + " · PR로 올리면 사이트에서도 빠져요"
