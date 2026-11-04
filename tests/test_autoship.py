"""자동 진행: the next step after each job (changes → PR → review → merge → deploy → 확인)."""
from app.work import autoship
from app.office import jobs
from app import office
from app import sites
from app.work import workers


def _setup(monkeypatch, tmp_path, flow, dirty=False, busy=()):
    started = []
    monkeypatch.setattr(autoship, "STATE_PATH", tmp_path / "autoship.json")
    monkeypatch.setattr(office, "read_flow", lambda: dict(flow))
    monkeypatch.setattr(autoship, "_dirty", lambda: dirty)
    monkeypatch.setattr(autoship, "_site_busy", lambda sid: list(busy))
    monkeypatch.setattr(workers, "_pr_has_posts", lambda f: True)
    monkeypatch.setattr(jobs, "start_task", lambda task, line, *a, **k: started.append((task, a)) or "x")
    return started


def test_steps_in_order(monkeypatch, tmp_path):
    with sites.use("okpy"):
        s = _setup(monkeypatch, tmp_path, {}, dirty=True)
        autoship.next_step()
        assert s[-1][0] == "issue"                                   # changes → PR
        s = _setup(monkeypatch, tmp_path, {"pr": {"number": 7}})
        autoship.next_step()
        assert s[-1][0] == "review" and s[-1][1] == (workers.review_and_merge,)   # PR → review
        s = _setup(monkeypatch, tmp_path, {"pr": {"number": 7}, "reviewed": True, "verdict_ok": True})
        autoship.next_step()
        assert s[-1][0] == "review" and s[-1][1][-1] is True        # passed → merge
        s = _setup(monkeypatch, tmp_path, {"pr": {"number": 7}, "reviewed": True, "verdict_ok": False, "review_round": 1})
        autoship.next_step()
        assert s[-1][0] == "fix"                                     # asks for fixes → 고치기
        # mixed PR with code 필수 → 개발팀 (okcaddie-style), even when posts also changed
        s = _setup(monkeypatch, tmp_path, {
            "pr": {"number": 50}, "reviewed": True, "verdict_ok": False, "review_round": 1,
            "required": ["`app/routes/assets.py` — PNG"],
        })
        autoship.next_step()
        assert s[-1][0] == "fix_code"
        s = _setup(monkeypatch, tmp_path, {"pr": {"number": 7}, "merged": True, "deployed": False})
        autoship.next_step()
        assert s[-1][0] == "deploy"                                  # merged → deploy


def test_waits_and_stops(monkeypatch, tmp_path):
    with sites.use("okpy"):
        s = _setup(monkeypatch, tmp_path, {}, dirty=True, busy=["seoyeon"])
        assert autoship.next_step() is None and not s                # someone still working: wait for the batch
        s = _setup(monkeypatch, tmp_path, {"pr": {"number": 7}, "reviewed": True, "verdict_ok": False,
                                           "review_round": workers.MAX_FIX_ROUNDS})
        assert autoship.next_step() is None and not s                # review keeps blocking: a person decides
        s = _setup(monkeypatch, tmp_path, {}, dirty=True)
        autoship.set_enabled(False)
        assert autoship.next_step() is None and not s                # floor off: buttons as before
        autoship.set_enabled(True)
        assert autoship.enabled()


def test_stage_asks_for_site_check(monkeypatch, tmp_path):
    with sites.use("okpy"):
        _setup(monkeypatch, tmp_path, {"deployed": True, "deployed_at": "2026-10-24 08:30"})
        st = autoship.stage({})
        assert st["key"] == "check" and st["needs_person"] and st["url"] == "https://okpy.net"
        assert st["office"] == "datalab" and st["floor_label"] == "1층"
        autoship.mark_checked()
        assert autoship.stage({})["key"] == "idle"


def test_enabled_is_per_office(monkeypatch, tmp_path):
    monkeypatch.setattr(autoship, "STATE_PATH", tmp_path / "autoship.json")
    with sites.use("okpy"):
        autoship.set_enabled(False)   # datalab off
        assert not autoship.enabled("okpy")
        assert not autoship.enabled("companydb")   # same floor
        assert autoship.enabled("okramen")         # playground still on by default
    with sites.use("okramen"):
        autoship.set_enabled(False, office_id="playground")
        assert not autoship.enabled("okramen")
        assert not autoship.enabled("jpfun")
        assert not autoship.enabled("okpy")        # datalab still off from above
    states = autoship.office_states()
    assert states["datalab"] is False and states["playground"] is False


def test_migrates_legacy_site_keys(monkeypatch, tmp_path):
    path = tmp_path / "autoship.json"
    path.write_text(
        '{"okpy": {"on": true, "checked_at": "2026-11-01 10:00"},'
        ' "companydb": {"on": false},'
        ' "jpfun": {"on": false},'
        ' "okramen": {"on": true}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(autoship, "STATE_PATH", path)
    # datalab: any on → on; playground: any on → on
    assert autoship.enabled("okpy") is True
    assert autoship.enabled("okramen") is True
    assert autoship.checked_at("okpy") == "2026-11-01 10:00"
    # writing rewrites to offices/sites shape
    autoship.set_enabled(True, office_id="basement")
    raw = path.read_text(encoding="utf-8")
    assert '"offices"' in raw and '"sites"' in raw
    assert autoship.checked_at("okpy") == "2026-11-01 10:00"
