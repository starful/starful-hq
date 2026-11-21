"""Corrupt dated proposal JSON can be healed from proposals.json."""
from __future__ import annotations

from pathlib import Path

from app.work import planning


def test_heal_corrupt_dated_proposal(tmp_path, monkeypatch):
    monkeypatch.setattr(planning, "PROPOSAL_DIR", tmp_path / "p")
    monkeypatch.setattr(planning, "PROPOSAL_PATH", tmp_path / "p.json")
    monkeypatch.setattr(planning, "office_dir", lambda: tmp_path / "office_p")
    aid = planning.save({"meeting": True, "mode": "leaders", "headline": "ok", "ideas": []})
    path = tmp_path / "p" / f"{aid}.json"
    path.write_text(path.read_text() + "TRAILING_JUNK", encoding="utf-8")
    assert planning.read(aid)["headline"] == "ok"
    assert "TRAILING_JUNK" not in path.read_text(encoding="utf-8")
    assert any(h["id"] == aid for h in planning.history())
