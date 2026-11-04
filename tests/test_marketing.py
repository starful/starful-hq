from app.work import marketing
from app import snapshot
def _posts(tmp_path, monkeypatch):
    root = tmp_path / "posts"
    for i, name in enumerate(["old", "mid", "new", "seen4"]):
        (root / "python").mkdir(parents=True, exist_ok=True)
        p = root / "python" / f"{name}.md"
        p.write_text("---\ntitle: x\n---\nbody\n")
        import os
        os.utime(p, (1000 + i, 1000 + i))
    monkeypatch.setattr(snapshot, "POSTS_ROOT", root)
    monkeypatch.setattr(marketing, "POLISH_PATH", tmp_path / "polish.json")
    return root


def test_polish_candidates_order_and_four_try_limit(tmp_path, monkeypatch):
    _posts(tmp_path, monkeypatch)
    for _ in range(4):
        marketing.record_polish("python/seen4.md")
    site = {"search": {"rows": [{"pattern": "low_impression", "url": "https://okpy.net/blog/new", "impressions": 7, "rel": ""}]}}
    out = marketing.polish_candidates(site, {"polish": [{"rel": "python/mid.md", "why": "참여율 낮음"}]})
    assert [r["rel"] for r in out["rows"]] == ["python/mid.md", "python/new.md", "python/old.md"]
    assert [r["tag"] for r in out["rows"]] == ["ai", "gsc", "old"]
    assert out["skipped"] == 1


def test_mkt_targets_merges_search_and_polish(tmp_path, monkeypatch):
    _posts(tmp_path, monkeypatch)
    site = {
        "search": {
            "rows": [
                {
                    "pattern": "low_ctr",
                    "pattern_label": "클릭 낮음",
                    "url": "https://okpy.net/blog/mid",
                    "path": "mid",
                    "rel": "python/mid.md",
                    "ctr": 0.01,
                    "impressions": 100,
                    "position": 12,
                    "seo_count": 0,
                },
                {
                    "pattern": "low_impression",
                    "pattern_label": "노출 적음",
                    "url": "https://okpy.net/blog/only-search",
                    "path": "only-search",
                    "rel": "",
                    "ctr": 0.02,
                    "impressions": 5,
                    "position": 20,
                    "seo_count": 0,
                },
            ]
        }
    }
    analysis = {
        "search": [{"url": "https://okpy.net/blog/mid", "why": "CTR 낮음"}],
        "polish": [{"rel": "python/mid.md", "why": "참여율 낮음"}],
    }
    out = marketing.mkt_targets(site, analysis)
    by_rel = {r["rel"]: r for r in out["rows"] if r.get("rel")}
    mid = by_rel["python/mid.md"]
    assert mid["jobs"] == ["search", "polish"]
    assert "ai" in mid["tags"] and "search" in mid["tags"] and "polish" in mid["tags"]
    assert mid["value"].endswith("search,polish")
    assert any(r["url"] == "https://okpy.net/blog/only-search" and r["jobs"] == ["search"] for r in out["rows"])
    assert any(r["rel"] == "python/old.md" and r["jobs"] == ["polish"] for r in out["rows"])
    assert out["search_n"] >= 2 and out["polish_n"] >= 2


def test_slug_of_decodes_blog_urls():
    assert marketing.slug_of("https://okpy.net/blog/pandas%E5%85%A5%E9%96%80#2") == "pandas入門"
    assert marketing.slug_of("/blog/abc/") == "abc"


def test_numbers_are_computed_not_guessed():
    nums = marketing.numbers_from({"ga4": {"totals": {"sessions": 1947, "engagement_rate": 0.3}, "deltas": {"sessions": 5.2}},
                                   "gsc_daily": {"totals": {"clicks": 10, "ctr": 0.01, "position": 12.3}, "deltas": {"clicks": -3}}})
    by = {n["label"]: n for n in nums}
    assert by["방문(세션)"]["value"] == "1,947" and by["방문(세션)"]["delta"] == "+5.2%"
    assert by["검색 클릭"]["delta"] == "-3.0%"


def test_traffic_card_compacts_overview(monkeypatch):
    monkeypatch.setattr(marketing, "_okadmin_import", lambda: None)
    monkeypatch.setattr(marketing, "_ensure_ga4_creds", lambda: None)

    def fake_overview(site_id, *, days=28, refresh=False, phase="all"):
        return {
            "compare_label": "이전 28일 대비",
            "cache_hit": True,
            "ga4": {
                "totals": {"sessions": 100, "users": 80, "events": 400, "pageviews": 200, "engagement_rate": 0.32},
                "deltas": {"users": 65.3, "events": 59.9, "pageviews": 62.4, "engagement_rate": -11.5},
                "rows": [{"date": "20261001", "sessions": 3, "users": 2, "events": 10, "pageviews": 5, "engaged_sessions": 1}],
                "start": "2026-09-01", "end": "2026-09-28",
            },
            "gsc_daily": {"totals": {"clicks": 12, "impressions": 900}, "deltas": {"clicks": 1.0}},
            "gsc_queries": {"rows": [{"query": "라멘", "clicks": 5, "impressions": 100, "ctr": 0.05, "position": 4.2}]},
        }

    import sys
    api = type(sys)("analytics_api")
    api.load_analytics_overview = fake_overview
    api.fetch_ga4_summary = lambda *a, **k: {"rows": []}
    api.site_analytics_config = lambda _sid: {"ga4_property_id": ""}
    sys.modules["analytics_api"] = api
    card = marketing.traffic_card()
    assert card["ga4"]["ok"]
    assert card["ga4"]["kpis"][0]["id"] == "users" and card["ga4"]["kpis"][0]["value"] == "80"
    assert card["ga4"]["kpis"][0]["pct"] == "+65.3%"
    assert card["gsc"]["queries"][0]["query"] == "라멘"
    assert card["ga4"]["rows"][0]["engagement"] == 1 / 3


def test_prior_rows_are_disk_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(marketing, "PRIOR_DIR", tmp_path / "prior")
    monkeypatch.setattr(marketing, "_okadmin_import", lambda: None)
    calls = {"n": 0}

    def fake_fetch(prop, *, days=28, end_date=None):
        calls["n"] += 1
        return {"rows": [{"date": "20260801", "sessions": 1, "users": 1, "events": 2, "pageviews": 2, "engaged_sessions": 1}]}

    import sys
    api = type(sys)("analytics_api")
    api.fetch_ga4_summary = fake_fetch
    sys.modules["analytics_api"] = api

    a, hit = marketing._prior_rows(oid="okramen", prop="p1", days=28, current_start="2026-09-01", refresh=False)
    assert not hit and calls["n"] == 1 and a[0]["users"] == 1
    b, hit2 = marketing._prior_rows(oid="okramen", prop="p1", days=28, current_start="2026-09-01", refresh=False)
    assert hit2 and calls["n"] == 1 and b[0]["users"] == 1
    marketing._prior_rows(oid="okramen", prop="p1", days=28, current_start="2026-09-01", refresh=True)
    assert calls["n"] == 2


def test_analysis_history_keeps_every_run(tmp_path, monkeypatch):
    monkeypatch.setattr(marketing, "ANALYSIS_DIR", tmp_path / "analysis")
    monkeypatch.setattr(marketing, "ANALYSIS_PATH", tmp_path / "analysis.json")
    a1 = marketing.save_analysis({"at": "2026-10-19 23:16", "headline": "첫 번째", "numbers": []})
    import time; time.sleep(1.1)
    a2 = marketing.save_analysis({"at": "2026-10-20 07:40", "headline": "두 번째", "numbers": []})
    hist = marketing.list_analyses()
    assert [h["headline"] for h in hist] == ["두 번째", "첫 번째"]
    assert marketing.read_analysis()["headline"] == "두 번째"
    assert marketing.read_analysis(a1)["headline"] == "첫 번째"
    assert (tmp_path / "analysis" / f"{a2}.md").read_text().startswith("# OKPy GA4·GSC 분석")
    assert marketing.read_analysis("../etc")["headline"] == "두 번째"  # bad id → latest


def test_legacy_analysis_is_imported_once(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(marketing, "ANALYSIS_DIR", tmp_path / "analysis")
    monkeypatch.setattr(marketing, "ANALYSIS_PATH", tmp_path / "analysis.json")
    (tmp_path / "analysis.json").write_text(json.dumps({"at": "2026-10-19 23:16", "headline": "옛날"}))
    marketing.import_legacy_analysis()
    marketing.import_legacy_analysis()
    assert [h["headline"] for h in marketing.list_analyses()] == ["옛날"]
