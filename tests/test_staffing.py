"""채용 · 해고 · 리더 후임 (맥스 25 · 포지션 선택)."""

from __future__ import annotations

import pytest

from app import office
from app.office import staffing


@pytest.fixture(autouse=True)
def _staffing_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(staffing, "PATH", tmp_path / "staffing.json")
    office.rebuild_staff_caches()
    yield
    office.rebuild_staff_caches()


def test_max_is_25_and_can_hire_without_fire():
    assert staffing.MAX_STAFF == 25
    assert staffing.active_count() == 20
    assert staffing.summary()["can_hire"] is True
    assert staffing.summary()["open"] == 5


def test_hire_requires_team():
    out = staffing.propose_hire(office="playground")
    assert out["ok"] is False
    assert "팀" in out["message"]


def test_hire_by_position_then_approve():
    out = staffing.propose_hire(team="dev", office="playground")
    assert out["ok"] is True
    assert out["pending"]["team"] == "dev"
    assert out["pending"]["office"] == "playground"
    assert "개발" in out["pending"]["why"] or "dev" in out["pending"]["why"].lower() or "2층" in out["pending"]["why"]
    assert staffing.approve(out["pending"]["id"])["ok"] is True
    assert staffing.active_count() == 21
    hired = [p for p in office.ALL_PEOPLE if p.get("hired")]
    assert len(hired) == 1
    assert hired[0]["team"] == "dev"
    assert hired[0]["office"] == "playground"


def test_fire_then_hire_prefers_vacancy_seat():
    fire = staffing.propose_fire("marco")
    assert staffing.approve(fire["pending"]["id"])["ok"] is True
    assert staffing.active_count() == 19
    hire = staffing.propose_hire(team="mkt", office="playground")
    assert hire["ok"] is True
    assert hire["pending"]["vacancy_id"] == "vac_marco"
    assert staffing.approve(hire["pending"]["id"])["ok"] is True
    assert staffing.active_count() == 20


def test_leader_fire_requires_successor():
    out = staffing.propose_fire("seoyeon")
    assert out["ok"] is True
    assert out["pending"]["successor"]  # same-team first mate
    assert staffing.approve(out["pending"]["id"])["ok"] is True
    assert "seoyeon" not in {p["id"] for p in office.ALL_PEOPLE}
    assert "seoyeon" not in office.company_leaders()


def test_leader_fire_blocked_without_team_mate():
    for who in ("jiho", "nari", "kaito"):
        out = staffing.propose_fire(who)
        assert out["ok"] is True
        assert staffing.approve(out["pending"]["id"])["ok"] is True
    out = staffing.propose_fire("seoyeon")
    assert out["ok"] is False
    assert "후임" in out["message"] or "올릴" in out["message"]


def test_reject_pending():
    staffing.propose_hire(team="plan", office="datalab")
    pid = staffing.pending()[0]["id"]
    assert staffing.reject(pid)["ok"] is True
    assert staffing.pending() == []
    assert staffing.active_count() == 20
