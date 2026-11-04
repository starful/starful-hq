from app.money import usage
def test_track_and_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "PATH", tmp_path / "hq_usage.json")
    usage.track("post:site_run:review", site="okpy")
    usage.track("post:site_run:review", site="okpy")
    usage.track("ui:tab:plan", site="okramen")
    usage.track("ui:meet:all", site="okramen")
    s = usage.summary(14)
    assert s["total"] == 4
    assert s["top"][0]["id"] == "post:site_run:review" and s["top"][0]["n"] == 2
    assert any(x["id"] == "okpy" for x in s["sites"])


def test_pending_tasks_prefer_running(tmp_path, monkeypatch):
    from app.work import workqueue
    from app import sites
    monkeypatch.setattr(workqueue, "_queue_path", lambda site_id=None: tmp_path / f"{site_id or sites.current_id()}.json")
    monkeypatch.setattr(workqueue, "_LOCK", __import__("threading").Lock())
    with sites.use("okpy"):
        assert workqueue.enqueue_task("review", "PR #1 머지 차례")
        assert workqueue.pending_tasks()["review"] == "queued"
        assert "곧 시작" in workqueue.pending_label("queued")
        # mark running
        st = workqueue._state()
        st["items"][0]["status"] = workqueue.STATUS_RUNNING
        workqueue._save(st)
        assert workqueue.pending_tasks()["review"] == "running"
