from datetime import datetime

from app import sites
from app.office import meeting
from app.office import site_brief
from app.work import planning
from app.work import workqueue


PLAN = """<!doctype html><html><head><title>okramen 기획서</title><style>body{color:red}</style></head>
<body><nav class="toc"><a href="#s1">1 개요</a></nav>
<h2 id="s1">1. 개요</h2><p>여행자를 위한 &amp; 라멘 지도</p>
<h2 id="s6">6. 하지 말 것</h2><ul><li>카페는 쓰지 않음</li></ul>
<table><tr><th>수익원</th><td>Agoda</td></tr></table>
<figure><iframe srcdoc="&lt;div&gt;SCREEN&lt;/div&gt;"></iframe></figure>
<script>var x = 1;</script></body></html>"""


def test_plan_text_reads_the_drive_plan_without_tags(tmp_path, monkeypatch):
    monkeypatch.setattr(site_brief, "DRIVE_AI", tmp_path)
    site = sites.SITES["okramen"]
    assert site_brief.plan_text(site) == ""   # no file: left out
    (tmp_path / "okramen").mkdir()
    (tmp_path / "okramen" / "okramen 기획서.html").write_text(PLAN, encoding="utf-8")
    text = site_brief.plan_text(site)
    assert "1. 개요" in text and "여행자를 위한 & 라멘 지도" in text and "카페는 쓰지 않음" in text
    assert "수익원 | Agoda" in text
    for gone in ("<", "color:red", "SCREEN", "var x", "1 개요\n"):
        assert gone not in text
    short = site_brief.plan_text(site, limit=20)
    assert short.endswith("…(이하 생략)") and len(short) < 40


def test_pick_rows_top_views_and_low_ctr():
    rows = [{"path": f"/p{i}", "title": f"P{i}", "views": 100 - i, "clicks": c, "impressions": imp,
             "ctr": round(c / imp * 100, 1) if imp else None, "position": 8.0}
            for i, (c, imp) in enumerate([(50, 500), (1, 2000), (2, 900), (0, 10), (30, 300)])]
    top, low = site_brief.pick_rows(rows, n=2)
    assert [r["path"] for r in top] == ["/p0", "/p1"]
    # site CTR ≈ 3.9%: /p1 (0.05%) and /p2 (0.2%) are below it; /p3 has too few impressions
    assert [r["path"] for r in low] == ["/p1", "/p2"]


def test_stats_text_formats_and_survives_errors(monkeypatch):
    from app import url_stats
    rows = [{"path": "/ramen/a_en", "title": "Afuri", "views": 9, "clicks": 1, "impressions": 400, "ctr": 0.3, "position": 7.1},
            {"path": "/", "title": "Home", "views": 50, "clicks": 40, "impressions": 400, "ctr": 10.0, "position": 2.0}]
    monkeypatch.setattr(url_stats, "fetch", lambda sid, days=7, fresh=False: {"ok": True, "rows": rows})
    text = site_brief.stats_text("okramen")
    assert "Afuri (/ramen/a_en) 조회 9 · 클릭 1 · 노출 400 · CTR 0.3% · 순위 7.1" in text
    assert "노출 많고 CTR 낮은" in text

    def boom(*a, **k):
        raise RuntimeError("no GA4")
    monkeypatch.setattr(url_stats, "fetch", boom)
    assert site_brief.stats_text("okramen") == ""


def test_recent_text_joins_meetings_and_the_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(planning, "PROPOSAL_DIR", tmp_path / "p")
    monkeypatch.setattr(planning, "PROPOSAL_PATH", tmp_path / "p.json")
    monkeypatch.setattr(workqueue, "_queue_path", lambda site_id=None: tmp_path / "q.json")
    (tmp_path / "p").mkdir()
    old = {"id": "2026-10-01_090000", "meeting": True, "ideas": [{"title": "오래된 제안", "site": "okramen"}]}
    new = {"id": "2026-11-18_090000", "meeting": True, "ideas": [
        {"title": "지로 주문 가이드", "site": "okramen", "action": {"type": "write"}},
        {"title": "온천 글", "site": "okonsen", "action": {"type": "write"}},
        {"title": "SNS 계정", "site": "okramen", "action": {"type": "none"}},
        {"title": "제목 고치기", "site": "okramen", "action": {"type": "search"}}]}
    for p in (old, new):
        planning.marketing._write(tmp_path / "p" / f"{p['id']}.json", p)
    workqueue._write_json(tmp_path / "q.json", {"items": [
        {"source": "meeting", "pid": new["id"], "idx": 0, "status": "failed", "error": "이미 있는 가이드예요"},
        {"source": "meeting", "pid": new["id"], "idx": 3, "status": "done", "error": ""}]})
    text = site_brief.recent_text("okramen", now=datetime(2026, 11, 21))
    assert "지로 주문 가이드 [write] → 실패 (이미 있는 가이드예요)" in text
    assert "SNS 계정 [none] → 사람이 할 일" in text
    assert "제목 고치기 [search] → 완료" in text
    assert "온천 글" not in text and "오래된 제안" not in text


def test_meeting_prompt_carries_the_briefing(monkeypatch):
    monkeypatch.setattr(site_brief, "plan_text", lambda site, limit=0: "하지 말 것: 카페 가게")
    monkeypatch.setattr(site_brief, "stats_text", lambda sid: "조회수 상위 1:\n  - Afuri\n")
    monkeypatch.setattr(site_brief, "recent_text", lambda sid: "  - 2026-11-18 지로 → 실패 (이미 있는 가이드예요)\n")
    monkeypatch.setattr(meeting, "existing_pages", lambda site, limit=2000: [])
    seen = {}
    import llm_claude
    monkeypatch.setattr(llm_claude, "claude_json", lambda prompt, **kw: seen.setdefault("p", prompt) and {})
    with sites.use("okramen"):
        meeting._ask_all([meeting.office.LEADERS[0]], {}, "all", "", meeting.office.LEADERS[0])
    p = seen["p"]
    assert "### okramen" in p and "하지 말 것: 카페 가게" in p and "Afuri" in p and "이미 있는 가이드예요" in p
    assert p.index("사이트 브리핑") < p.index("데이터:")


def test_a_broken_briefing_never_stops_the_meeting(monkeypatch):
    def boom(site_list):
        raise RuntimeError("drive offline")
    monkeypatch.setattr(site_brief, "block", boom)
    assert meeting._brief_block([sites.SITES["okramen"]]) == ""
