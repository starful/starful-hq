from app import snapshot
def test_sentence_names_unrecorded_files_first():
    text = snapshot.sentence_for({"dirty": True, "file_count": 2}, {"shown_count": 4})
    assert text == "고친 파일 2개가 아직 기록에 없다"


def test_claude_line_uses_saved_windows():
    text = snapshot.claude_line(
        {"windows": [{"label": "5시간", "percent": 7}, {"label": "주간", "percent": 1}]}
    )
    assert text == "5시간 7% · 주간 1%"


def test_affiliate_rows_follow_the_active_programs_without_a8():
    rows = snapshot.affiliate_rows(
        {"months": {"2026-10": {"rakuten": {"clicks": 3, "orders": 1, "commission": 10}}}},
        month="2026-10",
    )
    assert [row["label"] for row in rows] == ["楽天", "Agoda", "Amazon"]
    assert "a8" not in [row["id"] for row in rows]
    assert rows[0]["clicks"] == 3
    assert rows[1]["clicks"] == 0


def test_search_row_hidden_after_four_tries_or_without_markdown():
    assert not snapshot.row_hidden({"has_md": True}, 3)
    assert snapshot.row_hidden({"has_md": True}, 4)
    assert snapshot.row_hidden({"has_md": False}, 0)


def test_search_lists_both_patterns_and_drops_fixed_four_times(tmp_path, monkeypatch):
    import json
    from app.work import workqueue
    hist = tmp_path / "hist.json"
    hist.write_text(json.dumps({
        "https://okpy.net/blog/a#2": {"seo_attempts": [{}, {}]},
        "https://okpy.net/blog/a": {"seo_attempts": [{}, {}]},
        "https://okpy.net/blog/b": {"seo_attempts": [{}]},
    }))
    cache = tmp_path / "cache.json"
    row = lambda u, md=True: {"label": u, "has_md": md, "impressions": 100, "ctr": 0.01, "position": 5,
                              "md_files": ["okpy/app/content/posts/python/x.md"]}
    cache.write_text(json.dumps({"cache_day": "2026-10-19", "gsc": {
        "low_ctr": [row("https://okpy.net/blog/a#2"), row("https://okpy.net/blog/b"), row("https://okpy.net/blog/b#3")],
        "low_impression": [row("https://okpy.net/blog/c"), row("https://okpy.net/blog/d", md=False)]}}))
    monkeypatch.setattr(snapshot, "URL_HISTORY", hist)
    monkeypatch.setattr(snapshot, "TARGETS_PATH", tmp_path / "none.json")
    # Real prefs may turn the backlog off; this test only covers row filtering.
    monkeypatch.setattr(workqueue, "search_backlog_on", lambda site_id=None: True)
    out = snapshot._search(cache)
    assert [r["path"] for r in out["rows"]] == ["b", "c"]
    assert [r["pattern"] for r in out["rows"]] == ["low_ctr", "low_impression"]
    assert out["rows"][0]["seo_count"] == 1 and out["rows"][0]["rel"] == "python/x.md"
    assert out["skipped_tries"] == 1
