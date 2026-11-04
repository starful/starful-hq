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
