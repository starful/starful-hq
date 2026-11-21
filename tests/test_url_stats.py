"""URL별 숫자: GA4 페이지 + GSC 페이지를 경로로 합친 표."""
import sys

from app import url_stats


def test_path_key_normalizes_urls_and_paths():
    assert url_stats.path_key("https://okramen.net/guide/tokyo/?x=1") == "/guide/tokyo"
    assert url_stats.path_key("/guide/tokyo") == "/guide/tokyo"
    assert url_stats.path_key("/guide/tokyo/") == "/guide/tokyo"
    assert url_stats.path_key("https://okramen.net/") == "/"
    assert url_stats.path_key("") == "/"
    assert url_stats.path_key("guide?lang=ko") == "/guide"


def test_merge_rows_joins_ga4_and_gsc_by_path():
    ga4 = [
        {"page": "/", "pageviews": 20, "users": 8, "sessions": 9},
        {"page": "/guide/tokyo", "pageviews": 12, "users": 5, "sessions": 5},
        {"page": "/guide/tokyo/", "pageviews": 3, "users": 1, "sessions": 1},
    ]
    gsc = [
        {"page": "https://x.com/guide/tokyo", "clicks": 4, "impressions": 200, "ctr": 0.02, "position": 5.0},
        {"page": "https://x.com/guide/osaka", "clicks": 1, "impressions": 100, "ctr": 0.01, "position": 8.0},
    ]
    rows = url_stats.merge_rows(ga4, gsc)
    by = {r["path"]: r for r in rows}
    assert [r["path"] for r in rows] == ["/", "/guide/tokyo", "/guide/osaka"]
    assert by["/guide/tokyo"]["views"] == 15 and by["/guide/tokyo"]["users"] == 6
    assert by["/guide/tokyo"]["clicks"] == 4 and by["/guide/tokyo"]["ctr"] == 2.0 and by["/guide/tokyo"]["position"] == 5.0
    assert by["/guide/osaka"]["views"] == 0 and by["/guide/osaka"]["impressions"] == 100
    assert by["/"]["ctr"] is None and by["/"]["position"] is None


def test_url_tab_renders_table(monkeypatch):
    from app import ga4_users
    from app.routes import app as flask_app

    monkeypatch.setattr(ga4_users, "_okadmin", lambda: None)
    api = type(sys)("analytics_api")
    api.fetch_ga4_pages = lambda prop, days=7: {"rows": [{"page": "/a", "pageviews": 7, "users": 3, "sessions": 4}]}
    api.fetch_gsc_pages = lambda url, days=7: {"rows": [{"page": "https://x.com/a/", "clicks": 2, "impressions": 50,
                                                         "ctr": 0.04, "position": 3.2}]}
    api.site_analytics_config = lambda oid: {"ga4_property_id": "123", "gsc_site_url": "sc-domain:x.com"}
    monkeypatch.setitem(sys.modules, "analytics_api", api)
    url_stats._CACHE.clear()

    html = flask_app.test_client().get("/site/okramen/urls?days=30").get_data(as_text=True)
    assert 'data-path="/a"' in html and "검색 클릭" in html
    assert 'data-views="7"' in html and 'data-clicks="2"' in html and "4.0%" in html

    data = url_stats.fetch("okramen", 30)
    assert data["ok"] and data["cached"] and data["totals"]["views"] == 7


def test_users_dialog_has_url_tab(monkeypatch):
    from app import site_users
    from app.routes import app as flask_app

    monkeypatch.setattr(site_users, "fetch", lambda sid, days, fresh=False: {
        "ok": True, "kind": "ga4", "days": days, "btn": "이용자 보기", "title": "t", "kpis": [], "funnel": []})
    html = flask_app.test_client().get("/site/okramen/users").get_data(as_text=True)
    assert 'data-su-tab="urls"' in html and 'id="su-urls"' in html and "/site/okramen/urls" in html


def test_page_titles_pick_most_viewed_and_drop_site_tail():
    rows = [
        {"page": "/guide/ramen-prices", "title": "How much is ramen in Japan? | OK Ramen", "views": 50},
        {"page": "/guide/ramen-prices/", "title": "Old title | OK Ramen", "views": 3},
        {"page": "/shops/ichiran", "title": "Ichiran Shibuya | OK Ramen", "views": 9},
        {"page": "/", "title": "(not set)", "views": 4},
    ]
    t = url_stats.page_titles(rows)
    assert t == {"/guide/ramen-prices": "How much is ramen in Japan?", "/shops/ichiran": "Ichiran Shibuya"}
    merged = url_stats.merge_rows([{"page": "/shops/ichiran", "pageviews": 9, "users": 5, "sessions": 5},
                                   {"page": "/", "pageviews": 4, "users": 2, "sessions": 2}], [], t)
    assert {r["path"]: r["title"] for r in merged} == {"/shops/ichiran": "Ichiran Shibuya", "/": ""}


def test_long_titles_stay_on_one_line():
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "app/static/sitekpi_users.css").read_text()
    assert ".su-urls-table .su-url a { display: block; overflow: hidden; text-overflow: ellipsis; }" in css
    html = (Path(__file__).resolve().parents[1] / "app/templates/_url_stats.html").read_text()
    assert "data-su-find" not in html and 'data-su-sort="title"' in html


def test_site_tail_removed_whatever_separator():
    t = url_stats.page_titles([
        {"page": "/a", "title": "Ramen After Drinking | OKRamen", "views": 3},
        {"page": "/b", "title": "일본 편의점 컵라면 추천 베스트 5 | 세븐일레븐 & 로손 - OKRamen", "views": 2},
        {"page": "/c", "title": "Lawson Ramen Guide — OKRamen", "views": 1},
        {"page": "/d", "title": "Tonkotsu vs Shoyu - which to order first", "views": 1},
    ])
    assert t["/a"] == "Ramen After Drinking"
    assert t["/b"] == "일본 편의점 컵라면 추천 베스트 5 | 세븐일레븐 & 로손"
    assert t["/c"] == "Lawson Ramen Guide"
    assert t["/d"] == "Tonkotsu vs Shoyu - which to order first"


def test_site_name_tail_removed_even_when_rare():
    rows = [{"page": f"/p{i}", "title": f"Plain title {i}", "views": 1} for i in range(10)]
    rows.append({"page": "/x", "title": "Best Japan Ramen Souvenirs | OKRamen", "views": 1})
    rows.append({"page": "/y", "title": "컵라면 추천 - OK Ramen", "views": 1})
    t = url_stats.page_titles(rows, ("OK Ramen", "okramen"))
    assert t["/x"] == "Best Japan Ramen Souvenirs" and t["/y"] == "컵라면 추천" and t["/p1"] == "Plain title 1"


def test_site_name_tail_without_spaces_or_fullwidth_bar():
    names = ("OKPy", "okpy")
    t = url_stats.page_titles([
        {"page": "/a", "title": "PyArrowで大規模データ処理を10倍高速化する【完全ガイド】— OKPy", "views": 1},
        {"page": "/b", "title": "Bokehで2軸グラフ｜OKPy", "views": 1},
        {"page": "/c", "title": "FastAPI - Pydantic v2 入門", "views": 1},
    ], names)
    assert t == {"/a": "PyArrowで大規模データ処理を10倍高速化する【完全ガイド】", "/b": "Bokehで2軸グラフ",
                 "/c": "FastAPI - Pydantic v2 入門"}


def test_double_tail_from_real_okonsen_titles():
    names = ("OK Onsen", "okonsen")
    rows = [{"page": f"/g{i}", "title": f"Guide {i} - OKOnsen Guide", "views": 1} for i in range(4)]
    rows += [
        {"page": "/guide/guide_expand_008_en", "views": 7,
         "title": "Japan Konbini Guide: Food, ATMs, Wi-Fi & Travel Tips | OKOnsen - OKOnsen Guide"},
        {"page": "/onsen/kasho", "views": 2,
         "title": "Jozankei Onsen Kasho Gyoen: Riverside Ryokan Near Sapporo - OKOnsen - OKOnsen Guide"},
    ]
    t = url_stats.page_titles(rows, names)
    assert t["/guide/guide_expand_008_en"] == "Japan Konbini Guide: Food, ATMs, Wi-Fi & Travel Tips"
    assert t["/onsen/kasho"] == "Jozankei Onsen Kasho Gyoen: Riverside Ryokan Near Sapporo"
    assert t["/g1"] == "Guide 1"


def test_site_name_guide_tail():
    t = url_stats.page_titles([
        {"page": "/a", "title": "Seoul vs Busan for Plastic Surgery in Korea - KR Care Guide", "views": 1},
        {"page": "/b", "title": "Clinic Guide for Busan", "views": 1},
    ], ("KR Care", "krcare"))
    assert t == {"/a": "Seoul vs Busan for Plastic Surgery in Korea", "/b": "Clinic Guide for Busan"}
