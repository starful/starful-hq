"""B1 Back Office: no people of its own; free people from upstairs come down for its work."""
from app.office import jobs
from app import office
from app import sites
def test_b1_is_a_borrowed_floor():
    off = sites.OFFICES["basement"]
    assert off.floor == -1 and off.floor_label == "B1" and "krcare" in off.sites
    with sites.use("krcare"):
        assert office.is_borrowed() and not sites.current().can_write   # 관리 · SEO only, no writing
    with sites.use("okpy"):
        assert not office.is_borrowed()


def test_b1_work_goes_to_free_people_from_upstairs_at_most_two():
    jobs.JOBS.clear()
    with sites.use("krcare"):
        search = office.ordered_members("search")
        assert search and all(office.staff_by_role(p)["team"] == "mkt" for p in search)
        assert {office.staff_by_role(p)["office"] for p in office.team_members("mkt")} == {"datalab", "playground"}
        assert all(office.staff_by_role(p)["team"] == "dev" for p in office.ordered_members("issue"))   # no planners on B1
        seat = office.work_seat("search", search[0], [])
        assert seat in [dict(s) for s in office.layout()["hot"]]
        jobs.JOBS["minjae"] = {"running": True, "site": "krcare", "task": "search", "line": "검색 글 고치는 중", "seat": seat}
        jobs.JOBS["kenta"] = {"running": True, "site": "krcare", "task": "review", "line": "리뷰 중"}
        assert office.ordered_members("images") == []                      # two are down already: B1 is full
        ob = office.build_office({"krcare": {}}, jobs.all_jobs(), {})
        assert ob["borrow"] and {r["id"] for r in ob["roster"]} == {"minjae", "kenta"}
        assert ob["floor_label"].startswith("B1")
    with sites.use("okpy"):
        ob = office.build_office({"okpy": {}}, jobs.all_jobs(), {})
        away = {z["id"]: z["away"] for z in ob["zones"] if z["away"]}
        assert away == {"minjae": "B1 · KR Care"}                          # not shown on 1층 while downstairs
    jobs.JOBS.clear()
