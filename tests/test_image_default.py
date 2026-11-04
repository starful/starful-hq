from pathlib import Path

from app.work import workers
from app import sites


def test_default_image_source_finds_repo_default(tmp_path, monkeypatch):
    repo = tmp_path / "site"
    images = repo / "app" / "static" / "images"
    images.mkdir(parents=True)
    (images / "default.jpg").write_bytes(b"jpeg-bytes")
    posts = images / "posts"
    posts.mkdir()
    monkeypatch.setattr(sites, "current", lambda: type("S", (), {
        "repo": repo,
        "images_rel": "app/static/images/posts",
    })())
    monkeypatch.setattr(sites, "IMAGES", posts)
    src = workers.default_image_source()
    assert src is not None and src.name == "default.jpg"


def test_images_to_default_copies_fitted(tmp_path, monkeypatch):
    from PIL import Image

    repo = tmp_path / "site"
    images = repo / "app" / "static" / "images"
    images.mkdir(parents=True)
    Image.new("RGB", (40, 30), (10, 20, 30)).save(images / "default.jpg", format="JPEG")
    target = images / "shop.jpg"
    Image.new("RGB", (80, 60), (200, 100, 50)).save(target, format="JPEG")
    monkeypatch.setattr(sites, "current", lambda: type("S", (), {
        "repo": repo,
        "images_rel": "app/static/images",
    })())
    monkeypatch.setattr(sites, "IMAGES", images)
    monkeypatch.setattr(workers.snapshot, "safe_image", lambda name: target if name == "shop.jpg" else None)
    monkeypatch.setattr(workers, "BACKUP_DIR", tmp_path / "bak")
    monkeypatch.setattr(workers, "_gcs_push", lambda names: "")
    work = type("W", (), {"say": lambda self, m: None})()
    msg = workers.images_to_default(work, "shop.jpg")
    assert "디폴트" in msg
    assert Image.open(target).size == (80, 60)
    assert (tmp_path / "bak" / "shop.jpg").is_file()
