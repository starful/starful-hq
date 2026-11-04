"""Replaced images go to the site's Google Cloud Storage folder, the same way okadmin (8090) does it.

Sites with a `gcs:` block in sites.yaml (okonsen, okramen, okcaddie, okpy, jpcampus, krcampus …) keep their images in
gs://<bucket>/<prefix>. okadmin uploads there whenever an image changes (image_site_content._upload_payload_to_gcs:
Cache-Control no-cache, then bump_site_thumbnail_cache moves the page's date so the site's ?v= changes). Starful HQ
used to change only app/static/images/, so okonsen (which loads its photos from GCS) never showed the new photo.

push(names): after any image change here. sync(work): every image a page uses whose GCS copy differs from the local
file (catches up on photos replaced before this existed). Uses okadmin's code and the Mac's Google login.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

from app import sites
from app import snapshot
_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}


def _okadmin() -> None:
    from app.work import workers
    workers._okadmin_import()


def site_key() -> str:
    """okadmin's image key of the current site when it has GCS storage, else ''."""
    try:
        _okadmin()
        from config import gcs_sites, image_site_key

        key = image_site_key(sites.current().oid)
        return key if key in gcs_sites() else ""
    except Exception:  # noqa: BLE001 - okadmin not importable: behave like a site without GCS
        return ""


def enabled() -> bool:
    return bool(site_key())


def _client():
    from google.cloud import storage

    return storage.Client()


def push(names: list[str]) -> str:
    """Upload these images (okadmin's way). A short note for the owner; '' when the site has no GCS storage."""
    key = site_key()
    if not key:
        return ""
    from image_site_content import _upload_payload_to_gcs

    client = _client()
    ok, errors = 0, []
    for name in names:
        path = snapshot.safe_image(Path(name).name)
        if path is None:
            continue
        res = _upload_payload_to_gcs(key, path.stem, path.read_bytes(), _TYPES.get(path.suffix.lower(), "image/jpeg"),
                                     path.name, client)
        if res.get("ok"):
            ok += 1
        else:
            errors.append(f"{path.name}: {str(res.get('error') or '')[:120]}")
    if errors:
        return f"사이트 사진 저장소(GCS)에 {len(errors)}장 못 올렸어요 ({errors[0]})" + (f" · {ok}장은 올렸어요" if ok else "")
    return f"사이트 사진 저장소(GCS)에도 올렸어요{f' ({ok}장)' if ok > 1 else ''}" if ok else ""


def _md5(path: Path) -> str:
    return base64.b64encode(hashlib.md5(path.read_bytes()).digest()).decode()


def changed() -> list[str]:
    """Images some page uses whose GCS copy is missing or different from the local file."""
    key = site_key()
    if not key:
        return []
    from config import gcs_sites

    cfg = gcs_sites()[key]
    prefix = cfg.get("prefix") or ""
    remote = {b.name[len(prefix):]: b.md5_hash for b in _client().list_blobs(cfg["bucket"], prefix=prefix)}
    used = snapshot._image_posts()
    out = []
    root = Path(snapshot.IMAGES_ROOT)
    for path in sorted(root.iterdir()) if root.is_dir() else []:
        if path.is_file() and path.name in used and path.suffix.lower() in _TYPES and remote.get(path.name) != _md5(path):
            out.append(path.name)
    return out


def sync(work) -> str:
    """Job: send every changed photo to GCS (the ones replaced here before uploads existed, too)."""
    if not enabled():
        raise RuntimeError("이 사이트는 GCS 사진 저장소를 쓰지 않아요")
    work.say("사이트 저장소와 다른 사진을 찾는 중")
    names = changed()
    if not names:
        return "사이트 저장소와 다른 사진이 없어요 (모두 반영돼 있어요)"
    work.say(f"사진 {len(names)}장 올리는 중")
    note = push(names)
    snapshot.refresh_snapshot()
    return f"바뀐 사진 {len(names)}장 · {note}"
