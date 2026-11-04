import sys
import types

from app.money import affiliate
from app.work import workers
def test_save_writes_only_changed_platforms(monkeypatch):
    month = affiliate.datetime.now().strftime("%Y-%m")
    monkeypatch.setattr(affiliate, "_reports", lambda: {"months": {month: {
        "rakuten": {"clicks": 10, "orders": 1, "commission": 500, "note": "n"},
        "agoda": {"clicks": 2, "orders": 0, "commission": 1.5}}}})
    monkeypatch.setattr(affiliate, "_outbound", lambda *a, **k: {})
    monkeypatch.setattr(workers, "_okadmin_import", lambda: None)
    calls = []
    stub = types.SimpleNamespace(save_platform=lambda pid, **kw: calls.append((pid, kw)))
    monkeypatch.setitem(sys.modules, "affiliate_report_store", stub)
    s = affiliate.summary()
    assert s["this_month"] == month and s["rows"][0]["commission_raw"] == 500
    assert [r["id"] for r in s["rows"]] == ["rakuten", "agoda", "amazon"]   # A8 not shown
    assert [p[0] for p in s["platforms"]] == ["rakuten", "agoda", "amazon"]
    n = affiliate.save(month, {"rakuten": {"clicks": "10", "orders": "1", "commission": "500"},   # same: skipped
                               "agoda": {"clicks": "3", "orders": "0", "commission": "1.50"},
                               "a8": {"clicks": "4", "orders": "1", "commission": "¥1,200"}})
    assert n == 2 and [c[0] for c in calls] == ["a8", "agoda"]
    assert calls[0][1]["commission"] == 1200.0   # A8 can still be stored and calls[1][1]["clicks"] == 3


def test_card_renders_alone(monkeypatch):
    from app.routes import app as flask_app
    monkeypatch.setattr(affiliate, "_reports", lambda: {})
    monkeypatch.setattr(affiliate, "_outbound", lambda *a, **k: {})
    html = flask_app.test_client().get("/site/okpy/affiliate").get_data(as_text=True)
    assert 'id="aff-card"' in html and "mc-foot" in html


def test_auto_fetch_reads_dashboards_and_keeps_what_it_cannot_read(tmp_path, monkeypatch):
    from app.money import affsync
    month = affiliate.datetime.now().strftime("%Y-%m")
    monkeypatch.setattr(affsync, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(affsync, "LOG_PATH", tmp_path / "log")
    monkeypatch.setattr(affiliate, "_reports", lambda: {"months": {month: {
        "agoda": {"clicks": 1, "orders": 2, "commission": 9.5}}}})
    monkeypatch.setattr(affiliate, "_outbound", lambda *a, **k: {})
    monkeypatch.setattr(workers, "_okadmin_import", lambda: None)
    saved = {}
    monkeypatch.setattr(affiliate, "save", lambda m, values: saved.update(values) or 3)
    monkeypatch.setattr(affsync, "_our_clicks", lambda day: {"rakuten": 40, "a8": 5, "agoda": 7, "amazon": 2})
    monkeypatch.setattr(affsync, "browser_ready", lambda: True)
    monkeypatch.setattr(affsync, "_page_texts", lambda: {
        "rakuten": {"url": "https://affiliate.rakuten.co.jp/", "text": "今月 成果 3件 報酬 1,875円"},
        "agoda": {"url": "x", "text": "", "error": "timeout"},
        "amazon": {"url": "https://affiliate.amazon.co.jp/home", "text": "今月の収益 ¥0"}})
    answers = {"rakuten": {"logged_in": True, "found": True, "orders": 3, "commission": 1875},
               "amazon": {"logged_in": True, "found": True, "orders": 0, "commission": 0}}
    stub = types.SimpleNamespace(MODEL_LIGHT="l", claude_json=lambda prompt, **kw: next(
        v for k, v in answers.items() if affsync.LABELS[k] in prompt))
    monkeypatch.setitem(sys.modules, "llm_claude", stub)
    state = affsync.fetch()
    st = state["platforms"]
    assert st["rakuten"]["ok"] and "a8" not in st and not st["agoda"]["ok"] and st["amazon"]["ok"]
    assert saved["rakuten"] == {"clicks": 40, "orders": 3, "commission": 1875}
    assert saved["agoda"] == {"clicks": 7, "orders": 2, "commission": 9.5}      # dashboard not read: kept
    assert "a8" not in saved and not state["running"]      # A8 dashboard is no longer read; stored numbers untouched
    s = affsync.summary()
    assert [r["label"] for r in s["rows"]] == ["楽天", "Agoda", "Amazon"] and s["at"]
