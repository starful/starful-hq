"""Sites and offices — loaded from /opt/work/sites.yaml.

Starful HQ (folder starful-hq) manages several sites. Every page and every job works on one site at a time: the
"current site". A web request takes it from the URL (/site/<id>/...); a job keeps the site it was
started for (jobs.start copies it into the job's thread), so hand-offs stay on the same site.

Registry (one place to add / soft-off / remove a site):
  /opt/work/sites.yaml
    hq_offices:          — floor name · blurb · floor number
    services[].hq:       — office · order · enabled · content · paths · blurb
  Soft-off: hq.enabled: false. Drop from HQ: remove the hq: block (okadmin yaml entry can stay).
  Content kits (poicontent / jpcontent / cdbcontent) still hold per-site generator details.

Offices group sites and have their own staff and floor plan:
  Data Lab   — okpy · jpcampus · krcampus · companydb
  Playground — okramen · okcaddie · okonsen · jpfun
A site is switched on (enabled) once its work is connected; until then it is listed as 준비 중.
"""

from __future__ import annotations
from app.paths import HQ_ROOT

import contextlib
import contextvars
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import yaml

ROOT = HQ_ROOT  # starful-hq/ (data lives next to app/)
WORK_ROOT = Path(os.environ.get("WORK_ROOT", "/opt/work"))
SITES_YAML = Path(os.environ.get("SITES_YAML", WORK_ROOT / "sites.yaml"))
DEFAULT_SITE = "okpy"
# Claude-heavy jobs running at the same time, over all sites (meetings excepted): protects the usage limit
MAX_CONCURRENT = int(os.environ.get("NEXT_MAX_JOBS", "6"))

# content values → how new posts / pages are made
#   okpy_posts     — okpy generate_posts.py
#   campus_guides  — jpcontent.py (jpcampus / krcampus)
#   companydb      — cdbcontent.py
#   poi            — poicontent.py (okramen / okcaddie / okonsen / jpfun)
#   ""             — listed but not connected yet


@dataclass(frozen=True)
class Site:
    id: str
    label: str
    office: str
    domain: str = ""
    okadmin_id: str = ""                 # id in okadmin (GSC cache, gsc_logs, deploy logs, pipeline)
    posts_rel: str = "app/content/posts"
    images_rel: str = "app/static/images/posts"
    flat: bool = False                   # posts sit directly in posts_rel (section = file-name prefix, e.g. guide_x.md)
    post_ext: str = ".md"                # companydb: built HTML pages
    content: str = ""                    # see module comment; "" = not yet connected
    enabled: bool = False
    automerge: bool = False              # merge by itself when the review passes
    blurb: str = ""
    order: int = 100                     # sort within an office (from hq.order)
    youtube: dict = field(default_factory=dict)  # hq.youtube — design team attach queue

    @property
    def repo(self) -> Path:
        return WORK_ROOT / self.id

    @property
    def oid(self) -> str:
        return self.okadmin_id or self.id

    @property
    def can_write(self) -> bool:
        """New posts and cover images: any of the four content kits (see contentkit.KINDS)."""
        return self.content in ("okpy_posts", "campus_guides", "companydb", "poi")  # keep inline to avoid import cycle

    @property
    def words(self) -> dict[str, str]:
        """What this site's content is called (okpy 글, campus 가이드, companydb 회사). Each pick also carries its own
        kind (클라우드 비교 · Python 라이브러리 · 가이드 · 회사): see kind_of / picks_text.
        Also seed_hint / topics_hint for the write form placeholders (from contentkit / kits)."""
        noun, unit, verb, done = {"campus_guides": ("가이드", "편", "쓰기", "썼어요"),
                                  "companydb": ("회사", "곳", "추가하기", "추가했어요"),
                                  "poi": (f"{self._poi_noun()}·가이드", "편", "쓰기", "썼어요")}.get(self.content, ("글", "편", "쓰기", "썼어요"))
        from app.content import contentkit
        return {"noun": noun, "unit": unit, "verb": verb, "done": done, "picks": f"추천 {noun}",
                "seed_hint": contentkit.seed_hint(self), "topics_hint": contentkit.topics_hint(self)}


    def _poi_noun(self) -> str:
        from app.content import poicontent
        k = poicontent.KITS.get(self.id)
        return k.noun_ko if k else "장소"

    @property
    def youtube_on(self) -> bool:
        """Design-team YouTube attach is on when hq.youtube.enabled is true."""
        y = self.youtube if isinstance(self.youtube, dict) else {}
        return bool(y.get("enabled"))

    @property
    def can_polish(self) -> bool:
        """다듬기 rewrites a post's Markdown body: not for sites whose pages are built (companydb)."""
        return self.post_ext == ".md"

    @property
    def icon(self) -> Path | None:
        """The site's own favicon (shown next to its jobs and events)."""
        for d in ("app/static/img", "app/static/images", "app/static", "static"):
            for name in ("favicon-32x32.png", "favicon-48x48.png", "apple-touch-icon.png", "favicon.png", "favicon.ico"):
                p = self.repo / d / name
                if p.is_file():
                    return p
        return None

    @property
    def about(self) -> str:
        return f"{self.label} ({self.domain or self.id}) — {self.blurb}"


def josa(word: str, pair: str) -> str:
    """word + the right Korean particle: josa('회사', '을/를') → '회사를', josa('가이드', '이/가') → '가이드가'."""
    a, b = pair.split("/")
    last = (word or " ")[-1]
    has_final = "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 != 0
    if not ("가" <= last <= "힣"):          # latin / digits: read the way it is said ('PR을', 'API를' is close enough)
        has_final = last.lower() in "lmnr0136789"
    if pair == "으로/로" and has_final and (ord(last) - 0xAC00) % 28 == 8:   # ㄹ받침 + 로
        return word + b
    return word + (a if has_final else b)


def kind_of(pick: dict, site: "Site | None" = None) -> str:
    """What one recommended item is: 클라우드 비교, Python 라이브러리, 가이드, 학교, 회사 …"""
    k = str(pick.get("kind") or "").strip()
    if k:
        return k
    label = str(pick.get("bank_label") or "")
    if pick.get("bank") in ("company", "queue"):
        return "회사"
    if pick.get("bank") == "guide":
        return "가이드"
    return label.split(" · ")[0] if label else (site or current()).words["noun"]


_UNIT_OF = {"회사": "곳", "가게": "곳", "골프장": "곳", "온천": "곳", "레저 스팟": "곳"}   # places are counted in 곳, writing in 편


def picks_text(picks: list, site: "Site | None" = None, top: int = 3) -> str:
    """'클라우드 비교 2편 · Python 라이브러리 1편' / '회사 53곳' — counted by kind."""
    s = site or current()
    counts: dict[str, int] = {}
    for p in picks:
        counts[kind_of(p, s)] = counts.get(kind_of(p, s), 0) + 1
    parts = sorted(counts.items(), key=lambda kv: -kv[1])
    text = " · ".join(f"{k} {n}{_UNIT_OF.get(k, s.words['unit'])}" for k, n in parts[:top])
    return text + (f" 외 {len(parts) - top}종" if len(parts) > top else "")


@dataclass(frozen=True)
class Office:
    id: str
    name: str
    blurb: str
    sites: tuple[str, ...] = field(default_factory=tuple)
    floor: int = 1

    @property
    def floor_label(self) -> str:
        return f"B{-self.floor}" if self.floor < 0 else f"{self.floor}층"   # floor -1 → B1


def _domain_of(svc: dict[str, Any]) -> str:
    prod = str((svc.get("links") or {}).get("production") or "").strip().rstrip("/")
    if not prod:
        return ""
    return prod.replace("https://", "").replace("http://", "").split("/")[0]


def _site_from_svc(svc: dict[str, Any]) -> Site | None:
    hq = svc.get("hq")
    if not isinstance(hq, dict):
        return None
    sid = str(svc.get("id") or "").strip()
    office = str(hq.get("office") or "").strip()
    if not sid or not office:
        return None
    yt = hq.get("youtube") if isinstance(hq.get("youtube"), dict) else {}
    return Site(
        id=sid,
        label=str(hq.get("label") or svc.get("label") or sid),
        office=office,
        domain=str(hq.get("domain") or _domain_of(svc)),
        okadmin_id=str(hq.get("okadmin_id") or sid),
        posts_rel=str(hq.get("posts_rel") or "app/content/posts"),
        images_rel=str(hq.get("images_rel") or "app/static/images/posts"),
        flat=bool(hq.get("flat", False)),
        post_ext=str(hq.get("post_ext") or ".md"),
        content=str(hq.get("content") or ""),
        enabled=bool(hq.get("enabled", False)),
        automerge=bool(hq.get("automerge", False)),
        blurb=str(hq.get("blurb") or ""),
        order=int(hq.get("order") or 100),
        youtube=dict(yt),
    )


def _load_hq(path: Path) -> tuple[dict[str, Office], dict[str, Site]]:
    """Build OFFICES + SITES from sites.yaml. Services without hq: are ignored by HQ."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    raw = raw or {}
    office_meta = raw.get("hq_offices") or {}
    built: list[Site] = []
    for svc in raw.get("services") or []:
        if not isinstance(svc, dict):
            continue
        site = _site_from_svc(svc)
        if site:
            built.append(site)
    by_office: dict[str, list[Site]] = {}
    for s in built:
        by_office.setdefault(s.office, []).append(s)
    for lst in by_office.values():
        lst.sort(key=lambda s: (s.order, s.id))

    offices: dict[str, Office] = {}
    # Prefer yaml office order (floor), then any office only referenced by a site
    office_ids = list(office_meta.keys()) if isinstance(office_meta, dict) else []
    for oid in by_office:
        if oid not in office_ids:
            office_ids.append(oid)
    for oid in office_ids:
        meta = (office_meta.get(oid) or {}) if isinstance(office_meta, dict) else {}
        members = tuple(s.id for s in by_office.get(oid, []))
        offices[oid] = Office(
            id=oid,
            name=str(meta.get("name") or oid),
            blurb=str(meta.get("blurb") or ""),
            sites=members,
            floor=int(meta.get("floor") or 1),
        )
    # SITES insertion order = office floor, then site order (matches enabled() expectations)
    sites: dict[str, Site] = {}
    for oid in sorted(offices, key=lambda x: (offices[x].floor < 1, offices[x].floor, x)):   # B1 after 1층 · 2층
        for sid in offices[oid].sites:
            sites[sid] = next(s for s in by_office[oid] if s.id == sid)
    return offices, sites


OFFICES, SITES = _load_hq(SITES_YAML)

_CURRENT: contextvars.ContextVar[str] = contextvars.ContextVar("okn_site", default=DEFAULT_SITE)
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def reload_hq(path: Path | None = None) -> None:
    """Re-read sites.yaml into OFFICES / SITES (tests or after editing the registry)."""
    global OFFICES, SITES
    OFFICES, SITES = _load_hq(path or SITES_YAML)


def get(site_id: str) -> Site | None:
    return SITES.get(site_id)


def current_id() -> str:
    sid = _CURRENT.get()
    return sid if sid in SITES else DEFAULT_SITE


def current() -> Site:
    return SITES[current_id()]


def set_current(site_id: str) -> contextvars.Token:
    return _CURRENT.set(site_id if site_id in SITES else DEFAULT_SITE)


@contextlib.contextmanager
def use(site_id: str) -> Iterator[Site]:
    token = set_current(site_id)
    try:
        yield current()
    finally:
        _CURRENT.reset(token)


def office_of(site_id: str | None = None) -> Office:
    return OFFICES[SITES[site_id or current_id()].office]


def enabled() -> list[Site]:
    return [s for s in SITES.values() if s.enabled]


def office_sites(office_id: str) -> list[Site]:
    return [SITES[s] for s in OFFICES[office_id].sites if s in SITES]


def git_lock(site_id: str | None = None) -> threading.Lock:
    """git work takes turns per repo: two sites can make PRs / deploy at the same time."""
    sid = site_id or current_id()
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(sid, threading.Lock())


def data_path(name: str, site_id: str | None = None) -> Path:
    """starful-hq's own data for a site. okpy keeps the original places (data/<name>)."""
    sid = site_id or current_id()
    return ROOT / "data" / name if sid == DEFAULT_SITE else ROOT / "data" / "sites" / sid / name


class Dyn(os.PathLike):
    """A path that follows the current site (repo, its posts/images folder, or its own data file).
    Works where a Path is used: `/`, str(), open(), Path(...), .is_file(), .glob(), ..."""

    def __init__(self, fn):
        self._fn = fn

    def path(self) -> Path:
        return Path(self._fn())

    def __fspath__(self) -> str:
        return str(self.path())

    def __str__(self) -> str:
        return str(self.path())

    def __repr__(self) -> str:
        return f"Dyn({self.path()!r})"

    def __truediv__(self, other: Any) -> Path:
        return self.path() / other

    def __eq__(self, other: Any) -> bool:
        return Path(self) == Path(other)

    def __hash__(self) -> int:
        return hash(str(self))

    def __getattr__(self, name: str) -> Any:
        return getattr(self.path(), name)


REPO = Dyn(lambda: current().repo)
POSTS = Dyn(lambda: current().repo / current().posts_rel)
IMAGES = Dyn(lambda: current().repo / current().images_rel)


def data(name: str) -> Dyn:
    return Dyn(lambda: data_path(name))
