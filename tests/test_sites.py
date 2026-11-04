import threading
import time

from app.office import jobs
from app import office
from app import sites
def test_registry_and_offices():
    assert [s.id for s in sites.office_sites("datalab")] == ["okpy", "jpcampus", "krcampus", "companydb"]
    assert [s.id for s in sites.office_sites("playground")] == ["okramen", "okcaddie", "okonsen", "jpfun"]
    assert sites.OFFICES["datalab"].name == "Data Lab" and sites.OFFICES["playground"].name == "Playground"
    assert [s.id for s in sites.enabled()] == [
        "okpy", "jpcampus", "krcampus", "companydb", "okramen", "okcaddie", "okonsen", "jpfun", "krcare",
    ]
    assert sites.SITES["okpy"].automerge and not sites.SITES["jpcampus"].automerge


def test_paths_follow_the_current_site():
    assert sites.current_id() == "okpy"
    assert str(sites.REPO).endswith("/okpy") and sites.data_path("git_flow.json").name == "git_flow.json"
    assert sites.data_path("git_flow.json").parent.name == "data"          # okpy keeps the old place
    with sites.use("jpcampus"):
        assert str(sites.REPO).endswith("/jpcampus")
        assert str(sites.POSTS).endswith("/jpcampus/app/content")
        assert str(office.FLOW_PATH).endswith("data/sites/jpcampus/git_flow.json")
    assert str(sites.REPO).endswith("/okpy")


def test_a_job_keeps_its_site_and_busy_work_is_per_site():
    jobs.JOBS.clear()
    seen, gate = {}, threading.Event()

    def work(w):
        seen["site"] = sites.current_id()
        gate.wait(3)
        return "ok"
    with sites.use("jpcampus"):
        assert jobs.start("daeun", "리뷰", work, _task="review")
    for _ in range(50):
        if "site" in seen:
            break
        time.sleep(0.02)
    assert seen["site"] == "jpcampus" and jobs.get("daeun")["site"] == "jpcampus"
    with sites.use("jpcampus"):
        assert "pr" in jobs.busy_groups()
    assert "pr" not in jobs.busy_groups()                 # okpy's PR lane is free
    assert sites.git_lock("okpy") is not sites.git_lock("jpcampus")
    gate.set()


def test_concurrency_cap_lets_hand_offs_through(monkeypatch):
    jobs.JOBS.clear()
    monkeypatch.setattr(sites, "MAX_CONCURRENT", 1)
    gate, handed = threading.Event(), {}

    def first(w):
        handed["ok"] = jobs.start("doyun", "푸시", lambda w2: "pushed", _task="push")   # hand-off from inside a job
        gate.wait(3)
        return "ok"
    assert jobs.start("daeun", "리뷰", first, _task="review")
    for _ in range(50):
        if "ok" in handed:
            break
        time.sleep(0.02)
    assert handed["ok"] is True
    assert jobs.start("haneul", "그림", lambda w: "x", _task="images") is False   # a new job waits for room
    gate.set()


def _unconnected(monkeypatch):
    """Every site is connected now: make one look 'not connected yet' for these tests."""
    import dataclasses

    monkeypatch.setitem(
        sites.SITES, "jpfun",
        dataclasses.replace(sites.SITES["jpfun"], enabled=False, content=""),
    )


def test_site_urls_and_not_ready_page(monkeypatch):
    from app.routes import app as flask_app

    _unconnected(monkeypatch)
    from app import routes as routes_mod
    from flask import url_for

    client = flask_app.test_client()
    with flask_app.test_request_context("/site/okpy/write"):
        routes_mod._pick_site("site_write", {"site": "okpy"})
        assert url_for("site_search") == "/site/okpy/search"
        assert url_for("site_search", site="jpcampus") == "/site/jpcampus/search"
    page = client.get("/site/jpfun")
    assert page.status_code == 200 and "아직 연결 전" in page.get_data(as_text=True)   # same page as the others, read-only
    assert client.get("/site/nope").status_code == 404


def _jp(tmp_path, monkeypatch):
    monkeypatch.setattr(sites, "WORK_ROOT", tmp_path)
    content = tmp_path / "jpcampus" / "app" / "content"
    content.mkdir(parents=True)
    (content / "guide_housing.md").write_text('---\n{\n  "title": "Housing Guide",\n  "id": "housing"\n}\n---\nbody\n')
    (content / "guide_housing_kr.md").write_text("---\n{}\n---\n# 집 구하기\n")
    (content / "univ_tokyo.md").write_text("x")
    return content


def test_flat_posts_site(tmp_path, monkeypatch):
    from app import snapshot
    _jp(tmp_path, monkeypatch)
    with sites.use("jpcampus"):
        c = snapshot._content()
        assert c["posts"] == 2 and c["sections"] == {"guide": 1, "univ": 1} and c["translations"] == 1   # _kr counted apart
        assert {r["rel"] for r in c["newest"]} == {"guide_housing.md", "univ_tokyo.md"}
        assert [k["label"] for k in snapshot.kinds_of(c)] == ["가이드", "대학"]
        assert snapshot.safe_post("guide_housing.md") and snapshot.safe_post("x/guide_housing.md") is None
        assert snapshot._title_of(snapshot.safe_post("guide_housing.md")) == "Housing Guide"
        row = {"md_files": ["jpcampus/app/content/guide_housing.md"]}
        assert snapshot._md_rel(row) == "guide_housing.md"


def test_flat_site_urls_map_to_files(tmp_path, monkeypatch):
    from app.work import marketing
    _jp(tmp_path, monkeypatch)
    with sites.use("jpcampus"):
        idx = marketing.post_index()
        assert marketing.slug_of("https://jpcampus.net/guide/housing") in idx
        assert marketing.slug_of("https://jpcampus.net/guide/housing?lang=kr") == "guide_housing_kr"
        assert marketing.slug_of("https://jpcampus.net/school/univ_tokyo") == "univ_tokyo"
    assert marketing.slug_of("https://okpy.net/blog/abc#2") == "abc"


def test_owner_wip_is_kept_out(tmp_path, monkeypatch):
    from app import snapshot
    monkeypatch.setattr(sites, "ROOT", tmp_path)
    with sites.use("jpcampus"):
        assert snapshot.owner_wip(["app/a8.py", "app/t.html"]) == ["app/a8.py", "app/t.html"]   # first look: saved
        assert snapshot.owner_wip(["app/a8.py", "app/new.py"]) == ["app/a8.py"]                 # later edits are not the owner's
        assert snapshot.owner_wip([]) == []                                                      # committed: free
        assert snapshot.owner_wip(["app/a8.py"]) == []
    assert snapshot.owner_wip(["x.py"]) == []                                                   # okpy: nothing to protect


def test_unconnected_site_gets_no_writing_work_and_meetings_follow_the_site(monkeypatch):
    from app.office import meeting
    _unconnected(monkeypatch)
    jobs.JOBS.clear()
    with sites.use("jpfun"):        # writing not connected yet
        assert jobs.start_task("write", "x", lambda w: "x") is None
        assert jobs.start_task_split("write", [["a", "b"]], lambda n: "x", lambda w, i: "x") == []
        ideas = meeting._normalize({"ideas": [{"who": "seoyeon", "title": "t", "action": {"type": "write"}}]}, ["seoyeon"])
        assert ideas[0]["action"]["type"] == "none"
    import llm_stub
    seen = {}
    llm_stub.capture = seen
    with sites.use("jpcampus"):
        meeting._ask_all(["seoyeon"], {}, "all", "", "seoyeon")
    assert "JP Campus" in seen["prompt"] and "OKPy" not in seen["prompt"] and "Budget" in seen["prompt"]


def test_owner_wip_holds_pr_and_deploy(tmp_path, monkeypatch):
    from app import snapshot
    jobs.JOBS.clear()
    monkeypatch.setattr(snapshot, "owner_wip", lambda files=None: ["app/a8.py"] if sites.current_id() == "jpcampus" else [])
    with sites.use("jpcampus"):
        assert jobs.start_task("issue", "x", lambda w: "x") is None
        assert jobs.start_task("deploy", "x", lambda w: "x") is None
        assert jobs.start_task("search", "x", lambda w: "x")      # SEO fixes on posts still go


def test_jpcampus_research_picks_and_covers(tmp_path, monkeypatch):
    from app.content import jpcontent
    from app import snapshot
    content = _jp(tmp_path, monkeypatch)
    with sites.use("jpcampus"):
        assert jpcontent.is_site() and sites.current().can_write
        assert jpcontent.existing_guides() == {"housing": "Housing Guide"}
        picks = jpcontent.picks_from({"topics": [
            {"title": "Housing Guide 2", "slug": "housing", "category": "Housing"},            # exists: dropped
            {"title": "Part-time Jobs in Osaka", "slug": "", "category": "Part-time", "why": "w"},
            {"title": "Dup", "slug": "part-time-jobs-in-osaka"},                               # same slug: dropped
            {"title": "Odd", "category": "Nope"}]})
        assert [p["row"]["slug"] for p in picks] == ["part-time-jobs-in-osaka", "odd"]
        assert picks[1]["category"] == "Life" and picks[0]["status"] == "new"
        assert "Housing Guide" in jpcontent.research_prompt([{"word": "留学", "from": "trend"}], 5)
        assert jpcontent._set_meta(content / "guide_housing.md", thumbnail="/static/images/guide_housing.jpg")
        meta = jpcontent._front(content / "guide_housing.md")[0]
        assert meta["thumbnail"] == "/static/images/guide_housing.jpg" and meta["title"] == "Housing Guide"
        images = content.parent / "static" / "images"
        images.mkdir(parents=True)
        (images / "guide_housing.jpg").write_bytes(b"x")
        assert snapshot._images()["files"][0]["rel"] == "guide_housing.md"


def test_krcampus_uses_its_own_translation_and_thumbnails(tmp_path, monkeypatch):
    from app.content import jpcontent
    from app.work import marketing
    monkeypatch.setattr(sites, "WORK_ROOT", tmp_path)
    content = tmp_path / "krcampus" / "app" / "content"
    content.mkdir(parents=True)
    (content / "guide_arc.md").write_text('---\n{"title": "ARC Guide"}\n---\nbody\n')
    (content / "guide_arc_ja.md").write_text('---\n{"title": "外国人登録"}\n---\nbody\n')
    with sites.use("krcampus"):
        k = jpcontent.kit()
        assert jpcontent.is_site() and k.suffix == "_ja" and k.translate_script == "3.create_japanese_content.py"
        assert k.thumb_base.startswith("https://krcampus.net/") and "Selection" in jpcontent.CATEGORIES
        assert jpcontent.existing_guides() == {"arc": "ARC Guide"}
        assert marketing.slug_of("https://krcampus.net/guide/arc?lang=ja") == "guide_arc_ja"
        assert "Korea" in jpcontent.research_prompt([{"word": "유학", "from": "t"}], 3)
    with sites.use("jpcampus"):
        assert jpcontent.kit().suffix == "_kr"


def test_guide_cover_is_a_real_photo(tmp_path, monkeypatch):
    import sys
    import types

    from PIL import Image

    from app.content import jpcontent
    from app.work import workers
    monkeypatch.setattr(sites, "WORK_ROOT", tmp_path)
    monkeypatch.setattr(workers, "BACKUP_DIR", tmp_path / "bk")
    monkeypatch.setattr(workers, "_okadmin_import", lambda: None)
    repo = tmp_path / "krcampus"
    content, images = repo / "app" / "content", repo / "app" / "static" / "images"
    content.mkdir(parents=True)
    images.mkdir(parents=True)
    (content / "guide_top-unis.md").write_text('---\n{"title": "Top Universities", "thumbnail": "https://images.unsplash.com/x"}\n---\nbody\n')
    (content / "guide_top-unis_ja.md").write_text('---\n{"title": "トップ大学"}\n---\nbody\n')
    (content / "guide_visa.md").write_text('---\n{"title": "Visa"}\n---\nbody\n')
    (content / "univ_snu.md").write_text('---\n{"title": "Seoul National University"}\n---\n')
    Image.new("RGB", (800, 500), "green").save(images / "univ_snu.jpg")
    for n in ("univ_a.jpg", "univ_b.jpg"):   # same placeholder file twice: never used as a cover
        Image.new("RGB", (10, 10), "white").save(images / n)
    answers = {"Top Universities": {"place_query": None, "school": "univ_snu.jpg"},
               "Visa": {"place_query": "Seoul Immigration Office", "school": None}}
    monkeypatch.setitem(sys.modules, "llm_claude", types.SimpleNamespace(
        MODEL_LIGHT="l", claude_json=lambda prompt, **kw: next(v for k, v in answers.items() if f"Guide title: {k}\n" in prompt)))

    class W:
        role = "haneul"

        def say(self, m):
            pass
    with sites.use("krcampus"):
        assert set(jpcontent._real_school_photos()) == {"univ_snu.jpg"}
        assert jpcontent.stock_cover_slugs() == ["top-unis", "visa"]
        r = jpcontent.place_cover(W(), "top-unis")
        assert r["ok"] and r["source"] == "school" and Image.open(images / "guide_top-unis.jpg").size == (1200, 675)
        assert jpcontent._front(content / "guide_top-unis_ja.md")[0]["thumbnail"] == "https://krcampus.net/static/images/guide_top-unis.jpg"
        seen = {}

        def fake_run(work, role, code, args, timeout, python=None):
            seen["args"] = args
            Image.new("RGB", (1200, 675)).save(args[2])
            return {"ok": True, "place": "Seoul Immigration Office", "credit": "Kim"}
        monkeypatch.setattr(workers, "_run_okpy", fake_run)
        r = jpcontent.place_cover(W(), "visa")
        assert r["source"] == "places" and seen["args"][0] == "Seoul Immigration Office" and seen["args"][1] == "KRCAMPUS_GOOGLE_MAPS_API_KEY"
        meta = jpcontent._front(content / "guide_visa.md")[0]
        assert meta["thumbnail_credit"] == "Photo: Kim / Google"
        assert jpcontent.stock_cover_slugs() == []


def test_companydb_pages_and_adding_a_company(tmp_path, monkeypatch):
    import json as _json

    from app.content import cdbcontent
    from app.work import marketing
    from app import snapshot
    from app.work import workers
    monkeypatch.setattr(sites, "WORK_ROOT", tmp_path)
    repo = tmp_path / "companydb"
    pages, data = repo / "output" / "companies", repo / "data"
    pages.mkdir(parents=True)
    data.mkdir()
    (pages / "7203.html").write_text("<html><head><title>Toyota Motor · CompanyDB</title></head></html>")
    (pages / "005930.html").write_text("<title>Samsung</title>")
    (pages / "NVDA.html").write_text("<title>NVIDIA</title>")
    (data / "catalog.json").write_text(_json.dumps([{"ticker": "7203", "company_en": "Toyota Motor"}]))
    with sites.use("companydb"):
        c = snapshot._content()
        assert c["posts"] == 3 and c["sections"] == {"JP": 1, "KR": 1, "US": 1}
        assert {r["title"] for r in c["newest"]} == {"Toyota Motor · CompanyDB", "Samsung", "NVIDIA"}
        assert snapshot.safe_post("NVDA.html") and snapshot.safe_post("x.md") is None
        assert marketing.slug_of("https://companydb.net/companies/XOM.html") == "XOM"
        assert workers.categories() == ("JP", "KR", "US")
        assert cdbcontent.resolve("JP", "6758") == {"market": "JP", "ticker": "6758", "name": ""}
        seen = {}
        monkeypatch.setattr(workers, "_run_okpy", lambda work, role, code, args, timeout, python=None, **k: seen.update(args=args) or {"ok": True, "title": "Sony"})

        class W:
            role = "seoyeon"

            def say(self, m):
                pass
        assert cdbcontent.add_company(W(), "JP", "6758")["title"] == "Sony" and seen["args"] == ["JP", "6758", ""]
        import pytest
        with pytest.raises(RuntimeError, match="이미 있는 회사"):
            cdbcontent.add_company(W(), "JP", "7203")
        picks = cdbcontent.picks_from({"topics": [{"market": "US", "ticker": "aapl", "name_en": "Apple"},
                                                  {"market": "JP", "ticker": "7203"}, {"market": "XX", "ticker": "1"}]})
        assert [p["row"]["ticker"] for p in picks] == ["AAPL"]
        assert jobs.start_task("polish", "x", lambda w: "x") is None


def test_companydb_queue_counts_as_ready_topics(tmp_path, monkeypatch):
    import json as _json

    from app.work import actions
    from app.work import workers
    monkeypatch.setattr(sites, "WORK_ROOT", tmp_path)
    monkeypatch.setattr(sites, "ROOT", tmp_path)
    data = tmp_path / "companydb" / "data"
    data.mkdir(parents=True)
    (data / "catalog.json").write_text(_json.dumps([{"ticker": "7203"}]))
    (data / "universe_extra.json").write_text(_json.dumps({"JP": ["7203", "4739"], "KR": ["36030"], "US": ["pg"],
                                                           "names": {"PG": "Procter & Gamble"}}))
    with sites.use("companydb"):
        picks = workers.read_research()["picks"]
        assert [p["row"]["ticker"] for p in picks] == ["4739", "036030", "PG"] and picks[2]["topic"] == "PG Procter & Gamble"
        assert workers.read_research(raw=True) == {}
        assert actions._new_picks() == ["q-JP-4739", "q-KR-036030", "q-US-PG"]
        workers._set_pick("q-US-PG", status="writing")        # queue picks are never saved into research.json
        assert not workers.read_research(raw=True).get("picks")


def test_companydb_has_no_polish():
    from app.work import marketing
    from app.office import meeting
    jobs.JOBS.clear()
    with sites.use("companydb"):
        assert not sites.current().can_polish
        assert jobs.start_task_split("polish", ["000270.html"], lambda n: "x", lambda w, r, a: "x") == []
        assert marketing.polish_candidates({}, {})["rows"] == []
        ideas = meeting._normalize({"ideas": [{"who": "subin", "title": "t", "action": {"type": "polish"}}]}, ["subin"])
        assert ideas[0]["action"]["type"] == "none"
    assert sites.SITES["okpy"].can_polish and sites.SITES["jpcampus"].can_polish


def test_each_site_names_its_content():
    assert sites.SITES["okpy"].words["picks"] == "추천 글"
    okpy = [{"kind": "클라우드 비교"}, {"kind": "클라우드 비교"}, {"kind": "Python 라이브러리"}]
    assert sites.picks_text(okpy, sites.SITES["okpy"]) == "클라우드 비교 2편 · Python 라이브러리 1편"
    assert sites.picks_text([{"bank": "queue"}] * 3, sites.SITES["companydb"]) == "회사 3곳"
    assert sites.josa("회사", "을/를") == "회사를" and sites.josa("가이드", "이/가") == "가이드가" and sites.josa("글", "을/를") == "글을"
    assert sites.SITES["jpcampus"].words["picks"] == sites.SITES["krcampus"].words["picks"] == "추천 가이드"
    w = sites.SITES["companydb"].words
    assert w["picks"] == "추천 회사" and w["unit"] == "곳" and w["verb"] == "추가하기"
    assert "pandas" in sites.SITES["okpy"].words["seed_hint"]
    assert "ramen" in sites.SITES["okramen"].words["seed_hint"]
    assert "7203" in sites.SITES["companydb"].words["topics_hint"]
    from app import office
    with sites.use("companydb"):
        assert office.run_label("write", "") == "추천 회사 추가하기"


def test_okramen_place_site_layout(tmp_path, monkeypatch):
    """okramen: shop pairs at the top, guides/ folder; _ko is the translation; /ramen/<id> maps to the file."""
    from app.work import marketing
    from app import snapshot
    content = tmp_path / "app" / "content"
    (content / "guides").mkdir(parents=True)
    for n in ("afuri_harajuku_en", "afuri_harajuku_ko", "ichiran_shinjuku_en", "ichiran_shinjuku_ko"):
        (content / f"{n}.md").write_text(f"---\ntitle: {n}\n---\nbody\n", encoding="utf-8")
    for n in ("tsukemen_guide_en", "tsukemen_guide_ko"):
        (content / "guides" / f"{n}.md").write_text(f"---\ntitle: {n}\n---\nbody\n", encoding="utf-8")
    monkeypatch.setattr(sites, "WORK_ROOT", tmp_path.parent)
    with sites.use("okramen"):
        monkeypatch.setattr(snapshot, "POSTS_ROOT", content)
        c = snapshot._content()
        assert c["posts"] == 3 and c["sections"] == {"guide": 1, "shop": 2} and c["translations"] == 3
        assert [l["label"] for l in c["langs"]] == ["영어", "한국어"]
        assert snapshot.rel_of(content / "guides" / "tsukemen_guide_en.md") == "guides/tsukemen_guide_en.md"
        assert snapshot.safe_post("guides/tsukemen_guide_ko.md") is not None
        assert marketing.slug_of("https://okramen.net/ramen/afuri_harajuku_ko") == "afuri_harajuku_ko"
        assert marketing.slug_of("https://okramen.net/guide/tsukemen_guide?lang=ko") == "tsukemen_guide_ko"
        assert sites.picks_text([{"kind": "가게"}, {"kind": "가게"}, {"kind": "가이드"}]) == "가게 2곳 · 가이드 1편"


def test_generated_files_are_noise():
    from app import snapshot
    assert snapshot.is_generated("app/__pycache__/a8_affiliate.cpython-314.pyc")
    assert snapshot.is_generated(".pytest_cache/v/cache/nodeids") and snapshot.is_generated("app/static/.DS_Store")
    assert not snapshot.is_generated("app/a8_affiliate.py") and not snapshot.is_generated("app/templates/detail.html")


def test_okcaddie_three_languages(tmp_path, monkeypatch):
    """okcaddie: en + ko + ja per course, guides with - ids; course files keep the generator's name rule."""
    from app.content import poicontent
    from app import snapshot
    content = tmp_path / "app" / "content"
    (content / "guides").mkdir(parents=True)
    for lang in ("en", "ko", "ja"):
        (content / f"abc_golf_club_{lang}.md").write_text("---\ntitle: x\n---\nb\n", encoding="utf-8")
        (content / "guides" / f"autumn-leaves-golf_{lang}.md").write_text("---\ntitle: x\n---\nb\n", encoding="utf-8")
    with sites.use("okcaddie"):
        monkeypatch.setattr(snapshot, "POSTS_ROOT", content)
        c = snapshot._content()
        assert c["posts"] == 2 and c["sections"] == {"course": 1, "guide": 1} and c["translations"] == 4
        assert [l["label"] for l in c["langs"]] == ["영어", "일본어", "한국어"] or [l["label"] for l in c["langs"]] == ["영어", "한국어", "일본어"]
        assert poicontent.CATEGORIES == ("골프장", "가이드")
        assert poicontent.safe_name("Taiheiyo Club & Co. Gotemba") == "taiheiyo_club_and_co_gotemba"
        assert poicontent.guide_id("Summer Golf Hokkaido") == "summer-golf-hokkaido"
        assert sites.current().words["noun"] == "골프장·가이드"
        assert sites.picks_text([{"kind": "골프장"}, {"kind": "가이드"}]) == "골프장 1곳 · 가이드 1편"
    with sites.use("okramen"):
        assert poicontent.safe_name("Ramen Danbo Fukuoka") == "ramen_danbo_fukuoka" and poicontent.CATEGORIES[0] == "가게"
