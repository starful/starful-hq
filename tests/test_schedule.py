"""근무 시간 · 휴가 · 알바 대행."""
from __future__ import annotations

import json
import time

from app import office
from app import sites
from app.office import jobs
from app.office import schedule


def _noon_monday():
    return time.mktime((2026, 11, 9, 12, 0, 0, 0, 0, -1))  # Mon


def _saturday():
    return time.mktime((2026, 11, 7, 15, 0, 0, 0, 0, -1))  # Sat


def _evening_monday():
    return time.mktime((2026, 11, 9, 23, 0, 0, 0, 0, -1))  # after end=22


def test_work_hours_default_includes_weekend(monkeypatch, tmp_path):
    path = tmp_path / "office_schedule.json"
    path.write_text(json.dumps({
        "hours": {"start": 8, "end": 22}, "workdays": [0, 1, 2, 3, 4, 5, 6],
        "lunch_hours": [12], "enforce_hours": True, "vacations": [], "alba": [],
    }), encoding="utf-8")
    monkeypatch.setattr(schedule, "SCHEDULE_PATH", path)
    assert schedule.is_work_hours(_noon_monday())
    assert schedule.is_work_hours(_saturday())
    assert schedule.off_reason(_saturday()) == ""
    assert schedule.off_reason(_evening_monday()) == "퇴근"
    assert schedule.is_workday(_saturday())


def test_custom_weekday_only_still_treats_saturday_off(monkeypatch, tmp_path):
    path = tmp_path / "office_schedule.json"
    path.write_text(json.dumps({
        "hours": {"start": 8, "end": 22}, "workdays": [0, 1, 2, 3, 4],
        "lunch_hours": [12], "enforce_hours": True, "vacations": [], "alba": [],
    }), encoding="utf-8")
    monkeypatch.setattr(schedule, "SCHEDULE_PATH", path)
    assert not schedule.is_work_hours(_saturday())
    assert schedule.off_reason(_saturday()) == "주말"


def test_vacation_one_per_team(monkeypatch, tmp_path):
    path = tmp_path / "office_schedule.json"
    path.write_text(json.dumps({
        "hours": {"start": 8, "end": 22}, "workdays": [0, 1, 2, 3, 4, 5, 6],
        "lunch_hours": [12], "enforce_hours": True, "vacations": [],
        "alba": list(schedule._DEFAULT_ALBA),
    }), encoding="utf-8")
    monkeypatch.setattr(schedule, "SCHEDULE_PATH", path)
    schedule.set_vacation("jiho", "2026-11-10", "2026-11-12", "여행")
    ok, why = schedule.can_add_vacation("seoyeon", "2026-11-11", "2026-11-11")  # same plan team
    assert not ok and "1명만" in why
    schedule.set_vacation("minjae", "2026-11-11", "2026-11-11", "병원")  # other team ok
    assert schedule.on_vacation("jiho", time.mktime((2026, 11, 11, 10, 0, 0, 0, 0, -1)))
    assert not schedule.on_vacation("jiho", time.mktime((2026, 11, 13, 10, 0, 0, 0, 0, -1)))


def test_alba_covers_and_ordered_members(monkeypatch, tmp_path):
    path = tmp_path / "office_schedule.json"
    path.write_text(json.dumps({
        "hours": {"start": 8, "end": 22}, "workdays": [0, 1, 2, 3, 4, 5, 6],
        "lunch_hours": [12], "enforce_hours": False, "vacations": [
            {"who": "jiho", "from": "2026-11-10", "to": "2026-11-14", "note": "오키나와"},
        ],
        "alba": list(schedule._DEFAULT_ALBA),
    }), encoding="utf-8")
    monkeypatch.setattr(schedule, "SCHEDULE_PATH", path)
    from datetime import date
    monkeypatch.setattr(schedule, "_today", lambda now=None: date(2026, 11, 11))
    monkeypatch.setattr(schedule, "off_reason", lambda now=None: "")  # work hours
    now = time.mktime((2026, 11, 11, 11, 0, 0, 0, 0, -1))
    asg = schedule.alba_assignments(now)
    assert "jiho" in asg
    alba_id = asg["jiho"]["id"]
    with sites.use("okpy"):
        members = office.ordered_members("write")
        assert "jiho" not in members
        assert alba_id in members
        built = office.build_office({}, {}, {})
        zone = next(z for z in built["zones"] if z["id"] == "jiho")
        assert zone["person"] and zone["person"]["role"] == alba_id
        assert "대행" in zone["person"]["line"]


def test_off_hours_and_vacation_hide_and_notice(monkeypatch, tmp_path):
    path = tmp_path / "office_schedule.json"
    path.write_text(json.dumps({
        "hours": {"start": 8, "end": 22}, "workdays": [0, 1, 2, 3, 4, 5, 6],
        "lunch_hours": [12], "enforce_hours": True, "vacations": [
            {"who": "jiho", "from": "2026-11-10", "to": "2026-11-14", "note": "여행"},
        ],
        "alba": list(schedule._DEFAULT_ALBA),
    }), encoding="utf-8")
    monkeypatch.setattr(schedule, "SCHEDULE_PATH", path)
    from datetime import date
    monkeypatch.setattr(schedule, "_today", lambda now=None: date(2026, 11, 11))
    monkeypatch.setattr(schedule, "off_reason", lambda now=None: "퇴근")
    with sites.use("okpy"):
        built = office.build_office({}, {}, {})
        assert "사장님도 퇴근해 주세요" in (built.get("hours_notice") or "")
        assert "사장님도 퇴근해 주세요" in built["next_up"]
        assert all(z.get("away") for z in built["zones"])
        assert all(z.get("person") is None for z in built["zones"])
        assert all(p.get("off_note") for p in built["plates"])
        assert all((p.get("line") or "").strip() and "팀 ·" not in (p.get("line") or "")
                   for p in built["plates"])
    monkeypatch.setattr(schedule, "off_reason", lambda now=None: "")
    with sites.use("okpy"):
        built = office.build_office({}, {}, {})
        jiho = next(z for z in built["zones"] if z["id"] == "jiho")
        # work hours + vacation → alba at desk (not the vacationer sprite as themselves)
        assert jiho.get("person") and jiho["person"]["role"].startswith("alba_")
        assert not jiho.get("away")
        assert "휴가" in (built.get("vacation_notice") or "")
        assert "여행" in built["vacation_notice"]
        notice = schedule.vacation_notice("datalab")
        assert "휴가 중" in notice and "대행" in notice


def test_team_off_quip_changes_by_day():
    a = schedule.team_off_quip("plan", time.mktime((2026, 11, 7, 23, 0, 0, 0, 0, -1)))
    b = schedule.team_off_quip("plan", time.mktime((2026, 11, 8, 23, 0, 0, 0, 0, -1)))
    c = schedule.team_off_quip("mkt", time.mktime((2026, 11, 7, 23, 0, 0, 0, 0, -1)))
    assert a and b and a != b
    assert c and c != a


def test_start_task_refuses_off_hours(monkeypatch, tmp_path):
    path = tmp_path / "office_schedule.json"
    path.write_text(json.dumps({
        "hours": {"start": 8, "end": 22}, "workdays": [0, 1, 2, 3, 4, 5, 6],
        "lunch_hours": [12], "enforce_hours": True, "vacations": [], "alba": [],
    }), encoding="utf-8")
    monkeypatch.setattr(schedule, "SCHEDULE_PATH", path)
    monkeypatch.setattr(schedule, "is_work_hours", lambda now=None: False)
    monkeypatch.setattr(schedule, "off_reason", lambda now=None: "퇴근")
    with sites.use("okpy"):
        assert jobs.start_task("analyze", "분석", lambda work: "ok") is None
        assert "퇴근" in jobs.request_result()["why"]
