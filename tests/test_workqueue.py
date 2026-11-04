"""Execution queue: meeting ideas and waiting tasks drain when free."""

from __future__ import annotations

import json

from app.work import actions
from app.office import jobs
from app import office
from app.work import planning
from app import sites
from app.work import workqueue
def _queue_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(workqueue, "_queue_path", lambda site_id=None: tmp_path / f"q-{(site_id or sites.current_id())}.json")
    monkeypatch.setattr(workqueue, "_prefs_path", lambda site_id=None: tmp_path / f"prefs-{(site_id or sites.current_id())}.json")
    monkeypatch.setattr(workqueue, "kick", lambda sid: None)


def test_enqueue_meeting_puts_runnable_ideas_on_queue(tmp_path, monkeypatch):
    jobs.JOBS.clear()
    monkeypatch.setattr(planning, "PROPOSAL_DIR", tmp_path / "p")
    monkeypatch.setattr(planning, "PROPOSAL_PATH", tmp_path / "p.json")
    _queue_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(actions, "_open_pr", lambda: None)
    pid = planning.save({"meeting": True, "ideas": [
        {"title": "polars 글", "action": {"type": "write", "category": "python", "topics": ["polars"]}},
        {"title": "관련 글", "action": {"type": "feature", "area": "front", "spec": "카드"}},
        {"title": "SNS", "action": {"type": "none"}},
    ]})
    n = workqueue.enqueue_meeting(pid)
    assert n == 2
    items = workqueue.list_items()
    assert len([x for x in items if x["status"] == "queued"]) == 2
    assert actions.proposal_run(pid, 0)["ok"] is False  # already queued
    out = actions.proposal_run(pid, 2)
    assert not out["ok"]


def test_feature_held_on_open_pr_then_released_after_merge(tmp_path, monkeypatch):
    """E2E: meeting → write queued · feature held (open PR) → release after merge → start write."""
    jobs.JOBS.clear()
    monkeypatch.setattr(planning, "PROPOSAL_DIR", tmp_path / "p")
    monkeypatch.setattr(planning, "PROPOSAL_PATH", tmp_path / "p.json")
    _queue_paths(tmp_path, monkeypatch)
    flow = {"pr": {"number": 99}, "merged": False}
    monkeypatch.setattr(office, "read_flow", lambda: flow)
    pid = planning.save({"meeting": True, "ideas": [
        {"title": "SEO 글", "site": "okpy", "action": {"type": "write", "category": "python", "topics": ["polars"]}},
        {"title": "카드 UI", "site": "okpy", "action": {"type": "feature", "area": "front", "spec": "관련 글"}},
    ]})
    assert workqueue.enqueue_meeting(pid) == 2
    with sites.use("okpy"):
        by_task = {x["task"]: x for x in workqueue.list_items()}
        assert by_task["write"]["status"] == workqueue.STATUS_QUEUED
        assert by_task["feature"]["status"] == workqueue.STATUS_HELD
        assert "PR #99" in by_task["feature"]["error"]

        workqueue._release_held()
        assert {x["task"]: x["status"] for x in workqueue.list_items()}["feature"] == workqueue.STATUS_HELD

        flow["merged"] = True
        workqueue._release_held()
        statuses = {x["task"]: x["status"] for x in workqueue.list_items()}
        assert statuses["feature"] == workqueue.STATUS_QUEUED
        assert statuses["write"] == workqueue.STATUS_QUEUED

        started = []

        def fake_idea(idea):
            started.append(idea.get("title"))
            return "jiho", "기획팀이 글 쓰기"

        monkeypatch.setattr(actions, "run_idea", fake_idea)
        monkeypatch.setattr(workqueue, "_finish_when_idle", lambda *a, **k: None)
        assert workqueue.next_item() is True
        assert started == ["SEO 글"]
        assert any(x["status"] == workqueue.STATUS_RUNNING and x["task"] == "write"
                   for x in workqueue.list_items())


def test_search_backlog_always_on_and_clear_targets(tmp_path, monkeypatch):
    monkeypatch.setattr(workqueue, "_prefs_path", lambda site_id=None: tmp_path / "prefs.json")
    monkeypatch.setattr(workqueue, "_queue_path", lambda site_id=None: tmp_path / "q.json")
    assert workqueue.search_backlog_on() is True
    workqueue.set_prefs(search_backlog=False)
    assert workqueue.search_backlog_on() is True  # toggle removed — always on
    targets = tmp_path / "gsc_targets.json"
    monkeypatch.setattr(workqueue.sites, "data_path", lambda name, site_id=None: tmp_path / name)
    from app import snapshot
    monkeypatch.setattr(snapshot, "TARGETS_PATH", targets)
    targets.write_text(json.dumps({"gsc": {"low_ctr": [1], "low_ctr_count": 1}}), encoding="utf-8")
    workqueue.clear_search_targets()
    data = json.loads(targets.read_text(encoding="utf-8"))
    assert data["gsc"]["low_ctr_count"] == 0


def test_zombie_running_shows_stuck_then_sweep_clears(tmp_path, monkeypatch):
    jobs.JOBS.clear()
    _queue_paths(tmp_path, monkeypatch)
    with sites.use("okpy"):
        workqueue.enqueue_task("review", "PR #1 리뷰")
        # pretend it started but the process died
        with workqueue._LOCK:
            st = workqueue._state()
            st["items"][0]["status"] = workqueue.STATUS_RUNNING
            st["items"][0]["started_at"] = "2026-10-27 21:00:00"
            workqueue._save(st)
        pulse = workqueue.site_pulse("okpy")
        assert pulse["kind"] == "stuck"
        assert pulse["label"] == "멈춤"
        assert pulse["entries"] and pulse["entries"][0]["title"]
        assert workqueue.sweep_zombies("okpy") == 1
        assert workqueue.site_pulse("okpy")["kind"] == "idle"
        assert workqueue.list_items("okpy")[0]["status"] == workqueue.STATUS_FAILED


def test_start_now_starts_a_queued_task(tmp_path, monkeypatch):
    jobs.JOBS.clear()
    _queue_paths(tmp_path, monkeypatch)
    started = []

    def fake_waiting(task):
        started.append(task)
        return "minjae"

    monkeypatch.setattr(actions, "run_waiting", fake_waiting)
    monkeypatch.setattr(workqueue, "_finish_when_idle", lambda *a, **k: None)
    with sites.use("okpy"):
        row = workqueue.enqueue_task("review", "PR #2 리뷰")
        assert row and row["status"] == "queued"
        out = workqueue.start_now(row["id"], "okpy")
        assert out["ok"] and out.get("started")
        assert started == ["review"]
        assert workqueue.list_items("okpy")[0]["status"] == "running"
