"""근무 시간 · 휴가 · 알바 대행 (재미용 스케줄).

data/office_schedule.json
  hours: {start, end} 로컬 시각 (end 미포함)
  workdays: 0=월 … 6=일 (기본 월–일 — 주말 휴무 없음)
  lunch_hours: 점심으로 보일 시각
  enforce_hours: true면 퇴근 시각 이후 새 잡 시작 거부
  vacations: [{who, from, to, note}] 날짜 inclusive (YYYY-MM-DD)
  alba: [{id, name, name_ko, sex, art, teams}] 최대 3명 · 팀당 동시 휴가 1명일 때 대행
"""

from __future__ import annotations

import json
import zlib
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.paths import HQ_ROOT

SCHEDULE_PATH = HQ_ROOT / "data" / "office_schedule.json"
MAX_ALBA = 3
MAX_VACATION_PER_TEAM = 1

# Default part-timers: reuse existing sprite art; not on the permanent roster.
_DEFAULT_ALBA = (
    {"id": "alba_mina", "name": "Mina", "name_ko": "미나", "name_ja": "ミナ", "name_en": "Mina",
     "sex": "f", "art": "emma", "teams": ["plan", "mkt"]},
    {"id": "alba_rio", "name": "Rio", "name_ko": "리오", "name_ja": "リオ", "name_en": "Rio",
     "sex": "m", "art": "kaito", "teams": ["design", "dev"]},
    {"id": "alba_noa", "name": "Noa", "name_ko": "노아", "name_ja": "ノア", "name_en": "Noa",
     "sex": "f", "art": "ana", "teams": ["plan", "mkt", "design", "dev"]},
)

_DEFAULT = {
    "hours": {"start": 8, "end": 22},
    "workdays": [0, 1, 2, 3, 4, 5, 6],  # weekends too — HQ runs Sat/Sun
    "lunch_hours": [12],
    "enforce_hours": True,
    "vacations": [],
    "alba": list(_DEFAULT_ALBA),
}


def _today(now: float | None = None) -> date:
    return datetime.fromtimestamp(now if now is not None else datetime.now().timestamp()).date()


def _parse_day(s: str) -> date | None:
    try:
        return date.fromisoformat(str(s or "")[:10])
    except ValueError:
        return None


def load() -> dict[str, Any]:
    try:
        data = json.loads(SCHEDULE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return dict(_DEFAULT)
    except (OSError, json.JSONDecodeError):
        return dict(_DEFAULT)
    out = dict(_DEFAULT)
    out.update({k: data[k] for k in data if k in _DEFAULT or k in ("hours", "workdays", "lunch_hours",
                                                                    "enforce_hours", "vacations", "alba")})
    hours = out.get("hours") if isinstance(out.get("hours"), dict) else {}
    out["hours"] = {
        "start": int(hours.get("start", _DEFAULT["hours"]["start"])),
        "end": int(hours.get("end", _DEFAULT["hours"]["end"])),
    }
    out["workdays"] = [int(x) for x in (out.get("workdays") or _DEFAULT["workdays"])]
    out["lunch_hours"] = [int(x) for x in (out.get("lunch_hours") or _DEFAULT["lunch_hours"])]
    out["enforce_hours"] = bool(out.get("enforce_hours", True))
    vacs = out.get("vacations") if isinstance(out.get("vacations"), list) else []
    out["vacations"] = [v for v in vacs if isinstance(v, dict) and v.get("who")]
    alba = out.get("alba") if isinstance(out.get("alba"), list) else []
    cleaned = []
    for a in alba:
        if isinstance(a, dict) and a.get("id"):
            cleaned.append(a)
    out["alba"] = (cleaned or list(_DEFAULT_ALBA))[:MAX_ALBA]
    return out


def save(data: dict[str, Any]) -> None:
    SCHEDULE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = load()
    payload.update(data)
    tmp = SCHEDULE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(SCHEDULE_PATH)


def lunch_hours(now: float | None = None) -> tuple[int, ...]:
    return tuple(load()["lunch_hours"])


def is_workday(now: float | None = None) -> bool:
    data = load()
    lt = datetime.fromtimestamp(now if now is not None else datetime.now().timestamp())
    return int(lt.weekday()) in set(data["workdays"])


def is_work_hours(now: float | None = None) -> bool:
    """True during configured workday work window (lunch still counts as work hours)."""
    if not is_workday(now):
        return False
    data = load()
    lt = datetime.fromtimestamp(now if now is not None else datetime.now().timestamp())
    start, end = data["hours"]["start"], data["hours"]["end"]
    return start <= lt.hour < end


def off_reason(now: float | None = None) -> str:
    if is_work_hours(now):
        return ""
    if not is_workday(now):
        return "주말"
    return "퇴근"


# Short desk bubbles when closed — rotate by calendar day. No team-name prefix (plate already has it).
_TEAM_OFF_QUIPS: dict[str, tuple[str, ...]] = {
    "plan": (
        "내일 주제는 꿈에서",
        "회의록은 이불 속",
        "키워드도 퇴근",
        "제안서는 내일에게",
        "슬랙 OFF · 별 보기",
        "사장님도 쉬어요",
        "PR 제목은 아침에",
    ),
    "mkt": (
        "검색순위도 쉼",
        "GA4는 꿈나라로",
        "CTR보다 수면",
        "다듬기는 카페인 후",
        "이불이 더 핫해요",
        "로그는 내일 봐요",
        "키워드는 베개 위",
    ),
    "design": (
        "픽셀도 잠드는 중",
        "팔레트 · 밤하늘",
        "시안은 햇살에",
        "생성기 전원 OFF",
        "여백이 제일 예뻐요",
        "눈도 좀 쉬어요",
        "썸네일은 꿈 미리보기",
    ),
    "dev": (
        "빌드는 내일",
        "머지 버튼 퇴근함",
        "로그보다 이불",
        "배포는 CI에게",
        "핫픽스보다 핫초코",
        "터미널 닫아 주세요",
        "git status: sleeping",
    ),
}


def hours_notice(now: float | None = None) -> str:
    """Bar above the map when the office is closed (boss logged in or not)."""
    why = off_reason(now)
    if why == "퇴근":
        return "근무 시간이 끝났어요 · 사장님도 퇴근해 주세요"
    if why == "주말":
        return "오늘은 근무일이 아니에요 · 사무실을 비웠어요"
    return ""


def team_off_quip(team: str, now: float | None = None) -> str:
    """One fun sticky line for a team desk — changes every calendar day."""
    lines = _TEAM_OFF_QUIPS.get(team) or ("오늘도 수고했어요 · 내일 봐요",)
    day = _today(now).toordinal()
    idx = (day + zlib.crc32(team.encode("utf-8"))) % len(lines)
    return lines[idx]


def enforce_hours() -> bool:
    return bool(load().get("enforce_hours", True))


def _person_team(who: str) -> str:
    from app import office
    for p in office.ALL_PEOPLE:
        if p["id"] == who:
            return str(p.get("team") or "")
    return ""


def _person_office(who: str) -> str:
    from app import office
    for p in office.ALL_PEOPLE:
        if p["id"] == who:
            return str(p.get("office") or "")
    return ""


def active_vacations(now: float | None = None) -> list[dict[str, Any]]:
    day = _today(now)
    out = []
    for v in load()["vacations"]:
        a, b = _parse_day(str(v.get("from") or "")), _parse_day(str(v.get("to") or ""))
        if not a or not b or a > b:
            continue
        if a <= day <= b:
            out.append({
                "who": str(v["who"]),
                "from": a.isoformat(),
                "to": b.isoformat(),
                "note": str(v.get("note") or "")[:40],
                "team": _person_team(str(v["who"])),
                "office": _person_office(str(v["who"])),
            })
    return out


def on_vacation(who: str, now: float | None = None) -> dict[str, Any] | None:
    for v in active_vacations(now):
        if v["who"] == who:
            return v
    return None


def vacation_label(who: str, now: float | None = None) -> str:
    v = on_vacation(who, now)
    if not v:
        return ""
    note = v.get("note") or ""
    return f"휴가 · {note}" if note else "휴가"


def vacation_notice(office_id: str | None = None, now: float | None = None) -> str:
    """One Korean line for the map when someone on this floor is on vacation."""
    from app import office as _off

    oid = office_id or _off._here()
    bits: list[str] = []
    asg = alba_assignments(now)
    for v in active_vacations(now):
        if v.get("office") != oid:
            continue
        who = v["who"]
        name = _off.staff_by_role(who).get("name") or who
        note = (v.get("note") or "").strip()
        alba = asg.get(who)
        if alba:
            an = alba.get("name") or alba["id"]
            bits.append(f"{name}{' · ' + note if note else ''} → 알바 {an} 대행")
        else:
            bits.append(f"{name}{' · ' + note if note else ''} (대행 없음)")
    if not bits:
        return ""
    return "휴가 중 · " + " · ".join(bits)


def _overlaps(a0: date, a1: date, b0: date, b1: date) -> bool:
    return a0 <= b1 and b0 <= a1


def can_add_vacation(who: str, start: str, end: str) -> tuple[bool, str]:
    """Team may have at most one overlapping vacation (this person's own row is ignored)."""
    a, b = _parse_day(start), _parse_day(end)
    if not a or not b or a > b:
        return False, "휴가 날짜가 이상해요"
    team = _person_team(who)
    if not team:
        return False, "없는 사원이에요"
    for v in load()["vacations"]:
        if str(v.get("who")) == who:
            continue
        if _person_team(str(v.get("who"))) != team:
            continue
        c, d = _parse_day(str(v.get("from") or "")), _parse_day(str(v.get("to") or ""))
        if c and d and _overlaps(a, b, c, d):
            return False, f"{team} 팀은 동시에 휴가 1명만 가능해요"
    return True, ""


def set_vacation(who: str, start: str, end: str, note: str = "") -> str:
    """Add or replace this person's vacation. Enforces max 1 concurrent per team."""
    ok, why = can_add_vacation(who, start, end)
    if not ok:
        raise RuntimeError(why)
    data = load()
    vacs = [v for v in data["vacations"] if str(v.get("who")) != who]
    vacs.append({"who": who, "from": str(start)[:10], "to": str(end)[:10], "note": (note or "")[:40]})
    data["vacations"] = vacs
    save(data)
    return f"{who} 휴가 {start}~{end}"


def clear_vacation(who: str) -> bool:
    data = load()
    before = len(data["vacations"])
    data["vacations"] = [v for v in data["vacations"] if str(v.get("who")) != who]
    if len(data["vacations"]) == before:
        return False
    save(data)
    return True


def alba_pool() -> list[dict[str, Any]]:
    return list(load()["alba"])[:MAX_ALBA]


def is_alba(person: str) -> bool:
    return any(a["id"] == person for a in alba_pool())


def alba_by_id(person: str) -> dict[str, Any] | None:
    for a in alba_pool():
        if a["id"] == person:
            return dict(a)
    return None


def alba_assignments(now: float | None = None) -> dict[str, dict[str, Any]]:
    """who(on vacation) → alba dict covering them. At most MAX_ALBA concurrent covers."""
    vacs = active_vacations(now)
    pool = alba_pool()
    if not vacs or not pool:
        return {}
    # Stable order: by who id so polls don't reshuffle.
    vacs = sorted(vacs, key=lambda v: v["who"])
    used: set[str] = set()
    out: dict[str, dict[str, Any]] = {}
    for v in vacs:
        team = v.get("team") or ""
        candidates = [a for a in pool if a["id"] not in used and (not a.get("teams") or team in (a.get("teams") or []))]
        if not candidates:
            candidates = [a for a in pool if a["id"] not in used]
        if not candidates:
            break
        # Pick deterministically from candidates for this vacationer
        pick = candidates[zlib.crc32(f"{v['who']}:{v['from']}".encode()) % len(candidates)]
        used.add(pick["id"])
        out[v["who"]] = {**pick, "covering": v["who"], "covering_team": team,
                         "office": v.get("office") or "", "note": v.get("note") or ""}
    return out


def covering_for_team(team: str, now: float | None = None) -> dict[str, Any] | None:
    for who, alba in alba_assignments(now).items():
        if alba.get("covering_team") == team:
            return alba
    return None


def staff_row(alba: dict[str, Any]) -> dict[str, Any]:
    """Roster-shaped dict so maps/jobs can treat an alba like a person."""
    from app import office
    art = str(alba.get("art") or "emma")
    team = str(alba.get("covering_team") or (alba.get("teams") or ["plan"])[0])
    covered = str(alba.get("covering") or "")
    desk = dict(office.DESKS.get(covered) or {"x": 50, "y": 50})
    title = "알바"
    if covered:
        title = f"알바 · {office.staff_by_role(covered).get('name') or covered} 대행"
    return {
        "id": alba["id"], "sex": alba.get("sex") or "", "name": alba.get("name") or alba["id"],
        "name_ko": alba.get("name_ko") or "", "name_ja": alba.get("name_ja") or "",
        "name_en": alba.get("name_en") or "", "team": team, "title": title, "art": art,
        "lead": False, "office": alba.get("office") or office._here(), "seat": desk,
        "alba": True, "covering": covered,
    }


def available_members(team_ids: list[str] | tuple[str, ...], now: float | None = None) -> list[str]:
    """Team member ids minus vacation, plus covering alba id when present."""
    out = [p for p in team_ids if not on_vacation(p, now)]
    team = ""
    for p in team_ids:
        team = _person_team(p)
        if team:
            break
    alba = covering_for_team(team, now) if team else None
    if alba and alba["id"] not in out:
        out.append(alba["id"])
    return out
