"""Planning write screen helpers: seed chips + analysis topics → picks."""

from __future__ import annotations

import json


def test_seed_chips_caps_and_prefers_defaults(monkeypatch):
    from app.content import contentkit
    from app.work import workers

    monkeypatch.setattr(contentkit, "is_okpy", lambda: False)
    monkeypatch.setattr(workers, "seed_options", lambda: {
        "defaults": ["A", "B", "C"],
        "ja": ["A", "D", "E", "F"],
        "ko": ["G", "H"],
        "en": [f"E{i}" for i in range(20)],
    })
    chips = workers.seed_chips(20)
    assert len(chips["all"]) == 20
    assert chips["all"][0] == "A"
    assert set(chips["checked"]) <= set(chips["all"])
    assert len(chips["checked"]) <= 3


def test_okpy_seed_chips_follow_writable_categories(monkeypatch):
    from app.content import contentkit
    from app.work import workers

    monkeypatch.setattr(contentkit, "is_okpy", lambda: True)
    chips = workers.seed_chips(20)
    assert chips["all"][0] == "Python ライブラリ"
    assert "파이썬 라이브러리" not in chips["all"]
    assert "python library" not in chips["all"]
    # round-robin across categories → second row starts with cloud
    assert "AWS GCP Azure 比較" in chips["all"]
    assert "A/Bテスト" in chips["all"]  # data-analysis
    assert "エンジニア キャリア" in chips["all"]  # career
    assert set(chips["by_category"]) == set(contentkit.OKPY_CATEGORIES)
    assert len(chips["checked"]) == len(contentkit.OKPY_CATEGORIES)
    opts = workers.seed_options()
    assert opts["ko"] == [] and opts["en"] == []
    assert len(opts["defaults"]) == len(contentkit.OKPY_CATEGORIES)
    assert opts["defaults"][0] == "Python ライブラリ"
    assert opts["defaults"][4] == "A/Bテスト"


def test_okpy_fill_category_gaps(monkeypatch):
    from app.work import workers

    monkeypatch.setattr(workers, "topic_already_covered", lambda *_a, **_k: False)
    existing = {b: set() for b in workers.BANK_TO_CATEGORY}
    picks = [
        workers._okpy_make_pick("terraform", "Terraform A", idx=1),
        workers._okpy_make_pick("terraform", "Terraform B", idx=2),
        workers._okpy_make_pick("ai_models", "GPT vs Claude", idx=3),
    ]

    def fake_claude(prompt, **_kw):
        assert "need" in prompt.lower() or "MISSING" in prompt
        return {"topics": [
            {"bank": "python", "row": {"lib_name": "httpx"}, "keyword": "Python", "why": "채움"},
            {"bank": "python", "row": {"lib_name": "rich"}, "keyword": "Python", "why": "채움"},
            {"bank": "cloud", "row": {"Topic": "AWS vs GCP storage"}, "why": "채움"},
            {"bank": "cloud", "row": {"Topic": "EKS vs GKE"}, "why": "채움"},
            {"bank": "dev_method", "row": {"Topic": "CI/CD パイプライン"}, "why": "채움"},
            {"bank": "dev_method", "row": {"Topic": "テスト戦略"}, "why": "채움"},
            {"bank": "data_analysis", "row": {"Topic": "Effect size in A/B tests"}, "why": "채움"},
            {"bank": "data_analysis", "row": {"Topic": "Sample size for experiments"}, "why": "채움"},
            {"bank": "eng_comms", "row": {"Topic": "要件のすり合わせ"}, "why": "채움"},
            {"bank": "eng_comms", "row": {"Topic": "ステークホルダー合意"}, "why": "채움"},
            {"bank": "ai_models", "row": {"Topic": "Gemini vs Claude coding"}, "why": "채움"},
            {"bank": "career", "row": {"Topic": "シニアエンジニアロードマップ"}, "why": "채움"},
            {"bank": "career", "row": {"Topic": "転職ポートフォリオ"}, "why": "채움"},
        ]}

    out = workers._okpy_fill_category_gaps(
        picks, existing, [{"word": "Python ライブラリ", "from": "시드"}], fake_claude, "heavy", per_cat=2,
    )
    from collections import Counter
    counts = Counter(p["bank"] for p in out)
    assert all(counts[b] >= 2 for b in workers.BANK_TO_CATEGORY)


def test_ensure_analysis_picks_merges_once(monkeypatch, tmp_path):
    from app.content import contentkit
    from app.work import workers, marketing

    research = tmp_path / "research.json"
    monkeypatch.setattr(workers, "RESEARCH_PATH", research)
    monkeypatch.setattr(workers, "categories", lambda: ("python", "cloud"))
    monkeypatch.setattr(contentkit, "is_companydb", lambda: False)
    monkeypatch.setattr(contentkit, "is_okpy", lambda: False)  # campus/poi still merge
    monkeypatch.setattr(marketing, "read_analysis", lambda: {
        "topics": ["Topic One", "Topic Two", "Topic One"],
    })

    workers.ensure_analysis_picks()
    first = workers.read_research(raw=True)["picks"]
    assert len(first) == 2
    assert all(p["source"] == "analysis" and p["status"] == "new" for p in first)

    workers.ensure_analysis_picks()
    assert len(workers.read_research(raw=True)["picks"]) == 2


def test_okpy_skips_analysis_picks_and_strips(monkeypatch, tmp_path):
    from app.content import contentkit
    from app.work import workers, marketing

    research = tmp_path / "research.json"
    research.write_text(json.dumps({"picks": [
        {"id": "ana-1", "topic": "분석 주제", "source": "analysis", "status": "new", "category": "python"},
        {"id": "r1", "topic": "리서치", "source": "research", "status": "new", "category": "terraform"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(workers, "RESEARCH_PATH", research)
    monkeypatch.setattr(contentkit, "is_okpy", lambda: True)
    monkeypatch.setattr(contentkit, "is_companydb", lambda: False)
    monkeypatch.setattr(marketing, "read_analysis", lambda: {"topics": ["새 분석 아이디어"]})

    workers.ensure_analysis_picks()
    picks = workers.read_research(raw=True)["picks"]
    assert [p["id"] for p in picks] == ["r1"]
    workers.ensure_analysis_picks()
    assert [p["id"] for p in workers.read_research(raw=True)["picks"]] == ["r1"]


def test_pick_groups_for_write_okpy_lists_all_categories(monkeypatch, tmp_path):
    from app.content import contentkit
    from app.work import workers

    research = tmp_path / "research.json"
    research.write_text(json.dumps({"picks": [
        {"id": "a", "topic": "T1", "status": "new", "category": "terraform", "bank": "terraform"},
        {"id": "b", "topic": "T2", "status": "new", "category": "ai-models", "bank": "ai_models"},
        {"id": "c", "topic": "done", "status": "written", "category": "python"},
        {"id": "d", "topic": "ana", "status": "new", "category": "python", "source": "analysis"},
    ]}), encoding="utf-8")
    monkeypatch.setattr(workers, "RESEARCH_PATH", research)
    monkeypatch.setattr(contentkit, "is_okpy", lambda: True)

    groups = workers.pick_groups_for_write(workers.read_research(raw=True))
    assert groups is not None
    assert [g["category"] for g in groups] == list(contentkit.OKPY_CATEGORIES)
    by = {g["category"]: g for g in groups}
    assert [p["id"] for p in by["terraform"]["picks"]] == ["a"]
    assert [p["id"] for p in by["ai-models"]["picks"]] == ["b"]
    assert by["python"]["picks"] == []
    assert by["career"]["picks"] == []
    assert "Terraform" in by["terraform"]["label"]
    assert by["data-analysis"]["label"] == "Business Analysis (영문)"

    monkeypatch.setattr(contentkit, "is_okpy", lambda: False)
    assert workers.pick_groups_for_write({"picks": []}) is None


def test_ba_ja_pending_lists_en_drafts(monkeypatch, tmp_path):
    from app.content import contentkit
    from app.work import workers

    posts = tmp_path / "app" / "content" / "posts" / "data-analysis"
    posts.mkdir(parents=True)
    (posts / "en-one.md").write_text(
        "---\ntitle: 'Effect Size Guide'\nlang: en\nja_pending: true\n---\n\n# Hi\n",
        encoding="utf-8",
    )
    (posts / "ja-done.md").write_text(
        "---\ntitle: '済'\nlang: ja\n---\n\n# 日本語\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(contentkit, "is_okpy", lambda: True)
    monkeypatch.setattr(workers, "OKPY", tmp_path)

    rows = workers.ba_ja_pending()
    assert len(rows) == 1
    assert rows[0]["title"] == "Effect Size Guide"
    assert rows[0]["name"] == "en-one.md"

    monkeypatch.setattr(contentkit, "is_okpy", lambda: False)
    assert workers.ba_ja_pending() == []


def test_ensure_analysis_picks_companydb_companies_only(monkeypatch, tmp_path):
    from app.content import cdbcontent, contentkit
    from app.work import workers, marketing

    research = tmp_path / "research.json"
    research.write_text(json.dumps({"picks": [
        {"id": "ana-old", "topic": "SEC 10-K 가이드", "source": "analysis", "status": "new"},
        {"id": "ana-xom", "topic": "XOM Exxon", "source": "analysis", "status": "failed",
         "row": {"market": "US", "ticker": "XOM", "name": "Exxon"}, "error": "이미 있는"},
        {"id": "keep-me", "topic": "리서치 회사", "source": "research", "status": "new",
         "row": {"market": "JP", "ticker": "9999", "name": "Keep"}},
    ]}), encoding="utf-8")
    monkeypatch.setattr(workers, "RESEARCH_PATH", research)
    monkeypatch.setattr(contentkit, "is_okpy", lambda: False)
    monkeypatch.setattr(contentkit, "is_companydb", lambda: True)
    monkeypatch.setattr(cdbcontent, "covered", lambda: {"XOM": "Exxon Mobil Corporation"})
    monkeypatch.setattr(marketing, "read_analysis", lambda: {
        "topics": [
            "EDINET 읽는 법",  # guide string — ignore
            {"market": "US", "ticker": "XOM", "name_en": "Exxon"},  # already covered
            {"market": "KR", "ticker": "035420", "name_en": "NAVER", "why": "검색 수요"},
            {"market": "KR", "ticker": "035420", "name_en": "NAVER"},  # dup
            {"market": "XX", "ticker": "BAD"},  # bad market
        ],
    })

    workers.ensure_analysis_picks()
    picks = workers.read_research(raw=True)["picks"]
    assert [p["id"] for p in picks] == ["keep-me", "ana-035420"]
    assert picks[1]["row"]["ticker"] == "035420"
    assert picks[1]["category"] == "KR"

    workers.ensure_analysis_picks()
    assert [p["id"] for p in workers.read_research(raw=True)["picks"]] == ["keep-me", "ana-035420"]


def test_normalize_analysis_topics_companydb(monkeypatch):
    from app.content import cdbcontent, contentkit
    from app.work import marketing

    monkeypatch.setattr(contentkit, "is_companydb", lambda: True)
    monkeypatch.setattr(cdbcontent, "covered", lambda: {"7203": "Toyota"})
    out = marketing.normalize_analysis_topics([
        "가이드 글",
        {"market": "jp", "ticker": "6758", "name_en": "Sony", "why": "왜"},
        {"market": "JP", "ticker": "7203", "name_en": "Toyota"},
        {"market": "JP", "ticker": "6758"},
    ])
    assert out == [{"market": "JP", "ticker": "6758", "name_en": "Sony", "why": "왜"}]

    monkeypatch.setattr(contentkit, "is_companydb", lambda: False)
    assert marketing.normalize_analysis_topics(["A", "A", "B"]) == ["A", "A", "B"]


def test_write_picks_edinet_preflight_skips_missing(monkeypatch, tmp_path):
    from app.content import cdbcontent, contentkit
    from app.work import workers

    research = tmp_path / "research.json"
    picks = [
        {"id": "q-JP-4739", "topic": "4739 ITOCHU", "category": "JP", "status": "new",
         "row": {"market": "JP", "ticker": "4739", "name": "ITOCHU"}},
        {"id": "q-JP-7203", "topic": "7203 Toyota", "category": "JP", "status": "new",
         "row": {"market": "JP", "ticker": "7203", "name": "Toyota"}},
        {"id": "q-KR-1", "topic": "035420 NAVER", "category": "KR", "status": "new",
         "row": {"market": "KR", "ticker": "035420", "name": "NAVER"}},
    ]
    research.write_text(json.dumps({"picks": picks}), encoding="utf-8")
    monkeypatch.setattr(workers, "RESEARCH_PATH", research)
    monkeypatch.setattr(workers, "_need_content", lambda: None)
    monkeypatch.setattr(workers, "_models_for_covers", lambda: None)
    monkeypatch.setattr(workers, "_okadmin_import", lambda: None)
    monkeypatch.setattr(workers.tools, "need", lambda *_a, **_k: None)
    monkeypatch.setattr(contentkit, "is_companydb", lambda: True)
    monkeypatch.setattr(contentkit, "uses_topic_bank", lambda: False)
    monkeypatch.setattr(
        cdbcontent, "edinet_ready_tickers",
        lambda work, tickers: ({"7203"}, [t for t in tickers if t != "7203"]),
    )
    dropped: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    monkeypatch.setattr(
        cdbcontent, "drop_from_queue",
        lambda market, ticker: dropped.append((market, ticker)) or True,
    )
    monkeypatch.setattr(
        cdbcontent, "mark_skipped",
        lambda market, ticker, **kw: skipped.append((market, ticker)) or True,
    )

    written = []

    def fake_write(work, cat, topic, row=None):
        written.append((cat, (row or {}).get("ticker")))
        return {"title": topic, "path": f"{(row or {}).get('ticker')}.html"}

    monkeypatch.setattr(contentkit, "write", fake_write)

    class W:
        role = "jiho"

        def say(self, m):
            pass

    out = workers.write_picks(W(), ["q-JP-4739", "q-JP-7203", "q-KR-1"])
    assert "2편을 썼어요" in out and "1편 실패" in out
    assert written == [("JP", "7203"), ("KR", "035420")]
    assert dropped == [("JP", "4739")]
    assert skipped == [("JP", "4739")]
    saved = {p["id"]: p for p in json.loads(research.read_text())["picks"]}
    assert saved["q-JP-4739"]["status"] == "failed"
    assert "EDINET" in saved["q-JP-4739"]["error"]
    assert saved["q-JP-7203"]["status"] == "written"
    assert saved["q-KR-1"]["status"] == "written"


def test_drop_from_queue_removes_extra(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app import sites
    from app.content import cdbcontent

    repo = tmp_path / "companydb"
    (repo / "data").mkdir(parents=True)
    path = repo / "data" / "universe_extra.json"
    path.write_text(json.dumps({
        "JP": ["4739", "7203"], "KR": ["036030"], "US": ["AMD"],
        "names": {"4739": "ITOCHU", "036030": "NTS"},
        "sec_ciks": {}, "dart_codes": {"036030": "001"},
    }), encoding="utf-8")

    monkeypatch.setattr(sites, "current", lambda: SimpleNamespace(repo=repo))
    assert cdbcontent.drop_from_queue("JP", "4739") is True
    data = json.loads(path.read_text())
    assert data["JP"] == ["7203"] and "4739" not in data["names"]
    assert cdbcontent.drop_from_queue("KR", "36030") is True  # zfill match
    data = json.loads(path.read_text())
    assert data["KR"] == [] and "036030" not in (data.get("dart_codes") or {})
    assert cdbcontent.drop_from_queue("JP", "9999") is False


def test_universe_skip_blocks_picks_and_queue(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app import sites
    from app.content import cdbcontent

    repo = tmp_path / "companydb"
    (repo / "data").mkdir(parents=True)
    (repo / "data" / "catalog.json").write_text("[]", encoding="utf-8")
    (repo / "data" / "universe_extra.json").write_text(json.dumps({
        "JP": ["4739", "7203"], "KR": [], "US": [], "names": {"4739": "X", "7203": "Toyota"},
    }), encoding="utf-8")
    monkeypatch.setattr(sites, "current", lambda: SimpleNamespace(repo=repo))

    assert cdbcontent.mark_skipped("JP", "4739", reason="EDINET 유가증권보고서 없음", name="X")
    assert cdbcontent.is_skipped("JP", "4739")
    assert "4739" in cdbcontent.skipped_tickers()
    picks = cdbcontent.picks_from({
        "topics": [
            {"market": "JP", "ticker": "4739", "name_en": "X"},
            {"market": "JP", "ticker": "9984", "name_en": "SoftBank"},
        ],
    })
    assert [p["row"]["ticker"] for p in picks] == ["9984"]
    queued = cdbcontent.queue_picks()
    assert [p["row"]["ticker"] for p in queued] == ["7203"]
    assert "4739" in cdbcontent.research_prompt([], 3)


def test_companydb_skips_keyword_research_ui_flag():
    from app import sites

    assert sites.SITES["companydb"].can_research is False
    assert sites.SITES["okpy"].can_research is True


def test_parse_add_line_and_enqueue_ticker(tmp_path, monkeypatch):
    from app.content import cdbcontent, jpcontent
    from app import sites
    from types import SimpleNamespace

    assert cdbcontent.parse_add_line("JP 4661") == {"market": "JP", "ticker": "4661"}
    assert cdbcontent.parse_add_line("003550") == {"market": "KR", "ticker": "003550"}
    assert cdbcontent.parse_add_line("cost") == {"market": "US", "ticker": "COST"}

    repo = tmp_path / "companydb"
    (repo / "script").mkdir(parents=True)
    (repo / "script" / "build_company_page.py").write_text("# stub\n", encoding="utf-8")
    monkeypatch.setattr(sites, "current", lambda: SimpleNamespace(repo=repo, content="companydb"))
    monkeypatch.setattr(jpcontent, "_site_python", lambda: "python3")

    calls = {}

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        class P:
            returncode = 0
            stdout = json.dumps({"ok": True, "market": "JP", "ticker": "4661", "already_in_universe": False, "built": False})
            stderr = ""
        return P()

    monkeypatch.setattr("subprocess.run", fake_run)
    out = cdbcontent.enqueue_ticker("JP", "4661", name_en="Oriental Land")
    assert out["ticker"] == "4661" and "--add" in calls["cmd"] and "Oriental Land" in calls["cmd"]


def test_companydb_market_quota_60_20_20():
    from app.content import cdbcontent

    assert cdbcontent.market_quota(10) == {"JP": 6, "KR": 2, "US": 2}
    assert cdbcontent.market_quota(5) == {"JP": 3, "KR": 1, "US": 1}
    assert sum(cdbcontent.market_quota(8).values()) == 8
    assert all(cdbcontent.market_quota(8)[m] >= 1 for m in cdbcontent.CATEGORIES)


def test_companydb_picks_from_respects_quota(monkeypatch):
    from app.content import cdbcontent

    monkeypatch.setattr(cdbcontent, "covered", lambda: {})
    monkeypatch.setattr(cdbcontent, "skipped_tickers", lambda: set())
    topics = [{"market": "JP", "ticker": f"{1000 + i}", "name_en": f"J{i}"} for i in range(10)]
    topics += [{"market": "KR", "ticker": f"{i:06d}", "name_en": f"K{i}"} for i in range(5)]
    topics += [{"market": "US", "ticker": f"U{i}", "name_en": f"U{i}"} for i in range(5)]
    picks = cdbcontent.picks_from({"topics": topics}, n=10)
    from collections import Counter
    c = Counter(p["row"]["market"] for p in picks)
    assert c == {"JP": 6, "KR": 2, "US": 2}


def test_find_and_queue_companies_no_owner_input(monkeypatch, tmp_path):
    from app.content import cdbcontent, contentkit
    from app.work import workers

    research = tmp_path / "research.json"
    monkeypatch.setattr(workers, "RESEARCH_PATH", research)
    monkeypatch.setattr(workers, "_need_content", lambda: None)
    monkeypatch.setattr(workers, "_okadmin_import", lambda: None)
    monkeypatch.setattr(workers, "_search_keywords", lambda: [])
    monkeypatch.setattr(workers, "seed_options", lambda: {
        "defaults": ["japanese stocks"], "en": [], "ko": [], "ja": [],
    })
    monkeypatch.setattr(contentkit, "is_companydb", lambda: True)
    monkeypatch.setattr(contentkit, "research_say", lambda n: f"키워드 {n}개")

    class FakeTools:
        @staticmethod
        def need(_name):
            return None

    monkeypatch.setattr(workers, "tools", FakeTools)

    import sys
    import types

    fake_llm = types.ModuleType("llm_claude")
    fake_llm.MODEL_HEAVY = "heavy"
    fake_llm.claude_json = lambda *a, **k: {"topics": [
        {"market": "US", "ticker": "MSFT", "name_en": "Microsoft", "why": "테스트"},
        {"market": "JP", "ticker": "7203", "name_en": "Toyota", "why": "테스트"},
        {"market": "KR", "ticker": "005930", "name_en": "Samsung", "why": "테스트"},
    ]}
    monkeypatch.setitem(sys.modules, "llm_claude", fake_llm)
    monkeypatch.setattr(cdbcontent, "covered", lambda: {})
    monkeypatch.setattr(cdbcontent, "skipped_tickers", lambda: set())
    monkeypatch.setattr(
        cdbcontent, "enqueue_ticker",
        lambda m, t, name_en="": {"ok": True, "market": m, "ticker": t, "built": False},
    )

    class W:
        def say(self, *_a, **_k):
            return None

    msg = workers.find_and_queue_companies(W(), 8)
    assert "3곳 찾아 대기열에 넣었어요" in msg
    saved = json.loads(research.read_text())
    assert len(saved["picks"]) == 3
