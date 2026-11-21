"""채용 · 해고 (맥스 25 · 리더 해고 시 후임 필수).

data/staffing.json — 사장님이 팀·층을 고른 뒤 공고 → 확인/거절만.
알바(휴가 대행)는 맥스·명단 밖. B1 TF temp도 맥스에 포함(회사 인원).
"""

from __future__ import annotations

import json
import re
import time
import zlib
from copy import deepcopy
from typing import Any

from app.paths import HQ_ROOT

PATH = HQ_ROOT / "data" / "staffing.json"
MAX_STAFF = 25
TEAMS = ("plan", "mkt", "design", "dev")
OFFICES = ("datalab", "playground", "basement")
OFFICE_LABEL = {"datalab": "1층", "playground": "2층", "basement": "B1"}
TEAM_LABEL = {"plan": "기획", "mkt": "마케", "design": "디자인", "dev": "개발"}
TEAM_TITLE = {
    "plan": ("기획 리더", "기획"),
    "mkt": ("마케 리더", "마케팅"),
    "design": ("디자인 리더", "디자인"),
    "dev": ("개발 리더", "개발"),
}
# Reuse existing sprite art for new hires.
_ART_POOL = (
    ("f", "nari"), ("f", "hina"), ("f", "sora"), ("f", "yejin"), ("f", "mio"), ("f", "emma"), ("f", "ana"),
    ("m", "kaito"), ("m", "marco"), ("m", "hyunwoo"), ("m", "kenta"), ("m", "liam"), ("m", "wei"), ("m", "research"),
)
_NAME_POOL = (
    ("Aya", "아야", "アヤ", "Aya", "f"),
    ("Ken", "켄", "ケン", "Ken", "m"),
    ("Yuri", "유리", "ユリ", "Yuri", "f"),
    ("Jin", "진", "ジン", "Jin", "m"),
    ("Mika", "미카", "ミカ", "Mika", "f"),
    ("Leo", "레오", "レオ", "Leo", "m"),
    ("Suki", "스키", "スキ", "Suki", "f"),
    ("Otto", "오토", "オット", "Otto", "m"),
)


def _default() -> dict[str, Any]:
    return {"max": MAX_STAFF, "fired": [], "vacancies": [], "hired": [], "pending": [], "leads": {}}


def load() -> dict[str, Any]:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _default()
    except (OSError, json.JSONDecodeError):
        return _default()
    out = _default()
    # Cap is code-owned (MAX_STAFF); file may lag after bumps.
    out["max"] = MAX_STAFF
    out["fired"] = [str(x) for x in (data.get("fired") or []) if str(x).strip()]
    out["vacancies"] = [v for v in (data.get("vacancies") or []) if isinstance(v, dict) and v.get("id")]
    out["hired"] = [h for h in (data.get("hired") or []) if isinstance(h, dict) and h.get("id")]
    out["pending"] = [p for p in (data.get("pending") or []) if isinstance(p, dict) and p.get("id")]
    leads = data.get("leads") if isinstance(data.get("leads"), dict) else {}
    out["leads"] = {str(k): str(v) for k, v in leads.items() if str(k) in TEAMS and str(v).strip()}
    return out


def save(data: dict[str, Any]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = load()
    payload.update(data)
    payload["max"] = MAX_STAFF
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(PATH)


def _base_people() -> list[dict[str, Any]]:
    from app.office import STAFF
    return [deepcopy(p) for people in STAFF.values() for p in people]


def _apply_leads(people: list[dict[str, Any]], leads: dict[str, str]) -> None:
    """Company leads (non-temp). leads map overrides baked-in STAFF lead flags per team."""
    for p in people:
        if p.get("temp") or p.get("office") == "basement":
            p["lead"] = False
            continue
        team = p["team"]
        forced = leads.get(team)
        if not forced:
            continue
        p["lead"] = p["id"] == forced
        lead_title, member_title = TEAM_TITLE[team]
        p["title"] = lead_title if p["lead"] else member_title


def active_staff_dicts() -> list[dict[str, Any]]:
    """All active people (hired − fired) with lead overrides applied."""
    state = load()
    fired = set(state["fired"])
    people = [p for p in _base_people() if p["id"] not in fired]
    hired_ids = {p["id"] for p in people}
    for h in state["hired"]:
        if h["id"] in fired or h["id"] in hired_ids:
            continue
        row = deepcopy(h)
        row["hired"] = True
        people.append(row)
        hired_ids.add(row["id"])
    _apply_leads(people, state["leads"])
    return people


def active_staff(office_id: str) -> tuple[dict[str, Any], ...]:
    return tuple(p for p in active_staff_dicts() if p.get("office") == office_id)


def active_count() -> int:
    return len(active_staff_dicts())


def max_staff() -> int:
    return MAX_STAFF


def vacancies() -> list[dict[str, Any]]:
    return list(load()["vacancies"])


def pending() -> list[dict[str, Any]]:
    return list(load()["pending"])


def summary() -> dict[str, Any]:
    state = load()
    n = active_count()
    open_n = max(0, state["max"] - n)
    return {
        "max": state["max"],
        "count": n,
        "open": open_n,
        "vacancies": len(state["vacancies"]),
        "pending": state["pending"],
        "fired": state["fired"],
        "can_hire": open_n > 0,
        "can_fire": n > 4,
        "teams": [{"id": t, "label": TEAM_LABEL[t]} for t in TEAMS],
        "offices": [{"id": o, "label": OFFICE_LABEL[o]} for o in OFFICES if o != "basement"]
        + [{"id": "basement", "label": "B1 TF"}],
    }


def _person(who: str) -> dict[str, Any] | None:
    return next((p for p in active_staff_dicts() if p["id"] == who), None)


def _team_members(team: str, *, exclude: str = "") -> list[dict[str, Any]]:
    return [
        p for p in active_staff_dicts()
        if p.get("team") == team and not p.get("temp") and p["id"] != exclude
    ]


def successor_candidates(leader_id: str) -> list[dict[str, Any]]:
    lead = _person(leader_id)
    if not lead or not lead.get("lead"):
        return []
    return [p for p in _team_members(lead["team"], exclude=leader_id) if not p.get("lead")]


def _new_pending_id() -> str:
    return f"st{int(time.time())}{zlib.crc32(str(time.time_ns()).encode()) % 1000}"


def _pick_name(used: set[str]) -> tuple[str, str, str, str, str]:
    for row in _NAME_POOL:
        if row[3].lower() not in used and row[0].lower() not in used:
            return row
    n = len(used) + 1
    return (f"Staff{n}", f"사원{n}", f"スタッフ{n}", f"Staff{n}", "f")


def _pick_art(sex: str, used_art: set[str]) -> str:
    for s, art in _ART_POOL:
        if s == sex and art not in used_art:
            return art
    return "nari" if sex == "f" else "kaito"


def _seat_for(office: str, team: str) -> dict[str, Any]:
    """Pick a free-ish desk spot on that floor (hot desk, else near teammates)."""
    people = [p for p in active_staff_dicts() if p.get("office") == office]
    used = {
        (round(float(p["seat"].get("x", 0)), 1), round(float(p["seat"].get("y", 0)), 1))
        for p in people if isinstance(p.get("seat"), dict)
    }
    try:
        from app.office import LAYOUTS
        for d in (LAYOUTS.get(office) or {}).get("hot") or ():
            key = (round(float(d["x"]), 1), round(float(d["y"]), 1))
            if key not in used:
                return {"x": float(d["x"]), "y": float(d["y"]), **({} if "face" not in d else {"face": d["face"]})}
    except Exception:
        pass
    mates = [p for p in people if p.get("team") == team and isinstance(p.get("seat"), dict)]
    if mates:
        base = mates[-1]["seat"]
        bx, by = float(base.get("x", 50)), float(base.get("y", 40))
        for dx in (6.0, -6.0, 12.0, -12.0, 0.0):
            for dy in (0.0, 5.0, -5.0):
                cand = {"x": bx + dx, "y": by + dy}
                key = (round(cand["x"], 1), round(cand["y"], 1))
                if key not in used and 2 <= cand["x"] <= 98 and 5 <= cand["y"] <= 90:
                    if base.get("face"):
                        cand["face"] = base["face"]
                    return cand
    return {"x": 50.0, "y": 40.0}


def _match_vacancy(state: dict[str, Any], team: str, office: str) -> dict[str, Any] | None:
    exact = next((v for v in state["vacancies"] if v.get("team") == team and v.get("office") == office), None)
    if exact:
        return exact
    return next((v for v in state["vacancies"] if v.get("team") == team), None)


def propose_hire(*, team: str = "", office: str = "", why: str = "") -> dict[str, Any]:
    """사장님이 고른 팀·층으로 채용 공고(후보 포함 제안)를 큐에 담는다."""
    team = (team or "").strip()
    office = (office or "").strip() or "playground"
    if team not in TEAMS:
        return {"ok": False, "message": "팀(기획·마케·디자인·개발)을 골라 주세요"}
    if office not in OFFICES:
        return {"ok": False, "message": "층을 골라 주세요"}
    state = load()
    if active_count() >= state["max"]:
        return {"ok": False, "message": f"정원 {state['max']}명 · 더 채용할 수 없어요"}

    vac = _match_vacancy(state, team, office)
    used_names = {str(p.get("name_en") or p.get("name") or "").lower() for p in active_staff_dicts()}
    used_art = {str(p.get("art") or "") for p in active_staff_dicts()}
    name, name_ko, name_ja, name_en, sex = _pick_name(used_names)
    art = (vac or {}).get("art") or _pick_art(sex, used_art)
    _, member_title = TEAM_TITLE[team]
    temp = bool((vac or {}).get("temp")) or office == "basement"
    if temp:
        member_title = f"TF {TEAM_LABEL[team]}"
    seat = dict((vac or {}).get("seat") or _seat_for(office, team))
    place_office = str((vac or {}).get("office") or office)
    cid = f"hire_{re.sub(r'[^a-z0-9]+', '', name_en.lower())}_{int(time.time()) % 10000}"
    candidate = {
        "id": cid,
        "sex": sex,
        "name": name,
        "name_ko": name_ko,
        "name_ja": name_ja,
        "name_en": name_en,
        "team": team,
        "title": member_title,
        "art": art,
        "lead": False,
        "temp": temp,
        "office": place_office,
        "seat": seat,
        "hired": True,
    }
    vac_id = (vac or {}).get("id") or ""
    label = f"{OFFICE_LABEL.get(place_office, place_office)} {TEAM_LABEL[team]}"
    item = {
        "id": _new_pending_id(),
        "kind": "hire",
        "team": team,
        "office": place_office,
        "why": why or f"{label} 채용 공고",
        "vacancy_id": vac_id,
        "candidate": candidate,
        "at": time.strftime("%Y-%m-%d %H:%M"),
    }
    # one open hire posting at a time
    state["pending"] = [p for p in state["pending"] if p.get("kind") != "hire"] + [item]
    save(state)
    return {"ok": True, "message": f"공고 · {label} · {name_en}", "pending": item}


def propose_fire(who: str, *, successor: str = "", why: str = "") -> dict[str, Any]:
    person = _person(who)
    if not person:
        return {"ok": False, "message": "그 사원을 찾지 못했어요"}
    succ = (successor or "").strip()
    if person.get("lead"):
        cands = successor_candidates(who)
        if not cands:
            return {"ok": False, "message": "같은 팀에 리더로 올릴 사람이 없어요"}
        if not succ:
            succ = cands[0]["id"]
        if succ not in {c["id"] for c in cands}:
            return {"ok": False, "message": "후임은 같은 팀 사원이어야 해요"}
    elif succ:
        return {"ok": False, "message": "리더가 아니면 후임이 필요 없어요"}
    state = load()
    item = {
        "id": _new_pending_id(),
        "kind": "fire",
        "who": who,
        "who_name": person.get("name_en") or person.get("name"),
        "lead": bool(person.get("lead")),
        "team": person["team"],
        "successor": succ or "",
        "successor_name": (_person(succ) or {}).get("name_en") if succ else "",
        "why": why or ("리더 교체" if person.get("lead") else "인력 조정"),
        "at": time.strftime("%Y-%m-%d %H:%M"),
    }
    state["pending"] = [p for p in state["pending"] if not (p.get("kind") == "fire" and p.get("who") == who)] + [item]
    save(state)
    return {"ok": True, "message": f"해고 제안 · {item['who_name']}", "pending": item}


def propose_fire_auto() -> dict[str, Any]:
    """Pick a non-lead staffer to fire (prefer 2층 · larger teams)."""
    people = [p for p in active_staff_dicts() if not p.get("lead")]
    if not people:
        for lead in [p for p in active_staff_dicts() if p.get("lead")]:
            if successor_candidates(lead["id"]):
                return propose_fire(lead["id"], why="리더 교체 제안")
        return {"ok": False, "message": "해고할 사원이 없어요"}
    people.sort(key=lambda p: (0 if p.get("office") == "playground" else 1, p.get("hired") is not True, p["id"]))
    return propose_fire(people[0]["id"], why="인력 조정 제안")


def reject(pending_id: str) -> dict[str, Any]:
    state = load()
    before = len(state["pending"])
    state["pending"] = [p for p in state["pending"] if p.get("id") != pending_id]
    if len(state["pending"]) == before:
        return {"ok": False, "message": "제안을 찾지 못했어요"}
    save(state)
    return {"ok": True, "message": "거절했어요"}


def approve(pending_id: str, *, successor: str = "") -> dict[str, Any]:
    state = load()
    item = next((p for p in state["pending"] if p.get("id") == pending_id), None)
    if not item:
        return {"ok": False, "message": "제안을 찾지 못했어요"}
    if item["kind"] == "hire":
        return _approve_hire(state, item)
    if item["kind"] == "fire":
        return _approve_fire(state, item, successor=successor or item.get("successor") or "")
    return {"ok": False, "message": "모르는 제안이에요"}


def _approve_hire(state: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    if active_count() >= state["max"]:
        return {"ok": False, "message": f"정원 {state['max']}명이에요"}
    vac_id = item.get("vacancy_id") or ""
    vac = next((v for v in state["vacancies"] if v.get("id") == vac_id), None) if vac_id else None
    cand = deepcopy(item.get("candidate") or {})
    if not cand.get("id"):
        return {"ok": False, "message": "후보가 없어요"}
    if vac:
        cand["office"] = vac.get("office") or cand.get("office")
        cand["team"] = vac.get("team") or cand.get("team")
        cand["seat"] = vac.get("seat") or cand.get("seat")
        cand["temp"] = bool(vac.get("temp"))
        state["vacancies"] = [v for v in state["vacancies"] if v.get("id") != vac_id]
    cand["lead"] = False
    cand["hired"] = True
    if not cand.get("seat"):
        cand["seat"] = _seat_for(str(cand.get("office") or "playground"), str(cand.get("team") or "plan"))
    state["hired"] = [h for h in state["hired"] if h.get("id") != cand["id"]] + [cand]
    state["pending"] = [p for p in state["pending"] if p.get("id") != item["id"]]
    save(state)
    _refresh_office()
    name = cand.get("name_en") or cand.get("name")
    label = f"{OFFICE_LABEL.get(cand.get('office'), '')} {TEAM_LABEL.get(cand.get('team'), '')}".strip()
    return {"ok": True, "message": f"{name}을(를) 채용했어요 · {label} · {active_count()}/{state['max']}명"}


def _approve_fire(state: dict[str, Any], item: dict[str, Any], *, successor: str) -> dict[str, Any]:
    who = str(item.get("who") or "")
    person = _person(who)
    if not person:
        return {"ok": False, "message": "이미 없는 사원이에요"}
    if person.get("lead"):
        cands = {c["id"] for c in successor_candidates(who)}
        if not successor or successor not in cands:
            return {"ok": False, "message": "리더 해고는 후임을 지정해야 해요"}
        state["leads"][person["team"]] = successor
    vac = {
        "id": f"vac_{who}",
        "from": who,
        "office": person.get("office"),
        "team": person.get("team"),
        "seat": person.get("seat"),
        "art": person.get("art"),
        "sex": person.get("sex"),
        "title": person.get("title"),
        "temp": bool(person.get("temp")),
    }
    state["vacancies"] = [v for v in state["vacancies"] if v.get("id") != vac["id"] and v.get("from") != who] + [vac]
    state["fired"] = list(dict.fromkeys([*state["fired"], who]))
    state["hired"] = [h for h in state["hired"] if h.get("id") != who]
    state["pending"] = [p for p in state["pending"] if p.get("id") != item["id"]]
    save(state)
    _refresh_office()
    name = person.get("name_en") or person.get("name")
    msg = f"{name}을(를) 해고했어요"
    if person.get("lead") and successor:
        succ = _person(successor) or {}
        msg += f" · 새 리더 {(succ.get('name_en') or successor)}"
    msg += f" · {active_count()}/{state['max']}명"
    return {"ok": True, "message": msg}


def _refresh_office() -> None:
    from app import office
    office.rebuild_staff_caches()
