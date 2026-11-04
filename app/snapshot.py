"""Site state (one block per site: posts, search, images, git, deploys). Pages read this file.
Counting happens on refresh, for the current site (sites.current_id())."""

from __future__ import annotations
from app.paths import HQ_ROOT

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from app import sites
ROOT = HQ_ROOT  # starful-hq/ (data lives next to app/)
WORK_ROOT = Path(os.environ.get("WORK_ROOT", "/opt/work"))
OKPY_ROOT = sites.REPO                 # the current site's repo (name kept: used across modules)
OKADMIN_ROOT = WORK_ROOT / "okadmin"
SNAPSHOT_PATH = ROOT / "data" / "home.json"
CACHE_DIR = OKADMIN_ROOT / "data" / "gsc_dashboard_cache"
GSC_STATUS = sites.Dyn(lambda: OKADMIN_ROOT / "data" / "gsc_logs" / f"{sites.current().oid}.json")
DEPLOY_LOG_DIR = OKADMIN_ROOT / "ops" / "logs"
CLAUDE_CACHE = OKADMIN_ROOT / "data" / "claude_usage_cache.json"
SPEND_CACHE = OKADMIN_ROOT / "data" / "ai_spend_bq_cache.json"
AFFILIATE_STORE = OKADMIN_ROOT / "data" / "affiliate_reports.json"
POSTS_ROOT = sites.POSTS
IMAGES_ROOT = sites.IMAGES
SEARCH_LIMIT = 1000
SEO_MAX_TRIES = 4  # a post fixed this many times is left alone
URL_HISTORY = sites.Dyn(lambda: OKADMIN_ROOT / "data" / "gsc_logs" / "url_history" / f"{sites.current().oid}.json")
TARGETS_PATH = sites.data("gsc_targets.json")  # full list, written by 마케팀 분석
PATTERN_LABEL = {"low_ctr": "클릭 낮음", "low_impression": "노출 적음"}
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
AFFILIATE_ORDER = (
    ("rakuten", "楽天"),
    ("agoda", "Agoda"),
    ("amazon", "Amazon"),
)


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def read_snapshot() -> dict[str, Any] | None:
    if not SNAPSHOT_PATH.is_file():
        return None
    try:
        data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_snapshot(data: dict[str, Any]) -> None:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SNAPSHOT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SNAPSHOT_PATH)


def sentence_for(git: dict[str, Any], search: dict[str, Any]) -> str:
    if git.get("dirty") and git.get("ahead"):   # committed on a work branch, not in main yet
        return f"커밋된 파일 {int(git.get('file_count') or 0)}개 · PR로 올릴 차례"
    if git.get("dirty"):
        return f"고친 파일 {int(git.get('file_count') or 0)}개가 아직 기록에 없다"
    if search.get("backlog_off"):
        return "오늘 손댈 일이 없다"
    shown = int(search.get("shown_count") or 0)
    if shown:
        return f"클릭이 낮은 글 {shown}편을 고칠 차례"
    return "오늘 손댈 일이 없다"


def claude_line(summary: dict[str, Any] | None) -> str:
    if not isinstance(summary, dict):
        return "저장된 값 없음"
    parts: list[str] = []
    for window in summary.get("windows") or []:
        if not isinstance(window, dict) or window.get("percent") is None:
            continue
        label = str(window.get("label") or window.get("key") or "").strip()
        if not label:
            continue
        parts.append(f"{label} {float(window['percent']):.0f}%")
    return " · ".join(parts) if parts else "저장된 값 없음"


def yen_line(block: dict[str, Any] | None, *, month: str) -> str:
    if not isinstance(block, dict):
        return "저장된 값 없음"
    yen = block.get("estimated_yen")
    if yen is None:
        yen = block.get("cost")
    try:
        amount = int(round(float(yen or 0)))
    except (TypeError, ValueError):
        return "저장된 값 없음"
    text = f"{amount:,}엔"
    if month:
        text += f" · {month}"
    return text


def affiliate_rows(store: dict[str, Any] | None, *, month: str) -> list[dict[str, Any]]:
    months = store.get("months") if isinstance(store, dict) else None
    block = months.get(month) if isinstance(months, dict) else None
    if not isinstance(block, dict):
        block = {}
    rows = []
    for key, label in AFFILIATE_ORDER:
        entry = block.get(key) if isinstance(block.get(key), dict) else {}
        rows.append(
            {
                "id": key,
                "label": label,
                "clicks": int(float(entry.get("clicks") or 0)),
                "orders": int(float(entry.get("orders") or 0)),
                "commission": float(entry.get("commission") or 0),
            }
        )
    return rows


def base_url(url: str) -> str:
    return str(url or "").split("#", 1)[0]


def seo_counts() -> dict[str, int]:
    """How many times each article (URL without #anchor) went through SEO, from okadmin's history."""
    data = _load_json(URL_HISTORY) or {}
    out: dict[str, int] = {}
    for url, block in data.items():
        if not isinstance(block, dict):
            continue
        n = len([a for a in (block.get("seo_attempts") or []) if isinstance(a, dict)])
        key = base_url(url)
        out[key] = out.get(key, 0) + n
    return out


def row_hidden(row: dict[str, Any], tries: int = 0) -> bool:
    """Off the list: no markdown to edit, or already fixed SEO_MAX_TRIES times."""
    if not row.get("has_md"):
        return True
    return tries >= SEO_MAX_TRIES


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _account() -> dict[str, Any]:
    claude = _load_json(CLAUDE_CACHE) or {}
    spend_raw = _load_json(SPEND_CACHE) or {}
    spend = spend_raw.get("summary") if isinstance(spend_raw.get("summary"), dict) else {}
    month = str(spend.get("month") or datetime.now().strftime("%Y-%m"))
    affiliate = _load_json(AFFILIATE_STORE) or {}
    aff_month = month if isinstance((affiliate.get("months") or {}).get(month), dict) else ""
    if not aff_month:
        months = affiliate.get("months") if isinstance(affiliate.get("months"), dict) else {}
        aff_month = sorted(months)[-1] if months else month
    return {
        "claude": claude_line(claude.get("summary") if isinstance(claude.get("summary"), dict) else None),
        "gemini": yen_line(spend.get("gemini") if isinstance(spend.get("gemini"), dict) else None, month=month),
        "gcp": yen_line(spend.get("total") if isinstance(spend.get("total"), dict) else None, month=month),
        "affiliate_month": aff_month,
        "affiliate": affiliate_rows(affiliate, month=aff_month),
    }


def _git() -> dict[str, Any]:
    if not (OKPY_ROOT / ".git").exists():
        return {"branch": "", "dirty": False, "file_count": 0, "files": []}
    branch = subprocess.run(
        ["git", "-C", str(OKPY_ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    status = subprocess.run(
        ["git", "-C", str(OKPY_ROOT), "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    files = []
    for line in (status.stdout or "").splitlines():
        if len(line) < 4 or is_generated(line[3:].strip().strip('"')):
            continue
        files.append(line[3:].strip().strip('"'))
    # commits on a work branch that are not in main yet count as changes too (they go into the PR)
    name = (branch.stdout or "").strip()
    ahead = 0
    if name and name not in ("main", "HEAD"):
        cnt = subprocess.run(["git", "-C", str(OKPY_ROOT), "rev-list", "--count", "main..HEAD"],
                             capture_output=True, text=True, timeout=20, check=False)
        ahead = int((cnt.stdout or "0").strip() or 0) if cnt.returncode == 0 else 0
        if ahead:
            names = subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(OKPY_ROOT), "diff", "--name-only", "main...HEAD"],
                                   capture_output=True, text=True, timeout=20, check=False)
            files += [f for f in (names.stdout or "").splitlines() if f and f not in files]
    return {
        "branch": name,
        "dirty": bool(files),
        "ahead": ahead,
        "file_count": len(files),
        "files": files[:40],
    }


def _title_of(path: Path) -> str:
    if path.suffix == ".html":
        from app.content import cdbcontent
        return cdbcontent.title_of(path)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[:40]
    except OSError:
        return path.stem
    for line in lines:
        m = re.match(r'\s*"title":\s*"(.+?)"\s*,?\s*$', line)   # JSON front matter (jpcampus)
        if m:
            return m.group(1)
        if line.startswith("title:"):
            return line.split(":", 1)[1].strip().strip("'\"")
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def section_of(path: Path) -> str:
    """okpy: the folder (posts/<section>/x.md). Flat sites: the file-name prefix (guide_x.md → guide).
    companydb: the market of the ticker (JP · KR · US)."""
    from app.content import contentkit
    kit = contentkit.section_of(path)
    if kit is not None:
        return kit
    if sites.current().flat:
        return path.stem.split("_", 1)[0] if "_" in path.stem else "etc"
    return path.parent.name


def _is_translation(path: Path) -> bool:
    """The second-language copy of a page (counted apart): campus _kr/_ja, place sites _ko."""
    from app.content import contentkit
    return contentkit.is_translation(path)


def rel_of(path: Path) -> str:
    from app.content import contentkit
    return contentkit.post_rel(path, flat=sites.current().flat)


def post_files() -> list[Path]:
    """Every post .md of the current site."""
    from app.content import contentkit
    return contentkit.list_posts(POSTS_ROOT, sites.current().post_ext)


SECTION_LABEL = {
    # okpy folders
    "python": "Python 라이브러리", "cloud": "클라우드 비교", "terraform": "Terraform", "eng-comms": "엔지니어 소통",
    "ai-models": "AI 모델 비교", "data-analysis": "데이터 분석", "career": "커리어", "dev-method": "개발 방법론",
    "agile-scrum": "애자일·스크럼", "pmbok": "PMBOK", "data-model": "데이터 모델", "fit-journey": "Fit Journey",
    # campus file prefixes
    "guide": "가이드", "school": "어학교", "univ": "대학", "stay": "숙소", "shop": "가게", "course": "골프장", "onsen": "온천", "spot": "레저 스팟",
    # companydb markets
    "JP": "일본 회사", "KR": "한국 회사", "US": "미국 회사",
}
TRANSLATION_SUFFIXES = ("_kr", "_ja")
BASE_LANG = {"okpy": "일본어", "jpcampus": "영어", "krcampus": "영어", "companydb": "영어", "okramen": "영어", "okcaddie": "영어", "okonsen": "영어", "jpfun": "영어"}   # the language a site writes in
SUFFIX_LANG = {"_kr": "한국어", "_ja": "일본어", "_ko": "한국어"}
LANG_FLAG = {"일본어": "🇯🇵", "영어": "🇺🇸", "한국어": "🇰🇷"}


def kinds_of(content: dict[str, Any]) -> list[dict[str, Any]]:
    """[{label, n}] biggest first, for showing what a site holds (가이드 237 · 대학 512 …)."""
    secs = content.get("sections") or {}
    return [{"key": k, "label": SECTION_LABEL.get(k, k), "n": n} for k, n in sorted(secs.items(), key=lambda kv: -kv[1])]


def _content() -> dict[str, Any]:
    by_section: dict[str, int] = {}
    newest: list[tuple[float, Path]] = []
    translations = 0
    by_lang: dict[str, int] = {}
    for path in post_files():
        if sites.current().flat and _is_translation(path):
            translations += 1   # the Korean / Japanese copy of a page: counted apart, not as more content
            lang = SUFFIX_LANG.get(path.stem[-3:], "번역")
            by_lang[lang] = by_lang.get(lang, 0) + 1
            continue
        sec = section_of(path)
        by_section[sec] = by_section.get(sec, 0) + 1
        try:
            newest.append((path.stat().st_mtime, path))
        except OSError:
            continue
    by_section = dict(sorted(by_section.items()))
    newest.sort(key=lambda item: item[0], reverse=True)
    recent = []
    for mtime, path in newest[:8]:
        recent.append(
            {
                "title": _title_of(path),
                "section": section_of(path),
                "name": path.name,
                "rel": rel_of(path),
                "when": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d"),
            }
        )
    posts = sum(by_section.values())
    base = BASE_LANG.get(sites.current_id(), "영어")
    langs = [{"label": base, "n": posts, "flag": LANG_FLAG.get(base, ""), "base": True}]
    langs += [{"label": k, "n": n, "flag": LANG_FLAG.get(k, ""), "base": False} for k, n in sorted(by_lang.items(), key=lambda kv: -kv[1])]
    return {"posts": posts, "sections": by_section, "translations": translations, "langs": langs, "newest": recent}


def _latest_cache() -> Path | None:
    files = sorted(CACHE_DIR.glob(f"{sites.current().oid}_v1_*.json"))
    return files[-1] if files else None


def _trend_word(label: Any) -> str:
    word = str(label or "").strip()
    if word in ("", "1회", "비교불가"):
        return ""
    return word


def _path_label(url: str) -> str:
    path = unquote(urlparse(url).path or url)
    if path.startswith("/blog/"):
        path = path[len("/blog/") :]
    return path or url


def _md_rel(row: dict[str, Any]) -> str:
    key = sites.current().posts_rel.strip("/") + "/"
    for f in row.get("md_files") or []:
        if sites.current().flat:
            if key in str(f) and str(f).endswith(".md"):
                return str(f).split(key, 1)[1]
            continue
        parts = str(f).split("/")
        if len(parts) >= 2 and parts[-1].endswith(".md"):
            return f"{parts[-2]}/{parts[-1]}"
    return ""


def _search(cache_path: Path | None) -> dict[str, Any]:
    """Posts to fix for search: low CTR and low impressions, one row per article."""
    empty = {"cache_day": "", "low_ctr_count": 0, "low_impression_count": 0, "shown_count": 0,
             "skipped_tries": 0, "rows": []}
    try:
        from app.work import workqueue
        if not workqueue.search_backlog_on():
            return {**empty, "backlog_off": True}
    except Exception:  # noqa: BLE001 - prefs missing is fine
        pass
    payload = _load_json(TARGETS_PATH) or {}
    cached = _load_json(cache_path) if cache_path else None
    if cached and str(cached.get("cache_day") or "") > str(payload.get("cache_day") or ""):
        payload = cached  # okadmin refreshed after our last full fetch
    if not payload:
        return empty
    gsc = payload.get("gsc") if isinstance(payload.get("gsc"), dict) else {}
    counts = seo_counts()
    shown: list[dict[str, Any]] = []
    seen: set[str] = set()
    skipped = 0
    for pattern in ("low_ctr", "low_impression"):
        rows = gsc.get(pattern) if isinstance(gsc.get(pattern), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            url = str(row.get("label") or row.get("page") or "")
            key = base_url(url)
            if not key or key in seen:
                continue
            tries = counts.get(key, 0)
            if row_hidden(row, tries):
                if row.get("has_md") and tries >= SEO_MAX_TRIES:
                    skipped += 1
                seen.add(key)
                continue
            seen.add(key)
            hist = row.get("url_history") if isinstance(row.get("url_history"), dict) else {}
            shown.append(
                {
                    "url": url,
                    "path": _path_label(key),
                    "pattern": pattern,
                    "pattern_label": PATTERN_LABEL[pattern],
                    "rel": _md_rel(row),
                    "clicks": row.get("clicks") or 0,
                    "impressions": row.get("impressions") or 0,
                    "ctr": row.get("ctr") or 0,
                    "position": row.get("position") or 0,
                    "seo_count": tries,
                    "trend": _trend_word(hist.get("trend_label")),
                }
            )
    shown = shown[:SEARCH_LIMIT]
    return {
        "cache_day": str(payload.get("cache_day") or ""),
        "low_ctr_count": int(gsc.get("low_ctr_count") or 0),
        "low_impression_count": int(gsc.get("low_impression_count") or 0),
        "shown_count": len(shown),
        "skipped_tries": skipped,
        "rows": shown,
    }


IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def _image_posts() -> dict[str, dict[str, str]]:
    """image file name → the post that uses it (title, rel)."""
    out: dict[str, dict[str, str]] = {}
    pat = re.compile(r"([\w.-]+\.(?:jpe?g|png|webp|gif))", re.I)
    for path in post_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        title = _title_of(path)
        for name in set(pat.findall(text)):
            out.setdefault(name, {"title": title, "rel": rel_of(path)})
        from app.content import contentkit
        contentkit.link_place_images(path, out, title, rel_of(path), IMAGE_EXT)
    return out


_SIG_CACHE: dict[tuple[str, float, int], str] = {}


def _image_sig(path: Path, mtime: float) -> str:
    """Same photo saved under several names (a whole onsen town got one picture): size + first 64 KB."""
    try:
        size = path.stat().st_size
        key = (str(path), mtime, size)
        if key not in _SIG_CACHE:
            with path.open("rb") as fh:
                _SIG_CACHE[key] = f"{size}:{hashlib.sha1(fh.read(65536)).hexdigest()}"
        return _SIG_CACHE[key]
    except OSError:
        return ""


def _image_kind(post: dict[str, str]) -> str:
    """온천 / 가이드 / 글 … or 안 씀 — so a guide image and a place photo don't look the same in the list."""
    from app.content import contentkit
    if not post:
        return "안 씀"
    if str(post.get("rel") or "").startswith("guides/"):
        return "가이드"
    return contentkit.image_noun()


def _image_label(name: str, post: dict[str, str]) -> str:
    """Which page this is, short: 'Noboribetsu Onsen Adzuma' (the title before ':' / '—'), else the file name."""
    from app.content import contentkit
    title = str(post.get("title") or "")
    head = re.split(r"\s*[:|｜—–]\s+|\s+-\s+", title, maxsplit=1)[0].strip() if title else ""
    if head:
        return head
    stem = contentkit.image_base_stem(Path(name).stem)
    return " ".join(w[:1].upper() + w[1:] for w in re.split(r"[_-]+", stem) if w)


def _images() -> dict[str, Any]:
    """Every post image, newest first, with the post that uses it, its kind and how many files share the photo."""
    if not IMAGES_ROOT.is_dir():
        return {"count": 0, "files": []}
    found: list[tuple[float, Path]] = []
    for path in IMAGES_ROOT.iterdir():
        if not path.is_file() or path.name.startswith(".") or path.suffix.lower() not in IMAGE_EXT:
            continue
        try:
            found.append((path.stat().st_mtime, path))
        except OSError:
            continue
    found.sort(key=lambda item: item[0], reverse=True)
    posts = _image_posts()
    sigs = {path.name: _image_sig(path, mtime) for mtime, path in found}
    same: dict[str, int] = {}
    for sig in sigs.values():
        if sig:
            same[sig] = same.get(sig, 0) + 1
    files = []
    for mtime, path in found:
        post = posts.get(path.name) or {}
        files.append({"name": path.name, "when": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d"),
                      "post": post.get("title", ""), "rel": post.get("rel", ""), "kind": _image_kind(post),
                      "label": _image_label(path.name, post),
                      "dup": same.get(sigs[path.name], 1) if sigs[path.name] else 1})
    return {"count": len(files), "files": files, "unused": sum(1 for f in files if not f["post"]),
            "dups": sum(1 for f in files if f["dup"] > 1)}


def _seo_last() -> dict[str, Any]:
    data = _load_json(GSC_STATUS) or {}
    seo = data.get("seo") if isinstance(data.get("seo"), dict) else {}
    return {
        "finished_at": seo.get("finished_at") or "",
        "ok": seo.get("ok"),
        "applied": seo.get("applied"),
        "count": seo.get("count"),
    }


def _last_ship() -> dict[str, Any]:
    if not DEPLOY_LOG_DIR.is_dir():
        return {}
    files = list(DEPLOY_LOG_DIR.glob(f"deploy-{sites.current().oid}-*.log"))
    if not files:
        return {}
    latest = max(files, key=lambda path: path.stat().st_mtime)
    when = datetime.fromtimestamp(latest.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    tail = ""
    try:
        lines = latest.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = _ANSI.sub("", lines[-1]).strip() if lines else ""
    except OSError:
        tail = ""
    return {"when": when, "tail": tail[:180]}


def safe_post(rel: str) -> Path | None:
    from app.content import contentkit
    rel = (rel or "").strip().lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    path = (POSTS_ROOT / rel).resolve()
    root = POSTS_ROOT.resolve()
    home = contentkit.safe_post_home(path, root, flat=sites.current().flat)
    if home != root or path.suffix != sites.current().post_ext or not path.is_file():
        return None
    return path


def safe_image(name: str) -> Path | None:
    name = Path(name or "").name
    if not name or name.startswith("."):
        return None
    path = (IMAGES_ROOT / name).resolve()
    if path.parent != IMAGES_ROOT.resolve() or not path.is_file():
        return None
    return path


WIP_PATH = sites.data("wip_baseline.json")


def owner_wip(files: list[str] | None = None) -> list[str]:
    """Files the owner left uncommitted before this office started on the site (jpcampus had some).
    Saved the first time the site is seen; a file drops off the list once it is committed or cleaned up.
    While any is left, code tidy-ups and PRs wait, so the owner's work is never swept into a PR or reverted.
    okpy is managed from the start: nothing to protect."""
    if sites.current_id() == sites.DEFAULT_SITE:
        return []
    now = files if files is not None else _git_all_dirty()
    if now is None:   # git could not tell: never save an empty list by mistake
        return []
    path = Path(WIP_PATH)
    saved = _load_json(path)
    if saved is None:
        keep = sorted(now)
    else:
        keep = [f for f in (saved.get("files") or []) if f in set(now)]
    if saved is None or keep != (saved.get("files") or []):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"files": keep, "at": now_stamp()}, ensure_ascii=False, indent=1), encoding="utf-8")
    return keep


GENERATED_PARTS = ("__pycache__/", ".pytest_cache/")
GENERATED_EXT = (".pyc", ".pyo", ".DS_Store")


def is_generated(path: str) -> bool:
    """Files Python / pytest / Finder write by themselves: never anyone's work (okramen even tracks its .pyc in git)."""
    return any(part in path for part in GENERATED_PARTS) or path.endswith(GENERATED_EXT)


def revert_generated() -> list[str]:
    """Put tracked generated files (.pyc …) back to the committed version, so running the site's tests or scripts
    never shows up as a change, a PR file or a job's 'forbidden file'."""
    if not (Path(OKPY_ROOT) / ".git").exists():
        return []
    proc = subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(OKPY_ROOT), "status", "--porcelain"],
                          capture_output=True, text=True, timeout=20, check=False)
    paths = [ln[3:].strip().strip('"') for ln in (proc.stdout or "").splitlines()
             if len(ln) >= 4 and ln[:2].strip() in ("M", "MM", "D") and is_generated(ln[3:].strip().strip('"'))]
    if paths:
        subprocess.run(["git", "-C", str(OKPY_ROOT), "checkout", "--", *paths], capture_output=True, text=True, timeout=30, check=False)
    return paths


def _git_all_dirty() -> list[str] | None:
    if not (Path(OKPY_ROOT) / ".git").exists():
        return None
    proc = subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(OKPY_ROOT), "status", "--porcelain", "-uall"],
                          capture_output=True, text=True, timeout=20, check=False)
    if proc.returncode != 0:
        return None
    out = []
    for line in (proc.stdout or "").splitlines():
        if len(line) >= 4:
            p = line[3:].strip().strip('"')
            p = p.split(" -> ", 1)[1] if " -> " in p else p
            if not is_generated(p):
                out.append(p)
    return out


def site_block() -> dict[str, Any]:
    """Everything the pages show for the current site."""
    git = _git()
    git["owner_wip"] = owner_wip()
    search = _search(_latest_cache())
    content = _content()
    return {
        "sentence": sentence_for(git, search),
        "content": content,
        "search": search,
        "images": _images(),
        "git": git,
        "ship": _last_ship(),
        "seo_last": _seo_last(),
        "refreshed_at": now_stamp(),
    }


def build_snapshot(old: dict[str, Any] | None = None) -> dict[str, Any]:
    """Refresh the current site's block (other sites keep their last one)."""
    sid = sites.current_id()
    snap = dict(old or read_snapshot() or {})
    block = site_block()
    snap.update(refreshed_at=now_stamp(), account=_account())
    snap[sid] = block
    rows = {r.get("id"): r for r in snap.get("sites") or [] if isinstance(r, dict)}
    rows[sid] = {"id": sid, "label": sites.current().label, "sentence": block["sentence"],
                 "posts": block["content"]["posts"], "at": block["refreshed_at"]}
    snap["sites"] = [rows[k] for k in sites.SITES if k in rows]
    return snap


def refresh_snapshot() -> dict[str, Any]:
    snap = build_snapshot()
    write_snapshot(snap)
    return snap


def site_data(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    """The current site's block from a snapshot."""
    snap = snap if snap is not None else load_or_refresh()
    block = snap.get(sites.current_id())
    return block if isinstance(block, dict) else {}


def load_or_refresh() -> dict[str, Any]:
    snap = read_snapshot()
    if snap and snap.get("account") and snap.get(sites.current_id()):
        return snap
    return refresh_snapshot()
