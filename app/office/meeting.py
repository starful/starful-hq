"""전사 제안 회의: everyone who is free walks to the meeting room; each proposes one idea from their
own position (기획 · 마케팅 · 디자인 · 개발) and the leader ranks them — all in one Claude call.
Every idea carries a ready-to-run action (actions.run_idea).

People show it on the map: chairs around the meeting table, the speaker "제안하는 중", the rest
"듣는 중". The result is kept like the other proposals (planning.save → data/proposals/<id>.json/.md).
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from typing import Any

from app.office import jobs
from app import sites
from app.work import marketing
from app import office
from app.work import planning
from app import snapshot
from app import tools
# Everyone in a team can do any of its work; in a meeting each speaks from their team's point of view.
_TEAM_VIEW = {"plan": "기획팀 — 콘텐츠, 리서치, 새 사업 기회, 작업 흐름",
              "mkt": "마케팀 — 검색 유입, 클릭률, GA4 지표, 어필리에이트 수익",
              "design": "디자인팀 — 화면 UX, 이미지·썸네일, 브랜드",
              "dev": "개발팀 — 사이트 기능, 속도, 코드 품질, 배포·자동화"}
ROLE_VIEW = {p["id"]: ("리더 · " if p.get("lead") else "") + _TEAM_VIEW.get(p["team"], "") for p in office.ALL_PEOPLE}
HOST_ORDER = ("daeun", "seoyeon", "minjae")   # who ranks the ideas (first one present)
SHOW_SEC = 6
MODES = {"all": "전사 회의 (회의실 A)", "leaders": "리더 회의 (회의실 B)", "boss": "사장실 호출 (CEO)",
         "plan": "기획회의 (팀 책상)", "mkt": "마케회의 (팀 책상)", "design": "디자인회의 (팀 책상)", "dev": "개발회의 (팀 책상)"}
TEAM_MODES = ("plan", "mkt", "design", "dev")   # each team at its own desks: teams can meet at the same time
MODE_TASK = {"all": "meeting", "leaders": "meeting_leaders", "boss": "meeting_boss",
             **{t: f"meeting_{t}" for t in TEAM_MODES}}
MODE_ROOM = {"all": "회의실 A", "leaders": "회의실 B", "boss": "사장실",
             **{t: f"{office.TEAM_INFO[t]['name']} 책상" for t in TEAM_MODES}}
MODE_KIND = {"all": "전사", "leaders": "리더", "boss": "사장실 호출",
             **{t: f"{office.TEAM_INFO[t]['name']} 팀" for t in TEAM_MODES}}
# A team meeting with no topic talks about the team's own work
TEAM_TOPIC = {"plan": "콘텐츠 기획 · 새 글 시리즈 · 리서치 · 작업 흐름", "mkt": "검색 유입 · 클릭률 · GA4 지표 · 어필리에이트 수익",
              "design": "썸네일 · 이미지 · 화면 UX · 브랜드", "dev": "사이트 기능 · 속도 · 코드 품질 · 배포 자동화"}
BOSS_MAX = 5   # six chairs at the CEO table: Taekki + five people
OFFICE_MODES = ("all",)   # 전사 회의: about every site the office runs, not just the one on screen


class Meeting:
    def __init__(self, members: list[str], focus: str, ask: str, mode: str = "all", topic: str = ""):
        self.members = members
        self.focus = focus
        self.ask = ask
        self.mode = mode
        self.topic = topic
        self.done = threading.Event()
        self.site = sites.current_id()
        self.office = mode in OFFICE_MODES
        self.people: list[dict[str, Any]] = []
        self.error = ""
        self.result_id = ""


_CURRENT: dict[str, Meeting] = {}


def running(mode: str | None = None) -> bool:
    modes = [mode] if mode else list(MODES)
    return any(m in _CURRENT and not _CURRENT[m].done.is_set() for m in modes)


def _say(role: str, line: str, state: str = "meeting") -> None:
    jobs._set(role, line=line, state=state)


def _participant(work, meeting: Meeting) -> str:
    meeting.done.wait(timeout=60 * 90)
    if meeting.error:
        raise RuntimeError(f"회의를 마치지 못했어요: {meeting.error}")
    mine = next((p for p in meeting.people if p["id"] == work.role), None)
    if not mine or not mine["ideas"]:
        return "회의 끝"
    return f"회의 끝 · {', '.join(str(i['rank']) for i in mine['ideas'])}순위 제안"


def _topic_line(focus: str, topic: str, mode: str = "") -> str:
    """Main agenda label. For 전사(all), optional topic is only a note — FOCUS stays the main."""
    base = planning.FOCUS.get(focus, planning.FOCUS["all"])
    t = topic.strip()
    if mode == "all":
        return f"{base} · 참고 {t}" if t else base
    return t if t else base


def _write_line(site_list: list[sites.Site]) -> str:
    from app.work import workers
    lines = []
    for s in site_list:
        if s.can_write:
            with sites.use(s.id):
                cats = "|".join(workers.categories())
            if s.content == "companydb":
                lines.append(f"{s.id}: 새 회사 페이지 추가. category는 {cats}(시장), topics는 티커나 회사명 1~3개")
                continue
            lang = "일본어" if s.content == "okpy_posts" else "영어나 한국어"
            lines.append(f"{s.id}: category는 {cats}, topics는 {lang} 글 주제 1~3개")
    if not lines:   # posts are not generated here yet: a new post is a 'none' (사람이 할 일)
        return ("- (write는 쓰지 마라: 새 글 자동 작성이 연결되지 않았다. 새 글·새 페이지 제안은 "
                "type none으로 하고 how에 무엇을 만들지 적어라)\n")
    if len(site_list) == 1:
        return f"- write: 새 글 쓰기. {lines[0].split(': ', 1)[1]}\n"
    return "- write: 새 글 쓰기 (사이트마다: " + " / ".join(lines) + "; 여기 없는 사이트는 write 대신 none)\n"


def _ask_all(members: list[str], ctx: dict[str, Any], focus: str, ask: str, host: str,
             per_person: int = 1, topic: str = "", kind: str = "전사", site_list: list[sites.Site] | None = None) -> dict[str, Any]:
    """One Claude call: each person proposes from their position, then the leader ranks them.
    site_list (전사 회의): every site of the office; each idea names the site it is for."""
    from llm_claude import MODEL_HEAVY, claude_json

    roster = [{"id": m, "name": office.staff_by_role(m)["name"], "position": ROLE_VIEW.get(m, "")} for m in members]
    leader = office.staff_by_role(host)["name"]
    site = sites.current()
    many = site_list and len(site_list) > 1
    write_line = _write_line(site_list if many else [site])
    if many:
        head = (f"{sites.office_of().name} 오피스의 {kind} 제안 회의를 한 번에 진행하라. 이 오피스가 맡은 사이트 전부가 대상이다:\n"
                + "".join(f"- {s.id}: {s.about}\n" for s in site_list)
                + "각 제안에는 그 제안이 해당하는 사이트 id(site)를 붙여라. 여러 사이트 공통이면 먼저 해 볼 사이트 하나를 고른다. "
                  "사이트가 한쪽으로 몰리지 않게 하라. 한국어, JSON만 출력.\n")
    else:
        head = f"{site.about} 사이트의 {kind} 제안 회의를 한 번에 진행하라. 이 사이트에 대한 제안만 하라. 한국어, JSON만 출력.\n"
    aside = (topic or "").strip()
    if kind == "전사":
        main = planning.FOCUS.get(focus, planning.FOCUS["all"])
        topic_block = f"회의 주제(메인): {main}\n"
        if aside:
            topic_block += (f"참고 관심 주제(필수는 아님 · 메인 주제를 바꾸거나 이 주제만 다루지 마라. "
                            f"관련되면 살짝 반영): {aside}\n")
    else:
        topic_block = f"회의 주제: {_topic_line(focus, topic)}\n"
    prompt = (
        head
        + topic_block
        + (f"사장님 요청: {ask}\n" if ask else "")
        + f"참석자(각자 자기 포지션에서 겹치지 않는 제안 딱 {per_person}개씩): {json.dumps(roster, ensure_ascii=False)}\n"
        f"마지막에 리더 {leader}이(가) 효과와 실행 쉬움을 보고 모든 제안의 우선순위를 정한다.\n"
        "각 제안에는 바로 실행할 일(action)을 붙여라. type은 다음 중 하나:\n"
        + write_line
        + "- feature: 사이트에 기능 추가(코드 변경). area는 front|back|all, spec은 개발자에게 줄 구체적 요구사항\n"
        + "- search: 클릭 낮은 글 제목·설명 고치기 / polish: 글 본문 다듬기 / analyze: GA4·GSC 분석\n"
        "- none: 사람이 직접 해야 하는 일(외부 계약, SNS 계정 등)\n"
        '형식: {"ideas": [{"key": "who id + 번호(예: daeun-1)", "who": "참석자 id", "site": "사이트 id", "title": "", "why": "데이터 근거", "how": "첫 단계", "impact": "", '
        '"action": {"type": "", "category": "", "topics": [], "area": "", "spec": ""}}], '
        '"ranking": ["우선순위대로 key"], "headline": "회의 결론 한 문장", "leader_note": "리더의 한마디"}\n'
        "title은 구체적 한 줄로 써라. '이름 제안'처럼 쓰지 마라.\n"
        "write의 topics는 아직 없는 새 주제만. 이미 있는 글·비슷한 제목은 다시 제안하지 마라.\n"
        "숫자를 지어내지 말고 없는 데이터는 '확인 필요'라고 써라.\n\n"
        f"데이터: {json.dumps(ctx, ensure_ascii=False, default=str)[:14000]}\n"
    )
    return claude_json(prompt, timeout_sec=600, model=MODEL_HEAVY) or {}


def _normalize(data: dict[str, Any], members: list[str], per_person: int = 1,
               site_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """site_ids: the sites an idea may be for (전사 회의); anything else → the meeting's own site."""
    allowed = site_ids or [sites.current_id()]
    ideas: dict[str, dict[str, Any]] = {}
    count: dict[str, int] = {}
    for x in data.get("ideas") or []:
        if not (isinstance(x, dict) and x.get("who") in members and x.get("title")):
            continue
        who = x["who"]
        if count.get(who, 0) >= per_person:
            continue
        count[who] = count.get(who, 0) + 1
        key = str(x.get("key") or "") or f"{who}-{count[who]}"
        if key in ideas:
            key = f"{who}-{count[who]}"
        act = x.get("action") if isinstance(x.get("action"), dict) else {}
        sid = str(x.get("site") or "").strip()
        if sid not in allowed:
            sid = sites.current_id() if sites.current_id() in allowed else allowed[0]
        if act.get("type") == "write" and not sites.SITES[sid].can_write:
            act = {**act, "type": "none"}   # new posts are not connected for this site yet
        if act.get("type") == "polish" and not sites.SITES[sid].can_polish:
            act = {**act, "type": "none"}   # built pages (companydb) are not polished
        person = office.staff_by_role(who)
        ideas[key] = {
            "who": who, "site": sid, "name": person["name"], "full": person.get("full") or person["name"],
            "position": ROLE_VIEW.get(who, ""),
            **{k: str(x.get(k) or "")[:300] for k in ("title", "why", "how", "impact")},
            "action": {"type": str(act.get("type") or "none"), "category": str(act.get("category") or ""),
                       "topics": [str(t)[:200] for t in (act.get("topics") or [])][:3],
                       "area": str(act.get("area") or ""), "spec": str(act.get("spec") or "")[:600]},
        }
    rank = [str(k) for k in (data.get("ranking") or [])]
    # the ranking may list keys or plain who ids
    order: list[str] = []
    for r in rank:
        if r in ideas and r not in order:
            order.append(r)
        else:
            order += [k for k, v in ideas.items() if v["who"] == r and k not in order][:1]
    order += [k for k in ideas if k not in order]
    return [{**ideas[k], "rank": i + 1} for i, k in enumerate(order)]


def _run(meeting: Meeting) -> None:
    sites.set_current(meeting.site)   # this thread has no site of its own
    try:
        tools.need("claude")
        marketing._okadmin_import()
        for role in meeting.members:
            _say(role, "👂 회의 중")
        site_list = [s for s in sites.office_sites(sites.current().office) if s.enabled] if meeting.office else [sites.current()]
        if len(site_list) > 1:   # 전사 회의: what we know about every site of the office
            ctx = {}
            for s in site_list:
                with sites.use(s.id):
                    ctx[s.id] = planning.context(snapshot.load_or_refresh())
                    ctx[s.id].pop("affiliate_this_month_all_sites", None) if s.id != site_list[0].id else None
        else:
            ctx = planning.context(snapshot.load_or_refresh())
        order = office.LEADERS if meeting.mode in TEAM_MODES else (*HOST_ORDER, *office.LEADERS)   # team meeting: the team leader ranks
        host = next((r for r in order if r in meeting.members), meeting.members[0])
        for role in meeting.members:
            _say(role, "💬 제안 정리 중")
        _say(host, "📝 우선순위 정하는 중", "work")
        per = 2 if meeting.mode == "leaders" or (meeting.mode in ("boss", *TEAM_MODES) and len(meeting.members) <= 3) else 1
        kind = MODE_KIND[meeting.mode]
        data = _ask_all(meeting.members, ctx, meeting.focus, meeting.ask, host, per, meeting.topic, kind, site_list)
        ideas = _normalize(data, meeting.members, per, [s.id for s in site_list])
        if not ideas:
            raise RuntimeError("제안을 받지 못했어요 (Claude 응답 없음)")
        meeting.people = [{"id": m, "ideas": [i for i in ideas if i["who"] == m]} for m in meeting.members]
        for m in meeting.members:
            mine = [i for i in ideas if i["who"] == m]
            if mine:
                _say(m, f"✅ {mine[0]['rank']}순위 · {mine[0]['title'][:18]}")
        result = {
            "at": marketing._stamp(), "meeting": True, "mode": meeting.mode, "mode_label": MODES[meeting.mode],
            "focus": meeting.focus, "topic": meeting.topic[:200],
            "focus_label": _topic_line(meeting.focus, meeting.topic, meeting.mode), "ask": meeting.ask[:300],
            "host": office.staff_by_role(host)["name"],
            "attendees": [office.staff_by_role(m).get("full") or office.staff_by_role(m)["name"] for m in meeting.members],
            "headline": str(data.get("headline") or "")[:240], "leader_note": str(data.get("leader_note") or "")[:300],
            "ideas": ideas,
            "office": len(site_list) > 1, "sites": [s.id for s in site_list],
        }
        meeting.result_id = planning.save(result, office=len(site_list) > 1)
        office.append_event(who=f"{kind} 회의", dept=MODE_ROOM[meeting.mode],
                            text=f"제안 {len(ideas)}개 · {result['headline'][:60]}",
                            site="" if len(site_list) > 1 else meeting.site)
        try:  # 실행 큐: 바로 돌릴 수 있는 제안을 담아 두고, 자리 나면 순서대로 시작
            from app.work import workqueue
            with sites.use(meeting.site):
                n = workqueue.enqueue_meeting(meeting.result_id, meeting.site)
            if n:
                office.append_event(who=f"{kind} 회의", dept=MODE_ROOM[meeting.mode],
                                    text=f"실행 큐에 {n}건 담음", site=meeting.site)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        time.sleep(SHOW_SEC)  # let people show their pick on the map before walking back
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        meeting.error = str(exc)[:200]
    finally:
        meeting.done.set()
        _CURRENT.pop(meeting.mode, None)


def start(focus: str = "all", ask: str = "", mode: str = "all", topic: str = "", people: list[str] | None = None) -> list[str]:
    """Call people into a meeting room. mode all: everyone free → 회의실 A; leaders: team leaders → 회의실 B;
    boss: the people Taekki calls (leaders by default, up to five) → 사장실, empty topic = 사업·기능·콘텐츠 전체.
    For 전사(all), topic is an optional aside and does not replace the main FOCUS."""
    mode = mode if mode in MODES else "all"
    task = MODE_TASK[mode]
    if running(mode) or task in jobs.busy_groups():
        return []
    if mode in TEAM_MODES:
        pool = office.TEAMS[mode]
        topic = topic or TEAM_TOPIC[mode]
    elif mode == "boss":
        known = {p["id"] for p in office.PEOPLE}
        pool = tuple(m for m in (people or office.LEADERS) if m in known)
    else:
        pool = office.LEADERS if mode == "leaders" else tuple(p["id"] for p in office.PEOPLE)
    members = [m for m in pool if not jobs.running(m)]
    if mode == "boss":
        members = members[:BOSS_MAX]
        if not members:
            return []
    elif len(members) < 2:
        return []
    seats = {"leaders": office.LEADER_SEATS, "boss": office.BOSS_TABLE}.get(mode, office.MEETING_SEATS)
    meeting = Meeting(members, focus if focus in planning.FOCUS else "all", ask, mode, topic)
    came = []
    for i, role in enumerate(members):
        going = "팀 책상에서 회의" if mode in TEAM_MODES else "회의실로 가는 중"
        if jobs.start(role, going, _participant, meeting, _task=task):
            seat = office.DESKS[role] if mode in TEAM_MODES else seats[i % len(seats)]   # team meeting: at their own desks
            jobs._set(role, seat=seat, state="meeting")
            came.append(role)
    meeting.members = came
    if len(came) < (1 if mode == "boss" else 2):
        meeting.error = "모인 사람이 너무 적어요"
        meeting.done.set()
        return []
    _CURRENT[mode] = meeting
    threading.Thread(target=_run, args=(meeting,), name=f"meeting-{mode}", daemon=True).start()
    return came
