"""HQ 자가 점검: 같은 실패 반복 차단 · 루프 되살리기."""

from __future__ import annotations

import time

from app.work import watchdog


def _iso(monkeypatch, tmp_path):
    monkeypatch.setattr(watchdog, "PATH", tmp_path / "watchdog.json")


def test_three_same_failures_block_then_success_clears(tmp_path, monkeypatch):
    _iso(monkeypatch, tmp_path)
    line = "못 했어요. Step #0 - \"seo-guard\": SEO guard failed: 1 issue(s). (로그: /opt/x/deploy-1.log)"
    assert watchdog.record("krcampus", "deploy", False, line) == ""
    assert watchdog.record("krcampus", "deploy", False, line.replace("1.log", "2.log")) == ""
    note = watchdog.record("krcampus", "deploy", False, line.replace("1.log", "3.log"))
    assert "3번 실패" in note
    assert watchdog.blocked("krcampus", "deploy")
    assert watchdog.blocked("krcampus", "write") is None          # other work goes on
    assert watchdog.blocked("okpy", "deploy") is None             # other sites go on
    assert not watchdog.status()["ok"]
    watchdog.record("krcampus", "deploy", True)
    assert watchdog.blocked("krcampus", "deploy") is None


def test_different_failures_do_not_block(tmp_path, monkeypatch):
    _iso(monkeypatch, tmp_path)
    for why in ("A 오류", "B 오류", "C 오류"):
        watchdog.record("okpy", "issue", False, f"못 했어요. {why}")
    assert watchdog.blocked("okpy", "issue") is None


def test_person_press_unblocks(tmp_path, monkeypatch):
    _iso(monkeypatch, tmp_path)
    for _ in range(3):
        watchdog.record("okpy", "issue", False, "못 했어요. Claude가 JSON을 주지 않았어요")
    assert watchdog.blocked("okpy", "issue")
    watchdog.unblock("okpy", "issue")
    assert watchdog.blocked("okpy", "issue") is None


def test_blocked_start_is_refused_without_running(tmp_path, monkeypatch):
    from app import sites
    from app.office import jobs
    _iso(monkeypatch, tmp_path)
    sid = sites.current_id()
    for _ in range(3):
        watchdog.record(sid, "analyze", False, "못 했어요. 같은 오류")
    called = []
    jobs.reset_request()
    who = jobs.start_task("analyze", "분석 시작", lambda work: called.append(1) or "ok")
    assert who is None and not called
    assert jobs.request_result()["code"] == "repeat"


def test_silent_loop_is_restarted(monkeypatch):
    ran = []
    watchdog.register("t-loop", lambda: ran.append(1))
    watchdog._BEATS["t-loop"] = time.time() - watchdog.STALE_SEC - 5
    assert watchdog._revive_loops() == ["t-loop"]
    time.sleep(0.2)
    assert ran == [1]
    assert watchdog._revive_loops() == []   # just revived: fresh beat
    watchdog._LOOPS.pop("t-loop", None)
