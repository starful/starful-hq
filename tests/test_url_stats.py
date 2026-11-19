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
