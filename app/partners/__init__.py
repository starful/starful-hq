"""Partner outreach lists (public contact emails). Local HQ only.

Campus shares school_emails.json (JP/KR filter). Other sites each have their own file.
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

from app.paths import HQ_ROOT

DATA_DIR = HQ_ROOT / "data" / "partners"
COLLECT_LIMIT = 20
# Floor batch shares daily Places budget (~30); leave headroom for photos etc.
FLOOR_PLACES_BUDGET = 24
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_SKIP_EMAIL = re.compile(
    r"(example\.|sentry\.|wixpress|cloudflare|schema\.org|png$|jpg$|webp$|"
    r"noreply@|no-reply@|mailer-daemon|github\.com|google(mail)?\.com$)",
    re.I,
)
_CONTACT_PATHS = (
    "",
    "/contact",
    "/contact/",
    "/contact.html",
    "/contact/index.html",
    "/en/contact",
    "/en/contact/",
    "/inquiry",
    "/inquiry/",
    "/toiawase",
    "/toiawase/",
    "/toiawase.html",
    "/form",
    "/form/",
    "/about/contact",
    "/company/contact",
    "/company/",
    "/access",
    "/access/",
    "/info",
    "/info/",
)
_CONTACT_HREF = re.compile(
    r"""href=["']([^"']*(?:contact|inquiry|toiawase|mailto:|mailform|フォーム|問合|問い合)[^"']*)["']""",
    re.I,
)
# Directory / aggregator pages — no public mailto; skip for email collect & content backfill
_SKIP_SOURCE_URL = re.compile(
    r"(nisshinkyo\.org/search|rakuten\.co\.jp/gora|gora\.golf|gdo\.com|"
    r"jalan\.net|booking\.com|agoda\.com)",
    re.I,
)

# site_id → list config (UI + storage)
SITE_CFG: dict[str, dict[str, Any]] = {
    "jpcampus": {
        "file": "school_emails.json",
        "items_key": "schools",
        "noun": "어학원",
        "noun_col": "학교",
        "country": "JP",
        "market": "일본",
        "from_mail": "support@jpcampus.net",
    },
    "krcampus": {
        "file": "school_emails.json",
        "items_key": "schools",
        "noun": "어학원",
        "noun_col": "학교",
        "country": "KR",
        "market": "한국",
        "from_mail": "support@krcampus.net",
    },
    "okonsen": {
        "file": "okonsen.json",
        "items_key": "items",
        "noun": "온천·료칸",
        "noun_col": "시설명",
        "country": "JP",
        "market": "일본",
        "from_mail": "support@okonsen.net",
    },
    "okcaddie": {
        "file": "okcaddie.json",
        "items_key": "items",
        "noun": "골프장",
        "noun_col": "코스",
        "country": "JP",
        "market": "일본",
        "from_mail": "support@okcaddie.net",
    },
    "jpfun": {
        "file": "jpfun.json",
        "items_key": "items",
        "noun": "레저 시설",
        "noun_col": "시설명",
        "country": "JP",
        "market": "일본",
        "from_mail": "support@jpfun.net",
    },
    "krcare": {
        "file": "krcare.json",
        "items_key": "items",
        "noun": "클리닉·병원",
        "noun_col": "기관",
        "country": "KR",
        "market": "한국",
        "from_mail": "support@krcare.net",
    },
}

_LANG_SUFFIX = re.compile(r"_(ko|en|ja|zh|zh_tw|cn)$", re.I)


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def enabled_sites() -> frozenset[str]:
    return frozenset(SITE_CFG)


def floor_site_ids(office_id: str | None = None) -> list[str]:
    """Partner-enabled site ids on this office floor (enabled HQ sites only)."""
    from app import sites as sites_mod

    oid = office_id or sites_mod.current().office
    out: list[str] = []
    for s in sites_mod.office_sites(oid):
        if s.enabled and s.id in SITE_CFG:
            out.append(s.id)
    return out


def floor_per_site_limit(n_sites: int | None = None) -> int:
    n = max(1, int(n_sites or 1))
    return max(1, min(COLLECT_LIMIT, FLOOR_PLACES_BUDGET // n))


def floor_collect_needed(office_id: str | None = None) -> tuple[bool, str]:
    """Whether this floor still has mail-less partners to work on."""
    from app import sites as sites_mod

    sids = floor_site_ids(office_id)
    if not sids:
        return False, "이 층에 제휴 메일 사이트가 없어요"
    need_n = 0
    for sid in sids:
        need_n += len(items(sid, has_email="0"))
    if not need_n:
        return False, "이 층은 메일 없는 곳이 없어요"
    lim = floor_per_site_limit(len(sids))
    off = sites_mod.OFFICES.get(office_id or sites_mod.current().office)
    label = off.floor_label if off else "이 층"
    return True, f"{label} {len(sids)}개 사이트 · 사이트당 최대 {lim}곳"


def floor_collect_pending(office_id: str | None = None) -> str:
    """Live queue status for partner_collect_floor on any partner site of this floor."""
    from app.work import workqueue

    for sid in floor_site_ids(office_id):
        st = workqueue.pending_tasks(sid).get("partner_collect_floor") or ""
        if st:
            return st
    return ""


def enqueue_floor_collect(office_id: str | None = None) -> tuple[bool, str, bool]:
    """Queue partner_collect_floor for this office. Returns (ok, msg, already_queued)."""
    from app import sites as sites_mod
    from app.work import workqueue

    oid = office_id or sites_mod.current().office
    sids = floor_site_ids(oid)
    if not sids:
        return False, "이 층에 제휴 메일 사이트가 없어요", False
    ok, why = floor_collect_needed(oid)
    if not ok:
        return False, why, False
    pending = floor_collect_pending(oid)
    if pending:
        return False, f"이미 {workqueue.pending_label(pending)}", True
    off = sites_mod.OFFICES.get(oid)
    lim = floor_per_site_limit(len(sids))
    label = f"{(off.floor_label if off else oid)} 제휴 메일 수집 ({len(sids)}사이트 · 사이트당 최대 {lim})"
    host = sids[0]
    if sites_mod.current_id() in sids:
        host = sites_mod.current_id()
    row = workqueue.enqueue(
        {
            "source": "task",
            "task": "partner_collect_floor",
            "title": label,
            "label": label,
            "site": host,
        },
        site_id=host,
    )
    if not row:
        st = workqueue.pending_tasks(host).get("partner_collect_floor", "queued")
        return False, f"이미 {workqueue.pending_label(st)}", True
    workqueue.kick(host)
    return True, f"큐에 담았어요 · 곧 시작 · {label}", False


def cfg(site_id: str) -> dict[str, Any] | None:
    return SITE_CFG.get(site_id)


def _path(site_id: str) -> Path | None:
    c = SITE_CFG.get(site_id)
    return DATA_DIR / c["file"] if c else None


def load(site_id: str) -> dict[str, Any]:
    c = SITE_CFG.get(site_id)
    if not c:
        return {"items": [], "updated_at": ""}
    key = c["items_key"]
    try:
        data = json.loads(_path(site_id).read_text(encoding="utf-8"))  # type: ignore[union-attr]
        if not isinstance(data, dict):
            return {key: [], "updated_at": ""}
        # campus file uses "schools"; normalize accessor via items_key
        if key not in data and key == "schools" and "schools" not in data:
            data[key] = []
        return data
    except (OSError, json.JSONDecodeError, TypeError):
        return {key: [], "updated_at": ""}


def save(site_id: str, data: dict[str, Any]) -> None:
    c = SITE_CFG.get(site_id)
    if not c:
        raise KeyError(site_id)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {**data, "updated_at": _stamp()}
    _path(site_id).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")  # type: ignore[union-attr]


def items(site_id: str, *, has_email: str = "") -> list[dict[str, Any]]:
    c = SITE_CFG.get(site_id)
    if not c:
        return []
    key = c["items_key"]
    rows = [s for s in (load(site_id).get(key) or []) if isinstance(s, dict)]
    country = c.get("country") or ""
    # campus shared file: filter by market
    if site_id in ("jpcampus", "krcampus") and country in ("JP", "KR"):
        rows = [s for s in rows if s.get("country") == country]
    if has_email == "1":
        rows = [s for s in rows if str(s.get("email") or "").strip()]
    elif has_email == "0":
        rows = [s for s in rows if not str(s.get("email") or "").strip()]
    return rows


def summary(site_id: str) -> dict[str, Any]:
    rows = items(site_id)
    with_mail = sum(1 for s in rows if str(s.get("email") or "").strip())
    c = SITE_CFG.get(site_id) or {}
    data = load(site_id)
    pending = collect_candidates(site_id)
    need_url = need_website_fill(site_id)
    return {
        "total": len(rows),
        "with_email": with_mail,
        "without_email": len(rows) - with_mail,
        "country": c.get("country") or "",
        "noun": c.get("noun") or "",
        "updated_at": data.get("updated_at") or "",
        "last_collect_at": data.get("last_collect_at") or "",
        "last_collect_found": int(data.get("last_collect_found") or 0),
        "last_collect_tried": int(data.get("last_collect_tried") or 0),
        "collect_pending": len(pending),
        "need_website": need_url,
        "collect_limit": COLLECT_LIMIT,
    }


def _usable_source_url(url: str) -> bool:
    u = str(url or "").strip()
    return u.startswith("http") and not _SKIP_SOURCE_URL.search(u)


def collect_candidates(site_id: str, *, limit: int = COLLECT_LIMIT) -> list[dict[str, Any]]:
    """메일 없는 곳 · 쓸 수 있는 source_url 있는 곳만 · 미시도 우선 · 최대 limit."""
    miss = [s for s in items(site_id, has_email="0") if isinstance(s, dict)]
    with_url = [s for s in miss if _usable_source_url(s.get("source_url") or "")]

    def sort_key(s: dict[str, Any]) -> tuple:
        tried = str(s.get("collect_tried_at") or "")
        return (1 if tried else 0, tried, str(s.get("name") or ""))

    return sorted(with_url, key=sort_key)[: max(0, int(limit))]


def need_website_fill(site_id: str) -> int:
    """메일 없고 쓸 수 있는 source_url이 없는 곳 수 (콘텐츠·Places로 채울 후보)."""
    return sum(
        1
        for s in items(site_id, has_email="0")
        if isinstance(s, dict) and not _usable_source_url(s.get("source_url") or "")
    )


def clear_no_url_tries(site_id: str) -> int:
    """URL 없이 '시도'로 찍힌 기록·디렉터리 URL 정리."""
    c = SITE_CFG.get(site_id)
    if not c:
        return 0
    key = c["items_key"]
    data = load(site_id)
    rows = [s for s in (data.get(key) or []) if isinstance(s, dict)]
    n = 0
    for s in rows:
        url = str(s.get("source_url") or "").strip()
        bad_dir = bool(url) and not _usable_source_url(url)
        no_url = not url.startswith("http")
        if bad_dir and not str(s.get("email") or "").strip():
            s["source_url"] = ""
            n += 1
            no_url = True
        if not no_url:
            continue
        if not s.get("collect_tried_at") and "웹사이트 없음" not in str(s.get("note") or ""):
            continue
        s.pop("collect_tried_at", None)
        note = str(s.get("note") or "")
        if "웹사이트 없음" in note:
            s["note"] = ""
        n += 1
    if n:
        data[key] = rows
        save(site_id, data)
    return n


def _parse_md_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out: dict[str, str] = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^([A-Za-z0-9_]+)\s*:\s*(.*)$", line)
        if not m:
            continue
        out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def _content_url_map(site_id: str) -> dict[str, str]:
    """base_id / school id → http URL from site content frontmatter."""
    from app import sites as sites_mod

    site = sites_mod.SITES.get(site_id)
    if not site:
        return {}
    root = site.repo / "app" / "content"
    if not root.is_dir():
        return {}
    out: dict[str, str] = {}
    for path in root.rglob("*.md"):
        if "guide" in path.name.lower():
            continue
        try:
            fm = _parse_md_frontmatter(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        url = ""
        for k in ("website", "source_url", "official_url", "homepage", "url", "booking_url"):
            v = str(fm.get(k) or "").strip()
            if _usable_source_url(v):
                url = v
                break
        if not url:
            continue
        stem = path.stem
        keys = {stem, base_id(stem), re.sub(r"^school_", "", stem)}
        if stem.startswith("school_"):
            keys.add(stem)
            keys.add("school_" + base_id(stem.removeprefix("school_")))
        for k in keys:
            if k and k not in out:
                out[k] = url
    return out


def backfill_urls_from_content(site_id: str) -> int:
    """Fill empty source_url from content MD frontmatter. Returns how many updated."""
    c = SITE_CFG.get(site_id)
    if not c:
        return 0
    url_map = _content_url_map(site_id)
    if not url_map:
        return 0
    key = c["items_key"]
    data = load(site_id)
    rows = [s for s in (data.get(key) or []) if isinstance(s, dict)]
    n = 0
    for s in rows:
        if _usable_source_url(s.get("source_url") or ""):
            continue
        sid = str(s.get("id") or "")
        url = (
            url_map.get(sid)
            or url_map.get(base_id(sid))
            or url_map.get(sid.removeprefix("school_"))
            or url_map.get("school_" + sid.removeprefix("school_"))
            or ""
        )
        if not _usable_source_url(url):
            continue
        s["source_url"] = url
        n += 1
    if n:
        data[key] = rows
        save(site_id, data)
    return n


def _places_website(
    name: str,
    address: str = "",
    *,
    region: str = "JP",
    hint: str = "",
) -> str:
    """Google Places Text Search → websiteUri (1 Places call)."""
    from app.content import placepick
    from app.money import costs

    key = placepick._env_key()
    if not key:
        return ""
    q = " ".join(x for x in (name, hint, address) if x).strip()
    if not q:
        return ""
    costs.take("places", 1)
    data = placepick._post_json(
        "https://places.googleapis.com/v1/places:searchText",
        {
            "textQuery": q[:200],
            "languageCode": "en",
            "regionCode": region,
            "maxResultCount": 5,
        },
        key,
        "places.displayName,places.formattedAddress,places.websiteUri",
    )
    # Prefer a result whose website isn't an aggregator / city portal when possible
    ranked: list[str] = []
    for p in data.get("places") or []:
        w = str(p.get("websiteUri") or "").strip()
        if not w.startswith("http") or _SKIP_SOURCE_URL.search(w):
            continue
        ranked.append(w)
    return ranked[0] if ranked else ""


def fill_websites_via_places(
    site_id: str,
    *,
    limit: int = COLLECT_LIMIT,
    say: Callable[[str], None] | None = None,
) -> int:
    """Fill missing source_url via Places for up to `limit` no-mail items. Returns filled count."""
    c = SITE_CFG.get(site_id)
    if not c:
        return 0
    key = c["items_key"]
    data = load(site_id)
    rows = [s for s in (data.get(key) or []) if isinstance(s, dict)]
    country = c.get("country") or "JP"
    region = "KR" if country == "KR" else "JP"
    hint = {
        "okcaddie": "golf course Japan",
        "okonsen": "onsen ryokan Japan",
        "jpfun": "Japan",
        "krcare": "clinic Korea",
        "jpcampus": "Japanese language school",
        "krcampus": "Korean language school",
    }.get(site_id, "")
    need = [
        s
        for s in rows
        if not str(s.get("email") or "").strip()
        and not _usable_source_url(s.get("source_url") or "")
        and (site_id not in ("jpcampus", "krcampus") or s.get("country") == country)
    ]
    need.sort(key=lambda s: str(s.get("name") or ""))
    targets = need[: max(0, int(limit))]
    filled = 0
    for i, s in enumerate(targets, 1):
        name = str(s.get("name") or s.get("name_en") or s.get("id") or "")[:80]
        addr = str(s.get("address") or "")[:120]
        if say:
            say(f"웹사이트 찾기 {i}/{len(targets)} 「{name[:40]}」")
        try:
            url = _places_website(name, addr, region=region, hint=hint)
        except Exception as exc:  # noqa: BLE001 — keep batch going unless Places budget/key is dead
            from app.money import costs as costs_mod
            if isinstance(exc, costs_mod.OverLimit) or "만료" in str(exc) or "키가" in str(exc):
                if say:
                    say(f"Places 중단: {exc}")
                break
            if say:
                say(f"「{name[:40]}」 Places 실패 · {exc}")
            continue
        if url:
            s["source_url"] = url
            filled += 1
    if filled:
        data[key] = rows
        save(site_id, data)
    return filled


def _fetch_text(url: str, timeout: float = 8.0) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "StarfulHQ-partner-collect/1.0 (+local; public contact only)",
            "Accept": "text/html,application/xhtml+xml",
        },
        method="GET",
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # noqa: S310
        raw = resp.read(400_000)
        ctype = (resp.headers.get("Content-Type") or "").lower()
    if "html" not in ctype and "text" not in ctype and "json" not in ctype:
        return ""
    for enc in ("utf-8", "euc-jp", "cp949", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _emails_in(text: str) -> list[str]:
    raw = (text or "")
    # common obfuscations
    raw = raw.replace("&#64;", "@").replace("&#x40;", "@")
    raw = re.sub(r"\s*[\[(]\s*at\s*[\])]\s*", "@", raw, flags=re.I)
    found: list[str] = []
    for m in _EMAIL_RE.findall(raw):
        e = m.strip().rstrip(".,;:)>\"'")
        if _SKIP_EMAIL.search(e):
            continue
        if e.lower() not in {x.lower() for x in found}:
            found.append(e)
    return found


def _pick_email_from_site(base_url: str) -> tuple[str, str]:
    """Return (email, page_url) from public contact pages under base_url."""
    base = (base_url or "").strip()
    if not base.startswith("http"):
        return "", ""
    parsed = urlparse(base)
    root = f"{parsed.scheme}://{parsed.netloc}"
    candidates = [base]
    for path in _CONTACT_PATHS:
        if not path:
            continue
        candidates.append(urljoin(root + "/", path.lstrip("/")))
    seen: set[str] = set()
    tried = 0
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        tried += 1
        try:
            html = _fetch_text(url)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            continue
        emails = _emails_in(html)
        if emails:
            pref = sorted(
                emails,
                key=lambda e: (
                    0 if e.lower().startswith(("info@", "contact@", "inquiry@", "mail@", "hello@", "office@")) else 1,
                    len(e),
                ),
            )
            return pref[0], url
        # discover contact links from the first page(s)
        if tried <= 2:
            for m in _CONTACT_HREF.findall(html):
                href = (m or "").strip()
                if href.lower().startswith("mailto:"):
                    em = _emails_in(href)
                    if em:
                        return em[0], url
                    continue
                full = urljoin(url, href)
                if full.startswith("http") and urlparse(full).netloc == parsed.netloc and full not in seen:
                    candidates.append(full)
        if tried >= 8:
            break
    return "", ""


def collect_batch(
    site_id: str,
    *,
    limit: int = COLLECT_LIMIT,
    say: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Fetch public emails for up to `limit` items that already have source_url."""
    c = SITE_CFG.get(site_id)
    if not c:
        raise KeyError(site_id)
    key = c["items_key"]
    data = load(site_id)
    rows = [s for s in (data.get(key) or []) if isinstance(s, dict)]
    by_id = {str(s.get("id")): s for s in rows}
    country = c.get("country") or ""
    targets = collect_candidates(site_id, limit=limit)
    found_n = 0
    tried_n = 0
    stamp = _stamp()
    for i, t in enumerate(targets, 1):
        tid = str(t.get("id") or "")
        item = by_id.get(tid)
        if not item or str(item.get("email") or "").strip():
            continue
        if site_id in ("jpcampus", "krcampus") and country and item.get("country") != country:
            continue
        url = str(item.get("source_url") or "").strip()
        if not _usable_source_url(url):
            continue
        name = str(item.get("name") or tid)[:40]
        if say:
            say(f"{i}/{len(targets)} 「{name}」 공개 메일 찾는 중")
        email, page = _pick_email_from_site(url)
        tried_n += 1
        item["collect_tried_at"] = stamp
        if email:
            item["email"] = email
            item["collected_at"] = stamp
            if page:
                item["source_url"] = page
            item["note"] = (item.get("note") or "") or "공개 문의 (자동 수집)"
            found_n += 1
    data[key] = rows
    data["last_collect_at"] = stamp
    data["last_collect_found"] = found_n
    data["last_collect_tried"] = tried_n
    save(site_id, data)
    return {
        "ok": True,
        "tried": tried_n,
        "found": found_n,
        "at": stamp,
        "limit": limit,
        "summary": summary(site_id),
    }


def prepare_and_collect(
    site_id: str,
    *,
    limit: int = COLLECT_LIMIT,
    say: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Backfill URLs (content → Places) then collect public emails. URL 없는 곳은 시도로 세지 않음."""
    clear_no_url_tries(site_id)
    from_content = backfill_urls_from_content(site_id)
    if say and from_content:
        say(f"콘텐츠에서 웹사이트 {from_content}곳 채움")
    have = len(collect_candidates(site_id, limit=limit))
    filled_places = 0
    if have < limit:
        need = min(limit - have, need_website_fill(site_id))
        if need:
            if say:
                say(f"웹사이트 없는 곳 Places로 {need}곳 찾는 중")
            filled_places = fill_websites_via_places(site_id, limit=need, say=say)
            if say:
                say(f"Places로 웹사이트 {filled_places}곳 채움")
    cands = collect_candidates(site_id, limit=limit)
    if not cands:
        return {
            "ok": False,
            "tried": 0,
            "found": 0,
            "at": _stamp(),
            "limit": limit,
            "from_content": from_content,
            "from_places": filled_places,
            "msg": "웹사이트 있는 곳이 없어요 · 메일 수집을 건너뛰어요",
            "summary": summary(site_id),
        }
    out = collect_batch(site_id, limit=limit, say=say)
    out["from_content"] = from_content
    out["from_places"] = filled_places
    return out


def base_id(item_id: str) -> str:
    return _LANG_SUFFIX.sub("", item_id or "")


def _pick_title(rows: list[dict[str, Any]]) -> tuple[str, str, str, str]:
    """Return (name, name_en, name_ja, name_ko) from lang variants."""
    by_lang = {str(r.get("lang") or ""): r for r in rows}
    en = (by_lang.get("en") or {}).get("title") or ""
    ja = (by_lang.get("ja") or {}).get("title") or ""
    ko = (by_lang.get("ko") or {}).get("title") or ""
    name = en or ja or ko or (rows[0].get("title") if rows else "") or ""
    return str(name), str(en), str(ja), str(ko)


def _pick_website(rows: list[dict[str, Any]]) -> str:
    for r in rows:
        w = str(r.get("website") or "").strip()
        if w.startswith("http"):
            return w
    return ""


def _pick_address(rows: list[dict[str, Any]]) -> str:
    for lang in ("en", "ja", "ko", "zh"):
        for r in rows:
            if r.get("lang") == lang and r.get("address"):
                return str(r["address"])
    for r in rows:
        if r.get("address"):
            return str(r["address"])
    return ""


def seed_from_poi(
    site_id: str,
    source_items: list[dict[str, Any]],
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build/merge partner list from POI rows (multi-lang). Keeps existing emails."""
    c = SITE_CFG.get(site_id)
    if not c or site_id in ("jpcampus", "krcampus"):
        raise KeyError(site_id)
    key = c["items_key"]
    existing = {str(s.get("id")): s for s in (load(site_id).get(key) or []) if isinstance(s, dict)}
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in source_items:
        if not isinstance(row, dict):
            continue
        bid = base_id(str(row.get("base_id") or row.get("id") or ""))
        if not bid:
            continue
        groups.setdefault(bid, []).append(row)

    out: list[dict[str, Any]] = []
    for bid, rows in sorted(groups.items()):
        name, name_en, name_ja, name_ko = _pick_title(rows)
        website = _pick_website(rows)
        prev = existing.get(bid) or existing.get(f"place_{bid}") or {}
        # prefer stable id without place_ prefix for new seeds
        pid = str(prev.get("id") or bid)
        activity = ""
        for r in rows:
            if r.get("activity"):
                activity = str(r["activity"])
                break
        cats: list[str] = []
        for r in rows:
            for cat in r.get("categories") or []:
                if cat and cat not in cats:
                    cats.append(str(cat))
        item = {
            "id": pid,
            "country": c["country"],
            "name": name[:120],
            "name_en": name_en[:120],
            "name_ja": name_ja[:120],
            "name_ko": name_ko[:120],
            "address": _pick_address(rows)[:200],
            "email": str(prev.get("email") or "").strip(),
            "source_url": str(prev.get("source_url") or website or "").strip(),
            "note": str(prev.get("note") or "").strip(),
            "activity": activity,
            "categories": cats[:8],
        }
        if extra:
            item.update(extra)
        out.append(item)

    data = {key: out, "updated_at": _stamp()}
    save(site_id, data)
    return summary(site_id)
