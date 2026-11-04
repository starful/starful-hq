"""Pick a page's photo from several Google Places candidates (one image at a time).

Places sometimes returns the wrong shop / school. For one selected image this searches Places with the page's
name + address, shows a few candidate photos (from the best match and the other matching places), and
replaces the image with the one the owner picks. Thumbnails go through this server (the API key never reaches
the browser) and are cached in data/places_thumbs. Every Places call counts against 💰 the daily Places limit.

Works for: place sites (okramen shop photos <shop>.jpg) and krcampus school / university photos.
"""

from __future__ import annotations
from app.paths import HQ_ROOT

import hashlib
import json
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from app import sites
from app import snapshot
ROOT = HQ_ROOT  # starful-hq/ (data lives next to app/)
THUMBS = ROOT / "data" / "places_thumbs"
MAX_CANDIDATES = 3      # photos of the best-matching place only (each thumbnail is one Places call)
_REF = re.compile(r"^places/[A-Za-z0-9_-]+/photos/[A-Za-z0-9_-]+$")


def _env_key() -> str:
    """Places key: GCP Secret Manager first (like okadmin), then the site's own .env
    (okramen GOOGLE_PLACES_API_KEY, krcampus KRCAMPUS_GOOGLE_MAPS_API_KEY)."""
    from app import gsecrets
    names = ["GOOGLE_PLACES_API_KEY", "GOOGLE_MAPS_API_KEY"]
    if sites.current().content == "campus_guides":
        from app.content import jpcontent
        names.insert(0, jpcontent.kit().maps_key)
    for n in names:
        if gsecrets.get(n):
            return gsecrets.get(n)
    vals: dict[str, str] = {}
    try:
        for line in (sites.current().repo / ".env").read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip("'\"")
    except OSError:
        pass
    return next((vals[n] for n in names if vals.get(n)), "")


def _page_of(name: str) -> Path | None:
    stem = Path(name).stem
    root = Path(snapshot.POSTS_ROOT)
    for cand in (root / f"{stem}_en.md", root / f"{stem}.md", root / f"{stem}_ko.md"):
        if cand.is_file():
            return cand
    return None


def eligible(name: str) -> bool:
    stem = Path(name).stem
    if sites.current().content == "poi":
        from app.content import poicontent
        return stem in poicontent.existing_shops()
    if sites.current().content == "campus_guides":
        from app.content import jpcontent
        return jpcontent.has_places() and stem.startswith(jpcontent.PLACE_PREFIXES) and _page_of(name) is not None
    return False


def eligible_names(names: list[str]) -> set[str]:
    return {n for n in names if eligible(n)}


def _meta(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    m = re.match(r"---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    block = m.group(1)
    try:
        data = json.loads(block)   # campus: JSON front matter
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass
    out: dict[str, Any] = {}
    for line in block.splitlines():   # okramen: simple YAML lines
        if ":" in line and not line.startswith((" ", "-")):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip("'\"")
    return out


def query_for(name: str) -> str:
    page = _page_of(name)
    if page is None:
        raise RuntimeError("이 사진을 쓰는 페이지를 찾지 못했어요")
    meta = _meta(page)
    title = str(meta.get("shop_name") or meta.get("name") or meta.get("school_name") or meta.get("name_en") or "")
    if not title:
        title = re.split(r"\s[—|:-]\s", str(meta.get("title") or Path(name).stem.replace("_", " ")))[0]
    addr = str(meta.get("address") or meta.get("location") or meta.get("city") or "")
    return " ".join(x for x in (title, addr) if x).strip()


def _post_json(url: str, body: dict, key: str, fields: str) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": fields})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:   # Google says why (key, API not enabled, bad field …)
        try:
            msg = json.loads(exc.read().decode()).get("error", {}).get("message", "")
        except Exception:  # noqa: BLE001
            msg = ""
        if "expired" in msg.lower() or "invalid" in msg.lower():
            raise RuntimeError("Google Places 키가 만료됐거나 잘못됐어요 (Secret Manager의 키도 확인해 주세요)") from None
        raise RuntimeError(f"Places {exc.code}: {msg[:160]}") from None


def candidates(name: str, query: str = "") -> dict[str, Any]:
    """{query, cands: [{ref, place, address}]} — up to 3 photos of the best-matching place (the first result that has
    photos). Other places are left out: they were mostly the town or a neighbour, which is how duplicates crept in."""
    from app.money import costs
    key = _env_key()
    if not key:
        raise RuntimeError("이 사이트 .env에 Google Places 키가 없어요")
    q = (query or "").strip()[:200] or query_for(name)
    costs.take("places", 1)
    data = _post_json("https://places.googleapis.com/v1/places:searchText",
                      {"textQuery": q, "languageCode": "en", "regionCode": "KR" if sites.current().id == "krcampus" else "JP",
                       "maxResultCount": 5},
                      key, "places.displayName,places.formattedAddress,places.photos")
    out: list[dict[str, str]] = []
    places = data.get("places") or []
    for p in places:
        for ph in (p.get("photos") or [])[:MAX_CANDIDATES]:
            ref = str(ph.get("name") or "")
            if _REF.match(ref):
                out.append({"ref": ref, "place": (p.get("displayName") or {}).get("text", ""),
                            "address": str(p.get("formattedAddress") or "")})
        if out:
            break
    return {"query": q, "cands": out[:MAX_CANDIDATES]}


def _media(ref: str, width: int) -> bytes:
    key = _env_key()
    if not key or not _REF.match(ref or ""):
        raise RuntimeError("사진을 찾지 못했어요")
    url = f"https://places.googleapis.com/v1/{ref}/media?" + urllib.parse.urlencode({"maxWidthPx": width, "key": key})
    with urllib.request.urlopen(url, timeout=25) as r:
        if not str(r.headers.get("Content-Type", "")).startswith("image"):
            raise RuntimeError("사진을 받지 못했어요")
        return r.read()


def thumb(ref: str) -> bytes:
    """A small candidate photo (cached on disk; a new one counts as one Places call)."""
    from app.money import costs
    if not _REF.match(ref or ""):
        raise RuntimeError("잘못된 사진")
    path = THUMBS / (hashlib.sha1(ref.encode()).hexdigest() + ".jpg")
    if path.is_file():
        return path.read_bytes()
    costs.take("places", 1)
    raw = _media(ref, 400)
    THUMBS.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def apply(name: str, ref: str) -> str:
    """Replace the image with the picked Places photo (800px wide, the old file kept in image_backups)."""
    from app.money import costs
    from app.work import workers
    path = snapshot.safe_image(name)
    if path is None or not eligible(name):
        raise RuntimeError("이 이미지는 Places 사진으로 바꿀 수 없어요")
    costs.take("places", 1)
    raw = _media(ref, 800)
    workers.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, workers.BACKUP_DIR / path.name)
    path.write_bytes(raw)
    import app.work.workers as _w
    note = _w._gcs_push([path.name])
    return f"{name}을 고른 Google Places 사진으로 바꿨어요" + (f" · {note}" if note else "")
