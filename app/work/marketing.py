"""마케팀 work besides SEO fixes: polishing posts (다듬기) and GA4 · GSC analysis (분석).

- 다듬기: Claude rewrites a post body for readability. Each post may be polished at most
  POLISH_MAX_TRIES times (data/polish_history.json).
- 분석: okadmin's analytics_api gives GA4 + GSC numbers; Claude reads them and says what to do.
  The same run refreshes the full GSC target list (all low-CTR / low-impression pages, not just 60),
  so the 검색 list grows.
"""

from __future__ import annotations
from app.paths import HQ_ROOT

from app import sites
import json
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from app import snapshot
from app import tools
ROOT = HQ_ROOT  # starful-hq/ (data lives next to app/)
POLISH_PATH = sites.data("polish_history.json")     # per site (okpy: data/…)
ANALYSIS_PATH = sites.data("analysis.json")          # latest
ANALYSIS_DIR = sites.data("analysis")                # every run: <id>.json + <id>.md
PRIOR_DIR = ROOT / "data" / "traffic_prior"          # GA4 previous-period daily rows (HQ local)
POLISH_MAX_TRIES = 4
POLISH_LIST_LIMIT = 150
_POLISH_LOCK = threading.Lock()
_PRIOR_LOCK = threading.Lock()


def _okadmin_import() -> None:
    path = str(snapshot.OKADMIN_ROOT)
    if path not in sys.path:
        sys.path.insert(0, path)


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ---------- posts index ----------

def slug_of(url: str) -> str:
    """https://okpy.net/blog/<slug>#2 → <slug> (decoded)."""
    from app.content import contentkit
    kit_slug = contentkit.slug_of(url)
    if kit_slug is not None:
        return kit_slug
    path = unquote(urlparse(str(url or "")).path or str(url or ""))
    path = path.split("#", 1)[0].rstrip("/")
    if sites.current().flat:   # campus sites: /guide/x → guide_x, /school/univ_x → univ_x, ?lang=kr → _kr, ?lang=ja → _ja
        segs = [s for s in path.split("/") if s]
        if not segs:
            return ""
        last = segs[-1]
        stem = last if ("_" in last or len(segs) < 2) else f"{segs[0]}_{last}"
        query = urlparse(str(url or "")).query or ""
        for lang in ("kr", "ja"):
            if f"lang={lang}" in query and not stem.endswith(f"_{lang}"):
                stem += f"_{lang}"
        return stem
    if "/blog/" in path:
        path = path.split("/blog/", 1)[1]
    return path.strip("/")


def post_index() -> dict[str, dict[str, Any]]:
    """slug (file stem) → {rel, title, section, mtime}."""
    out: dict[str, dict[str, Any]] = {}
    root = snapshot.POSTS_ROOT
    if not root.is_dir():
        return out
    for path in snapshot.post_files():
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        out[path.stem] = {"rel": snapshot.rel_of(path), "section": snapshot.section_of(path),
                          "title": path.stem, "mtime": mtime}
    return out


# ---------- 다듬기 ----------

def polish_counts() -> dict[str, int]:
    data = _read(POLISH_PATH)
    return {rel: len(v) for rel, v in data.items() if isinstance(v, list)}


def record_polish(rel: str, ask: str = "") -> None:
    with _POLISH_LOCK:
        data = _read(POLISH_PATH)
        data.setdefault(rel, []).append({"at": _stamp(), "ask": ask[:200]})
        _write(POLISH_PATH, data)


def mkt_targets(site: dict[str, Any], analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """One list for the 마케팀 drawer: pick posts, then run search-fix and/or polish as needed.

    Each row: title, tags, jobs (search / polish), url, rel, reason bits.
    Search rows come first (merged with polish when the same rel); polish-only (e.g. old) follow.
    """
    analysis = analysis if analysis is not None else read_analysis()
    polish = polish_candidates(site, analysis) if sites.current().can_polish else {
        "rows": [], "total": 0, "skipped": 0, "max_tries": POLISH_MAX_TRIES,
    }
    polish_by_rel = {str(r.get("rel") or ""): r for r in (polish.get("rows") or []) if r.get("rel")}
    ai_urls = {str(x.get("url") or "") for x in (analysis.get("search") or []) if x.get("url")}
    ai_why_url = {str(x.get("url") or ""): str(x.get("why") or "") for x in (analysis.get("search") or [])}
    ai_rels = {str(x.get("rel") or "") for x in (analysis.get("polish") or []) if x.get("rel")}
    ai_why_rel = {str(x.get("rel") or ""): str(x.get("why") or "") for x in (analysis.get("polish") or [])}

    rows: list[dict[str, Any]] = []
    seen_rel: set[str] = set()
    search = site.get("search") if isinstance(site.get("search"), dict) else {}
    for srow in search.get("rows") or []:
        if not isinstance(srow, dict):
            continue
        url = str(srow.get("url") or "")
        rel = str(srow.get("rel") or "")
        if not url and not rel:
            continue
        job_kinds = ["search"] if url else []
        tags: list[str] = []
        if srow.get("pattern"):
            tags.append(str(srow["pattern"]))
        tags.append("search")
        why = ai_why_url.get(url) or ""
        if url in ai_urls or (rel and rel in ai_rels):
            tags.append("ai")
            why = why or ai_why_rel.get(rel) or "분석 추천"
        prow = polish_by_rel.get(rel) if rel else None
        if prow:
            job_kinds.append("polish")
            tags.append("polish")
            ptag = str(prow.get("tag") or "")
            if ptag and ptag not in tags:
                tags.append(ptag)
            if ptag == "gsc" and "low_impression" not in tags:
                tags.append("low_impression")
            seen_rel.add(rel)
            why = why or str(prow.get("reason") or "")
        if not job_kinds:
            continue
        title = str(srow.get("path") or "") or (prow or {}).get("title") or url
        rows.append({
            "title": title,
            "url": url,
            "rel": rel,
            "jobs": job_kinds,
            "tags": tags,
            "pattern": srow.get("pattern") or "",
            "pattern_label": srow.get("pattern_label") or "",
            "ctr": srow.get("ctr") or 0,
            "impressions": srow.get("impressions") or 0,
            "position": srow.get("position") or 0,
            "seo_count": srow.get("seo_count") or 0,
            "polish_count": (prow or {}).get("count") or 0,
            "section": (prow or {}).get("section") or "",
            "when": (prow or {}).get("when") or "",
            "why": why,
            "value": f"{url}\t{rel}\t{','.join(job_kinds)}",
        })

    for prow in polish.get("rows") or []:
        rel = str(prow.get("rel") or "")
        if not rel or rel in seen_rel:
            continue
        seen_rel.add(rel)
        ptag = str(prow.get("tag") or "old")
        tags = ["polish", ptag]
        if ptag == "gsc":
            tags.append("low_impression")
        if rel in ai_rels and "ai" not in tags:
            tags.append("ai")
        rows.append({
            "title": prow.get("title") or rel,
            "url": "",
            "rel": rel,
            "jobs": ["polish"],
            "tags": tags,
            "pattern": "",
            "pattern_label": "",
            "ctr": 0,
            "impressions": 0,
            "position": 0,
            "seo_count": 0,
            "polish_count": prow.get("count") or 0,
            "section": prow.get("section") or "",
            "when": prow.get("when") or "",
            "why": ai_why_rel.get(rel) or str(prow.get("reason") or ""),
            "value": f"\t{rel}\tpolish",
        })

    # Prefer AI / search-fixable rows before polish-only old posts.
    def _rank(r: dict[str, Any]) -> tuple[int, int, str]:
        tags = r.get("tags") or []
        if "ai" in tags:
            pri = 0
        elif "search" in tags:
            pri = 1
        elif "gsc" in tags:
            pri = 2
        else:
            pri = 3
        return (pri, 0 if "search" in (r.get("jobs") or []) else 1, str(r.get("title") or ""))

    rows.sort(key=_rank)
    return {
        "rows": rows,
        "total": len(rows),
        "polish_max": polish.get("max_tries") or POLISH_MAX_TRIES,
        "can_polish": sites.current().can_polish,
        "search_n": sum(1 for r in rows if "search" in (r.get("jobs") or [])),
        "polish_n": sum(1 for r in rows if "polish" in (r.get("jobs") or [])),
        "ai_n": sum(1 for r in rows if "ai" in (r.get("tags") or [])),
    }


def polish_candidates(site: dict[str, Any], analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """Posts worth polishing, best first (none for sites whose pages are built, e.g. companydb).

    1. 분석 추천 (from the last GA4·GSC analysis)
    2. GSC 노출 적음 (search rows with pattern low_impression)
    3. every other post, oldest edit first
    Posts polished POLISH_MAX_TRIES times are left out.
    """
    if not sites.current().can_polish:
        return {"rows": [], "total": 0, "skipped": 0, "max_tries": POLISH_MAX_TRIES}
    index = post_index()
    by_rel = {v["rel"]: v for v in index.values()}
    counts = polish_counts()
    analysis = analysis if analysis is not None else read_analysis()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    skipped = 0

    def add(rel: str, reason: str, tag: str) -> None:
        nonlocal skipped
        if not rel or rel in seen or rel not in by_rel:
            return
        seen.add(rel)
        n = counts.get(rel, 0)
        if n >= POLISH_MAX_TRIES:
            skipped += 1
            return
        info = by_rel[rel]
        rows.append({"rel": rel, "title": info["title"], "section": info["section"], "reason": reason, "tag": tag,
                     "count": n, "when": datetime.fromtimestamp(info["mtime"]).strftime("%Y-%m-%d")})

    for rec in analysis.get("polish") or []:
        add(str(rec.get("rel") or ""), str(rec.get("why") or "분석 추천"), "ai")
    search = site.get("search") if isinstance(site.get("search"), dict) else {}
    for row in search.get("rows") or []:
        if row.get("pattern") == "low_impression":
            rel = row.get("rel") or (index.get(slug_of(row.get("url"))) or {}).get("rel", "")
            add(rel, f"검색 노출 적음 · 노출 {int(row.get('impressions') or 0)}회", "gsc")
    for info in sorted(index.values(), key=lambda v: v["mtime"]):
        add(info["rel"], "오래 손대지 않은 글", "old")
    return {"rows": rows[:POLISH_LIST_LIMIT], "total": len(rows), "skipped": skipped, "max_tries": POLISH_MAX_TRIES}


def _split_frontmatter(text: str) -> tuple[str, str]:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            cut = text.find("\n", end + 4)
            cut = len(text) if cut == -1 else cut + 1
            return text[:cut], text[cut:]
    return "", text


def polish_one(work, rel: str, ask: str) -> str:
    if not sites.current().can_polish:
        raise RuntimeError(f"{sites.current().label} 페이지는 만들어지는 HTML이라 다듬지 않아요")
    path = snapshot.safe_post(rel)
    if path is None:
        raise RuntimeError("글을 찾지 못했어요")
    _okadmin_import()
    from llm_claude import MODEL_HEAVY, claude_text

    front, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    prompt = (
        "다음은 블로그 글 본문(Markdown)이다. 원문과 같은 언어로, 사실·코드·링크·이미지·제목 구조는 그대로 두고 "
        "더 읽기 쉽게 다듬어라. 머리말(frontmatter)은 쓰지 말고 다듬은 본문 Markdown만 출력하라.\n"
        + (f"추가 요청: {ask.strip()}\n" if ask.strip() else "")
        + "\n----- 본문 -----\n"
        + body
    )
    new = claude_text(prompt, timeout_sec=900, model=MODEL_HEAVY)
    if not new or len(new) < len(body) * 0.5:
        raise RuntimeError("다듬은 글이 너무 짧아서 저장하지 않았어요")
    path.write_text(front + new.strip() + "\n", encoding="utf-8")
    record_polish(rel, ask)
    return path.stem


def polish_many(work, rels: list[str], ask: str = "") -> str:
    """One person's share of a 다듬기 batch."""
    tools.need("claude")
    done, failed = [], []
    for n, rel in enumerate(rels, 1):
        work.say(f"{n}/{len(rels)}편 다듬는 중 · {Path(rel).stem[:24]}")
        try:
            done.append(polish_one(work, rel, ask))
        except Exception as exc:  # noqa: BLE001 - keep going with the rest
            failed.append(f"{Path(rel).stem[:20]}: {exc}")
    if not done:
        raise RuntimeError(failed[0] if failed else "다듬을 글이 없어요")
    msg = f"{len(done)}편을 다듬었어요"
    if failed:
        msg += f" · {len(failed)}편 실패 ({failed[0]})"
    return msg


# ---------- 분석 (GA4 · GSC) ----------

def _safe_id(aid: str) -> str:
    aid = str(aid or "").strip()
    return aid if aid and all(c.isalnum() or c in "-_" for c in aid) else ""


def read_analysis(aid: str = "") -> dict[str, Any]:
    """Latest analysis, or one past run by id."""
    aid = _safe_id(aid)
    if aid:
        data = _read(ANALYSIS_DIR / f"{aid}.json")
        if data:
            return data
    return _read(ANALYSIS_PATH)


def list_analyses(limit: int = 30) -> list[dict[str, Any]]:
    """Past runs, newest first: id, at, headline."""
    if not ANALYSIS_DIR.is_dir():
        return []
    out = []
    for path in sorted(ANALYSIS_DIR.glob("*.json"), reverse=True)[:limit]:
        data = _read(path)
        if data:
            out.append({"id": path.stem, "at": data.get("at") or path.stem, "headline": data.get("headline") or ""})
    return out


def analysis_markdown(a: dict[str, Any]) -> str:
    """The report as a Markdown file people can open or share."""
    lines = [f"# {sites.current().label} GA4·GSC 분석 · {a.get('at', '')}", "", f"기간: 최근 28일 ({a.get('period', '')})", ""]
    if a.get("headline"):
        lines += [f"**{a['headline']}**", ""]
    lines += ["## 핵심 숫자", "", "| 출처 | 항목 | 값 | 변화 |", "|---|---|---|---|"]
    lines += [f"| {n.get('src', '')} | {n.get('label', '')} | {n.get('value', '')} | {n.get('delta', '')} |" for n in a.get("numbers") or []]
    if a.get("findings"):
        lines += ["", "## 보인 것", ""] + [f"- {x}" for x in a["findings"]]
    if a.get("actions"):
        lines += ["", "## 할 일", ""] + [f"- **{x.get('team', '')}** {x.get('text', '')}" for x in a["actions"]]
    if a.get("search"):
        lines += ["", "## 먼저 고칠 검색 글", ""] + [f"- {unquote(x.get('url', ''))} — {x.get('why', '')}" for x in a["search"]]
    if a.get("polish"):
        lines += ["", "## 다듬을 글", ""] + [f"- {x.get('title', x.get('rel', ''))} — {x.get('why', '')}" for x in a["polish"]]
    if a.get("topics"):
        lines += ["", "## 새 글 아이디어", ""] + [f"- {x}" for x in a["topics"]]
    if a.get("errors"):
        lines += ["", "## 못 받은 숫자", ""] + [f"- {x}" for x in a["errors"]]
    return "\n".join(lines) + "\n"


def save_analysis(result: dict[str, Any]) -> str:
    """Write latest + a dated copy (json and md). Returns the run id."""
    aid = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    result = {**result, "id": aid}
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    _write(ANALYSIS_DIR / f"{aid}.json", result)
    (ANALYSIS_DIR / f"{aid}.md").write_text(analysis_markdown(result), encoding="utf-8")
    _write(ANALYSIS_PATH, result)
    return aid


def import_legacy_analysis() -> None:
    """The first runs only wrote analysis.json: keep them in the history too."""
    data = _read(ANALYSIS_PATH)
    if not data or data.get("id"):
        return
    aid = str(data.get("at") or "").replace(" ", "_").replace(":", "") or datetime.now().strftime("%Y-%m-%d_%H%M%S")
    if (ANALYSIS_DIR / f"{aid}.json").is_file():
        return
    data["id"] = aid
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    _write(ANALYSIS_DIR / f"{aid}.json", data)
    (ANALYSIS_DIR / f"{aid}.md").write_text(analysis_markdown(data), encoding="utf-8")
    _write(ANALYSIS_PATH, data)


def _pct(v: Any) -> str:
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return "-"


def _delta(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ""
    return f"{'+' if f >= 0 else ''}{f:.1f}%"


def numbers_from(overview: dict[str, Any]) -> list[dict[str, str]]:
    """Key numbers we compute ourselves (Claude only explains them)."""
    ga4 = overview.get("ga4") if isinstance(overview.get("ga4"), dict) else {}
    gsc = overview.get("gsc_daily") if isinstance(overview.get("gsc_daily"), dict) else {}
    gt, gd = ga4.get("totals") or {}, ga4.get("deltas") or {}
    st, sd = gsc.get("totals") or {}, gsc.get("deltas") or {}
    out = []
    if gt:
        out += [
            {"label": "방문(세션)", "value": f"{int(gt.get('sessions') or 0):,}", "delta": _delta(gd.get("sessions")), "src": "GA4"},
            {"label": "사용자", "value": f"{int(gt.get('users') or 0):,}", "delta": _delta(gd.get("users")), "src": "GA4"},
            {"label": "참여율", "value": _pct(gt.get("engagement_rate")), "delta": _delta(gd.get("engagement_rate")), "src": "GA4"},
            {"label": "평균 머문 시간", "value": f"{int(float(gt.get('avg_session_duration') or 0))}초",
             "delta": _delta(gd.get("avg_session_duration")), "src": "GA4"},
        ]
    if st:
        out += [
            {"label": "검색 클릭", "value": f"{int(st.get('clicks') or 0):,}", "delta": _delta(sd.get("clicks")), "src": "GSC"},
            {"label": "검색 노출", "value": f"{int(st.get('impressions') or 0):,}", "delta": _delta(sd.get("impressions")), "src": "GSC"},
            {"label": "클릭률", "value": _pct(st.get("ctr")), "delta": _delta(sd.get("ctr")), "src": "GSC"},
            {"label": "평균 순위", "value": f"{float(st.get('position') or 0):.1f}위", "delta": _delta(sd.get("position")), "src": "GSC"},
        ]
    return out


def _ensure_ga4_creds() -> None:
    """Point at okadmin's firebase key when HQ was started without the env var."""
    import os

    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return
    cand = Path(snapshot.OKADMIN_ROOT) / "secrets" / "firebase-key.json"
    if cand.is_file():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(cand)


def _fmt_delta(block: dict[str, Any], key: str) -> dict[str, Any]:
    raw = (block.get("deltas") or {}).get(key)
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return {}
    return {"pct": f"{v:+.1f}%", "up": v >= 0}


def _ga4_day_row(r: dict[str, Any]) -> dict[str, Any]:
    sessions = int(r.get("sessions") or 0)
    engaged = int(r.get("engaged_sessions") or 0)
    return {
        "date": str(r.get("date") or ""),
        "users": int(r.get("users") or 0),
        "events": int(r.get("events") or 0),
        "pageviews": int(r.get("pageviews") or 0),
        "sessions": sessions,
        "engagement": (engaged / sessions) if sessions else 0.0,
    }


def _prior_path(oid: str, days: int, current_start: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in oid)[:40]
    return Path(PRIOR_DIR) / f"{safe}_d{days}_{current_start}.json"


def _read_prior(oid: str, days: int, current_start: str) -> list[dict[str, Any]] | None:
    path = _prior_path(oid, days, current_start)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    return [r for r in rows if isinstance(r, dict)]


def _write_prior(oid: str, days: int, current_start: str, rows: list[dict[str, Any]]) -> None:
    path = _prior_path(oid, days, current_start)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"at": _stamp(), "oid": oid, "days": days, "current_start": current_start, "rows": rows}
    tmp = path.with_suffix(".tmp")
    with _PRIOR_LOCK:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)


def _prior_rows(*, oid: str, prop: str, days: int, current_start: str, refresh: bool) -> tuple[list[dict[str, Any]], bool]:
    """Previous-period daily series. Disk-cached by (site, days, current window start)."""
    from datetime import date, timedelta

    from analytics_api import fetch_ga4_summary

    if not refresh:
        cached = _read_prior(oid, days, current_start)
        if cached is not None:
            return cached, True
    try:
        start = date.fromisoformat(current_start[:10])
        prior = fetch_ga4_summary(prop, days=days, end_date=start - timedelta(days=1)) or {}
    except (TypeError, ValueError, OSError):
        return [], False
    if prior.get("error"):
        cached = _read_prior(oid, days, current_start)
        return (cached or []), bool(cached)
    rows = [_ga4_day_row(r) for r in (prior.get("rows") or []) if isinstance(r, dict)]
    if rows:
        try:
            _write_prior(oid, days, current_start, rows)
        except OSError:
            pass
    return rows, False


def traffic_card(*, refresh: bool = False, soft: bool = False, days: int = 28) -> dict[str, Any]:
    """GA4 home-style metrics + prior-period chart, plus GSC top queries (okadmin cache).

    soft=True (현황 첫 로드): 캐시만 읽고 산 조회를 돌리지 않음 → 화면이 안 막힘. ↻(fresh)만 재조회.
    """
    _okadmin_import()
    _ensure_ga4_creds()
    from analytics_api import load_analytics_overview, site_analytics_config

    ov = load_analytics_overview(sites.current().oid, days=days, refresh=refresh, phase="all") or {}
    ga4 = ov.get("ga4") if isinstance(ov.get("ga4"), dict) else {}
    gsc_d = ov.get("gsc_daily") if isinstance(ov.get("gsc_daily"), dict) else {}
    gsc_q = ov.get("gsc_queries") if isinstance(ov.get("gsc_queries"), dict) else {}
    # Bad GA4 cache → live refetch only when the user asked (↻). Soft paint keeps the page snappy.
    if not soft and not refresh and ga4.get("error") and not (ga4.get("rows") or ga4.get("totals")):
        keep_d, keep_q = gsc_d, gsc_q
        ov = load_analytics_overview(sites.current().oid, days=days, refresh=True, phase="all") or {}
        ga4 = ov.get("ga4") if isinstance(ov.get("ga4"), dict) else {}
        gsc_d = ov.get("gsc_daily") if isinstance(ov.get("gsc_daily"), dict) else {}
        gsc_q = ov.get("gsc_queries") if isinstance(ov.get("gsc_queries"), dict) else {}
        if gsc_d.get("error") and (keep_d.get("totals") or keep_q.get("rows")):
            gsc_d, gsc_q = keep_d, keep_q
        elif gsc_q.get("error") and keep_q.get("rows"):
            gsc_q = keep_q

    rows = [_ga4_day_row(r) for r in (ga4.get("rows") or []) if isinstance(r, dict)]
    prior_rows: list[dict[str, Any]] = []
    prior_cached = False
    prop = (site_analytics_config(sites.current().oid).get("ga4_property_id") or "").strip()
    start_s = str(ga4.get("start") or "")[:10]
    if prop and rows and not ga4.get("error") and start_s:
        if soft and not refresh:
            # soft paint: disk prior only — never block the status tab on a live GA4 prior fetch
            cached = _read_prior(sites.current().oid, days, start_s)
            if cached is not None:
                prior_rows, prior_cached = cached, True
        else:
            prior_rows, prior_cached = _prior_rows(
                oid=sites.current().oid, prop=prop, days=days, current_start=start_s, refresh=refresh,
            )

    queries = []
    for r in (gsc_q.get("rows") or [])[:20]:
        if not isinstance(r, dict) or not r.get("query"):
            continue
        queries.append({
            "query": str(r.get("query") or "")[:80],
            "clicks": int(r.get("clicks") or 0),
            "impressions": int(r.get("impressions") or 0),
            "ctr": float(r.get("ctr") or 0),
            "position": float(r.get("position") or 0),
        })

    gt = ga4.get("totals") or {}
    st = gsc_d.get("totals") or {}
    users = int(gt.get("users") or 0)
    events = int(gt.get("events") or 0)
    pageviews = int(gt.get("pageviews") or 0)
    engage = float(gt.get("engagement_rate") or 0)

    def _kpi(kid: str, label: str, value: str, delta_key: str) -> dict[str, Any]:
        d = _fmt_delta(ga4, delta_key)
        return {"id": kid, "label": label, "value": value, **d}

    return {
        "days": days,
        "period": ov.get("compare_label") or f"최근 {days}일",
        "cache_hit": bool(ov.get("cache_hit")) and not refresh,
        "prior_cached": prior_cached,
        "site_label": sites.current().label,
        "ga4": {
            "error": str(ga4.get("error") or ""),
            "ok": bool(rows) and not ga4.get("error"),
            "kpis": [
                _kpi("users", "활성 사용자", f"{users:,}", "users"),
                _kpi("events", "이벤트 수", f"{events:,}", "events"),
                _kpi("pageviews", "조회수", f"{pageviews:,}", "pageviews"),
                _kpi("engagement", "참여율", f"{engage * 100:.1f}%", "engagement_rate"),
            ],
            "rows": rows,
            "prior_rows": prior_rows,
            "start": ga4.get("start") or "",
            "end": ga4.get("end") or "",
        },
        "gsc": {
            "error": str(gsc_d.get("error") or gsc_q.get("error") or ""),
            "clicks": int(st.get("clicks") or 0),
            "impressions": int(st.get("impressions") or 0),
            "d_clicks": _fmt_delta(gsc_d, "clicks"),
            "d_impressions": _fmt_delta(gsc_d, "impressions"),
            "queries": queries,
            "start": gsc_q.get("start") or gsc_d.get("start") or "",
            "end": gsc_q.get("end") or gsc_d.get("end") or "",
        },
    }


def refresh_targets(work) -> dict[str, int]:
    """All low-CTR / low-impression pages from GSC (okadmin caps its own list at 60 each)."""
    _okadmin_import()
    from analytics_api import fetch_gsc_pages, site_analytics_config
    from gsc_seo_worker import _build_content_stem_index, enrich_gsc_rows_md
    from gsc_service import analyze_gsc_page_patterns

    gsc_url = site_analytics_config(sites.current().oid).get("gsc_site_url") or ""
    if not gsc_url:
        return {}
    work.say("GSC에서 고칠 글 목록을 전부 받는 중")
    pages = fetch_gsc_pages(gsc_url) or {}
    if pages.get("error") or not pages.get("rows"):
        return {}
    analyzed = analyze_gsc_page_patterns(pages["rows"], key="page")
    index = _build_content_stem_index(sites.current().oid)
    for key in ("low_ctr", "low_impression"):
        rows = [{**r, "pattern": key} for r in analyzed[key]]
        analyzed[key] = enrich_gsc_rows_md(sites.current().oid, rows, max_rows=len(rows), stem_index=index)
    _write(snapshot.TARGETS_PATH, {"cache_day": datetime.now().strftime("%Y-%m-%d"), "cached_at": _stamp(),
                                   "gsc": {**analyzed, "start": pages.get("start"), "end": pages.get("end")}})
    return {"low_ctr": analyzed["low_ctr_count"], "low_impression": analyzed["low_impression_count"]}


def _compact_overview(ov: dict[str, Any]) -> dict[str, Any]:
    def rows(block: str, n: int) -> list:
        b = ov.get(block) if isinstance(ov.get(block), dict) else {}
        return (b.get("rows") or [])[:n]

    return {
        "period": ov.get("compare_label") or "",
        "channels": rows("ga4_channels", 8),
        "devices": rows("ga4_devices", 4),
        "top_queries": rows("gsc_queries", 20),
        "events": rows("ga4_events", 10),
        "indexing": (ov.get("gsc_indexing") or {}).get("totals") if isinstance(ov.get("gsc_indexing"), dict) else {},
    }


def analyze(work) -> str:
    tools.need("claude")
    _okadmin_import()
    from analytics_api import fetch_ga4_pages, load_analytics_overview, site_analytics_config
    from llm_claude import MODEL_HEAVY, claude_json

    work.say("GA4·GSC 숫자를 모으는 중")
    ov = load_analytics_overview(sites.current().oid, days=28, refresh=True, phase="all") or {}
    errors = [str((ov.get(k) or {}).get("error")) for k in ("ga4", "gsc_daily") if isinstance(ov.get(k), dict) and (ov.get(k) or {}).get("error")]
    numbers = numbers_from(ov)
    if not numbers:
        raise RuntimeError("GA4·GSC 숫자를 받지 못했어요" + (f": {errors[0][:120]}" if errors else ""))

    prop = site_analytics_config(sites.current().oid).get("ga4_property_id") or ""
    ga4_pages = (fetch_ga4_pages(prop, days=28) or {}).get("rows", [])[:40] if prop else []
    counts = refresh_targets(work)
    snap = snapshot.refresh_snapshot()
    search_rows = ((snap.get(sites.current_id()) or {}).get("search") or {}).get("rows") or []
    index = post_index()

    cands = [{"url": r["url"], "pattern": r["pattern"], "impressions": r["impressions"],
              "ctr": round(float(r["ctr"] or 0), 4), "position": round(float(r["position"] or 0), 1)}
             for r in search_rows[:80]]
    pages = [{"page": p.get("page"), "views": p.get("pageviews"), "users": p.get("users")} for p in ga4_pages
             if slug_of(p.get("page")) in index]

    work.say("숫자를 읽고 할 일을 정리하는 중")
    prompt = (
        f"너는 {sites.current().label}({sites.current().domain or sites.current().id}) — {sites.current().blurb} — 의 마케터다. 아래 GA4·GSC 28일 데이터를 읽고 한국어로 답하라.\n"
        "JSON만 출력한다. 형식:\n"
        '{"headline": "한 문장 요약", "findings": ["발견 3~5개, 숫자 근거 포함"], '
        '"actions": [{"team": "마케팀|기획팀|디자인팀", "text": "구체적인 할 일"}], '
        '"search": [{"url": "후보 URL 그대로", "why": "짧은 이유"}], '
        '"polish": [{"page": "GA4 page 또는 후보 URL 그대로", "why": "짧은 이유"}], '
        '"topics": ["새 글 주제 아이디어 (일본어 키워드 포함)"]}\n'
        "search는 검색 후보에서 제목·설명을 먼저 고칠 글 최대 10개, polish는 본문을 다듬을 글 최대 10개.\n"
        "URL은 주어진 목록의 값을 그대로 써라. 지어내지 마라.\n\n"
        f"핵심 숫자: {json.dumps(numbers, ensure_ascii=False)}\n"
        f"기타: {json.dumps(_compact_overview(ov), ensure_ascii=False, default=str)[:6000]}\n"
        f"GA4 많이 본 글: {json.dumps(pages, ensure_ascii=False)[:5000]}\n"
        f"검색 후보(클릭 낮음·노출 적음): {json.dumps(cands, ensure_ascii=False)[:9000]}\n"
    )
    data = claude_json(prompt, timeout_sec=600, model=MODEL_HEAVY)
    if not data:
        raise RuntimeError("분석 결과를 읽지 못했어요. 다시 맡겨 주세요")

    valid_urls = {snapshot.base_url(r["url"]): r["url"] for r in search_rows}
    search = []
    for rec in data.get("search") or []:
        url = valid_urls.get(snapshot.base_url(str((rec or {}).get("url") or "")))
        if url:
            search.append({"url": url, "why": str(rec.get("why") or "")[:80]})
    polish = []
    for rec in data.get("polish") or []:
        info = index.get(slug_of(str((rec or {}).get("page") or (rec or {}).get("url") or "")))
        if info:
            polish.append({"rel": info["rel"], "title": info["title"], "why": str(rec.get("why") or "")[:80]})
    result = {
        "at": _stamp(),
        "period": ov.get("compare_label") or "이전 28일 대비",
        "numbers": numbers,
        "headline": str(data.get("headline") or "")[:200],
        "findings": [str(x)[:240] for x in (data.get("findings") or [])][:6],
        "actions": [{"team": str(a.get("team") or ""), "text": str(a.get("text") or "")[:200]}
                    for a in (data.get("actions") or []) if isinstance(a, dict)][:6],
        "search": search[:10],
        "polish": polish[:10],
        "topics": [str(x)[:120] for x in (data.get("topics") or [])][:6],
        "targets": counts,
        "errors": errors,
    }
    aid = save_analysis(result)
    bits = [f"분석 끝 ({aid}): {result['headline'][:80] or '리포트를 만들었어요'}"]
    if counts:
        bits.append(f"검색 대상 {counts.get('low_ctr', 0) + counts.get('low_impression', 0)}개로 늘렸어요")
    return " · ".join(bits)
