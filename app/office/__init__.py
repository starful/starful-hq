"""Virtual office for Starful HQ (starful-hq). Who is working, not which tool ran.

Each floor (1층 Data Lab · 2층 Playground) has its own people, one desk each. Several can work at the same time (jobs are per person).
Coordinates are percent of the map box (x, y) and are the feet position of a character.
"""

from __future__ import annotations
from app.paths import APP_DIR, HQ_ROOT

import json
import random
import time
import zlib
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Any

from app import sites
ROOT = HQ_ROOT  # starful-hq/ (data lives next to app/)
STATIC = APP_DIR / "static"  # code assets (floor art, sprites) live under app/
EVENTS_PATH = ROOT / "data" / "office_events.jsonl"
FLOW_PATH = sites.data("git_flow.json")        # per site (okpy: data/git_flow.json)
RESEARCH_PATH = sites.data("research.json")

# Coordinates match static/office/floor.svg (top-down office plan, 2400x2000). Feet position, percent.
#
# Four teams. Work belongs to a team; whoever on the team is free takes it.
#   기획팀  (plan)   리서치 · 글쓰기 · 이슈·PR        — window desk on the left
#   마케팀  (mkt)    클릭 낮은 글(SEO) · 글 다듬기     — middle window desk
#   디자인팀 (design) 이미지                          — right window desk
#   개발팀  (dev)    리뷰·머지 · git 푸시 · Cloud Build — middle desk block (리더/프론트/백엔드)
# 1층 Data Lab
_STAFF_1F = (
    # id, names (한국어 · 日本語 · English), team, title, art (sprite prefix), team leader, seat
    {"id": "seoyeon", "sex": "f", "name": "Seoyeon", "name_ko": "서연", "name_ja": "ソヨン", "name_en": "Seoyeon", "team": "plan", "title": "기획 리더", "art": "write", "lead": True, "seat": {"x": 10.0, "y": 14.85}},
    {"id": "jiho", "sex": "m", "name": "Haruto", "name_ko": "하루토", "name_ja": "陽翔", "name_en": "Haruto", "team": "plan", "title": "기획", "art": "research", "lead": False, "seat": {"x": 16.25, "y": 14.85}},
    {"id": "yuna", "sex": "f", "name": "Yui", "name_ko": "유이", "name_ja": "結衣", "name_en": "Yui", "team": "plan", "title": "기획", "art": "issue", "lead": False, "seat": {"x": 22.5, "y": 14.85}},
    {"id": "minjae", "sex": "m", "name": "Minjae", "name_ko": "민재", "name_ja": "ミンジェ", "name_en": "Minjae", "team": "mkt", "title": "마케 리더", "art": "search", "lead": True, "seat": {"x": 13.12, "y": 24.25, "face": "up"}},
    {"id": "subin", "sex": "f", "name": "Sakura", "name_ko": "사쿠라", "name_ja": "さくら", "name_en": "Sakura", "team": "mkt", "title": "마케팅", "art": "search3", "lead": False, "seat": {"x": 19.38, "y": 24.25, "face": "up"}},
    {"id": "haneul", "sex": "f", "name": "Haneul", "name_ko": "하늘", "name_ja": "ハヌル", "name_en": "Haneul", "team": "design", "title": "디자인 리더", "art": "images", "lead": True, "seat": {"x": 42.29, "y": 15.8}},
    {"id": "mio", "sex": "f", "name": "Mio", "name_ko": "미오", "name_ja": "美緒", "name_en": "Mio", "team": "design", "title": "디자인", "art": "mio", "lead": False, "seat": {"x": 48.54, "y": 15.8}},
    {"id": "daeun", "sex": "f", "name": "Daeun", "name_ko": "다은", "name_ja": "ダウン", "name_en": "Daeun", "team": "dev", "title": "개발 리더", "art": "review", "lead": True, "seat": {"x": 12.08, "y": 44.6}},
    {"id": "hajun", "sex": "m", "name": "Ren", "name_ko": "렌", "name_ja": "蓮", "name_en": "Ren", "team": "dev", "title": "개발", "art": "write3", "lead": False, "seat": {"x": 18.33, "y": 44.6}},
    {"id": "doyun", "sex": "m", "name": "Sota", "name_ko": "소타", "name_ja": "颯太", "name_en": "Sota", "team": "dev", "title": "개발", "art": "ship", "lead": False, "seat": {"x": 24.58, "y": 44.6}},
    {"id": "liam", "sex": "m", "name": "Liam", "name_ko": "리암", "name_ja": "リアム", "name_en": "Liam", "team": "dev", "title": "개발", "art": "liam", "lead": False, "seat": {"x": 12.08, "y": 54.0, "face": "up"}},
    {"id": "priya", "sex": "f", "name": "Priya", "name_ko": "프리야", "name_ja": "プリヤ", "name_en": "Priya", "team": "dev", "title": "개발", "art": "priya", "lead": False, "seat": {"x": 18.33, "y": 54.0, "face": "up"}},
    {"id": "wei", "sex": "m", "name": "Wei", "name_ko": "웨이", "name_ja": "ウェイ", "name_en": "Wei", "team": "dev", "title": "개발", "art": "wei", "lead": False, "seat": {"x": 24.58, "y": 54.0, "face": "up"}},
)
# 2층 Playground: its own people (jobs are per person, so the two floors never wait for each other).
# Their own floor plan (static/office/floor-2.svg): seats below are percent of that picture.
_STAFF_2F = (
    {"id": "nari", "sex": "f", "name": "Nari", "name_ko": "나리", "name_ja": "ナリ", "name_en": "Nari", "team": "plan", "title": "기획 리더", "art": "nari", "lead": True, "seat": {"x": 34.17, "y": 35.8}},
    {"id": "kaito", "sex": "m", "name": "Kaito", "name_ko": "카이토", "name_ja": "海斗", "name_en": "Kaito", "team": "plan", "title": "기획", "art": "kaito", "lead": False, "seat": {"x": 40.42, "y": 35.8}},
    {"id": "emma", "sex": "f", "name": "Emma", "name_ko": "엠마", "name_ja": "エマ", "name_en": "Emma", "team": "plan", "title": "기획", "art": "emma", "lead": False, "seat": {"x": 46.67, "y": 35.8}},
    {"id": "hina", "sex": "f", "name": "Hina", "name_ko": "히나", "name_ja": "陽菜", "name_en": "Hina", "team": "mkt", "title": "마케 리더", "art": "hina", "lead": True, "seat": {"x": 37.29, "y": 45.2, "face": "up"}},
    {"id": "marco", "sex": "m", "name": "Marco", "name_ko": "마르코", "name_ja": "マルコ", "name_en": "Marco", "team": "mkt", "title": "마케팅", "art": "marco", "lead": False, "seat": {"x": 43.54, "y": 45.2, "face": "up"}},
    {"id": "sora", "sex": "f", "name": "Sora", "name_ko": "소라", "name_ja": "そら", "name_en": "Sora", "team": "design", "title": "디자인 리더", "art": "sora", "lead": True, "seat": {"x": 59.17, "y": 35.8}},
    {"id": "yejin", "sex": "f", "name": "Yejin", "name_ko": "예진", "name_ja": "イェジン", "name_en": "Yejin", "team": "design", "title": "디자인", "art": "yejin", "lead": False, "seat": {"x": 65.42, "y": 35.8}},
    {"id": "hyunwoo", "sex": "m", "name": "Hyunwoo", "name_ko": "현우", "name_ja": "ヒョヌ", "name_en": "Hyunwoo", "team": "dev", "title": "개발 리더", "art": "hyunwoo", "lead": True, "seat": {"x": 81.67, "y": 35.8}},
    {"id": "kenta", "sex": "m", "name": "Kenta", "name_ko": "켄타", "name_ja": "健太", "name_en": "Kenta", "team": "dev", "title": "개발", "art": "kenta", "lead": False, "seat": {"x": 87.92, "y": 35.8}},
    {"id": "ana", "sex": "f", "name": "Ana", "name_ko": "아나", "name_ja": "アナ", "name_en": "Ana", "team": "dev", "title": "개발", "art": "ana", "lead": False, "seat": {"x": 94.17, "y": 35.8}},
)
# B1 Back Office has no people of its own: whoever is free upstairs comes down for its work (see ordered_members).
STAFF = {"datalab": _STAFF_1F, "playground": _STAFF_2F, "basement": ()}
for _office, _people in STAFF.items():
    for _p in _people:
        _p["office"] = _office
ALL_PEOPLE = tuple(p for people in STAFF.values() for p in people)


def _here() -> str:
    return sites.current().office


class _PerOffice(Sequence):
    """A tuple that is the current office's (sites.current()): office.PEOPLE on 2층 is 2층's people."""

    def __init__(self, build):
        self._build, self._cache = build, {}

    def _now(self) -> tuple:
        o = _here()
        if o not in self._cache:
            self._cache[o] = tuple(self._build(o))
        return self._cache[o]

    def __getitem__(self, i):
        return self._now()[i]

    def __len__(self):
        return len(self._now())

    def __iter__(self):
        return iter(self._now())

    def __contains__(self, x):
        return x in self._now()

    def __eq__(self, other):
        return tuple(self._now()) == tuple(other)

    def __repr__(self):
        return repr(self._now())


class _PerOfficeMap(Mapping):
    """A dict of the current office (iterating, `in`); looking a person up by id works on any floor."""

    def __init__(self, build):
        self._build, self._cache = build, {}

    def _of(self, o: str) -> dict:
        if o not in self._cache:
            self._cache[o] = dict(self._build(o))
        return self._cache[o]

    def _now(self) -> dict:
        return self._of(_here())

    def __getitem__(self, k):
        now = self._now()
        if k in now:
            return now[k]
        for o in STAFF:
            if k in self._of(o):
                return self._of(o)[k]
        raise KeyError(k)

    def __iter__(self):
        return iter(self._now())

    def __len__(self):
        return len(self._now())

    def __contains__(self, k):
        return k in self._now()

    def __repr__(self):
        return repr(self._now())


PEOPLE = _PerOffice(lambda o: STAFF.get(o, ()))
TEAM_INFO = {
    "plan": {"name": "Planning", "work": "리서치 · 글 · 이슈·PR · 제안", "step": "write", "plate": {"x": 2.17, "y": 8.5}},
    "mkt": {"name": "Marketing", "work": "검색 · 다듬기 · GA4·GSC 분석", "step": "search", "plate": {"x": 2.17, "y": 29.6}},
    "design": {"name": "Design", "work": "이미지", "step": "images", "plate": {"x": 33.0, "y": 29.6}},
    "dev": {"name": "Dev", "work": "리뷰·머지 · 공통화·소스정리 · git · Cloud Build", "step": "ship", "plate": {"x": 2.17, "y": 61.6}},
}
TEAMS = _PerOfficeMap(lambda o: {t: tuple(p["id"] for p in STAFF.get(o, ()) if p["team"] == t) for t in TEAM_INFO})
LEADERS = _PerOffice(lambda o: (p["id"] for p in STAFF.get(o, ()) if p.get("lead")))


def full_name(p: dict) -> str:
    """Names are shown in English only (Korean/Japanese stay as data: name_ko, name_ja)."""
    return p.get("name_en") or p.get("name") or ""
TASK_TEAM = {"research": "plan", "write": "plan", "issue": "plan", "propose": "plan", "search": "mkt", "polish": "mkt",
             "analyze": "mkt", "fix": "mkt", "fix_code": "dev",
             "refactor": "dev", "refactor_front": "dev", "refactor_back": "dev", "optimize": "dev", "images": "design", "review": "dev", "push": "dev", "deploy": "dev"}
# Work of the same group is done by one person at a time (start_task refuses a second one).
# Item tasks (write / search / polish) are split instead: only the same post/URL is refused.
TASK_GROUP = {"refactor": "refactor", "refactor_front": "refactor", "refactor_back": "refactor", "optimize": "refactor",
              "issue": "pr", "review": "pr", "fix": "pr", "fix_code": "pr", "push": "pr", "merge": "pr", "ship": "deploy"}
ITEM_TASKS = ("write", "search", "polish")
# Work you may do but don't have to (topic ideas, search fixes): listed as options, nobody waits at a desk for it.
OPTIONAL_TASKS = ("write", "search")


def task_group(task: str) -> str:
    return TASK_GROUP.get(task, task)


# A task can move the person somewhere else while they do it (research at the trend board).
_TASK_SEAT_1F = {"research": {"x": 5.0, "y": 9.0}, "review": {"x": 86.25, "y": 54.8}, "propose": {"x": 90.0, "y": 54.8}, "analyze": {"x": 93.75, "y": 54.8}}
# Chairs around the meeting table for the all-hands proposal meeting (north row first).
_MEETING_1F = ({"x": 6.46, "y": 81.3}, {"x": 10.21, "y": 81.3}, {"x": 13.96, "y": 81.3}, {"x": 17.71, "y": 81.3}, {"x": 21.46, "y": 81.3}, {"x": 25.21, "y": 81.3}, {"x": 6.46, "y": 88.6, "face": "up"}, {"x": 10.21, "y": 88.6, "face": "up"}, {"x": 13.96, "y": 88.6, "face": "up"}, {"x": 17.71, "y": 88.6, "face": "up"}, {"x": 21.46, "y": 88.6, "face": "up"}, {"x": 25.21, "y": 88.6, "face": "up"}, {"x": 2.71, "y": 84.0}, {"x": 29.17, "y": 84.0},)
# Leaders' meeting room (회의실 B)
_LEADERS_1F = ({"x": 86.25, "y": 35.8}, {"x": 90.0, "y": 35.8}, {"x": 93.75, "y": 35.8}, {"x": 86.25, "y": 41.4, "face": "up"}, {"x": 90.0, "y": 41.4, "face": "up"}, {"x": 93.75, "y": 41.4, "face": "up"},)
# CEO office: Taekki at the head of the table, up to five people called in (사장실 호출)
BOSS_TABLE = ({"x": 86.88, "y": 14.3}, {"x": 90.62, "y": 14.3}, {"x": 94.38, "y": 14.3}, {"x": 86.88, "y": 19.9, "face": "up"}, {"x": 90.62, "y": 19.9, "face": "up"}, {"x": 94.38, "y": 19.9, "face": "up"},)
# 회의실 C: team meetings (one team at a time)
_MEET3_1F = ({"x": 86.25, "y": 54.8}, {"x": 90.0, "y": 54.8}, {"x": 93.75, "y": 54.8}, {"x": 86.25, "y": 60.4, "face": "up"}, {"x": 90.0, "y": 60.4, "face": "up"}, {"x": 93.75, "y": 60.4, "face": "up"},)
BOSS_DESK = {"x": 76.25, "y": 13.3}
# Break time. Idle people go to one of these spots (feet, percent of floor.svg 2400x2000).
_REST_1F = {
    "chat": ({"x": 35.0, "y": 94.0, "face": "right"}, {"x": 39.17, "y": 94.0, "face": "right"}, {"x": 43.33, "y": 94.0, "face": "left"}, {"x": 47.5, "y": 94.0, "face": "right"}, {"x": 51.67, "y": 94.0, "face": "left"}, {"x": 55.42, "y": 86.0, "face": "left"},),
    "coffee": ({"x": 61.25, "y": 77.0, "face": ""}, {"x": 65.83, "y": 77.0, "face": ""}, {"x": 70.42, "y": 77.0, "face": ""}, {"x": 75.0, "y": 77.0, "face": ""},),
    "snack": ({"x": 38.33, "y": 78.8, "face": ""}, {"x": 43.33, "y": 78.8, "face": ""}, {"x": 48.33, "y": 78.8, "face": ""}, {"x": 38.33, "y": 85.0, "face": "up"}, {"x": 43.33, "y": 85.0, "face": "up"}, {"x": 48.33, "y": 85.0, "face": "up"},),
    "game": ({"x": 50.21, "y": 50.5, "face": "right"}, {"x": 63.96, "y": 50.5, "face": "left"}, {"x": 43.54, "y": 45.0, "face": "up"}, {"x": 48.12, "y": 45.0, "face": "up"}, {"x": 52.71, "y": 45.0, "face": "up"}, {"x": 67.5, "y": 58.5, "face": ""}, {"x": 71.67, "y": 55.0, "face": ""}, {"x": 68.75, "y": 50.5, "face": ""},),
    "smoke": ({"x": 89.58, "y": 94.0, "face": "right"}, {"x": 93.75, "y": 94.0, "face": "left"}, {"x": 97.08, "y": 94.0, "face": "left"},),
    "wc_m": ({"x": 87.5, "y": 76.0, "face": ""}, {"x": 90.0, "y": 76.0, "face": ""},),
    "wc_f": ({"x": 94.58, "y": 76.0, "face": ""}, {"x": 97.92, "y": 76.0, "face": ""},),
}
_LAYOUT_1F = {
    "task_seat": _TASK_SEAT_1F, "meeting": _MEETING_1F, "leaders": _LEADERS_1F, "meet3": _MEET3_1F, "rest": _REST_1F,
    "plates": {"plan": {"x": 2.17, "y": 8.5}, "mkt": {"x": 2.17, "y": 29.6}, "design": {"x": 33.0, "y": 29.6}, "dev": {"x": 2.17, "y": 61.6}},
    "floor": "", "front": "", "nav": "office/nav.json",
    "hot": ({"x": 64.17, "y": 9.8}, {"x": 64.17, "y": 20.8}, {"x": 54.17, "y": 64.3}),   # Free desks (floor.svg): work away from one's own desk happens here
}

# 2층 Playground floor plan (static/office/floor-2.svg, nav-2.json). Only the CEO room sits in the same corner as 1층.
_LAYOUT_2F = {
    "hot": ({"x": 33.33, "y": 59.8}, {"x": 33.33, "y": 70.8}, {"x": 89.58, "y": 68.3}),
    "task_seat": {"research": {"x": 29.17, "y": 36.0}, "review": {"x": 7.92, "y": 55.8}, "propose": {"x": 11.67, "y": 55.8}, "analyze": {"x": 15.42, "y": 55.8}},
    "meeting": ({"x": 6.46, "y": 13.3}, {"x": 10.21, "y": 13.3}, {"x": 13.96, "y": 13.3}, {"x": 17.71, "y": 13.3}, {"x": 21.46, "y": 13.3}, {"x": 25.21, "y": 13.3}, {"x": 6.46, "y": 20.6, "face": "up"}, {"x": 10.21, "y": 20.6, "face": "up"}, {"x": 13.96, "y": 20.6, "face": "up"}, {"x": 17.71, "y": 20.6, "face": "up"}, {"x": 21.46, "y": 20.6, "face": "up"}, {"x": 25.21, "y": 20.6, "face": "up"}, {"x": 2.71, "y": 16.0}, {"x": 29.17, "y": 16.0},),
    "leaders": ({"x": 7.92, "y": 36.8}, {"x": 11.67, "y": 36.8}, {"x": 15.42, "y": 36.8}, {"x": 7.92, "y": 42.4, "face": "up"}, {"x": 11.67, "y": 42.4, "face": "up"}, {"x": 15.42, "y": 42.4, "face": "up"},),
    "meet3": ({"x": 7.92, "y": 55.8}, {"x": 11.67, "y": 55.8}, {"x": 15.42, "y": 55.8}, {"x": 7.92, "y": 61.4, "face": "up"}, {"x": 11.67, "y": 61.4, "face": "up"}, {"x": 15.42, "y": 61.4, "face": "up"},),
    "rest": {
        "chat": ({"x": 35.0, "y": 85.5, "face": ""}, {"x": 40.83, "y": 85.5, "face": ""}, {"x": 46.67, "y": 85.5, "face": ""}, {"x": 52.5, "y": 85.5, "face": ""}, {"x": 60.42, "y": 90.0, "face": "left"}, {"x": 30.42, "y": 90.0, "face": "right"},),
        "coffee": ({"x": 61.25, "y": 14.0, "face": ""}, {"x": 65.83, "y": 14.0, "face": ""}, {"x": 61.25, "y": 20.0, "face": ""}, {"x": 65.83, "y": 20.0, "face": ""},),
        "snack": ({"x": 36.67, "y": 14.5, "face": "up"}, {"x": 40.83, "y": 14.5, "face": "up"}, {"x": 45.0, "y": 14.5, "face": "up"}, {"x": 49.17, "y": 14.5, "face": "up"}, {"x": 53.33, "y": 14.5, "face": "up"}, {"x": 41.04, "y": 24.0, "face": "right"},),
        "game": ({"x": 45.42, "y": 62.5, "face": "right"}, {"x": 52.08, "y": 61.0, "face": ""}, {"x": 59.17, "y": 63.0, "face": "left"}, {"x": 49.17, "y": 69.0, "face": "up"}, {"x": 55.83, "y": 69.5, "face": "up"}, {"x": 65.0, "y": 69.0, "face": "left"}, {"x": 70.83, "y": 65.0, "face": ""}, {"x": 38.75, "y": 67.5, "face": "right"},),
        "smoke": ({"x": 89.58, "y": 94.0, "face": "right"}, {"x": 93.75, "y": 94.0, "face": "left"}, {"x": 97.08, "y": 94.0, "face": "left"},),
        "wc_m": ({"x": 19.58, "y": 70.5, "face": ""}, {"x": 22.92, "y": 70.5, "face": ""},),
        "wc_f": ({"x": 19.58, "y": 77.5, "face": ""}, {"x": 22.92, "y": 77.5, "face": ""},),
    },
    "plates": {"plan": {"x": 28.33, "y": 32.5}, "mkt": {"x": 28.33, "y": 51.5}, "design": {"x": 54.83, "y": 51.5}, "dev": {"x": 75.25, "y": 51.5}},
    "floor": "office/floor-2.svg", "front": "office/floor-front-2.svg", "nav": "office/nav-2.json",
}

# B1 Back Office (static/office/floor-b1.svg, nav-b1.json): a smaller floor with only a workroom, a lounge,
# restrooms and a smoking room — no meeting rooms, no CEO room. Nobody is based here (STAFF['basement'] is empty):
# a free person of the right team comes down the stairs (entry), works at a free desk (hot) and goes back up.
_LAYOUT_B1 = {
    "borrow": True, "max_visitors": 2, "entry": {"x": 20.08, "y": 72.0},
    "hot": tuple([{"x": 18.96, "y": 24.3}, {"x": 18.96, "y": 33.7, "face": "up"}, {"x": 25.21, "y": 24.3}, {"x": 25.21, "y": 33.7, "face": "up"}, {"x": 31.46, "y": 24.3}, {"x": 31.46, "y": 33.7, "face": "up"}, {"x": 18.96, "y": 41.8}, {"x": 18.96, "y": 51.2, "face": "up"}, {"x": 25.21, "y": 41.8}, {"x": 25.21, "y": 51.2, "face": "up"}, {"x": 31.46, "y": 41.8}, {"x": 31.46, "y": 51.2, "face": "up"}]),
    "task_seat": {}, "meeting": (), "leaders": (), "meet3": (),
    "rest": {k: tuple(v) for k, v in {"chat": [{"x": 65.0, "y": 27.0, "face": "right"}, {"x": 68.33, "y": 32.5, "face": "up"}, {"x": 75.0, "y": 26.0, "face": "left"}, {"x": 75.83, "y": 20.0, "face": "left"}, {"x": 62.5, "y": 30.0, "face": "right"}, {"x": 70.83, "y": 32.5, "face": "up"}], "coffee": [{"x": 62.92, "y": 20.0}, {"x": 67.92, "y": 20.0}, {"x": 74.17, "y": 19.0}], "snack": [{"x": 66.67, "y": 32.0, "face": "up"}, {"x": 70.83, "y": 32.0, "face": "up"}], "game": [{"x": 65.0, "y": 27.0, "face": "right"}, {"x": 68.33, "y": 32.5, "face": "up"}, {"x": 75.0, "y": 26.0, "face": "left"}, {"x": 75.83, "y": 20.0, "face": "left"}, {"x": 62.5, "y": 30.0, "face": "right"}, {"x": 70.83, "y": 32.5, "face": "up"}], "smoke": [{"x": 79.79, "y": 52.1, "face": "right"}, {"x": 84.79, "y": 52.1, "face": "left"}, {"x": 82.29, "y": 56.1, "face": "up"}], "wc_m": [{"x": 62.58, "y": 48.6}, {"x": 66.75, "y": 48.6}], "wc_f": [{"x": 62.58, "y": 61.55}, {"x": 66.75, "y": 61.55}]}.items()},
    "plates": {"mkt": {"x": 11.75, "y": 19.0}, "design": {"x": 11.75, "y": 36.5}, "dev": {"x": 34.58, "y": 53.0}},
    "floor": "office/floor-b1.svg", "front": "office/floor-front-b1.svg", "nav": "office/nav-b1.json",
}
LAYOUTS = {"datalab": _LAYOUT_1F, "playground": _LAYOUT_2F, "basement": _LAYOUT_B1}
# Work of a borrowed floor goes to this team instead (B1 has no planners: the dev team opens its PRs).
BORROW_TEAM = {"plan": "dev"}


def is_borrowed(office_id: str | None = None) -> bool:
    """A floor without its own people (B1): its work is done by people who come down from the other floors."""
    return bool(layout(office_id).get("borrow"))


def _job_office(j: dict[str, Any]) -> str:
    sid = str(j.get("site") or sites.DEFAULT_SITE)
    return sites.SITES[sid].office if sid in sites.SITES else ""


def visitors(office_id: str | None = None, running: dict[str, dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """{person: job} of the people working on a borrowed floor right now (meetings don't count)."""
    o = office_id or _here()
    if running is None:
        from app.office import jobs
        running = _jobs_by_person(jobs.all_jobs())
    return {p: j for p, j in running.items()
            if _job_office(j) == o and not str(j.get("task") or "").startswith("meeting")}


def layout(office_id: str | None = None) -> dict[str, Any]:
    return LAYOUTS.get(office_id or _here(), _LAYOUT_1F)


TASK_SEAT = _PerOfficeMap(lambda o: layout(o)["task_seat"])   # old spots (boards / meeting room C): no longer used for work
HOT_DESKS = _PerOffice(lambda o: layout(o).get("hot") or ())
# Work done away from one's own desk (reading trends, reviewing a PR, analysis, proposals) goes to a Free desk —
# never a meeting room; meeting rooms are for meetings only. No Free desk left: at one's own desk.
AWAY_TASKS = ("research", "review", "propose", "analyze")


def work_seat(task: str, person: str, taken: list[dict] | tuple = ()) -> dict | None:
    """Where this person sits for this task: a free Free desk for AWAY_TASKS, else None (= own desk)."""
    if task not in AWAY_TASKS and not is_borrowed():   # B1: nobody has a desk there, everyone takes a free one
        return None
    used = {(round(float(t.get("x", -1)), 2), round(float(t.get("y", -1)), 2)) for t in taken if isinstance(t, dict)}
    for d in HOT_DESKS:
        if (round(d["x"], 2), round(d["y"], 2)) not in used:
            return dict(d)
    return None
MEETING_SEATS = _PerOffice(lambda o: layout(o)["meeting"])
LEADER_SEATS = _PerOffice(lambda o: layout(o)["leaders"])
MEET_C_SEATS = _PerOffice(lambda o: layout(o)["meet3"])
REST_SPOTS = _PerOfficeMap(lambda o: layout(o)["rest"])
PLATES = _PerOfficeMap(lambda o: layout(o)["plates"])
REST_LABEL = {"chat": "💬 수다", "coffee": "☕ 탕비실", "smoke": "🚬 담배", "snack": "🍪 간식", "lunch": "🍱 점심", "sofa": "🛋️ 쉬는 중", "wc": "🚻 화장실", "lib": "📚 독서", "desk": "💻 자리에서 공부", "game": "🎮 게임"}
REST_WEIGHT = (("desk", 5), ("chat", 3), ("coffee", 3), ("game", 3), ("snack", 2), ("smoke", 3), ("wc", 1))
SMOKERS = frozenset({"minjae", "hajun", "doyun", "marco", "kenta"})
LUNCH_HOURS = (12,)  # local time: everyone free goes to lunch
REST_SLOT_SEC = 300  # each person changes what they do every ~5 minutes (staggered)
def _chat(o: str) -> tuple:
    return layout(o)["rest"]["chat"]


LOUNGE = _PerOfficeMap(lambda o: {p["id"]: dict(_chat(o)[i % len(_chat(o))]) for i, p in enumerate(STAFF.get(o, ()))})


PEOPLE_SEAT = _PerOfficeMap(lambda o: {p["id"]: p["seat"] for p in STAFF.get(o, ())})
PEOPLE_SEX = _PerOfficeMap(lambda o: {p["id"]: p.get("sex", "") for p in STAFF.get(o, ())})


def rest_plan(idle: list[str] | tuple[str, ...], now: float | None = None) -> dict[str, dict[str, Any]]:
    """Where each idle person spends the break: chat, coffee, smoke, snack, or lunch at noon.

    Deterministic for a time slot, so every poll and every page agrees. Not a random walk:
    people only switch between fixed spots.
    """
    now = time.time() if now is None else now
    order = [p["id"] for p in PEOPLE if p["id"] in idle]
    taken: dict[str, int] = {k: 0 for k in REST_SPOTS}
    out: dict[str, dict[str, Any]] = {}

    def put(person: str, act: str, label_act: str | None = None) -> bool:
        if act == "desk":  # stays at own desk (reading, studying): no shared spot to take
            out[person] = {"face": "", **PEOPLE_SEAT[person], "act": "desk", "label": REST_LABEL["desk"]}
            return True
        key = act
        if act == "wc":  # restrooms are split: men / women
            key = "wc_f" if PEOPLE_SEX.get(person) == "f" else "wc_m"
        spots = REST_SPOTS[key]
        if taken[key] >= len(spots):
            return False
        spot = spots[taken[key]]
        taken[key] += 1
        out[person] = {**spot, "act": label_act or act, "label": REST_LABEL[label_act or act]}
        return True

    if time.localtime(now).tm_hour in LUNCH_HOURS:
        for person in order:
            put(person, "snack", "lunch") or put(person, "chat", "lunch") or put(person, "coffee", "lunch")
        return out
    for i, person in enumerate(order):
        offset = (zlib.crc32(person.encode()) % REST_SLOT_SEC)
        slot = int((now + offset) // REST_SLOT_SEC)
        rnd = random.Random(f"{person}:{slot}")
        choices = [(a, w) for a, w in REST_WEIGHT if a != "smoke" or person in SMOKERS]
        pick = rnd.choices([a for a, _ in choices], weights=[w for _, w in choices])[0]
        for act in (pick, "chat", "coffee", "snack"):
            if put(person, act):
                break
    chatters = [p for p, r in out.items() if r["act"] == "chat"]
    if len(chatters) == 1:  # nobody to talk to: just resting on the sofa
        out[chatters[0]].update(act="sofa", label=REST_LABEL["sofa"])
    return out

DESKS = PEOPLE_SEAT

def _roster_row(p: dict, lounge: dict) -> dict[str, Any]:
    return {
        "id": p["id"], "name": p["name"], "team": p["team"], "title": p["title"], "office": p["office"],
        "name_ko": p.get("name_ko", ""), "name_ja": p.get("name_ja", ""), "name_en": p.get("name_en", ""), "full": full_name(p), "lead": bool(p.get("lead")),
        "team_name": TEAM_INFO[p["team"]]["name"],
        "dept": f"{TEAM_INFO[p['team']]['name']} · {p['title']}",
        "agent": TEAM_INFO[p["team"]]["work"],
        "tone": p["team"],
        "step": TEAM_INFO[p["team"]]["step"],
        "avatar": f"office/{p['art']}-face.svg",
        "face": f"office/{p['art']}-face.svg",
        "sprite": f"office/{p['art']}-sheet.svg", "sprite_back": f"office/{p['art']}-back.svg",  # 4 frames: idle, walk A, walk B, typing
        "desk": p["seat"],
        "lounge": lounge,
    }


def _roster(o: str) -> list[dict[str, Any]]:
    people = STAFF.get(o, ())
    return [_roster_row(p, dict(_chat(o)[i % len(_chat(o))])) for i, p in enumerate(people)]


ROSTER = _PerOffice(_roster)
ALL_ROSTER = {row["id"]: row for o in STAFF for row in _roster(o)}
# Kept for older callers: zones are people now.
ZONES = ROSTER


# Office background picture (static/office/floor.*). When present it replaces the CSS-drawn
# floor; DESKS/LOUNGE must then match the spots in the picture.
FLOOR_NAMES = ("floor.svg", "floor.png", "floor.jpg", "floor.jpeg", "floor.webp")


def floor_image() -> str:
    if layout()["floor"]:
        return layout()["floor"]
    for name in FLOOR_NAMES:
        if (STATIC / "office" / name).is_file():
            return f"office/{name}"
    return ""


def floor_front() -> str:
    """Desk fronts drawn over people sitting at desks (flat office only)."""
    if layout()["front"]:
        return layout()["front"]
    return "office/floor-front.svg" if (STATIC / "office" / "floor-front.svg").is_file() else ""

STATE_LABEL = {"work": "작업 중", "think": "차례 기다림", "wait": "확인 필요", "idle": "대기 중", "meeting": "회의 중"}


def append_event(*, who: str, dept: str, text: str, state: str = "done", site: str | None = None) -> None:
    """site None = the current site; "" = the whole office (전사 회의)."""
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    from app.snapshot import now_stamp

    row = {"at": now_stamp(), "who": who, "dept": dept, "text": text, "state": state, "site": sites.current_id() if site is None else site,
           "office": sites.current().office}
    with EVENTS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _feed_face(who: str) -> dict[str, str]:
    """Match event who (display name or role id) → face path for the activity list."""
    if not who:
        return {"role": "", "face": ""}
    if who in ALL_ROSTER:
        p = ALL_ROSTER[who]
        return {"role": p["id"], "face": p["face"]}
    for p in ALL_ROSTER.values():
        if p["name"] == who:
            return {"role": p["id"], "face": p["face"]}
    return {"role": "", "face": ""}


def _with_faces(feed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in feed:
        meta = _feed_face(str(row.get("role") or row.get("who") or ""))
        out.append({
            **row,
            "role": row.get("role") or meta["role"],
            "face": row.get("face") or meta["face"],
            "who": row.get("who") or meta["role"] or "",
        })
    return out


def _event_office(row: dict[str, Any]) -> str:
    if row.get("office"):
        return str(row["office"])
    sid = str(row.get("site") or "")
    return sites.SITES[sid].office if sid in sites.SITES else "datalab"   # older rows: 1층


def read_events(limit: int = 12, office_id: str | None = None) -> list[dict[str, Any]]:
    """Newest first, this floor's only (office_id None = the current office)."""
    office_id = office_id or sites.current().office
    if not EVENTS_PATH.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = EVENTS_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and _event_office(row) == office_id:
            rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def read_flow() -> dict[str, Any]:
    """Where the git lane stands: open PR, reviewed, merged, deployed."""
    try:
        data = json.loads(FLOW_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_flow(**changes: Any) -> dict[str, Any]:
    data = read_flow()
    data.update(changes)
    FLOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    FLOW_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def staff_by_role(person: str) -> dict[str, Any]:
    """Anyone on any floor (the event feed and jobs can name people of the other floor)."""
    if person in ALL_ROSTER:
        return dict(ALL_ROSTER[person])
    return {"id": person, "name": person, "team": "", "title": "", "dept": "", "agent": "", "tone": "",
            "avatar": "", "face": "", "sprite": "", "desk": {"x": 50, "y": 50}, "lounge": {"x": 50, "y": 50}}


def team_members(team: str) -> tuple[str, ...]:
    if is_borrowed():   # B1: the same team of every other floor
        return tuple(p["id"] for oid, people in STAFF.items() if not is_borrowed(oid) for p in people if p["team"] == team)
    return TEAMS.get(team, ())


LAST_ASSIGNED: dict[str, float] = {}   # person → when they were last handed work (jobs.start)


def note_assigned(person: str) -> None:
    import time

    LAST_ASSIGNED[person] = time.time()


def ordered_members(task: str) -> list[str]:
    """Team members for a task: anyone on the team can do any of its work. Whoever has gone longest
    without being handed something comes first, so work goes round the team instead of to one person."""
    team = TASK_TEAM.get(task, "")
    if is_borrowed():
        return _borrowed_members(BORROW_TEAM.get(team, team))
    members = list(team_members(team))
    order = {m: i for i, m in enumerate(members)}
    return sorted(members, key=lambda m: (LAST_ASSIGNED.get(m, 0.0), order[m]))


def teams_here() -> Mapping[str, tuple[str, ...]]:
    """Teams as the current page shows them: on B1 everyone upstairs who could come down (planners → dev)."""
    if is_borrowed():
        return {t: team_members(BORROW_TEAM.get(t, t)) for t in TEAM_INFO}
    return TEAMS


def _borrowed_members(team: str) -> list[str]:
    """B1: free people of this team from upstairs (longest without work first), at most max_visitors downstairs.
    A hand-off from someone already downstairs (fix → push → review) may start: they go back up as it starts."""
    from app.office import jobs
    members = list(team_members(team))
    order = {m: i for i, m in enumerate(members)}
    room = int(layout().get("max_visitors", 2)) - len(visitors())
    if getattr(jobs._IN_JOB, "on", False):
        room += 1
    free = [m for m in sorted(members, key=lambda m: (LAST_ASSIGNED.get(m, 0.0), order[m])) if not jobs.running(m)]
    return free[:max(0, room)]


def _state_from_line(line: str) -> str:
    if "로그인" in line or "허가" in line or "만료" in line or "실패" in line or "못 했" in line:
        return "wait"
    if "기다리" in line or "생각" in line:
        return "think"
    return "work"


def _jobs_by_person(job: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Accept {person: job} (current) or one legacy {running, kind, line} job."""
    if not job:
        return {}
    if "running" in job or "kind" in job:
        kind = str(job.get("kind") or "")
        return {kind: job} if job.get("running") and kind else {}
    return {r: j for r, j in job.items() if isinstance(j, dict) and j.get("running")}


def _site_label(sid: str) -> str:
    return sites.SITES[sid].label if sid in sites.SITES else (sid or "")


def sites_busy(job: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Sites that currently have at least one running job (for the header switcher)."""
    running = _jobs_by_person(job)
    by: dict[str, list[dict[str, Any]]] = {}
    for role, j in running.items():
        sid = str(j.get("site") or sites.DEFAULT_SITE)
        by.setdefault(sid, []).append({"role": role, **j})
    out: list[dict[str, Any]] = []
    for sid, items in by.items():
        lines = [str(x.get("line") or "").strip() for x in items if str(x.get("line") or "").strip()]
        who = [staff_by_role(str(x.get("role") or ""))["name"] for x in items]
        out.append({
            "id": sid,
            "label": _site_label(sid),
            "n": len(items),
            "line": lines[0] if lines else "작업 중",
            "who": who[:4],
            "office": sites.SITES[sid].office if sid in sites.SITES else "",
        })
    out.sort(key=lambda r: (-r["n"], r["label"]))
    return out


def _seat(person: str, state: str, line: str, task: str = "", run: str = "", site: str = "") -> dict[str, Any]:
    p = staff_by_role(person)
    sid = site or sites.current_id()
    return {
        "role": person, "name": p["name"], "dept": p["dept"], "agent": p["agent"], "tone": p["tone"],
        "avatar": p["avatar"], "state": state, "state_label": STATE_LABEL.get(state, "작업 중"), "line": line,
        "task": task, "run": run, "site": sid, "site_label": _site_label(sid),
    }


def run_label(task: str, line: str) -> str:
    """What the ▶ button does for waiting work (app.site_run does it)."""
    if task == "review" and "고칠 게" in line:
        return "개발팀에 고치기 맡기기" if "코드" in line else "마케팀에 고치기 맡기기"
    return {"write": f"{sites.current().words['picks']} {sites.current().words['verb']}", "search": "15편 바로 고치기", "issue": "PR 만들기",
            "review": "리뷰·머지 하기", "deploy": "사이트 올리기"}.get(task, "바로 하기")


def waiting_work(snap: dict[str, Any], flow: dict[str, Any] | None = None) -> dict[str, str]:
    """Work that is queued, by task, as one short line each."""
    flow = read_flow() if flow is None else flow
    site = snap.get(sites.current_id()) if isinstance(snap.get(sites.current_id()), dict) else {}
    git = site.get("git") if isinstance(site.get("git"), dict) else {}
    search = site.get("search") if isinstance(site.get("search"), dict) else {}
    pr = flow.get("pr") if isinstance(flow.get("pr"), dict) else None
    out: dict[str, str] = {}
    picks = [p for p in (_read_json(RESEARCH_PATH).get("picks") or []) if p.get("status") == "new"]
    from app.content import contentkit
    if contentkit.is_companydb():   # the add-queue counts as ready topics there
        from app.content import cdbcontent
        picks += cdbcontent.queue_picks()
    if picks:
        out["write"] = "추천 " + sites.picks_text(picks)
    if int(search.get("shown_count") or 0) and not search.get("backlog_off"):
        out["search"] = f"고칠 검색 글 {search.get('shown_count')}편"
    open_pr = pr is not None and not flow.get("merged")
    if git.get("dirty") and not open_pr:
        out["issue"] = f"올릴 파일 {git.get('file_count') or 0}개"
    if open_pr and not flow.get("reviewed"):
        out["review"] = f"PR #{pr.get('number')} 리뷰 차례"
    elif open_pr and not flow.get("verdict_ok"):
        from app.work import workers
        kind = "" if workers._pr_has_posts(flow) else " (코드)"
        out["review"] = f"PR #{pr.get('number')} 고칠 게 있어요{kind}"
    elif open_pr:
        out["review"] = f"PR #{pr.get('number')} 머지 차례"
    if flow.get("merged") and not flow.get("deployed"):
        out["deploy"] = "머지됨 · 올릴 차례"
    return out


def build_office(
    snap: dict[str, Any],
    job: dict[str, Any] | None = None,
    flow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if is_borrowed():
        return _build_borrowed(snap, job, flow)
    site = snap.get(sites.current_id()) if isinstance(snap.get(sites.current_id()), dict) else {}
    git = site.get("git") if isinstance(site.get("git"), dict) else {}
    seo = site.get("seo_last") if isinstance(site.get("seo_last"), dict) else {}
    ship = site.get("ship") if isinstance(site.get("ship"), dict) else {}

    running = _jobs_by_person(job)
    waiting = waiting_work(snap, flow)
    try:
        from app.work import workqueue
        in_queue = workqueue.pending_tasks()
    except Exception:  # noqa: BLE001
        in_queue = {}

    occupied: dict[str, dict[str, Any]] = {}
    seats: dict[str, dict[str, Any]] = {}
    away: dict[str, str] = {}   # gone down to B1: not on this floor until the work there ends
    for person, j in running.items():
        if person not in DESKS:
            continue
        jo = _job_office(j)
        if jo and jo != _here() and is_borrowed(jo):
            away[person] = f"{sites.OFFICES[jo].floor_label} · {_site_label(str(j.get('site') or ''))}"
            continue
        line = str(j.get("line") or "")
        state = str(j.get("state") or "") or _state_from_line(line)
        occupied[person] = _seat(person, state, line or "작업 중", str(j.get("task") or ""), site=str(j.get("site") or ""))
        seats[person] = j.get("seat") or DESKS[person]
    # Queued work: shown on a free member of the team that owns it. No ▶ when someone already does
    # that kind of work (the button would be refused anyway). If the execution queue already has
    # this task, show status text only — don't offer ▶ 리뷰·머지 again.
    here = sites.current_id()
    busy_groups = {task_group(str(j.get("task") or "")) for j in running.values()
                   if j.get("task") and (j.get("site") or sites.DEFAULT_SITE) == here}
    for task, line in waiting.items():
        if task in OPTIONAL_TASKS:
            continue
        if task_group(task) in busy_groups:   # someone is already on it (a hand-off in progress): no "대기" bubble
            continue
        for person in ordered_members(task):
            if person not in occupied and person not in away:
                if task in in_queue:
                    from app.work import workqueue
                    msg = workqueue.pending_label(in_queue[task])
                    occupied[person] = _seat(person, "think", msg, task, run="")
                else:
                    run = "" if (task_group(task) in busy_groups and task not in ITEM_TASKS) else run_label(task, line)
                    occupied[person] = _seat(person, "idle", line, task, run)
                seats[person] = DESKS[person]
                break

    rest = rest_plan([r["id"] for r in ROSTER if r["id"] not in occupied and r["id"] not in away])
    zones = [
        {**row, "desk": seats.get(row["id"], row["desk"]), "empty": row["id"] not in occupied,
         "person": occupied.get(row["id"]), "rest": rest.get(row["id"]), "away": away.get(row["id"], "")}
        for row in ROSTER
    ]
    plates = []
    for team, info in TEAM_INFO.items():
        members = TEAMS[team]
        busy = [occupied[m] for m in members if m in occupied and occupied[m]["state"] == "work"]
        idle = [occupied[m] for m in members if m in occupied and occupied[m]["state"] != "work"]
        if len(busy) > 1:
            line = f"{len(busy)}명 작업 중"
        elif busy:
            line = busy[0]["line"]
        elif idle:
            line = idle[0]["line"]
        else:
            line = info["work"]
        plates.append({"team": team, "label": info["name"], "step": info["step"], "x": PLATES[team]["x"],
                       "y": PLATES[team]["y"], "busy": bool(busy), "line": line, "size": len(members)})

    working = [occupied[r] for r in running if r in occupied]
    if working:
        names = ", ".join(p["name"] for p in working)
        next_up = f"{names} 작업 중" if len(working) > 1 else working[0]["line"]
    elif any(t not in OPTIONAL_TASKS for t in waiting):
        task, line = next((t, l) for t, l in waiting.items() if t not in OPTIONAL_TASKS)
        next_up = f"{TEAM_INFO[TASK_TEAM.get(task, 'plan')]['name']} · {line}"
    elif any(r["act"] == "lunch" for r in rest.values()):
        next_up = "점심시간"
    else:
        next_up = "모두 쉬는 중"

    feed = read_events()
    now_rows = [{"at": "지금", "who": p["name"], "role": p["role"], "face": (ALL_ROSTER.get(p["role"]) or {}).get("face", ""),
                 "dept": p["dept"], "text": p["line"], "state": p["state"],
                 "site": p.get("site", ""), "site_label": p.get("site_label") or _site_label(str(p.get("site") or ""))}
                for p in working]
    feed = now_rows + feed
    if not feed:
        bits = []
        if seo.get("finished_at"):
            bits.append({"at": seo.get("finished_at"), "who": "마케팀", "dept": "검색",
                         "text": f"SEO {seo.get('applied') or 0}/{seo.get('count') or 0}편 반영",
                         "state": "done" if seo.get("ok") else "wait"})
        if ship.get("when"):
            bits.append({"at": ship.get("when"), "who": "개발팀", "dept": "올리기",
                         "text": ship.get("tail") or "사이트 올리기", "state": "done"})
        if git.get("dirty"):
            bits.append({"at": snap.get("refreshed_at") or "", "who": "기획팀", "dept": "이슈·PR",
                         "text": f"올릴 파일 {git.get('file_count') or 0}개", "state": "wait"})
        feed = bits[:8]
    feed = _with_faces([{**row, "site_label": row.get("site_label") or _site_label(str(row.get("site") or ""))} for row in feed])

    busy_n = sum(1 for p in occupied.values() if p["state"] == "work")
    busy_sites = sites_busy(job)
    return {
        "roster": list(ROSTER),
        "zones": zones,
        "plates": plates,
        "feed": feed[:12],
        "next_up": next_up,
        "busy": busy_n,
        "present": len(occupied),
        "active": working[0] if working else None,
        "working": [p["role"] for p in working],
        "sites_busy": busy_sites,
        "lounge": dict(LOUNGE),
        "floor": floor_image(),
        "floor_no": sites.office_of().floor,
        "nav": layout()["nav"],
        "floor_label": f"{sites.office_of().floor_label} · {sites.office_of().name}",
        "floor_front": floor_front(),
        "teams": {k: list(v) for k, v in TEAMS.items()},
        "borrow": False,
    }


def _build_borrowed(snap: dict[str, Any], job: dict[str, Any] | None, flow: dict[str, Any] | None) -> dict[str, Any]:
    """B1's map: only the people who came down for its work, at the desk they took; plates say what waits."""
    lay = layout()
    entry = dict(lay.get("entry") or {"x": 50, "y": 50})
    hot = list(lay.get("hot") or ())
    here = visitors(running=_jobs_by_person(job))
    roster, zones = [], []
    for i, (person, j) in enumerate(here.items()):
        base = dict(ALL_ROSTER.get(person) or staff_by_role(person))
        desk = j.get("seat") or (dict(hot[i % len(hot)]) if hot else entry)
        line = str(j.get("line") or "")
        state = str(j.get("state") or "") or _state_from_line(line)
        row = {**base, "desk": desk, "lounge": entry}
        roster.append(row)
        zones.append({**row, "empty": False, "rest": None, "away": "",
                      "person": _seat(person, state, line or "작업 중", str(j.get("task") or ""), site=str(j.get("site") or ""))})
    waiting = waiting_work(snap, flow)
    plates = []
    for team, pos in lay["plates"].items():
        mine = [z["person"] for z in zones if z["team"] == team]
        wait = next((l for t, l in waiting.items() if t not in OPTIONAL_TASKS
                     and BORROW_TEAM.get(TASK_TEAM.get(t, ""), TASK_TEAM.get(t, "")) == team), "")
        line = f"{len(mine)}명 작업 중" if len(mine) > 1 else (mine[0]["line"] if mine else (wait or "일이 생기면 위층에서 내려와요"))
        plates.append({"team": team, "label": TEAM_INFO[team]["name"], "step": TEAM_INFO[team]["step"], "x": pos["x"],
                       "y": pos["y"], "busy": bool(mine), "line": line, "size": len(mine)})
    working = [z["person"] for z in zones]
    if working:
        names = ", ".join(p["name"] for p in working)
        next_up = f"{names} 작업 중 (위층에서 내려옴)"
    elif any(t not in OPTIONAL_TASKS for t in waiting):
        task, line = next((t, l) for t, l in waiting.items() if t not in OPTIONAL_TASKS)
        team = BORROW_TEAM.get(TASK_TEAM.get(task, "plan"), TASK_TEAM.get(task, "plan"))
        next_up = f"{TEAM_INFO[team]['name']} · {line}"
    else:
        next_up = "지금은 아무도 없어요 · 일이 생기면 위층에서 내려와요"
    now_rows = [{"at": "지금", "who": p["name"], "role": p["role"], "face": (ALL_ROSTER.get(p["role"]) or {}).get("face", ""),
                 "dept": p["dept"], "text": p["line"], "state": p["state"],
                 "site": p.get("site", ""), "site_label": p.get("site_label") or _site_label(str(p.get("site") or ""))}
                for p in working]
    feed = _with_faces([{**row, "site_label": row.get("site_label") or _site_label(str(row.get("site") or ""))}
                        for row in now_rows + read_events()])
    off = sites.office_of()
    return {
        "roster": roster, "zones": zones, "plates": plates, "feed": feed[:12], "next_up": next_up,
        "busy": len(working), "present": len(working), "active": working[0] if working else None,
        "working": [p["role"] for p in working], "sites_busy": sites_busy(job), "lounge": {r["id"]: entry for r in roster},
        "floor": floor_image(), "floor_no": off.floor, "nav": lay["nav"],
        "floor_label": f"{off.floor_label} · {off.name}", "floor_front": floor_front(),
        "teams": {t: [r["id"] for r in roster if r["team"] == t] for t in lay["plates"]},
        "borrow": True, "max_visitors": int(lay.get("max_visitors", 2)),
        "everyone": list(ALL_ROSTER.values()),   # pages name whoever is next in turn, from any floor
    }


# ---------- 사원 일람 ----------

_LEGACY_NAMES = {"jiho": ("지호",), "yuna": ("유나",), "subin": ("수빈",), "hajun": ("하준",), "doyun": ("도윤",)}   # names before the rename


def _aliases() -> dict[str, str]:
    """Every name a person has appeared under in the event log → their id."""
    out: dict[str, str] = {}
    for p in ALL_PEOPLE:
        for name in (p.get("name"), p.get("name_ko"), p.get("name_ja"), p.get("name_en"), *_LEGACY_NAMES.get(p["id"], ())):
            if name:
                out[str(name)] = p["id"]
    return out


def staff_directory(jobs_now: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Floors → teams → people, with what each is doing now and what they've done (from the event log)."""
    from datetime import datetime, timedelta

    alias = _aliases()
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
    stats: dict[str, dict[str, Any]] = {p["id"]: {"done": 0, "week": 0, "sites": {}, "last": None} for p in ALL_PEOPLE}
    try:
        lines = EVENTS_PATH.read_text(encoding="utf-8").splitlines() if EVENTS_PATH.is_file() else []
    except OSError:
        lines = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        pid = alias.get(str(row.get("who") or "")) if isinstance(row, dict) else None
        if not pid:
            continue
        st = stats[pid]
        st["last"] = row   # the log is oldest first
        if row.get("state") == "done":
            st["done"] += 1
            if str(row.get("at") or "") >= week_ago:
                st["week"] += 1
            sid = str(row.get("site") or "")
            if sid:
                st["sites"][sid] = st["sites"].get(sid, 0) + 1
    running = _jobs_by_person(jobs_now or {})
    floors = []
    for oid, people in STAFF.items():
        if oid not in sites.OFFICES or not people:   # B1 has nobody of its own
            continue
        off = sites.OFFICES[oid]
        teams = []
        for team, info in TEAM_INFO.items():
            members = []
            for p in people:
                if p["team"] != team:
                    continue
                row = dict(ALL_ROSTER[p["id"]])
                j = running.get(p["id"])
                st = stats[p["id"]]
                row.update(
                    sex=p.get("sex", ""), smoker=p["id"] in SMOKERS,
                    now=(str(j.get("line") or "작업 중") if j else ""), now_site=(str(j.get("site") or "") if j else ""),
                    done=st["done"], week=st["week"], last=st["last"],
                    sites=sorted(st["sites"].items(), key=lambda kv: -kv[1]),
                )
                members.append(row)
            if members:
                teams.append({"team": team, "name": info["name"], "work": info["work"], "members": members,
                              "busy": sum(1 for m in members if m["now"])})
        floors.append({"id": oid, "name": off.name, "floor": off.floor, "floor_label": off.floor_label, "blurb": off.blurb,
                       "teams": teams, "count": sum(len(t["members"]) for t in teams), "busy": sum(t["busy"] for t in teams),
                       "sites": [sites.SITES[s] for s in off.sites if s in sites.SITES]})
    return floors
