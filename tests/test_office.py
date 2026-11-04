import pytest

from app import office
@pytest.fixture(autouse=True)
def _no_research_file(tmp_path, monkeypatch):
    monkeypatch.setattr(office, "RESEARCH_PATH", tmp_path / "research.json")


def _snap(dirty=False, files=0, shown=0):
    return {"okpy": {"git": {"dirty": dirty, "file_count": files}, "search": {"shown_count": shown}, "seo_last": {}, "ship": {}}}


def test_four_teams_thirteen_people():
    assert {t: len(m) for t, m in office.TEAMS.items()} == {"plan": 3, "mkt": 2, "design": 2, "dev": 6}
    assert office.LEADERS == ("seoyeon", "minjae", "haneul", "daeun")
    assert office.staff_by_role("jiho")["name"] == "Haruto" and office.staff_by_role("jiho")["name_ko"] == "하루토"
    titles = {office.staff_by_role(p)["title"] for p in office.TEAMS["dev"]}
    assert titles == {"개발 리더", "개발"}          # nobody owns a kind of work: the whole team can do all of it


def test_work_goes_round_the_team():
    assert set(office.ordered_members("review")) == set(office.TEAMS["dev"])
    assert set(office.ordered_members("search")) == {"minjae", "subin"}
    first = office.ordered_members("research")[0]
    office.note_assigned(first)
    assert office.ordered_members("research")[-1] == first          # the one who just got work goes last
    assert office.ordered_members("write")[-1] == first             # same team, any task


def test_several_people_work_at_once_and_research_moves_to_the_board():
    jobs = {
        "jiho": {"running": True, "line": "트렌드 보는 중", "state": "work", "seat": office.HOT_DESKS[0]},
        "seoyeon": {"running": True, "line": "글 쓰는 중", "state": "work"},
        "subin": {"running": True, "line": "앞 git 작업이 끝나길 기다리는 중", "state": "think"},
        "minjae": {"running": False, "line": "3편을 고쳤어요", "state": "done"},
    }
    view = office.build_office(_snap(), jobs, flow={})
    seated = {z["id"]: z for z in view["zones"] if not z["empty"]}
    assert set(seated) == {"jiho", "seoyeon", "subin"}
    assert seated["jiho"]["desk"] == office.HOT_DESKS[0]
    assert view["busy"] == 2
    plan = next(p for p in view["plates"] if p["team"] == "plan")
    assert plan["busy"] and plan["line"] == "2명 작업 중"


def test_dirty_files_wait_for_the_planning_team():
    view = office.build_office(_snap(dirty=True, files=4), {}, flow={})
    seated = {z["id"]: z["person"] for z in view["zones"] if not z["empty"]}
    nxt = office.ordered_members("issue")[0]
    assert seated[nxt]["state"] == "idle" and "4" in seated[nxt]["line"]


def test_git_lane_hands_work_down_the_line():
    pr = {"number": 12, "url": "u"}
    assert set(office.waiting_work(_snap(dirty=True), {"pr": pr})) == {"review"}
    assert office.waiting_work(_snap(), {"pr": pr, "reviewed": True})["review"].endswith("고칠 게 있어요 (코드)")   # no branch: no posts
    assert office.waiting_work(_snap(), {"pr": pr, "reviewed": True, "verdict_ok": True})["review"].endswith("머지 차례")
    assert set(office.waiting_work(_snap(), {"pr": pr, "reviewed": True, "merged": True})) == {"deploy"}
    assert office.waiting_work(_snap(), {"pr": None, "merged": False, "deployed": True}) == {}


def test_every_person_has_map_coordinates():
    view = office.build_office({"okpy": {}}, {}, flow={})
    assert len(view["zones"]) == 13 and len(view["plates"]) == 4
    for person in view["roster"]:
        assert person["desk"] == office.DESKS[person["id"]]
        assert person["lounge"] == office.LOUNGE[person["id"]]
        assert person["sprite"].endswith("-sheet.svg")


def test_rest_plan_lunch_and_breaks():
    import time
    everyone = [p["id"] for p in office.PEOPLE]
    noon = time.mktime((2026, 10, 19, 12, 10, 0, 0, 0, -1))
    lunch = office.rest_plan(everyone, noon)
    assert len(lunch) == 13 and all(r["act"] == "lunch" for r in lunch.values())
    assert len({(r["x"], r["y"]) for r in lunch.values()}) == 13  # nobody on the same spot
    for hour in range(9, 19):
        if hour == 12:
            continue
        for minute in (0, 17, 33, 51):
            t = time.mktime((2026, 10, 19, hour, minute, 0, 0, 0, -1))
            plan = office.rest_plan(everyone, t)
            assert len(plan) == 13
            assert len({(r["x"], r["y"]) for r in plan.values()}) == 13
            for person, r in plan.items():
                assert r["act"] != "smoke" or person in office.SMOKERS
                assert r["act"] != "lunch"


def test_lone_chatter_rests_on_sofa_and_rest_is_in_zones():
    import time
    t = time.mktime((2026, 10, 19, 15, 0, 0, 0, 0, -1))
    for person in [p["id"] for p in office.PEOPLE]:
        plan = office.rest_plan([person], t)
        assert plan[person]["act"] in ("sofa", "coffee", "snack", "smoke", "wc", "lib", "desk", "game")
    built = office.build_office({}, {}, flow={})
    assert all(z["rest"] for z in built["zones"])  # nobody works: everyone has a break spot


def test_optional_work_keeps_people_free_and_required_work_waits(monkeypatch, tmp_path):
    import json
    research = tmp_path / "research.json"
    research.write_text(json.dumps({"picks": [{"id": "a", "status": "new"}, {"id": "b", "status": "new"}]}))
    monkeypatch.setattr(office, "RESEARCH_PATH", research)
    built = office.build_office({}, {}, {})
    assert all(z["person"] is None for z in built["zones"])  # topic ideas are optional: nobody waits at a desk
    assert office.waiting_work({}, {})["write"] == "추천 글 2편"   # picks without a kind count as 글
    built = office.build_office({}, {}, {"pr": {"number": 3}, "merged": False, "reviewed": False})
    nxt = office.ordered_members("review")[0]
    dae = next(z for z in built["zones"] if z["id"] == nxt)
    assert dae["person"]["task"] == "review" and dae["person"]["run"] == "리뷰·머지 하기"


def test_rest_spots_spread_out_over_the_day():
    import time
    acts = set()
    for h in range(9, 19):
        if h in office.LUNCH_HOURS:
            continue
        for m in range(0, 60, 5):
            t = time.mktime((2026, 10, 19, h, m, 0, 0, 0, -1))
            acts |= {r["act"] for r in office.rest_plan([p["id"] for p in office.PEOPLE], t).values()}
    assert {"desk", "game", "coffee"} <= acts
    assert office.run_label("review", "PR #3 고칠 게 있어요") == "마케팀에 고치기 맡기기"
    assert office.run_label("review", "PR #3 고칠 게 있어요 (코드)") == "개발팀에 고치기 맡기기"


def test_any_dev_can_tidy_any_side():
    assert set(office.ordered_members("refactor_front")) == set(office.ordered_members("refactor_back")) == set(office.TEAMS["dev"])


def test_restrooms_are_split_and_back_seats_face_up():
    import time
    seen = {}
    for m in range(0, 24 * 60, 5):
        t = time.mktime((2026, 10, 19, 9 + m // 60 % 9, m % 60, 0, 0, 0, -1))
        for pid, r in office.rest_plan([p["id"] for p in office.PEOPLE], t).items():
            if r["act"] == "wc":
                seen.setdefault(office.PEOPLE_SEX[pid], set()).add((r["x"], r["y"]))
    women = {(s["x"], s["y"]) for s in office.REST_SPOTS["wc_f"]}
    men = {(s["x"], s["y"]) for s in office.REST_SPOTS["wc_m"]}
    assert seen.get("f", set()) <= women and seen.get("m", set()) <= men
    assert office.DESKS["liam"].get("face") == "up" and not office.DESKS["daeun"].get("face")
    assert office.DESKS["minjae"].get("face") == "up" and not office.DESKS["seoyeon"].get("face")


def test_floors_have_their_own_people():
    from app import sites
    with sites.use("okramen"):
        up = [p["id"] for p in office.PEOPLE]
        assert len(up) == 10 and "nari" in up and "seoyeon" not in up
        assert {t: len(m) for t, m in office.TEAMS.items()} == {"plan": 3, "mkt": 2, "design": 2, "dev": 3}
        assert office.LEADERS == ("nari", "hina", "sora", "hyunwoo")
        assert "daeun" not in office.DESKS and office.DESKS["daeun"]   # other floor: not here, but still known by id
        o = office.build_office({}, {})
        assert o["floor_no"] == 2 and {z["id"] for z in o["zones"]} == set(up)
    assert "nari" not in [p["id"] for p in office.PEOPLE] and office.staff_by_role("nari")["name"] == "Nari"
    assert not set(office.TEAMS["dev"]) & {"hyunwoo", "kenta", "ana"}


def test_staff_directory_lists_every_floor():
    floors = office.staff_directory({"kenta": {"running": True, "line": "PR 리뷰 중", "site": "okramen"}})
    assert [f["floor"] for f in floors] == [1, 2]
    assert sum(f["count"] for f in floors) == len(office.ALL_PEOPLE) == 23
    two = floors[1]
    kenta = next(m for t in two["teams"] for m in t["members"] if m["id"] == "kenta")
    assert kenta["now"] == "PR 리뷰 중" and two["busy"] == 1


def test_staff_card_renders():
    from app.routes import app as flask_app

    c = flask_app.test_client()
    r = c.get("/staff?s=okramen")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Seoyeon" in html and "Nari" in html and 'data-here="playground"' in html


def test_away_work_goes_to_free_desks_not_meeting_rooms():
    from app import sites
    for sid in ("okpy", "okramen"):
        with sites.use(sid):
            hot = list(office.HOT_DESKS)
            assert len(hot) == 3
            first = office.work_seat("review", "x")
            assert first == hot[0]
            assert office.work_seat("research", "y", [first]) == hot[1]   # the next one is taken: the next Free desk
            assert office.work_seat("analyze", "z", hot) is None          # all taken: at one's own desk
            assert office.work_seat("write", "w") is None                 # writing happens at one's own desk
            rooms = list(office.MEETING_SEATS) + list(office.LEADER_SEATS) + list(office.MEET_C_SEATS)
            assert not any(h in rooms for h in hot)
