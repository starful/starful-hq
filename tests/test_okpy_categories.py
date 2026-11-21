"""okpy writable categories for HQ research / write."""

from app.content import contentkit
from app.work import workers


def test_okpy_writable_categories_match_boss_list():
    assert contentkit.OKPY_CATEGORIES == (
        "python", "cloud", "terraform", "dev-method",
        "data-analysis", "eng-comms", "ai-models", "career",
    )
    # archive-only — site may still have folders, but HQ does not add new posts here
    assert contentkit.OKPY_ARCHIVE_ONLY == frozenset(
        {"data-model", "pmbok", "agile-scrum", "fit-journey"}
    )
    assert not (set(contentkit.OKPY_CATEGORIES) & contentkit.OKPY_ARCHIVE_ONLY)
    assert set(contentkit.OKPY_CATEGORY_SEEDS) == set(contentkit.OKPY_CATEGORIES)
    seeds = contentkit.okpy_seed_keywords(8)
    assert seeds == [
        contentkit.OKPY_CATEGORY_SEEDS[c][0] for c in contentkit.OKPY_CATEGORIES
    ]

def test_bank_to_category_covers_all_writable():
    cats = set(workers.BANK_TO_CATEGORY.values())
    assert cats == set(contentkit.OKPY_CATEGORIES)
    assert workers.BANK_TO_CATEGORY["data_analysis"] == "data-analysis"
    assert workers.BANK_TO_CATEGORY["dev_method"] == "dev-method"
    assert workers.BANK_TO_CATEGORY["career"] == "career"


def test_generate_posts_categories_aligned():
    import sys
    from app import snapshot

    sys.path.insert(0, str(snapshot.OKPY_ROOT / "scripts"))
    import generate_posts as g  # noqa: WPS433

    assert tuple(g.CATEGORIES) == contentkit.OKPY_CATEGORIES
    assert g.QUEUE_BANK["data-analysis"] == "data_analysis"
    assert g.QUEUE_BANK["dev-method"] == "dev_method"
