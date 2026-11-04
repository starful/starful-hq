"""Planning write screen helpers: seed chips + analysis topics → picks."""

from __future__ import annotations


def test_seed_chips_caps_and_prefers_defaults(monkeypatch):
    from app.work import workers

    monkeypatch.setattr(workers, "seed_options", lambda: {
        "defaults": ["A", "B", "C"],
        "ja": ["A", "D", "E", "F"],
        "ko": ["G", "H"],
        "en": [f"E{i}" for i in range(20)],
    })
    chips = workers.seed_chips(20)
    assert len(chips["all"]) == 20
    assert chips["all"][0] == "A"
    assert set(chips["checked"]) <= set(chips["all"])
    assert len(chips["checked"]) <= 3


def test_ensure_analysis_picks_merges_once(monkeypatch, tmp_path):
    from app.work import workers, marketing

    research = tmp_path / "research.json"
    monkeypatch.setattr(workers, "RESEARCH_PATH", research)
    monkeypatch.setattr(workers, "categories", lambda: ("python", "cloud"))
    monkeypatch.setattr(marketing, "read_analysis", lambda: {
        "topics": ["Topic One", "Topic Two", "Topic One"],
    })

    workers.ensure_analysis_picks()
    first = workers.read_research(raw=True)["picks"]
    assert len(first) == 2
    assert all(p["source"] == "analysis" and p["status"] == "new" for p in first)

    workers.ensure_analysis_picks()
    assert len(workers.read_research(raw=True)["picks"]) == 2
