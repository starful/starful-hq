"""YouTube attach is registry-driven (hq.youtube) and owned by the design team."""
from app import sites
from app.content import youtube_queue as ytq


def test_youtube_enabled_from_registry():
    assert sites.SITES["okramen"].youtube_on
    assert sites.SITES["jpfun"].youtube_on
    assert sites.SITES["okcaddie"].youtube_on
    assert sites.SITES["okonsen"].youtube_on
    assert not sites.SITES["okpy"].youtube_on
    assert "okramen" in ytq.youtube_site_ids()
    assert "okonsen" in ytq.youtube_site_ids()


def test_youtube_query_from_yaml():
    assert "ラーメン" in ytq.default_query("Ichiran", "Shinjuku, Tokyo", site_id="okramen")
    assert "ゴルフ" in ytq.default_query("Some Course", site_id="okcaddie")
    assert "温泉" in ytq.default_query("Yufuin So", site_id="okonsen")
    assert "スキー" in ytq.default_query("Hakuba", site_id="jpfun", activity="ski")
    assert ytq.site_noun("okramen") == "가게"
    assert ytq.site_noun("okonsen") == "온천"
    assert ytq.site_page_prefix("okcaddie") == "course"
    assert ytq.site_page_prefix("okonsen") == "onsen"


def test_youtube_page_redirects_to_design_tab():
    from app import sites
    from app.routes import app
    sites.reload_hq()
    c = app.test_client()
    assert c.get("/site/okpy/youtube").status_code == 404
    r2 = c.get("/site/okonsen/youtube", follow_redirects=False)
    assert r2.status_code in (301, 302)
    loc = r2.headers.get("Location") or ""
    assert "/site/okonsen/images" in loc
    assert loc.endswith("#youtube") or "#youtube" in loc


def test_youtube_ui_in_design_images():
    from app import sites
    from app.routes import app
    sites.reload_hq()
    c = app.test_client()
    r = c.get("/site/okonsen/images")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'data-design-mode="youtube"' in body
    assert 'id="yt-wrap"' in body
    assert "data-yt-api=" in body
    assert "후보 검색" in body
    assert 'id="yt-ship"' in body and "반영 맡기기" in body
    assert 'class="pick-f yt-tab on" data-filter="missing"' in body
    assert 'class="pick-f yt-tab" data-filter="attached"' in body
    assert ">없음<" in body and ">있음<" in body
    assert 'id="yt-pop-search"' not in body
    assert 'id="yt-pop-q"' not in body
    assert 'yt-tab" data-filter="all"' not in body
    r_off = c.get("/site/okpy/images")
    assert r_off.status_code == 200
    off = r_off.get_data(as_text=True)
    assert 'data-design-mode="youtube"' not in off


def test_youtube_ship_status_and_enqueue(monkeypatch, tmp_path):
    from app import sites
    from app.content import youtube_queue as ytq
    from app.routes import app
    from app.work import workqueue

    sites.reload_hq()
    monkeypatch.setattr(ytq, "dirty_youtube_files", lambda site_id=None: [
        "app/content/demo_en.md", "app/content/demo_ko.md",
    ])
    monkeypatch.setattr(workqueue, "pending_tasks", lambda: {})
    monkeypatch.setattr(workqueue, "enqueue_task", lambda task, label="": {"id": "q1", "task": task, "label": label})

    with sites.use("okonsen"):
        st = ytq.ship_status("okonsen")
    assert st["pending_places"] == 1 and st["can_enqueue"]
    assert "반영 맡기기" in st["label"]

    c = app.test_client()
    r = c.get("/site/okonsen/youtube/api/ship")
    assert r.status_code == 200
    body = r.get_json()
    assert body["pending_places"] == 1

    r2 = c.post("/site/okonsen/youtube/api/ship", json={})
    assert r2.status_code == 200
    out = r2.get_json()
    assert out["ok"] is True
    assert "큐에 담았어요" in out["message"]

    # no pending → refuse
    monkeypatch.setattr(ytq, "dirty_youtube_files", lambda site_id=None: [])
    r3 = c.post("/site/okonsen/youtube/api/ship", json={})
    assert r3.status_code == 409
    assert r3.get_json()["ok"] is False


def test_public_page_url_and_shop_links():
    assert ytq.public_page_url("okcaddie", "oarai_golf_club").endswith(
        "/course/oarai_golf_club"
    )
    assert ytq.public_page_url("okramen", "afuri_harajuku").endswith(
        "/ramen/afuri_harajuku_en"
    )
    assert ytq.public_page_url("okonsen", "amane_resort_seikai").endswith(
        "/onsen/amane_resort_seikai_en"
    )
    shops = ytq.list_shops(site_id="okcaddie", filter_mode="missing", limit=1)
    assert shops
    assert shops[0]["page_url"].startswith("https://")
    assert "youtube_thumb" in shops[0]
