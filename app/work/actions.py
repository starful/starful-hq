"""One-click work from the office map: click a team's room (or its plate) and pick an action.

Each action knows whether it can run now (someone free, nobody already doing it, something to do)
and how to start it with the same workers the step pages use. Meeting proposals are started here too.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from app import sites
from app.work import devwork
from app.office import jobs
from app.work import marketing
from app import office
from app.work import planning
from app import snapshot
from app.work import workers
MAX_BATCH = 15
# Open PR blocks these until merge — same files (SEO frontmatter) often conflict otherwise.
PR_HOLD_TASKS = frozenset({"feature", "search", "polish"})


def _site() -> dict[str, Any]:
    return snapshot.load_or_refresh().get(sites.current_id()) or {}


def _holder(task: str) -> str:
    who = jobs.busy_groups().get(office.task_group(task))
    return office.staff_by_role(who)["name"] if who else ""


def _free(task: str) -> list[str]:
    return [m for m in office.ordered_members(task) if not jobs.running(m)]


def _open_pr() -> dict[str, Any] | None:
    flow = office.read_flow()
    return flow.get("pr") if flow.get("pr") and not flow.get("merged") else None


def pr_hold_reason(task: str, site_id: str | None = None) -> str:
    """Non-empty when this task should wait for the open PR to merge."""
    if task not in PR_HOLD_TASKS:
        return ""
    sid = site_id or sites.current_id()
    with sites.use(sid):
        pr = _open_pr()
        if not pr:
            return ""
        return f"열린 PR #{pr.get('number')} 머지 후"


# ---------- what each action needs ----------

def _new_pick_rows() -> list[dict[str, Any]]:
    return [p for p in (workers.read_research().get("picks") or []) if p.get("status") == "new"]


def _new_picks() -> list[str]:
    return [p["id"] for p in (workers.read_research().get("picks") or []) if p.get("status") == "new"]


def _search_rows() -> list[dict[str, Any]]:
    rows = (_site().get("search") or {}).get("rows") or []
    recs = [r.get("url") for r in marketing.read_analysis().get("search") or []]
    taken = jobs.busy_items("search")
    rows = [r for r in rows if r["url"] not in taken]
    return [r for r in rows if r["url"] in recs] + [r for r in rows if r["url"] not in recs]


def _polish_rels() -> list[str]:
    cands = marketing.polish_candidates(_site())["rows"]
    taken = jobs.busy_items("polish")
    return [r["rel"] for r in cands if r["rel"] not in taken and r["tag"] in ("ai", "gsc")] or \
           [r["rel"] for r in cands if r["rel"] not in taken]


# ---------- the actions ----------

def _research():
    seeds = (workers.seed_options().get("defaults") or [])[:5]
    return jobs.start_task("research", "트렌드 리서치 시작", workers.research, seeds, 2, 10)


def _write():
    ids = _new_picks()[:MAX_BATCH]
    w = sites.current().words
    return bool(ids) and jobs.start_task_split("write", ids, lambda n: f"{w['picks']} {n}{w['unit']} {w['verb']} 시작", workers.write_picks) and "plan"


def _issue():
    return jobs.start_task("issue", "이슈·PR 만들기 시작", workers.issue_pr, "")


def _search():
    rows = _search_rows()[:MAX_BATCH]
    return bool(rows) and jobs.start_task_split("search", [r["url"] for r in rows], lambda n: f"글 {n}편 고치기 시작",
                                                workers.search_fix, rows) and "mkt"


def _search_empty_why() -> str:
    from app.content import cdbcontent
    if cdbcontent.is_site() or sites.current().flat:
        return ("고칠 글 목록이 비어 있어요 · CompanyDB/HTML 페이지는 마크다운 검색 백로그에 안 올라와요 "
                "· 회의 제안에 회사명이 있으면 그 페이지 제목·메타를 직접 고칩니다")
    return "고칠 글이 없어요 · 마케팀 분석 후 검색 목록을 채우거나 ↻로 GSC를 다시 받아 주세요"


def _gsc_pages_raw() -> list[dict[str, Any]]:
    """GSC target pages for the site, including HTML-only rows (has_md false)."""
    from app import snapshot
    payload = snapshot._load_json(snapshot.TARGETS_PATH) or {}
    gsc = payload.get("gsc") if isinstance(payload.get("gsc"), dict) else {}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pattern in ("low_ctr", "low_impression"):
        for row in gsc.get(pattern) or []:
            if not isinstance(row, dict):
                continue
            url = str(row.get("label") or row.get("page") or "")
            key = snapshot.base_url(url)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append({
                "url": url,
                "path": snapshot._path_label(key),
                "pattern": pattern,
                "pattern_label": snapshot.PATTERN_LABEL.get(pattern, pattern),
                "rel": snapshot._md_rel(row),
                "clicks": row.get("clicks") or 0,
                "impressions": row.get("impressions") or 0,
                "ctr": row.get("ctr") or 0,
                "position": row.get("position") or 0,
                "has_md": bool(row.get("has_md")),
            })
    return out


def _text_hits_page(text: str, url: str, path_label: str = "") -> bool:
    low = str(text or "").lower()
    if not low.strip():
        return False
    blob = f"{url} {path_label}".lower()
    if any(tok and tok in low for tok in re.findall(r"[a-z0-9]{2,}", blob)):
        # weak; require a stronger token from the page path
        pass
    name = path_label.rsplit("/", 1)[-1].replace(".html", "").replace(".md", "")
    if name and len(name) >= 2 and name.lower() in low:
        return True
    # companies/XOM.html → xom
    m = re.search(r"/companies/([a-z0-9]+)\.html", blob)
    if m and m.group(1) in low:
        return True
    return False


def _search_from_idea(idea: dict[str, Any]) -> tuple[str | None, str]:
    """Start search work for a meeting idea — backlog rows, or a named CompanyDB HTML page."""
    from app.content import cdbcontent

    brief = f"{idea.get('title') or ''} {idea.get('how') or idea.get('why') or ''}".strip()

    # CompanyDB: HTML pages never enter the md search backlog — patch title/meta on the page itself.
    if cdbcontent.is_site():
        path = cdbcontent.resolve_page_from_text(brief)
        if not path:
            for row in _gsc_pages_raw():
                if _text_hits_page(brief, row["url"], row.get("path") or ""):
                    stem = str(row.get("path") or "").rsplit("/", 1)[-1].replace(".html", "")
                    path = cdbcontent.page_path_for_ticker(stem)
                    if path:
                        break
        if not path:
            return None, _search_empty_why()
        who = jobs.start_task(
            "search", f"{path.stem} 제목·메타 고치기 시작",
            cdbcontent.fix_page_seo, path, brief[:500],
        )
        if who:
            return who, f"{path.stem} 제목·메타 고치기"
        return None, jobs.request_result().get("why") or "제목·메타 고치기를 시작하지 못했어요"

    rows = _search_rows()
    matched = [r for r in rows if _text_hits_page(brief, str(r.get("url") or ""), str(r.get("path") or ""))]
    pick = (matched or rows)[:MAX_BATCH]
    if not pick:
        return None, _search_empty_why()
    who = jobs.start_task_split(
        "search", [r["url"] for r in pick],
        lambda n: f"글 {n}편 고치기 시작", workers.search_fix, pick,
    )
    if who:
        return "mkt", f"검색 글 {len(pick)}편 고치기"
    return None, jobs.request_result().get("why") or _search_empty_why()


def _polish():
    rels = _polish_rels()[:MAX_BATCH]
    return bool(rels) and jobs.start_task_split("polish", rels, lambda n: f"글 {n}편 다듬기 시작", marketing.polish_many, "") and "mkt"


def _analyze():
    return jobs.start_task("analyze", "GA4·GSC 분석 시작", marketing.analyze)


def _partner_collect():
    from app import partners as partners_mod
    sid = sites.current_id()
    if sid not in partners_mod.enabled_sites():
        return None
    no_mail = len(partners_mod.items(sid, has_email="0"))
    if not no_mail:
        return None
    noun = (partners_mod.cfg(sid) or {}).get("noun") or "업체"
    return jobs.start_task(
        "partner_collect",
        f"{noun} 공개 메일 수집 시작 (최대 {partners_mod.COLLECT_LIMIT})",
        workers.partner_collect,
    )


def _partner_collect_need() -> tuple[bool, str]:
    from app import partners as partners_mod
    sid = sites.current_id()
    if sid not in partners_mod.enabled_sites():
        return False, "제휴 메일 목록이 없어요"
    with_url = len(partners_mod.collect_candidates(sid))
    need_url = partners_mod.need_website_fill(sid)
    lim = partners_mod.COLLECT_LIMIT
    if with_url:
        return True, f"웹사이트 있는 곳 {with_url}곳 · 최대 {lim}곳"
    if need_url:
        return True, f"웹사이트 없는 곳 {need_url}곳 · Places로 찾은 뒤 수집 (최대 {lim})"
    return False, "메일 없는 곳이 없어요"


def _partner_collect_floor():
    from app import partners as partners_mod
    ok, _why = partners_mod.floor_collect_needed()
    if not ok:
        return None
    off = sites.office_of()
    return jobs.start_task(
        "partner_collect_floor",
        f"{off.floor_label} 제휴 메일 수집 시작",
        workers.partner_collect_floor,
    )


def _partner_collect_floor_need() -> tuple[bool, str]:
    from app import partners as partners_mod
    return partners_mod.floor_collect_needed()


def _review():
    flow = office.read_flow()
    if flow.get("reviewed") and flow.get("verdict_ok") is False:
        who, _kind = workers.start_fix_for_review()
        return who
    return jobs.start_task("review", "PR 리뷰·머지 시작", workers.review_and_merge)


def _push():
    return jobs.start_task("push", "PR 브랜치에 푸시 시작", workers.push_fixes)


def _deploy():
    return jobs.start_task("deploy", "사이트 올리기 시작", workers.deploy)


def _refactor(kind: str, area: str):
    label = devwork.KINDS[kind][0]
    return lambda: jobs.start_task(devwork.area_task(area), f"{label} 시작", devwork.refactor, kind, area, "", True)


def _meeting():
    from app.office import meeting
    return bool(meeting.start("all", "")) and "all"


def _leaders():
    from app.office import meeting
    if meeting.is_tf():
        return ""  # B1: no 1층 leaders on the TF floor
    return bool(meeting.start("all", "", "leaders")) and "leaders"


def _summon():
    from app.office import meeting
    from app.office import boss
    if meeting.is_tf():
        return ""  # B1: boss call stays on 1·2F
    if not meeting.start("all", "", "boss"):
        return ""
    boss.move(office.BOSS_TABLE[-1]["x"], office.BOSS_TABLE[-1]["y"], forced=True)   # Taekki takes the last chair
    return "boss"


# id: (team, label, desc, task for busy check, run, need: () -> (ok, why))
ACTIONS: dict[str, dict[str, Any]] = {}


def _add(aid: str, team: str, label: str, desc: str, task: str, run: Callable, need: Callable[[], tuple[bool, str]] | None = None):
    # Seat-work tasks (start_task / ordered_members) must be in TASK_TEAM, or the
    # roster is empty and the queue looks “all busy”. Skip meetings: UI team may
    # be plan/mkt/… but task is meeting_* / meeting / meeting_leaders / meeting_boss.
    if team in office.TEAMS and not str(task).startswith("meeting"):
        mapped = office.TASK_TEAM.get(task)
        if not mapped:
            raise ValueError(
                f"action {aid!r}: task {task!r} missing from office.TASK_TEAM — "
                f"ordered_members would be empty and the queue looks stuck"
            )
        if mapped != team:
            raise ValueError(
                f"action {aid!r}: team {team!r} != TASK_TEAM[{task!r}]={mapped!r}"
            )
    ACTIONS[aid] = {"id": aid, "team": team, "label": label, "desc": desc, "task": task, "run": run,
                    "need": need or (lambda: (True, ""))}


_add("research", "plan", "트렌드 리서치", "트렌드로 주제 10개를 찾고 2편은 바로 써요", "research", _research,
     lambda: (sites.current().can_research,
              "" if sites.current().can_research else "CompanyDB는 키워드 리서치 없이 회사를 추가해요"))
_add("write", "plan", "추천 쓰기", "남은 추천을 최대 15개까지 나눠서 맡아요", "write", _write,
     lambda: (bool(_new_picks()), sites.picks_text(_new_pick_rows()) if _new_picks()
              else f"남은 {sites.josa(sites.current().words['picks'], '이/가')} 없어요"))


def _ba_ja_pending_count() -> int:
    return len(workers.ba_ja_pending(limit=20))


def _ba_ja():
    n = _ba_ja_pending_count()
    return bool(n) and jobs.start_task(
        "ba_ja", f"BA 영어 초안 {min(n, 8)}편 일본어화 시작", workers.translate_ba_ja, None,
    ) and "plan"


_add("ba_ja", "plan", "BA 일본어화", "Business Analysis 영어 초안을 일본어로 옮겨요 (쓰기와 별도)", "ba_ja", _ba_ja,
     lambda: (sites.current().content == "okpy_posts" and _ba_ja_pending_count() > 0,
              f"영어 초안 {_ba_ja_pending_count()}편" if _ba_ja_pending_count()
              else ("okpy만 해당" if sites.current().content != "okpy_posts" else "영어 초안이 없어요")))
_add("issue", "plan", "이슈·PR 만들기", "바뀐 파일로 이슈와 PR을 만들어요", "issue", _issue,
     lambda: (bool((_site().get("git") or {}).get("dirty")) or bool(_open_pr()),
              f"바뀐 파일 {(_site().get('git') or {}).get('file_count') or 0}개" if (_site().get("git") or {}).get("dirty") else "바뀐 파일이 없어요"))
_add("search", "mkt", "검색 글 15편 고치기", "클릭 낮음·노출 적음 글의 제목·설명을 고쳐요 (분석 추천 먼저)", "search", _search,
     lambda: ((False, pr_hold_reason("search")) if pr_hold_reason("search")
              else (bool(_search_rows()), f"대상 {len(_search_rows())}편" if _search_rows() else "고칠 글이 없어요")))
_add("polish", "mkt", "글 15편 다듬기", "분석 추천·노출 적은 글 본문을 읽기 쉽게 다듬어요", "polish", _polish,
     lambda: ((False, pr_hold_reason("polish")) if pr_hold_reason("polish")
              else (bool(_polish_rels()), f"대상 {len(_polish_rels())}편" if _polish_rels() else "다듬을 글이 없어요")))
_add("analyze", "mkt", "GA4·GSC 분석", "28일 숫자를 보고 할 일을 정리해요 (회의실)", "analyze", _analyze)
_add("partner_collect", "mkt", "제휴 메일 수집",
     "공개 문의 메일을 찾아요 (메일 없는 곳 최대 20곳)",
     "partner_collect", _partner_collect, _partner_collect_need)
_add("partner_collect_floor", "mkt", "층 제휴 메일 수집",
     "이 층 제휴 사이트 전부 · Places 한도 나눠 수집",
     "partner_collect_floor", _partner_collect_floor, _partner_collect_floor_need)
_add("review", "dev", "PR 리뷰·머지", "열린 PR을 리뷰하고 좋으면 머지해요", "review", _review,
     lambda: (bool(_open_pr()), f"PR #{(_open_pr() or {}).get('number')}" if _open_pr() else "열린 PR이 없어요"))
_add("push", "dev", "PR에 푸시", "고친 내용을 열린 PR 브랜치에 올려요", "push", _push,
     lambda: (bool(_open_pr()), "" if _open_pr() else "열린 PR이 없어요"))
_add("deploy", "dev", "사이트 올리기", "main을 Cloud Build로 올려요", "deploy", _deploy)
_add("optimize", "dev", "⚙️ 최적화", "개발팀 2명이 백엔드·프론트를 나눠 동시에 소스·주석 정리 → PR", "optimize",
     lambda: jobs.start_task("optimize", "최적화 시작", devwork.optimize, "", True),
     lambda: (not _open_pr(), "" if not _open_pr() else "열린 PR을 머지한 뒤에 할 수 있어요"))
_add("common", "dev", "🧩 공통화", "반복되는 코드를 묶고 PR로 넘겨요 · 최적화와 따로, 머지·배포 확인 뒤에", "refactor", _refactor("common", "all"),
     lambda: (not _open_pr(), "" if not _open_pr() else "열린 PR을 머지한 뒤에 할 수 있어요"))
def _not_tf(why: str):
    def need():
        from app.office import meeting
        return (False, why) if meeting.is_tf() else (True, "")
    return need


_add("meeting", "meet", "전사 제안 회의", "일 없는 사람이 모두 모여 오피스의 사이트 전부를 두고 1개씩 제안하고, 리더가 순서를 정해요", "meeting", _meeting)
_add("leaders", "meet2", "리더 회의", "팀 리더 4명이 모여 2개씩 제안해요 (주제는 현황 탭에서 정할 수 있어요)", "meeting_leaders", _leaders,
     _not_tf("B1 TF는 주제별 스탠드업만 해요"))
_add("summon", "boss", "📣 사장실로 호출", "리더 4명을 사장실로 불러 사업·기능·콘텐츠 전체 제안을 받아요 (사람·주제는 현황 탭에서 고를 수 있어요)",
     "meeting_boss", _summon, _not_tf("B1 TF는 사장실 호출이 없어요 · 1·2층에서 여세요"))


def _team_meeting(team: str):
    def go():
        from app.office import meeting
        return bool(meeting.start("all", "", team)) and team
    return go


def _team_meeting_need(team: str):
    def need():
        from app.office import meeting
        free = [m for m in office.TEAMS[team] if not jobs.running(m)]
        return len(free) >= 2, f"{len(free)}명 모일 수 있어요" if len(free) >= 2 else "모일 사람이 2명보다 적어요"
    return need


for _t in office.TEAM_INFO:
    _add(f"meet_{_t}", _t, "팀 회의", f"{office.TEAM_INFO[_t]['name']} 팀이 자기 책상에서 팀 일 제안을 정리해요",
         f"meeting_{_t}", _team_meeting(_t), _team_meeting_need(_t))

TEAM_LINKS = {"plan": ("write", "글 화면"), "mkt": ("search", "검색 화면"), "design": ("images", "이미지 화면"),
              "dev": ("ship", "반영 화면"), "meet": ("meet", "회의 화면"), "meet2": ("meet", "회의 화면"),
              "boss": ("status", "현황 화면")}
TEAM_TITLE = {**{k: v["name"] for k, v in office.TEAM_INFO.items()}, "meet": "회의실 A · 전사", "meet2": "회의실 B · 리더",
              "boss": "사장실 · CEO"}


CONTENT_TASKS = jobs.CONTENT_TASKS


def state(aid: str) -> dict[str, Any]:
    a = ACTIONS[aid]
    if aid == "write":
        w = sites.current().words
        a = {**a, "label": f"{w['picks']} {w['verb']}", "desc": f"남은 {sites.josa(w['picks'], '을/를')} 최대 15{w['unit']}까지 나눠서 맡아요"}
    holder = _holder(a["task"]) if a["task"] not in office.ITEM_TASKS else ""
    ok, why = a["need"]()
    if a["task"] in CONTENT_TASKS and not sites.current().can_write:
        ok, why = False, f"{sites.current().label}는 아직 글쓰기가 연결되지 않았어요"
    elif a["task"] == "polish" and not sites.current().can_polish:
        ok, why = False, "CompanyDB 페이지는 공시에서 만들어져서 다듬지 않아요"
    elif a["task"] == "research" and not sites.current().can_research:
        ok, why = False, "CompanyDB는 키워드 리서치 없이 대기열·분석에서 회사를 추가해요"
    elif a["task"] in jobs.WIP_TASKS and snapshot.owner_wip():
        ok, why = False, f"사장님 작업 파일 {len(snapshot.owner_wip())}개가 커밋 전이에요"
    if a["team"] in ("meet", "meet2", "boss"):
        pool = office.company_leaders() if a["team"] in ("meet2", "boss") else [p["id"] for p in office.PEOPLE]
        free = [m for m in pool if not jobs.running(m)]
        need = 1 if a["team"] == "boss" else 2
        enabled, reason = len(free) >= need and not holder, f"{len(free)}명 모일 수 있어요" if len(free) >= need else "모일 사람이 없어요"
    elif a["task"].startswith("meeting_") and a["team"] in office.TEAMS:
        free = [m for m in office.TEAMS[a["team"]] if not jobs.running(m)]
        enabled, reason = ok, why
    else:
        free = _free(a["task"])
        enabled = ok and bool(free) and not holder
        reason = (f"{holder}이(가) 하는 중" if holder else (why if not ok else ("모두 일하는 중" if not free else why)))
    return {"id": aid, "label": a["label"], "desc": a["desc"], "enabled": enabled, "reason": reason,
            "who": [office.staff_by_role(m)["name"] for m in free][:3]}


def panel(team: str) -> dict[str, Any]:
    members = office.TEAMS.get(team) or (office.company_leaders() if team in ("meet2", "boss") else tuple(p["id"] for p in office.PEOPLE) if team == "meet" else ())
    people = []
    for m in members:
        j = jobs.get(m)
        p = office.staff_by_role(m)
        people.append({"id": m, "name": p["name"], "full": p.get("full") or p["name"], "title": p["title"], "face": p["face"],
                       "busy": bool(j.get("running")), "line": j.get("line") or ""})
    link = TEAM_LINKS.get(team, ("status", "현황 화면"))
    return {"team": team, "title": TEAM_TITLE.get(team, team), "people": people, "link": link,
            "actions": [state(a) for a, v in ACTIONS.items() if v["team"] == team]}


# Map/panel actions that go on the execution queue (meetings still start now).
_QUEUE_AIDS = frozenset({
    "research", "write", "ba_ja", "issue", "search", "polish", "analyze",
    "partner_collect", "partner_collect_floor",
    "review", "push", "deploy", "optimize", "common",
})


def run(aid: str) -> dict[str, Any]:
    if aid not in ACTIONS:
        return {"ok": False, "message": "모르는 일이에요"}
    st = state(aid)
    if not st["enabled"]:
        return {"ok": False, "message": st["reason"] or "지금은 할 수 없어요"}
    if aid in _QUEUE_AIDS:
        from app.work import workqueue
        task = ACTIONS[aid]["task"]
        # optimize/common use area task names; keep the action id as the queue task key
        qtask = aid if aid in (
            "optimize", "common", "research", "polish", "analyze", "ba_ja",
            "partner_collect", "partner_collect_floor", "push",
        ) else task
        hold_why = pr_hold_reason(qtask)
        row = workqueue.enqueue(
            {"source": "task", "task": qtask, "title": st["label"], "label": st["reason"] or st["label"],
             "site": sites.current_id(),
             "hold": bool(hold_why), "hold_reason": hold_why}
        )
        if not row:
            return {"ok": False, "message": "이미 큐에 있어요"}
        workqueue.kick(sites.current_id())
        tip = f" · {hold_why}" if hold_why else ""
        return {"ok": True, "message": f"{st['label']}을(를) 큐에 담았어요{tip}"}
    who = ACTIONS[aid]["run"]()
    return {"ok": bool(who), "message": f"{st['label']} 맡겼어요" if who else "지금은 맡길 수 없어요"}


def run_waiting(task: str) -> str | None:
    """Start the default action for queued waiting work. Returns who took it."""
    flow = office.read_flow()
    if task == "write":
        ids = _new_picks()[:MAX_BATCH]
        if ids:
            return jobs.start_task_split(
                "write", ids,
                lambda n: f"{sites.current().words['picks']} {n}{sites.current().words['unit']} {sites.current().words['verb']} 시작",
                workers.write_picks,
            ) and "plan"
    elif task == "search":
        rows = _search_rows()[:MAX_BATCH]
        if rows:
            return jobs.start_task_split(
                "search", [r["url"] for r in rows], lambda n: f"글 {n}편 고치기 시작",
                workers.search_fix, rows,
            ) and "mkt"
    elif task == "issue":
        return jobs.start_task("issue", "이슈·PR 만들기 시작", workers.issue_pr, "")
    elif task == "review":
        if flow.get("reviewed") and flow.get("verdict_ok") is False:
            who, _kind = workers.start_fix_for_review()
            return who
        if flow.get("reviewed") and flow.get("verdict_ok"):
            return jobs.start_task("review", "머지 시작", workers.review_and_merge, True)
        return jobs.start_task("review", "PR 리뷰·머지 시작", workers.review_and_merge)
    elif task == "deploy":
        return jobs.start_task("deploy", "사이트 올리기 시작", workers.deploy)
    return None


# ---------- meeting proposals → work ----------

ACTION_LABEL = {"write": "기획팀이 글 쓰기", "feature": "개발팀이 만들기", "search": "마케팀이 검색 글 고치기",
                "polish": "마케팀이 다듬기", "analyze": "마케팀이 분석", "meeting": "", "none": ""}


def run_idea(idea: dict[str, Any]) -> tuple[str | None, str]:
    """Start the work a meeting proposal asks for. Returns (who, message)."""
    act = idea.get("action") or {}
    kind = act.get("type") or "none"
    if kind == "write" and not sites.current().can_write:
        return None, f"{sites.current().label}는 아직 글쓰기가 연결되지 않았어요"
    if kind == "write":
        from app.content import contentkit
        topics = [str(t)[:200] for t in (act.get("topics") or [idea.get("title")]) if t][:5]
        items: list[list[str]] = []
        for t in topics:
            cat = contentkit.resolve_write_category(act.get("category"), t)
            if workers.topic_already_covered(cat, t):
                continue
            items.append([cat, t])
        if not items:
            return None, "이미 쓴 주제 · 건너뜀"
        started = jobs.start_task_split(
            "write", items, lambda n: f"회의 제안 {n}편 쓰기 시작", workers.write_topics,
        )
        return ("plan" if started else None), f"글 {len(items)}편 쓰기"
    if kind == "feature":
        area = act.get("area") if act.get("area") in devwork.AREAS else "all"
        spec = f"{idea.get('title')}: {act.get('spec') or idea.get('how') or ''}"[:600]
        hold = pr_hold_reason("feature")
        if hold:
            return None, hold
        who = jobs.start_task(devwork.area_task(area), "회의 제안 기능 만들기 시작", devwork.refactor, "feature", area, spec, True)
        return who, "기능 만들기"
    if kind == "search":
        hold = pr_hold_reason("search")
        if hold:
            return None, hold
        return _search_from_idea(idea)
    if kind == "polish":
        hold = pr_hold_reason("polish")
        if hold:
            return None, hold
        who = _polish()
        return (who or None), ("글 다듬기" if who else (jobs.request_result().get("why") or "다듬을 글이 없어요"))
    if kind == "analyze":
        return _analyze(), "분석"
    return None, "이 제안은 바로 실행할 일이 없어요"


def proposal_run(pid: str, idx: int) -> dict[str, Any]:
    """Put one meeting idea on the execution queue (does not start immediately)."""
    from app.work import workqueue
    prop = planning.read(pid)
    ideas = prop.get("ideas") or []
    if not prop.get("meeting") or not (0 <= idx < len(ideas)):
        return {"ok": False, "message": "제안을 찾지 못했어요"}
    idea = ideas[idx]
    if idea.get("started"):
        return {"ok": False, "message": "이미 시작한 제안이에요"}
    act = idea.get("action") if isinstance(idea.get("action"), dict) else {}
    kind = act.get("type") or "none"
    if kind == "none" or not ACTION_LABEL.get(kind):
        return {"ok": False, "message": "이 제안은 바로 실행할 일이 없어요"}
    sid = idea.get("site") if idea.get("site") in sites.SITES else sites.current_id()
    hold_reason = pr_hold_reason(kind, sid)
    hold = bool(hold_reason)
    row = workqueue.enqueue(
        {
            "source": "meeting",
            "pid": prop.get("id") or pid,
            "idx": idx,
            "title": idea.get("title") or "",
            "who": idea.get("full") or idea.get("name") or idea.get("who") or "",
            "site": sid,
            "task": kind,
            "action": act,
            "label": ACTION_LABEL.get(kind, ""),
            "hold": hold,
            "hold_reason": hold_reason,
        },
        site_id=sid,
    )
    if not row:
        return {"ok": False, "message": "이미 큐에 있어요"}
    workqueue.kick(sid)
    what = ACTION_LABEL.get(kind, "일")
    if sid != sites.current_id():
        what = f"{sites.SITES[sid].label} · {what}"
    return {"ok": True, "message": f"{what}을(를) 큐에 담았어요"}


def proposal_enqueue_all(pid: str) -> dict[str, Any]:
    from app.work import workqueue
    n = workqueue.enqueue_meeting(pid)
    return {"ok": n > 0, "message": f"{n}건을 큐에 담았어요" if n else "새로 담을 제안이 없어요", "n": n}