"""SiteKPI visitlog helpers (facade tests live in test_site_users.py)."""
import hashlib
import hmac
import urllib.error

from app import sitekpi_users


def test_fetch_caches_and_errors(monkeypatch):
    sitekpi_users.forget()
    monkeypatch.setattr(sitekpi_users, "_token", lambda: "tok")
    calls = []

    def fake(days, token):
        calls.append(days)
        return {"days": days, "funnel": [{"label": "A", "n": 2}, {"label": "B", "n": 1}], "survey": []}

    monkeypatch.setattr(sitekpi_users, "_get", fake)
    a = sitekpi_users.fetch(7)
    b = sitekpi_users.fetch(7)
    assert a["ok"] and not a["cached"] and b["cached"] and calls == [7]
    assert [f["w"] for f in a["funnel"]] == [100, 50]

    sitekpi_users.forget()
    monkeypatch.setattr(sitekpi_users, "_get",
                        lambda d, t: (_ for _ in ()).throw(urllib.error.HTTPError("u", 404, "nf", {}, None)))
    assert "아직 이 창구가 없어요" in sitekpi_users.fetch(30)["error"]


def test_key_matches_sitekpi():
    assert sitekpi_users.derive("abc") == hmac.new(b"abc", b"sitekpi-hq-api-v1", hashlib.sha256).hexdigest()
    assert sitekpi_users.derive("") == ""
