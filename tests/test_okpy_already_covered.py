"""okpy _topic_already_covered must not false-positive on Japanese-only filenames."""

from __future__ import annotations


def test_topic_already_covered_ignores_empty_cjk_stem(monkeypatch, tmp_path):
    import sys

    from app import snapshot

    posts = tmp_path / "app" / "content" / "posts" / "cloud"
    posts.mkdir(parents=True)
    (posts / "クラウドネイティブ開発とは.md").write_text("# ja\n", encoding="utf-8")
    (posts / "クラウドとaiの統合.md").write_text("# ja\n", encoding="utf-8")
    (posts / "aws-ecs-vs-gcp-cloud-run-vs-azure-container-instances.md").write_text(
        "# en\n", encoding="utf-8",
    )

    sys.path.insert(0, str(snapshot.OKPY_ROOT / "scripts"))
    import generate_posts as g  # noqa: WPS433

    monkeypatch.setattr(g, "POSTS_DIR", tmp_path / "app" / "content" / "posts")

    topic = "AWS App Runner vs GCP Cloud Run vs Azure Container Apps for simplified container"
    # Must NOT match CJK-only files (empty / tiny ASCII stems).
    assert g._topic_already_covered("cloud", topic) is False

    # Real overlap with ECS vs Cloud Run post should still match.
    assert g._topic_already_covered(
        "cloud",
        "AWS ECS vs GCP Cloud Run vs Azure Container Instances",
    ) is True


def test_topic_already_covered_short_needle_false():
    import sys

    from app import snapshot

    sys.path.insert(0, str(snapshot.OKPY_ROOT / "scripts"))
    import generate_posts as g  # noqa: WPS433

    assert g._topic_already_covered("cloud", "AI") is False
