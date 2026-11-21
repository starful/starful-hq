import sys
from pathlib import Path

import pytest

from app import sites
@pytest.fixture(autouse=True)
def _office_proposals_in_tmp(tmp_path, monkeypatch):
    from app.work import planning
    monkeypatch.setattr(planning, "office_dir", lambda: tmp_path / "office_proposals")


@pytest.fixture(autouse=True)
def _spend_files_in_tmp(tmp_path, monkeypatch):
    from app.money import costs
    monkeypatch.setattr(costs, "LIMITS_PATH", tmp_path / "spend_limits.json")
    monkeypatch.setattr(costs, "COUNT_PATH", tmp_path / "spend_counter.json")
    monkeypatch.setattr(costs, "month_paid_yen", lambda: None)


@pytest.fixture(autouse=True)
def _fresh_turns():
    """Who-goes-next (office.LAST_ASSIGNED) starts empty in every test."""
    from app import office
    office.LAST_ASSIGNED.clear()
    yield
    office.LAST_ASSIGNED.clear()


@pytest.fixture(autouse=True)
def _office_schedule_tmp(tmp_path, monkeypatch):
    """Isolate schedule file; always-on hours so job tests don't hit 퇴근."""
    import json
    from app.office import schedule
    path = tmp_path / "office_schedule.json"
    path.write_text(json.dumps({
        "hours": {"start": 0, "end": 24},
        "workdays": [0, 1, 2, 3, 4, 5, 6],
        "lunch_hours": [12],
        "enforce_hours": False,
        "vacations": [],
        "alba": list(schedule._DEFAULT_ALBA),
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(schedule, "SCHEDULE_PATH", path)


@pytest.fixture(autouse=True)
def _boss_state_tmp(tmp_path, monkeypatch):
    """Isolate boss presence so rest_plan / map tests don't inherit a live HQ login."""
    from app.office import boss
    monkeypatch.setattr(boss, "STATE_PATH", tmp_path / "boss_state.json")
    monkeypatch.setattr(boss, "CONFIG_PATH", tmp_path / "boss.json")


@pytest.fixture(autouse=True)
def _staffing_tmp(tmp_path, monkeypatch):
    """Isolate hire/fire roster so office tests see the baked-in 20."""
    from app import office
    from app.office import staffing
    monkeypatch.setattr(staffing, "PATH", tmp_path / "staffing.json")
    office.rebuild_staff_caches()
    yield
    office.rebuild_staff_caches()


@pytest.fixture(autouse=True)
def _okpy_site():
    """Flask's test client sets the current site in this thread: start every test on okpy."""
    token = sites.set_current(sites.DEFAULT_SITE)
    yield
    sites._CURRENT.reset(token)


_STUBS = str(Path(__file__).parent / "_stubs")
sys.path.insert(0, _STUBS)   # llm_claude stand-in when okadmin is not on the path


@pytest.fixture(autouse=True)
def _llm_claude_stub():
    """App/okadmin can put a real llm_claude on sys.path; force the test stub first."""
    import importlib

    while _STUBS in sys.path:
        sys.path.remove(_STUBS)
    sys.path.insert(0, _STUBS)
    sys.modules.pop("llm_claude", None)
    sys.modules.pop("llm_stub", None)
    importlib.import_module("llm_stub")
    importlib.import_module("llm_claude")
    import llm_stub

    llm_stub.capture = {}
    yield
