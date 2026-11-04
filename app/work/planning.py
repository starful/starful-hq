"""기획팀 제안: business ideas and feature ideas for the site, with reasons.

Claude reads what we already know (posts per section, the last GA4·GSC analysis, affiliate results,
search targets, research keywords, the site's pages/routes) and proposes what to do next.
Every run is kept as a file: data/proposals/<id>.json + <id>.md, newest copy in data/proposals.json.
"""

from __future__ import annotations
from app.paths import HQ_ROOT

from app import sites
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.work import marketing
from app import snapshot
from app import tools
ROOT = HQ_ROOT  # starful-hq/ (data lives next to app/)
PROPOSAL_PATH = sites.data("proposals.json")   # per site (okpy: data/…)
PROPOSAL_DIR = sites.data("proposals")
FOCUS = {
    "all": "전체 (사업 · 기능 · 콘텐츠)",
    "money": "수익화 (어필리에이트 · 광고 · 유료)",
    "feature": "사이트 기능 추가",
    "content": "콘텐츠 전략",
    "growth": "유입 채널 늘리기",
}


def _site_pages() -> dict[str, Any]:
    """What the site already has: routes and templates (names only)."""
    app_dir = snapshot.OKPY_ROOT / "app"
    routes: list[str] = []
    for py in sorted(app_dir.rglob("*.py")):   # jpcampus keeps its routes in app/routes/
        try:
            text = py.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        routes += re.findall(r"""\.(?:route|get|post)\(\s*["']([^"']+)["']""", text)
    templates = sorted(p.relative_to(app_dir / "templates").as_posix() for p in (app_dir / "templates").rglob("*.html")) \
        if (app_dir / "templates").is_dir() else []
    return {"routes": sorted(set(routes))[:80], "templates": templates[:80],
            "modules": sorted(p.relative_to(app_dir).as_posix() for p in app_dir.rglob("*.py"))[:80]}


def context(snap: dict[str, Any]) -> dict[str, Any]:
    site = snap.get(sites.current_id()) or {}
    content = site.get("content") or {}
    search = site.get("search") or {}
    analysis = marketing.read_analysis()
    from app.work import workers
    research = workers.read_research()
    return {
        "site": sites.current().about,
        "new_posts_connected": sites.current().can_write,
        "posts": {"total": content.get("posts"), "by_section": content.get("sections")},
        "analysis": {k: analysis.get(k) for k in ("at", "headline", "numbers", "findings", "actions", "topics")} if analysis else {},
        "search": {"to_fix": search.get("shown_count"), "low_ctr": search.get("low_ctr_count"),
                   "low_impression": search.get("low_impression_count")},
        "affiliate_this_month_all_sites": (snap.get("account") or {}).get("affiliate"),
        "costs": {k: (snap.get("account") or {}).get(k) for k in ("gemini", "gcp")},
        "trend_keywords": [k.get("word") for k in (research.get("keywords") or [])][:15],
        "site_pages": _site_pages(),
    }


def _list(items: Any, n: int) -> list[dict[str, str]]:
    out = []
    for x in (items or [])[:n]:
        if isinstance(x, dict) and x.get("title"):
            out.append({k: str(x.get(k) or "")[:300] for k in ("title", "why", "how", "effort", "impact", "team")})
    return out


def propose(work, focus: str = "all", ask: str = "") -> str:
    tools.need("claude")
    marketing._okadmin_import()
    from llm_claude import MODEL_HEAVY, claude_json

    work.say("사이트 숫자와 지난 분석을 모으는 중")
    snap = snapshot.load_or_refresh()
    ctx = context(snap)
    work.say("사업·기능 아이디어를 정리하는 중")
    prompt = (
        f"너는 {sites.current().about} 사이트의 기획자다. 이 사이트에 대한 제안만, 아래 데이터만 근거로, 한국어로 실행 가능한 제안을 하라.\n"
        f"초점: {FOCUS.get(focus, FOCUS['all'])}\n"
        + (f"사장님 요청: {ask}\n" if ask else "")
        + "JSON만 출력한다. 형식:\n"
        '{"headline": "한 문장 요약", '
        '"business": [{"title": "", "why": "데이터 근거", "how": "구체적 첫 단계", "effort": "작음|중간|큼", "impact": "기대 효과"}], '
        '"features": [{"title": "", "why": "", "how": "", "effort": "작음|중간|큼", "impact": "", "team": "개발팀|디자인팀|마케팀|기획팀"}], '
        '"content": [{"title": "", "why": "", "how": "", "effort": "", "impact": ""}], '
        '"quick_wins": [{"title": "", "why": "", "how": "", "team": ""}]}\n'
        "business·features 각 3~5개, content 2~4개, quick_wins(이번 주 바로 할 일) 3개. 이미 있는 기능(site_pages)은 다시 제안하지 마라.\n"
        "숫자를 지어내지 말고, 없는 데이터는 '확인 필요'라고 써라.\n\n"
        f"데이터: {json.dumps(ctx, ensure_ascii=False, default=str)[:14000]}\n"
    )
    data = claude_json(prompt, timeout_sec=600, model=MODEL_HEAVY)
    if not data:
        raise RuntimeError("제안을 읽지 못했어요. 다시 맡겨 주세요")
    result = {
        "at": marketing._stamp(),
        "focus": focus,
        "focus_label": FOCUS.get(focus, FOCUS["all"]),
        "ask": ask[:300],
        "headline": str(data.get("headline") or "")[:240],
        "business": _list(data.get("business"), 6),
        "features": _list(data.get("features"), 6),
        "content": _list(data.get("content"), 5),
        "quick_wins": _list(data.get("quick_wins"), 4),
        "based_on": {"analysis_at": (ctx.get("analysis") or {}).get("at") or "", "posts": ctx["posts"]["total"]},
    }
    aid = save(result)
    n = sum(len(result[k]) for k in ("business", "features", "content", "quick_wins"))
    return f"제안 {n}개를 정리했어요 ({aid})"


# ---------- files + history ----------

SECTIONS = (("quick_wins", "이번 주 바로 할 일"), ("business", "사업 제안"), ("features", "기능 추가"), ("content", "콘텐츠 전략"))


def meeting_markdown(p: dict[str, Any]) -> str:
    where = sites.office_of().name if p.get("office") else sites.current().label
    lines = [f"# {where} {p.get('mode_label') or '전사 제안 회의'} · {p.get('at', '')}", "", f"주제: {p.get('focus_label', '')}",
             f"참석: {', '.join(p.get('attendees') or [])} · 우선순위: {p.get('host', '')}"]
    if p.get("ask"):
        lines.append(f"요청: {p['ask']}")
    lines.append("")
    if p.get("headline"):
        lines += [f"**{p['headline']}**", ""]
    if p.get("leader_note"):
        lines += [f"> {p.get('host', '')}: {p['leader_note']}", ""]
    lines += ["## 우선순위", ""]
    for x in p.get("ideas") or []:
        act = x.get("action") or {}
        site = f"[{sites.SITES[x['site']].label}] " if x.get("site") in sites.SITES and p.get("office") else ""
        lines += [f"{x.get('rank')}. {site}**{x.get('title', '')}** — {x.get('name', '')} ({x.get('position', '')})",
                  f"   - 왜: {x.get('why', '')}", f"   - 어떻게: {x.get('how', '')}"]
        if x.get("impact"):
            lines.append(f"   - 효과: {x['impact']}")
        if act.get("type") and act["type"] != "none":
            lines.append(f"   - 실행: {act['type']} {', '.join(act.get('topics') or [])} {act.get('spec', '')}".rstrip())
        if x.get("started"):
            lines.append(f"   - 시작함: {x['started']}")
    return "\n".join(lines) + "\n"


def update(aid: str, **changes: Any) -> dict[str, Any]:
    """Change a saved proposal (e.g. mark an idea as started) and rewrite its files."""
    data = read(aid)
    if not data or data.get("id") != aid:
        return {}
    data.update(changes)
    folder = (file_of(aid) or Path(PROPOSAL_DIR) / f"{aid}.json").parent
    marketing._write(folder / f"{aid}.json", data)
    (folder / f"{aid}.md").write_text(markdown(data), encoding="utf-8")
    if marketing._read(PROPOSAL_PATH).get("id") == aid:
        marketing._write(PROPOSAL_PATH, data)
    return data


def markdown(p: dict[str, Any]) -> str:
    if p.get("meeting"):
        return meeting_markdown(p)
    lines = [f"# {sites.current().label} 기획팀 제안 · {p.get('at', '')}", "", f"초점: {p.get('focus_label', '')}"]
    if p.get("ask"):
        lines.append(f"요청: {p['ask']}")
    lines.append("")
    if p.get("headline"):
        lines += [f"**{p['headline']}**", ""]
    for key, label in SECTIONS:
        items = p.get(key) or []
        if not items:
            continue
        lines += [f"## {label}", ""]
        for x in items:
            meta = " · ".join(v for v in (x.get("team"), x.get("effort") and f"노력 {x['effort']}", x.get("impact")) if v)
            lines.append(f"### {x.get('title', '')}" + (f"  \n_{meta}_" if meta else ""))
            if x.get("why"):
                lines.append(f"- 왜: {x['why']}")
            if x.get("how"):
                lines.append(f"- 어떻게: {x['how']}")
            lines.append("")
    return "\n".join(lines) + "\n"


def office_dir() -> Path:
    """전사 회의 (whole office, every site) results: data/offices/<office>/proposals/. Shown on each of its sites."""
    return ROOT / "data" / "offices" / sites.current().office / "proposals"


def _dirs() -> list[Path]:
    return [Path(PROPOSAL_DIR), office_dir()]


def save(result: dict[str, Any], office: bool = False) -> str:
    aid = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    result = {**result, "id": aid}
    folder = office_dir() if office else Path(PROPOSAL_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    marketing._write(folder / f"{aid}.json", result)
    (folder / f"{aid}.md").write_text(markdown(result), encoding="utf-8")
    if not office:
        marketing._write(PROPOSAL_PATH, result)
    return aid


def file_of(aid: str, ext: str = ".json") -> Path | None:
    aid = marketing._safe_id(aid)
    for folder in _dirs():
        if aid and (folder / f"{aid}{ext}").is_file():
            return folder / f"{aid}{ext}"
    return None


def read(aid: str = "") -> dict[str, Any]:
    """Load one proposal by id, or the newest (site or office) when aid is empty.
    If aid is given but the file is gone, return {} — do not fall back to another meeting
    (that made the work queue fail with '제안을 찾지 못했어요' on a wrong index)."""
    aid = (aid or "").strip()
    if aid:
        path = file_of(aid)
        if path is not None:
            data = marketing._read(path)
            if data:
                return data
        return {}
    latest = marketing._read(PROPOSAL_PATH)
    newest_office = sorted(office_dir().glob("*.json"))[-1:] if office_dir().is_dir() else []
    if newest_office and newest_office[0].stem > str(latest.get("id") or ""):
        return marketing._read(newest_office[0]) or latest
    return latest


def history(limit: int = 30) -> list[dict[str, Any]]:
    paths = [p for folder in _dirs() if folder.is_dir() for p in folder.glob("*.json")]
    out = []
    for path in sorted(paths, key=lambda p: p.stem, reverse=True)[:limit]:
        data = marketing._read(path)
        if data:
            out.append({"id": path.stem, "at": data.get("at") or path.stem, "headline": data.get("headline") or "",
                        "office": bool(data.get("office")),
                        "focus_label": ((data.get("mode_label") or "전사 회의").split(" (")[0] + " · " if data.get("meeting") else "")
                        + (data.get("focus_label") or "")})
    return out
