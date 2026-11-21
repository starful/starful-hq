"""B1 Task Force: SiteKPI 전용 · 임시 사원 상주 (borrow 아님)."""
from app.office import jobs
from app.office import meeting
from app.work import planning
from app import office
from app import sites


def test_b1_is_task_force_with_own_staff():
    off = sites.OFFICES["basement"]
    assert off.floor == -1 and off.floor_label == "B1" and off.name == "Task Force"
    assert "sitekpi" in off.sites and "krcare" not in off.sites
    assert sites.SITES["krcare"].office == "datalab"
    with sites.use("sitekpi"):
        assert not office.is_borrowed()
        assert not sites.current().can_write
        assert len(office.PEOPLE) == 4
        assert {p["id"] for p in office.PEOPLE} == {"tf_rio", "tf_mina", "tf_noa", "tf_jun"}
        assert all(p.get("temp") for p in office.PEOPLE)
        assert office.MEETING_SEATS and office.LEADER_SEATS
        ob = office.build_office({"sitekpi": {}}, {}, {})
        assert not ob["borrow"] and ob["floor_label"].startswith("B1")
        assert {z["id"] for z in ob["zones"]} == {"tf_rio", "tf_mina", "tf_noa", "tf_jun"}


def test_b1_tf_members_do_their_own_team_work():
    jobs.JOBS.clear()
    with sites.use("sitekpi"):
        assert office.ordered_members("research") == ["tf_rio"]
        assert office.ordered_members("search") == ["tf_mina"]
        assert office.ordered_members("images") == ["tf_noa"]
        assert office.ordered_members("issue") == ["tf_rio"]  # TF has its own planner (not borrow→dev)
        assert office.ordered_members("review") == ["tf_jun"]
    with sites.use("okpy"):
        assert not office.is_borrowed()
        assert "tf_rio" not in [p["id"] for p in office.PEOPLE]
    jobs.JOBS.clear()


def test_b1_meeting_focus_and_labels_differ_from_1f():
    with sites.use("okpy"):
        assert planning.focus_map() == planning.FOCUS
        assert meeting.mode_label("all") == "전사 회의 (회의실 A)"
        assert meeting.mode_kind("all") == "전사"
        assert meeting.team_topic("plan") == meeting.TEAM_TOPIC["plan"]
        assert "개발팀" in meeting.role_view("daeun")
    with sites.use("sitekpi"):
        assert meeting.is_tf()
        assert planning.focus_map() == planning.TF_FOCUS
        assert set(planning.TF_FOCUS) == {"all", "money", "ux", "feature"}
        assert "수익화" in planning.TF_FOCUS["money"]
        assert meeting.mode_label("all") == "TF 스탠드업 (탁자)"
        assert meeting.mode_kind("all") == "TF"
        assert meeting.mode_room("all") == "TF 탁자"
        assert meeting.team_topic("plan") == meeting._TF_TEAM_TOPIC["plan"]
        assert "제품 로드맵" in meeting.role_view("tf_rio")
        assert meeting._topic_line("ux", "", "all") == planning.TF_FOCUS["ux"]


def test_b1_rejects_leaders_and_boss_meetings():
    """다른 층 사원·리더를 B1으로 부르지 않음."""
    jobs.JOBS.clear()
    with sites.use("sitekpi"):
        assert meeting.start("all", "", "leaders") == []
        assert meeting.start("all", "", "boss") == []
        assert meeting._TF_BLOCKED == frozenset({"leaders", "boss"})
    jobs.JOBS.clear()
