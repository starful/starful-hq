from app.work import planning
def test_proposals_are_saved_with_history(tmp_path, monkeypatch):
    monkeypatch.setattr(planning, "PROPOSAL_DIR", tmp_path / "p")
    monkeypatch.setattr(planning, "PROPOSAL_PATH", tmp_path / "p.json")
    monkeypatch.setattr(planning, "office_dir", lambda: tmp_path / "office")
    pid = planning.save({"at": "2026-10-20 08:00", "focus_label": "전체", "headline": "요약",
                         "features": [{"title": "관련 글 카드", "why": "체류 57초", "how": "글 하단", "effort": "작음", "team": "개발팀"}]})
    assert planning.read()["headline"] == "요약"
    assert planning.read(pid)["features"][0]["title"] == "관련 글 카드"
    assert [h["id"] for h in planning.history()] == [pid]
    md = (tmp_path / "p" / f"{pid}.md").read_text()
    assert "## 기능 추가" in md and "### 관련 글 카드" in md and "- 왜: 체류 57초" in md


def test_read_missing_id_does_not_fall_back_to_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(planning, "PROPOSAL_DIR", tmp_path / "p")
    monkeypatch.setattr(planning, "PROPOSAL_PATH", tmp_path / "p.json")
    monkeypatch.setattr(planning, "office_dir", lambda: tmp_path / "office")
    planning.save({"at": "2026-10-20 08:00", "focus_label": "전체", "headline": "최신", "meeting": True, "ideas": []})
    assert planning.read("no-such-meeting") == {}
    assert planning.read()["headline"] == "최신"


def test_list_keeps_only_titled_items():
    got = planning._list([{"title": "a", "why": "w"}, {"why": "no title"}, "x"], 5)
    assert got == [{"title": "a", "why": "w", "how": "", "effort": "", "impact": "", "team": ""}]
