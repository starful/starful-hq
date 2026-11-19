"""Shared visitkit summarize (content sites, no survey)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path("/opt/work/_shared")))

from visitkit.summarize import summarize  # noqa: E402
from visitkit.token import derive_hq_token  # noqa: E402


def test_summarize_visitor_flow():
    now = 1_700_000_000
    events = [
        {"vid": "aaaaaaaaaaaaaaaaaaaaaaaa", "at": now - 100, "e": "pv", "p": {"screen": "home"}, "path": "/", "ref": "www.google.com", "lang": "ja"},
        {"vid": "aaaaaaaaaaaaaaaaaaaaaaaa", "at": now - 80, "e": "pv", "p": {"screen": "post"}, "path": "/blog/hello", "ref": "", "lang": "ja"},
        {"vid": "aaaaaaaaaaaaaaaaaaaaaaaa", "at": now - 60, "e": "affiliate", "p": {"href": "/go/x"}, "path": "/blog/hello", "ref": "", "lang": "ja"},
        {"vid": "bbbbbbbbbbbbbbbbbbbbbbbb", "at": now - 50, "e": "pv", "p": {}, "path": "/", "ref": "", "lang": "en"},
    ]
    out = summarize(events, days=7, now=now, prev_visitors=1)
    assert out["kpis"][0]["value"] == 2
    assert out["visitors"][0]["id"] == "aaaa" or out["visitors"][1]["id"] == "aaaa"
    person = next(v for v in out["visitors"] if v["id"] == "aaaa")
    assert person["from"] == "www.google.com"
    assert any(s["what"] == "콘텐츠" for s in person["steps"])
    assert any(s["what"] == "CTA·외부" for s in person["steps"])
    assert out["stuck"]


def test_derive_token_differs_by_site():
    a = derive_hq_token("sec", "okpy")
    b = derive_hq_token("sec", "jpcampus")
    assert a and b and a != b
