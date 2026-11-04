import time

from app.office import jobs
from app.office import meeting
from app import office
from app.work import planning
def test_everyone_free_meets_and_the_result_is_saved(tmp_path, monkeypatch):
    jobs.JOBS.clear()
    monkeypatch.setattr(planning, "PROPOSAL_DIR", tmp_path / "p")
    monkeypatch.setattr(planning, "PROPOSAL_PATH", tmp_path / "p.json")
    monkeypatch.setattr(meeting.tools, "need", lambda *a: None)
    monkeypatch.setattr(meeting.marketing, "_okadmin_import", lambda: None)
    monkeypatch.setattr(meeting.snapshot, "load_or_refresh", lambda: {})
    monkeypatch.setattr(meeting.planning, "context", lambda snap: {})
    calls = []

    def fake_all(members, ctx, focus, ask, host, per=1, topic='', kind='', *a, **kw):
        calls.append(host)
        return {"ideas": [{"who": m, "title": f"{m} 제안", "action": {"type": "write" if m == "jiho" else "feature", "topics": ["polars 入門"], "area": "front"}}
                          for m in members] + [{"who": "jiho", "title": "두 번째는 버림"}],
                "ranking": ["minjae", "jiho"], "headline": "결론", "leader_note": "먼저 SEO"}
    monkeypatch.setattr(meeting, "_ask_all", fake_all)
    monkeypatch.setattr(meeting, "SHOW_SEC", 0)
    # 하준 is busy: he stays at his desk
    import threading
    gate = threading.Event()
    jobs.start("hajun", "일하는 중", lambda w: (gate.wait(5), "ok")[1], _task="refactor_front")
    came = meeting.start("feature", "")
    assert "hajun" not in came and len(came) == 12
    assert all(jobs.get(r).get("seat") in office.MEETING_SEATS for r in came)
    assert meeting.start() == []  # one meeting at a time
    for _ in range(50):
        if not meeting.running():
            break
        time.sleep(0.1)
    time.sleep(0.3)
    saved = planning.read()
    assert calls == ["daeun"]  # one call, the leader ranks
    assert saved["meeting"] and saved["headline"] == "결론" and len(saved["ideas"]) == 12  # one idea per person
    assert [i["who"] for i in saved["ideas"][:2]] == ["minjae", "jiho"] and saved["ideas"][0]["rank"] == 1
    assert saved["office"] and saved["sites"] == ["okpy", "jpcampus", "krcampus", "companydb"]          # 전사 회의 = every site of the office
    assert all(i["site"] in ("okpy", "jpcampus", "krcampus", "companydb") for i in saved["ideas"])
    assert "## 우선순위" in planning.file_of(saved["id"], ".md").read_text()
    assert planning.history()[0]["office"]
    assert jobs.get("jiho")["line"].startswith("회의 끝")
    gate.set()


def test_running_an_idea_goes_on_the_execution_queue(tmp_path, monkeypatch):
    from app.work import actions
    from app.work import workqueue
    jobs.JOBS.clear()
    monkeypatch.setattr(planning, "PROPOSAL_DIR", tmp_path / "p")
    monkeypatch.setattr(planning, "PROPOSAL_PATH", tmp_path / "p.json")
    monkeypatch.setattr(workqueue, "_queue_path", lambda site_id=None: tmp_path / "q.json")
    monkeypatch.setattr(workqueue, "_prefs_path", lambda site_id=None: tmp_path / "prefs.json")
    monkeypatch.setattr(workqueue, "kick", lambda sid: None)
    monkeypatch.setattr(actions, "_open_pr", lambda: None)
    pid = planning.save({"meeting": True, "ideas": [
        {"title": "polars 글", "action": {"type": "write", "category": "python", "topics": ["polars 入門"]}},
        {"title": "관련 글 카드", "how": "글 아래", "action": {"type": "feature", "area": "front", "spec": "관련 글 3개"}},
        {"title": "SNS 계정", "action": {"type": "none"}}]})
    assert actions.proposal_run(pid, 0)["ok"]
    assert actions.proposal_run(pid, 1)["ok"]
    assert not actions.proposal_run(pid, 2)["ok"]
    queued = [x for x in workqueue.list_items() if x["status"] == "queued"]
    assert len(queued) == 2
    assert queued[0]["task"] == "write" and queued[1]["task"] == "feature"


def test_leaders_meet_in_room_b_with_two_ideas_each(tmp_path, monkeypatch):
    jobs.JOBS.clear()
    monkeypatch.setattr(planning, "PROPOSAL_DIR", tmp_path / "p")
    monkeypatch.setattr(planning, "PROPOSAL_PATH", tmp_path / "p.json")
    monkeypatch.setattr(meeting.tools, "need", lambda *a: None)
    monkeypatch.setattr(meeting.marketing, "_okadmin_import", lambda: None)
    monkeypatch.setattr(meeting.snapshot, "load_or_refresh", lambda: {})
    monkeypatch.setattr(meeting.planning, "context", lambda snap: {})
    monkeypatch.setattr(meeting, "SHOW_SEC", 0)
    seen = {}

    def fake_all(members, ctx, focus, ask, host, per=1, topic="", kind="", *a, **kw):
        seen.update(per=per, topic=topic, kind=kind)
        return {"ideas": [{"who": m, "key": f"{m}-{i}", "title": f"{m} {i}"} for m in members for i in (1, 2, 3)],
                "ranking": ["haneul-2", "daeun"], "headline": "리더 결론"}
    monkeypatch.setattr(meeting, "_ask_all", fake_all)
    came = meeting.start("all", "", "leaders", "어필리 수익 늘리기")
    assert set(came) == set(office.LEADERS)
    assert all(jobs.get(r).get("seat") in office.LEADER_SEATS for r in came)
    for _ in range(50):
        if not meeting.running("leaders"):
            break
        time.sleep(0.1)
    time.sleep(0.3)
    saved = planning.read()
    assert seen == {"per": 2, "topic": "어필리 수익 늘리기", "kind": "리더"}
    assert saved["mode"] == "leaders" and saved["focus_label"] == "어필리 수익 늘리기"
    assert len(saved["ideas"]) == 8  # 4 leaders x 2, the third idea is dropped
    assert saved["ideas"][0]["title"] == "haneul 2" and saved["ideas"][1]["who"] == "daeun"


def test_boss_calls_up_to_five_people_to_the_ceo_office(tmp_path, monkeypatch):
    jobs.JOBS.clear()
    monkeypatch.setattr(planning, "PROPOSAL_DIR", tmp_path / "p")
    monkeypatch.setattr(planning, "PROPOSAL_PATH", tmp_path / "p.json")
    monkeypatch.setattr(meeting.tools, "need", lambda *a: None)
    monkeypatch.setattr(meeting.marketing, "_okadmin_import", lambda: None)
    monkeypatch.setattr(meeting.snapshot, "load_or_refresh", lambda: {})
    monkeypatch.setattr(meeting.planning, "context", lambda snap: {})
    monkeypatch.setattr(meeting, "SHOW_SEC", 0)
    seen = {}

    def fake_all(members, ctx, focus, ask, host, per=1, topic="", kind="", *a, **kw):
        seen.update(per=per, topic=topic, kind=kind, n=len(members))
        return {"ideas": [{"who": m, "key": f"{m}-1", "title": f"{m} 1"} for m in members], "headline": "사장실 결론"}
    monkeypatch.setattr(meeting, "_ask_all", fake_all)
    want = ["seoyeon", "jiho", "yuna", "minjae", "subin", "haneul", "nobody"]
    came = meeting.start("all", "", "boss", "", want)
    assert came == ["seoyeon", "jiho", "yuna", "minjae", "subin"]   # five chairs; Taekki takes the sixth
    assert all(jobs.get(r).get("seat") in office.BOSS_TABLE[:5] for r in came)
    for _ in range(50):
        if not meeting.running("boss"):
            break
        time.sleep(0.1)
    time.sleep(0.3)
    saved = planning.read()
    assert seen == {"per": 1, "topic": "", "kind": "사장실 호출", "n": 5}
    assert saved["mode"] == "boss" and saved["focus_label"] == planning.FOCUS["all"]


def test_boss_login_rules(tmp_path, monkeypatch):
    from app.routes import app as flask_app
    from app.office import boss
    monkeypatch.setattr(boss, "CONFIG_PATH", tmp_path / "boss.json")
    monkeypatch.setattr(boss, "STATE_PATH", tmp_path / "state.json")
    assert boss.is_boss("Starful0418@gmail.com") and not boss.is_boss("someone@example.com") and not boss.is_boss("")
    client = flask_app.test_client()
    with client.session_transaction() as s:
        s["boss_email"] = "starful0418@gmail.com"
    called = []
    from app.office import jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "start_task", lambda *a, **k: called.append(a[0]) or "x")
    assert client.post("/site/okpy/ship").status_code == 403          # deploy
    assert client.post("/site/okpy/run/review", headers={"X-Requested-With": "fetch"}).status_code == 403
    assert called == []
    assert client.post("/boss/move", json={"x": 50, "y": 60}).get_json() == {"ok": True}
    st = client.get("/office").get_json()["boss"]
    assert st["me"] and st["present"] and st["pos"] == {"x": 50.0, "y": 60.0}
    client.get("/logout")
    assert client.post("/boss/move", json={"x": 1, "y": 1}).status_code == 403


def test_team_meetings_happen_at_team_desks(tmp_path, monkeypatch):
    jobs.JOBS.clear()
    monkeypatch.setattr(planning, "PROPOSAL_DIR", tmp_path / "p")
    monkeypatch.setattr(planning, "PROPOSAL_PATH", tmp_path / "p.json")
    monkeypatch.setattr(meeting.tools, "need", lambda *a: None)
    monkeypatch.setattr(meeting.marketing, "_okadmin_import", lambda: None)
    monkeypatch.setattr(meeting.snapshot, "load_or_refresh", lambda: {})
    monkeypatch.setattr(meeting.planning, "context", lambda snap: {})
    monkeypatch.setattr(meeting, "SHOW_SEC", 0)
    import threading
    gate = threading.Event()
    seen = []

    def fake_all(members, ctx, focus, ask, host, per=1, topic="", kind="", *a, **kw):
        gate.wait(5)
        seen.append({"host": host, "per": per, "topic": topic, "kind": kind, "n": len(members)})
        return {"ideas": [{"who": m, "key": f"{m}-1", "title": f"{m} 1"} for m in members], "headline": "결론"}
    monkeypatch.setattr(meeting, "_ask_all", fake_all)
    came = meeting.start("all", "", "mkt")
    assert came == ["minjae", "subin"]
    assert all(jobs.get(r).get("seat") == office.DESKS[r] for r in came)      # at their own desks
    assert meeting.start("all", "", "design") == ["haneul", "mio"]              # another team at the same time
    gate.set()
    for _ in range(80):
        if not any(meeting.running(m) for m in ("mkt", "design")):
            break
        time.sleep(0.1)
    time.sleep(0.3)
    mk = next(x for x in seen if x["kind"] == "Marketing 팀")
    assert mk == {"host": "minjae", "per": 2, "topic": meeting.TEAM_TOPIC["mkt"], "kind": "Marketing 팀", "n": 2}


def test_all_hands_optional_topic_is_aside_not_main(tmp_path, monkeypatch):
    jobs.JOBS.clear()
    monkeypatch.setattr(planning, "PROPOSAL_DIR", tmp_path / "p")
    monkeypatch.setattr(planning, "PROPOSAL_PATH", tmp_path / "p.json")
    monkeypatch.setattr(meeting.tools, "need", lambda *a: None)
    monkeypatch.setattr(meeting.marketing, "_okadmin_import", lambda: None)
    monkeypatch.setattr(meeting.snapshot, "load_or_refresh", lambda: {})
    monkeypatch.setattr(meeting.planning, "context", lambda snap: {})
    monkeypatch.setattr(meeting, "SHOW_SEC", 0)
    seen = {}

    def fake_all(members, ctx, focus, ask, host, per=1, topic="", kind="", *a, **kw):
        seen.update(topic=topic, kind=kind, focus=focus)
        return {"ideas": [{"who": m, "key": f"{m}-1", "title": f"{m} 1"} for m in members], "headline": "전사 결론"}
    monkeypatch.setattr(meeting, "_ask_all", fake_all)
    came = meeting.start("all", "", "all", "저CTR 글")
    assert len(came) >= 2
    for _ in range(50):
        if not meeting.running("all"):
            break
        time.sleep(0.1)
    time.sleep(0.3)
    saved = planning.read()
    assert seen["kind"] == "전사" and seen["topic"] == "저CTR 글"
    assert saved["topic"] == "저CTR 글"
    assert saved["focus_label"].startswith(planning.FOCUS["all"])
    assert "참고 저CTR 글" in saved["focus_label"]
    assert saved["focus_label"] != "저CTR 글"  # aside must not become the sole main


def test_affiliate_summary_reads_okadmin_numbers(tmp_path, monkeypatch):
    import json
    from app.money import affiliate
    d = tmp_path / "okadmin" / "data"
    d.mkdir(parents=True)
    (d / "affiliate_reports.json").write_text(json.dumps({"months": {
        "2026-09": {"rakuten": {"clicks": 3055, "orders": 4, "commission": 3228.0}},
        "2026-10": {"rakuten": {"clicks": 849, "orders": 3, "commission": 1875.0, "updated_at": "2026-10-19 14:25:38"},
                    "agoda": {"clicks": 10, "commission": 0.0}}}}))
    monkeypatch.setattr(affiliate.snapshot, "WORK_ROOT", tmp_path)
    s = affiliate.summary()
    assert s["month"] == "2026-10" and s["prev_month"] == "2026-09"
    rak = next(r for r in s["rows"] if r["id"] == "rakuten")
    assert rak["commission"] == "¥1,875" and rak["prev"] == "¥3,228" and rak["prev_clicks"] == 3055
    assert s["total"] == "¥1,875" and s["orders"] == 3 and s["updated"] == "2026-10-19 14:25"
    assert next(r for r in s["rows"] if r["id"] == "agoda")["commission"] == "$0.00"
