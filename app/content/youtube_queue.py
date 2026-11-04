"""YouTube attach queue — list places, cache candidates, write youtube_id.

Enabled per site via sites.yaml hq.youtube.enabled (design team). Add a new site by filling
hq.youtube (noun · page_prefix · query) — no code change required for the common POI pattern.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from app.paths import DATA
from app import sites
from app.content.youtube_api import search_videos
from app.money import costs

CACHE_DIR = DATA / "youtube"
GSC_SHOP_DAYS = 28
JST = timezone(timedelta(hours=9))

_AREA_TOKENS = ("Shinjuku", "Shibuya", "Ikebukuro", "Osaka", "Kyoto", "Tokyo", "신주쿠", "시부야")

_lock = threading.Lock()
_gsc_lock = threading.Lock()
_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
_YT_FM_RE = re.compile(r"^youtube_id\s*:\s*['\"]?([A-Za-z0-9_-]*)['\"]?\s*$", re.M)
_gsc_mem_by_site: dict[str, dict[str, Any]] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _jst_today() -> str:
    return datetime.now(JST).date().isoformat()


def youtube_site_ids() -> frozenset[str]:
    return frozenset(s.id for s in sites.SITES.values() if s.youtube_on)


def normalize_site_id(site_id: str | None) -> str:
    sid = (site_id or sites.current_id() or "").strip().lower()
    if sid in sites.SITES and sites.SITES[sid].youtube_on:
        return sid
    # Prefer current site if it supports youtube; else first enabled youtube site
    cur = sites.current_id()
    if cur in sites.SITES and sites.SITES[cur].youtube_on:
        return cur
    ids = sorted(youtube_site_ids())
    return ids[0] if ids else sid


def _cfg(site_id: str | None) -> dict[str, Any]:
    sid = normalize_site_id(site_id)
    site = sites.SITES.get(sid)
    return dict(site.youtube) if site else {}


def site_label(site_id: str | None) -> str:
    sid = normalize_site_id(site_id)
    site = sites.SITES.get(sid)
    return site.label if site else sid


def site_noun(site_id: str | None) -> str:
    cfg = _cfg(site_id)
    if cfg.get("noun"):
        return str(cfg["noun"])
    sid = normalize_site_id(site_id)
    site = sites.SITES.get(sid)
    if site:
        return site._poi_noun()
    return "장소"


def site_page_prefix(site_id: str | None) -> str:
    cfg = _cfg(site_id)
    if cfg.get("page_prefix"):
        return str(cfg["page_prefix"]).strip().lower()
    return "item"


def public_page_url(site_id: str | None, base_id: str) -> str:
    """Live site URL for a place (EN). course uses bare id; others use {base}_en."""
    sid = normalize_site_id(site_id)
    site = sites.SITES.get(sid)
    if not site or not base_id:
        return ""
    origin = f"https://{(site.domain or '').strip()}".rstrip("/")
    if not origin or origin == "https://":
        return ""
    prefix = site_page_prefix(sid)
    if prefix == "course":
        return f"{origin}/{prefix}/{base_id}"
    return f"{origin}/{prefix}/{base_id}_en"


def youtube_watch_url(video_id: str) -> str:
    vid = (video_id or "").strip()
    return f"https://www.youtube.com/watch?v={vid}" if vid else ""


def youtube_thumb_url(video_id: str) -> str:
    vid = (video_id or "").strip()
    return f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg" if vid else ""


def content_dir_for(site_id: str | None) -> Path:
    sid = normalize_site_id(site_id)
    site = sites.SITES[sid]
    return site.repo / site.posts_rel


def _repo_root(site_id: str | None) -> Path:
    return sites.SITES[normalize_site_id(site_id)].repo


def cache_path_for(site_id: str | None) -> Path:
    return CACHE_DIR / f"{normalize_site_id(site_id)}_candidates.json"


def item_base_id_from_page(page: str, page_prefix: str = "ramen") -> str | None:
    """Map a GSC page URL to a place base_id (EN/KO detail only)."""
    raw = (page or "").strip()
    if not raw:
        return None
    path = urlparse(raw).path if "://" in raw else raw.split("?", 1)[0]
    path = path.rstrip("/")
    parts = [p for p in path.split("/") if p]
    prefix = (page_prefix or "ramen").strip().lower()
    if len(parts) < 2 or parts[0].lower() != prefix:
        return None
    slug = parts[1]
    if slug.endswith("_en") or slug.endswith("_ko"):
        return slug.rsplit("_", 1)[0]
    if prefix == "course":
        return slug
    return None


def aggregate_shop_gsc(
    page_rows: list[dict[str, Any]],
    *,
    page_prefix: str = "ramen",
) -> dict[str, dict[str, Any]]:
    """Sum EN+KO detail-page metrics into one row per place."""
    acc: dict[str, dict[str, Any]] = {}
    for row in page_rows or []:
        base = item_base_id_from_page(str(row.get("page") or ""), page_prefix)
        if not base:
            continue
        clicks = int(row.get("clicks") or 0)
        impressions = int(row.get("impressions") or 0)
        position = float(row.get("position") or 0)
        slot = acc.setdefault(base, {"clicks": 0, "impressions": 0, "_pos_weight": 0.0})
        slot["clicks"] += clicks
        slot["impressions"] += impressions
        slot["_pos_weight"] += position * impressions
    out: dict[str, dict[str, Any]] = {}
    for base, slot in acc.items():
        imp = int(slot["impressions"])
        clicks = int(slot["clicks"])
        pos = (float(slot["_pos_weight"]) / imp) if imp else 0.0
        out[base] = {
            "clicks": clicks,
            "impressions": imp,
            "position": round(pos, 1),
            "ctr": (clicks / imp) if imp else 0.0,
        }
    return out


def _gsc_cache_path(site_id: str, days: int, day: str) -> Path:
    return CACHE_DIR / f"{normalize_site_id(site_id)}_gsc_shops_d{days}_{day}.json"


def _read_gsc_cache_file(site_id: str, days: int, day: str) -> dict[str, Any] | None:
    path = _gsc_cache_path(site_id, days, day)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("cache_day") != day:
        return None
    by_base = data.get("by_base")
    if not isinstance(by_base, dict):
        return None
    return data


def load_shop_gsc(
    *,
    site_id: str | None = None,
    days: int = GSC_SHOP_DAYS,
    force: bool = False,
) -> dict[str, Any]:
    """One GSC pages query per site per JST day (file + memory). Never per-place."""
    sid = normalize_site_id(site_id)
    day = _jst_today()
    mem_key = f"{sid}:{days}:{day}"
    with _gsc_lock:
        if not force and mem_key in _gsc_mem_by_site:
            hit = dict(_gsc_mem_by_site[mem_key])
            hit["cache_hit"] = True
            return hit
        if not force:
            cached = _read_gsc_cache_file(sid, days, day)
            if cached is not None:
                _gsc_mem_by_site[mem_key] = cached
                hit = dict(cached)
                hit["cache_hit"] = True
                return hit

        from analytics_api import fetch_gsc_pages, site_analytics_config

        site = sites.SITES.get(sid)
        oid = site.oid if site else sid
        ac = site_analytics_config(oid)
        gsc_url = (ac.get("gsc_site_url") or "").strip()
        if not gsc_url:
            result = {
                "cache_day": day,
                "days": days,
                "site_id": sid,
                "by_base": {},
                "error": "gsc_site_url 미설정",
                "cache_hit": False,
            }
            _gsc_mem_by_site[mem_key] = result
            return result

        raw = fetch_gsc_pages(gsc_url, days=days) or {}
        if raw.get("error"):
            result = {
                "cache_day": day,
                "days": days,
                "site_id": sid,
                "by_base": {},
                "error": str(raw.get("error") or "GSC 조회 실패"),
                "cache_hit": False,
            }
            _gsc_mem_by_site[mem_key] = result
            return result

        by_base = aggregate_shop_gsc(list(raw.get("rows") or []), page_prefix=site_page_prefix(sid))
        result = {
            "cache_day": day,
            "days": days,
            "site_id": sid,
            "start": raw.get("start"),
            "end": raw.get("end"),
            "page_rows": len(raw.get("rows") or []),
            "shop_count": len(by_base),
            "by_base": by_base,
            "fetched_at": _utc_now(),
            "cache_hit": False,
        }
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        store = dict(result)
        store.pop("cache_hit", None)
        _gsc_cache_path(sid, days, day).write_text(
            json.dumps(store, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _gsc_mem_by_site[mem_key] = result
        return result


def apply_shop_gsc(shops: list[dict[str, Any]], gsc: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Attach EN+KO GSC totals and sort clicks desc, then impressions."""
    by_base = (gsc or {}).get("by_base") if isinstance(gsc, dict) else {}
    if not isinstance(by_base, dict):
        by_base = {}
    out: list[dict[str, Any]] = []
    for shop in shops:
        row = dict(shop)
        metrics = by_base.get(row.get("base_id")) or {}
        if not isinstance(metrics, dict):
            metrics = {}
        row["clicks"] = int(metrics.get("clicks") or 0)
        row["impressions"] = int(metrics.get("impressions") or 0)
        row["position"] = float(metrics.get("position") or 0)
        row["ctr"] = float(metrics.get("ctr") or 0)
        out.append(row)
    out.sort(
        key=lambda s: (
            -int(s.get("clicks") or 0),
            -int(s.get("impressions") or 0),
            (s.get("shop_name") or "").lower(),
        )
    )
    return out


def _base_id(stem: str) -> str:
    if stem.endswith("_en") or stem.endswith("_ko"):
        return stem.rsplit("_", 1)[0]
    return stem


def _parse_meta(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        return {}
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}
    return meta if isinstance(meta, dict) else {}


_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
_GUIDE_TO_RE = re.compile(
    r"^(?:The\s+)?(?:Definitive\s+)?Guide\s+to\s+",
    re.IGNORECASE,
)
_EN_FLUFF_RE = re.compile(
    r"\s*(?:[:：].*)?(?:\s*\|\s*.*)?(?:\s*\((?:en|ko|ja)\))?\s*$",
    re.IGNORECASE,
)
_JA_FLUFF_RE = re.compile(
    r"(?:\s*[|｜].*)|(?:\s*(?:予約|料金|アクセス|グリーンフィー|レビュー|徹底ガイド|ガイド).*)\s*$"
)


def _humanize_base(base: str) -> str:
    return " ".join(p.capitalize() for p in (base or "").replace("-", "_").split("_") if p)


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def _clean_ja_label(text: str) -> str:
    n = (text or "").strip()
    if not n:
        return ""
    n = re.split(r"[|｜]", n, maxsplit=1)[0].strip()
    n = _JA_FLUFF_RE.sub("", n).strip()
    n = n.rstrip("：:・ ").strip()
    return n


def _clean_en_label(text: str) -> str:
    n = (text or "").strip()
    if not n:
        return ""
    n = n.replace(" - JPFun", "").replace("| OKOnsen", "").replace("| OKOnsen", "")
    for sep in (" - JPFun", " | OKOnsen", " | OKRamen"):
        if sep in n:
            n = n.split(sep, 1)[0].strip()
    m = _GUIDE_TO_RE.match(n)
    if m:
        n = n[m.end() :].strip()
    # "X Review: …" / "Oceanfront …: AMANE Resort Seikai"
    if ":" in n or "：" in n:
        left, right = re.split(r"[:：]", n, maxsplit=1)
        left, right = left.strip(), right.strip()
        if re.search(r"\bReview\b", left, re.I) and right:
            n = left
            n = re.sub(r"\s*Review\s*$", "", n, flags=re.I).strip()
        elif len(right) >= 4 and len(right) <= 48 and not right.lower().startswith("an "):
            n = right
        elif len(left) <= 64:
            n = left
    n = re.sub(r"\s*\((?:en|ko|ja)\)\s*$", "", n, flags=re.I).strip()
    n = re.sub(r"\s*\|\s*.*$", "", n).strip()
    return n


def _ja_place_label(content_dir: Path, base: str) -> str:
    """Prefer Japanese seo_title / title from *_ja.md when present (better YouTube hits)."""
    for lang in ("ja", "jp"):
        path = content_dir / f"{base}_{lang}.md"
        if not path.is_file():
            continue
        meta = _parse_meta(path)
        for key in ("shop_name", "shop", "name", "seo_title", "title"):
            raw = str(meta.get(key) or "").strip()
            if not raw or not _has_cjk(raw):
                continue
            cleaned = _clean_ja_label(raw)
            if cleaned and _has_cjk(cleaned):
                return cleaned
    return ""


def _place_name(meta: dict[str, Any], base: str) -> str:
    for key in ("shop_name", "shop", "name", "name_en"):
        val = str(meta.get(key) or "").strip()
        if val:
            return val
    title = _clean_en_label(str(meta.get("title") or ""))
    if title:
        return title
    seo = _clean_en_label(str(meta.get("seo_title") or ""))
    if seo:
        return seo
    return _humanize_base(base)


def _query_name(name: str, base: str) -> str:
    n = _clean_en_label(name) if not _has_cjk(name) else _clean_ja_label(name)
    if _has_cjk(n):
        return n
    n = _clean_en_label(n)
    # Drop leftover guide fluff that hurts YouTube search.
    n = re.sub(
        r"^(?:An?\s+)?(?:Expert\s+)?(?:Review|Guide)\s+(?:of|to)\s+",
        "",
        n,
        flags=re.I,
    ).strip()
    if len(n) > 56:
        n = n[:56].rsplit(" ", 1)[0].strip() or n[:56]
    return n or _humanize_base(base)


def list_shops(
    *,
    site_id: str | None = None,
    filter_mode: str = "missing",
    missing_only: bool | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List EN places (one row per base_id). Skips guides/ (non-recursive glob).

    filter_mode: ``missing`` (no youtube_id), ``attached`` (has youtube_id), ``all``.
    """
    sid = normalize_site_id(site_id)
    mode = (filter_mode or "missing").strip().lower()
    if missing_only is not None:
        mode = "missing" if missing_only else "all"
    if mode not in ("missing", "attached", "all"):
        mode = "missing"

    content = content_dir_for(sid)
    repo = _repo_root(sid)
    rows: list[dict[str, Any]] = []
    for path in sorted(content.glob("*_en.md")):
        meta = _parse_meta(path)
        stem = path.stem
        base = _base_id(stem)
        yid = str(meta.get("youtube_id") or "").strip()
        if mode == "missing" and yid:
            continue
        if mode == "attached" and not yid:
            continue
        shop = _place_name(meta, base)
        activity = str(meta.get("activity") or "").strip().lower()
        if activity == "scuba":
            activity = "dive"
        ja = _ja_place_label(content, base)
        qname = _query_name(ja or shop, base)
        ko_path = content / f"{base}_ko.md"
        try:
            en_rel = str(path.relative_to(repo))
        except ValueError:
            en_rel = path.name
        try:
            ko_rel = str(ko_path.relative_to(repo)) if ko_path.is_file() else None
        except ValueError:
            ko_rel = ko_path.name if ko_path.is_file() else None
        address = str(meta.get("address") or "").strip()
        rows.append(
            {
                "base_id": base,
                "en_id": stem,
                "site_id": sid,
                "shop_name": shop,
                "address": address,
                "activity": activity,
                "query": default_query(qname, address, site_id=sid, activity=activity),
                "youtube_id": yid,
                "has_youtube": bool(yid),
                "youtube_url": youtube_watch_url(yid),
                "youtube_thumb": youtube_thumb_url(yid),
                "page_url": public_page_url(sid, base),
                "lang_files": {
                    "en": en_rel,
                    "ko": ko_rel,
                },
            }
        )
        if limit and len(rows) >= limit:
            break
    return rows


def default_query(
    shop_name: str,
    address: str = "",
    *,
    site_id: str | None = None,
    activity: str = "",
) -> str:
    name = (shop_name or "").strip()
    sid = normalize_site_id(site_id)
    q = (_cfg(sid).get("query") or {}) if isinstance(_cfg(sid).get("query"), dict) else {}
    activity_map = q.get("activity") if isinstance(q.get("activity"), dict) else {}
    if activity_map:
        token = activity_map.get((activity or "").strip().lower()) or str(q.get("fallback") or "レビュー")
        return f"{name} {token}".strip()
    suffix = str(q.get("suffix") or "").strip()
    if q.get("with_area"):
        area = ""
        addr = (address or "").strip()
        for token in _AREA_TOKENS:
            if token.lower() in addr.lower() or token in addr:
                area = token
                break
        if area and suffix:
            return f"{name} {area} {suffix}".strip()
        if area:
            return f"{name} {area}".strip()
    if suffix:
        return f"{name} {suffix}".strip()
    return name


def _load_cache(site_id: str | None = None) -> dict[str, Any]:
    sid = normalize_site_id(site_id)
    path = cache_path_for(sid)
    if not path.is_file():
        return {"site_id": sid, "shops": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"site_id": sid, "shops": {}}
    if not isinstance(data, dict):
        return {"site_id": sid, "shops": {}}
    data.setdefault("site_id", sid)
    data.setdefault("shops", {})
    return data


def _save_cache(data: dict[str, Any], site_id: str | None = None) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path_for(site_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def get_cached_candidates(base_id: str, *, site_id: str | None = None) -> dict[str, Any] | None:
    with _lock:
        shops = _load_cache(site_id).get("shops") or {}
        entry = shops.get(base_id)
        return dict(entry) if isinstance(entry, dict) else None


def fetch_candidates(
    base_id: str,
    *,
    site_id: str | None = None,
    query: str | None = None,
    max_results: int = 5,
) -> dict[str, Any]:
    """Search YouTube for a place and cache top candidates by views."""
    sid = normalize_site_id(site_id)
    content = content_dir_for(sid)
    en_path = content / f"{base_id}_en.md"
    if not en_path.is_file():
        raise FileNotFoundError(f"missing {en_path.name}")
    meta = _parse_meta(en_path)
    shop = _place_name(meta, base_id)
    address = str(meta.get("address") or "").strip()
    activity = str(meta.get("activity") or "").strip().lower()
    if activity == "scuba":
        activity = "dive"
    ja = _ja_place_label(content, base_id)
    q_default = default_query(
        _query_name(ja or shop, base_id), address, site_id=sid, activity=activity
    )
    q = (query or "").strip() or q_default
    top_n = max(3, min(int(max_results or 5), 5))
    costs.take("youtube")  # one search action (retries below share this slot)
    candidates = search_videos(q, max_results=top_n)
    # Empty with a long EN guide title → retry Japanese / humanized base once.
    if not candidates and (query or "").strip() in ("", q_default):
        for alt in (ja, _humanize_base(base_id)):
            alt_q = default_query(
                _query_name(alt, base_id), address, site_id=sid, activity=activity
            ) if alt else ""
            if not alt_q or alt_q == q:
                continue
            candidates = search_videos(alt_q, max_results=top_n)
            if candidates:
                q = alt_q
                break
    entry = {
        "base_id": base_id,
        "site_id": sid,
        "shop_name": shop,
        "address": address,
        "activity": activity,
        "query": q,
        "fetched_at": _utc_now(),
        "candidates": candidates,
        "attached_video_id": str(meta.get("youtube_id") or "").strip() or None,
    }
    with _lock:
        data = _load_cache(sid)
        data["shops"][base_id] = entry
        data["updated_at"] = _utc_now()
        _save_cache(data, sid)
    return entry


def _set_youtube_id_in_text(text: str, video_id: str) -> str:
    """Insert or replace youtube_id in YAML frontmatter without reformatting body."""
    if not text.startswith("---"):
        raise ValueError("missing frontmatter")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("unclosed frontmatter")
    fm = parts[1]
    body = parts[2]
    lines = fm.splitlines(True)
    out_lines: list[str] = []
    replaced = False
    for line in lines:
        if re.match(r"^youtube_id\s*:", line):
            out_lines.append(f"youtube_id: {video_id}\n")
            replaced = True
        else:
            out_lines.append(line if line.endswith("\n") or not line else line + "\n")
    if not replaced:
        out_lines.insert(0, f"youtube_id: {video_id}\n")
    new_fm = "".join(out_lines).lstrip("\n")
    if not new_fm.endswith("\n"):
        new_fm += "\n"
    return f"---\n{new_fm}---{body}"


def attach_video(base_id: str, video_id: str, *, site_id: str | None = None) -> dict[str, Any]:
    """Write youtube_id to EN + KO markdown pair."""
    sid = normalize_site_id(site_id)
    vid = (video_id or "").strip()
    if not _YT_ID_RE.match(vid):
        raise ValueError("invalid youtube video id")

    content = content_dir_for(sid)
    updated: list[str] = []
    for lang in ("en", "ko"):
        path = content / f"{base_id}_{lang}.md"
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        path.write_text(_set_youtube_id_in_text(text, vid), encoding="utf-8")
        updated.append(path.name)

    if not updated:
        raise FileNotFoundError(f"no markdown for {base_id}")

    with _lock:
        data = _load_cache(sid)
        entry = data.get("shops", {}).get(base_id) or {"base_id": base_id, "candidates": []}
        entry["attached_video_id"] = vid
        entry["attached_at"] = _utc_now()
        data.setdefault("shops", {})[base_id] = entry
        data["updated_at"] = _utc_now()
        _save_cache(data, sid)

    return {
        "base_id": base_id,
        "site_id": sid,
        "youtube_id": vid,
        "updated_files": updated,
        "url": f"https://www.youtube.com/watch?v={vid}",
    }


def clear_video(base_id: str, *, site_id: str | None = None) -> dict[str, Any]:
    """Remove youtube_id from EN + KO markdown pair."""
    sid = normalize_site_id(site_id)
    content = content_dir_for(sid)
    updated: list[str] = []
    for lang in ("en", "ko"):
        path = content / f"{base_id}_{lang}.md"
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue
        fm = parts[1]
        body = parts[2]
        lines = [ln for ln in fm.splitlines(True) if not re.match(r"^youtube_id\s*:", ln)]
        new_fm = "".join(lines)
        if not new_fm.endswith("\n"):
            new_fm += "\n"
        path.write_text(f"---\n{new_fm}---{body}", encoding="utf-8")
        updated.append(path.name)

    with _lock:
        data = _load_cache(sid)
        if base_id in (data.get("shops") or {}):
            data["shops"][base_id]["attached_video_id"] = None
            data["shops"][base_id]["attached_at"] = None
            data["updated_at"] = _utc_now()
            _save_cache(data, sid)

    return {"base_id": base_id, "site_id": sid, "youtube_id": None, "updated_files": updated}


# ---------- batch ship (design tab → one PR for attached youtube_id changes) ----------

def _youtube_id_value(text: str) -> str:
    m = _YT_FM_RE.search(text or "")
    return (m.group(1) if m else "").strip()


def _git_dirty_under(repo: Path, rel_dir: str) -> list[str]:
    """Repo-relative paths dirty under rel_dir (tracked + untracked)."""
    if not (repo / ".git").is_dir():
        return []
    prefix = rel_dir.strip("/").replace("\\", "/") + "/"
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=false", "-C", str(repo), "status", "--porcelain", "-uall", "--", rel_dir],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if proc.returncode != 0:
        return []
    out: list[str] = []
    for line in (proc.stdout or "").splitlines():
        if len(line) < 4:
            continue
        p = line[3:].strip().strip('"')
        p = p.split(" -> ", 1)[1] if " -> " in p else p
        p = p.replace("\\", "/")
        if p.startswith(prefix) and (p.endswith("_en.md") or p.endswith("_ko.md")):
            out.append(p)
    return out


def _head_text(repo: Path, rel: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"HEAD:{rel}"],
        capture_output=True, text=True, timeout=15, check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout or ""


def dirty_youtube_files(site_id: str | None = None) -> list[str]:
    """Content markdown where youtube_id differs from HEAD (attach/clear not shipped yet)."""
    sid = normalize_site_id(site_id)
    site = sites.SITES[sid]
    repo = site.repo
    rel_dir = str(site.posts_rel).replace("\\", "/")
    changed: list[str] = []
    for rel in _git_dirty_under(repo, rel_dir):
        path = repo / rel
        if not path.is_file():
            continue
        try:
            now = _youtube_id_value(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        old_text = _head_text(repo, rel)
        if old_text is None:
            if now:
                changed.append(rel)
            continue
        if now != _youtube_id_value(old_text):
            changed.append(rel)
    return changed


def ship_status(site_id: str | None = None) -> dict[str, Any]:
    """Design-tab strip: pending youtube_id edits → one 「반영 맡기기」."""
    from app import snapshot
    from app.work import workqueue

    sid = normalize_site_id(site_id)
    with sites.use(sid):
        files = dirty_youtube_files(sid)
        places = sorted({Path(f).name.rsplit("_", 1)[0] for f in files})
        wip = snapshot.owner_wip()
        pending = workqueue.pending_tasks().get("issue")
        blocked = ""
        if wip:
            blocked = f"사장님 WIP {len(wip)}개 · PR이 멈춰 있어요"
        elif pending:
            blocked = f"이미 {workqueue.pending_label(pending)}"
        can = bool(files) and not blocked
        return {
            "site_id": sid,
            "pending_files": len(files),
            "pending_places": len(places),
            "files": files[:30],
            "places": places[:30],
            "can_enqueue": can,
            "queued": bool(pending),
            "blocked": blocked,
            "label": (
                f"반영 맡기기 · {len(places)}곳"
                if places else "반영 맡기기"
            ),
        }


def enqueue_ship(site_id: str | None = None) -> dict[str, Any]:
    """Queue one issue/PR for pending youtube_id edits; autoship continues to merge·deploy."""
    from app.work import workqueue

    sid = normalize_site_id(site_id)
    with sites.use(sid):
        st = ship_status(sid)
        if not st["can_enqueue"]:
            return {"ok": False, "message": st["blocked"] or "올릴 YouTube 변경이 없어요", **st}
        n = st["pending_places"] or st["pending_files"]
        label = f"YouTube 붙인 글 PR · {n}곳"
        row = workqueue.enqueue_task("issue", label)
        if not row:
            again = ship_status(sid)
            return {
                "ok": False,
                "message": again["blocked"] or "이미 큐에 있어요",
                **again,
            }
        return {
            "ok": True,
            "message": f"큐에 담았어요 · 곧 PR → 배포 · {n}곳",
            "queued": True,
            "label": label,
            **ship_status(sid),
        }
