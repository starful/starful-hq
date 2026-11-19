"""사이트별 이용자 보기: SiteKPI visitlog · 나머지 GA4."""
import urllib.error

from app import ga4_users, site_users, sitekpi_users


SAMPLE = {
    "days": 7, "generated_at": "11-14 17:00", "answered": 1, "keep_days": 90,
    "kpis": [{"label": "방문자", "value": 3, "unit": "명", "note": "이전 7일 1명"},
             {"label": "보드를 본 사람", "value": 2, "unit": "명", "note": ""},
             {"label": "GA4 연결", "value": 1, "unit": "명", "note": ""},
             {"label": "설문에 답한 사람", "value": 1, "unit": "명", "note": ""}],
    "funnel": [{"label": "랜딩", "n": 3, "pct": "100%"}, {"label": "견본 보드", "n": 2, "pct": "67%"}],
    "stuck": "가장 많이 멈추는 곳: 랜딩 → 견본 보드 (3명 중 2명)",
    "survey": [{"q": "오늘 무엇을 하려고 왔나", "rows": [{"label": "상사·고객 보고에 쓰고 싶다", "n": 1}]}],
    "wishes": [{"text": "주간 보고 그대로", "who": "a1b2 · 사이트 운영자"}],
    "visitors": [{"id": "a1b2", "first": "11-14 09:10", "last": "11-14 09:20", "from": "www.google.com",
                  "why": "상사·고객 보고에 쓰고 싶다", "role": "사이트 운영자", "did": "견본 보드", "visits": 2,
                  "lang": "ja", "steps": [{"at": "09:10", "what": "랜딩", "where": "/", "tone": ""}]}],
}


def _setup_sku(monkeypatch, result):
    sitekpi_users.forget()
    monkeypatch.setattr(sitekpi_users, "_token", lambda: "tok")

    def fake(days, token):
        if isinstance(result, Exception):
            raise result
        return {**result, "days": days}
    monkeypatch.setattr(sitekpi_users, "_get", fake)


def test_sitekpi_fetch_via_facade(monkeypatch):
    _setup_sku(monkeypatch, SAMPLE)
    u = site_users.fetch("sitekpi", 7)
    assert u["ok"] and u["kind"] == "visitlog" and u["survey_on"] is True
    assert u["btn"] == "이용자 · 설문 보기"


def test_ga4_users_builds_pages_and_channels(monkeypatch):
    ga4_users.forget()

    def fake_sum(prop, days=7):
        return {"totals": {"users": 10, "sessions": 12, "pageviews": 40, "new_users": 4}, "rows": []}

    def fake_pages(prop, days=7):
        return {"rows": [
            {"page": "/", "users": 8, "pageviews": 20, "sessions": 9},
            {"page": "/guide/tokyo", "users": 5, "pageviews": 12, "sessions": 5},
            {"page": "/guide/tokyo/visa", "users": 2, "pageviews": 3, "sessions": 2},
        ]}

    def fake_ch(prop, days=7):
        return {"rows": [{"channel": "Organic Search", "users": 6, "sessions": 7},
                         {"channel": "Direct", "users": 4, "sessions": 5}]}

    monkeypatch.setattr(ga4_users, "_okadmin", lambda: None)
    import sys
    api = type(sys)("analytics_api")
    api.fetch_ga4_summary = fake_sum
    api.fetch_ga4_pages = fake_pages
    api.fetch_ga4_channels = fake_ch
    api.site_analytics_config = lambda oid: {"ga4_property_id": "123"}
    sys.modules["analytics_api"] = api

    u = site_users.fetch("jpcampus", 7)  # visitlog not wired yet → GA4 fallback
    assert u["ok"] and u["kind"] == "ga4" and u["survey_on"] is False
    assert u["btn"] == "이용자 보기"
    assert u["kpis"][0]["value"] == 10
    assert u["pages"][0]["label"] == "/"
    assert u["channels"][0]["label"] == "Organic Search"
    assert any(f["label"] == "홈" for f in u["funnel"])


def test_popup_html_sitekpi(monkeypatch):
    from app.routes import app as flask_app
    _setup_sku(monkeypatch, SAMPLE)
    html = flask_app.test_client().get("/site/sitekpi/users?days=30").get_data(as_text=True)
    assert 'id="su-card"' in html and "이용자 · 설문 보기" in html
    assert '<dialog' in html and "주간 보고 그대로" in html and "a1b2의 흐름" in html
    assert 'data-su-days="30" aria-pressed="true"' in html


def test_status_has_button_on_all_sites():
    from app.routes import app as flask_app
    from app import sites
    assert sites.SITES["okpy"].visitlog is True
    c = flask_app.test_client()
    sku = c.get("/site/sitekpi").get_data(as_text=True)
    assert "이용자 · 설문 보기" in sku and 'id="su-card"' in sku
    okpy = c.get("/site/okpy").get_data(as_text=True)
    assert "이용자 보기" in okpy and 'id="su-card"' in okpy and "/site/okpy/users" in okpy
    assert "방문 <b>" not in okpy


def test_okpy_uses_visitlog_facade(monkeypatch):
    from app import visitlog_users
    monkeypatch.setattr(visitlog_users, "fetch", lambda sid, days=7, fresh=False: {
        "ok": True, "days": days, "kind": "visitlog", "kpis": [], "funnel": [],
        "visitors": [{"id": "abcd", "first": "11-19 10:00", "from": "직접", "why": "—", "did": "둘러봄",
                     "visits": 1, "steps": [{"at": "10:00", "what": "홈", "where": "/", "tone": ""}]}],
        "stuck": "", "survey": [], "answered": 0, "wishes": [], "generated_at": "11-19 10:00",
    })
    u = site_users.fetch("okpy", 7)
    assert u["kind"] == "visitlog" and u["survey_on"] is False
    assert u["visitors"][0]["id"] == "abcd"


def test_sitekpi_users_alias_still_works(monkeypatch):
    from app.routes import app as flask_app
    _setup_sku(monkeypatch, SAMPLE)
    html = flask_app.test_client().get("/site/sitekpi/sitekpi-users").get_data(as_text=True)
    assert 'id="su-card"' in html
