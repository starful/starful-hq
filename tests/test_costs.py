import pytest

from app.money import costs
def test_daily_limits_stop_paid_images_and_photos(monkeypatch):
    costs.set_limits(daily_images=2, daily_places=3, monthly_yen=1000)
    costs.take("image")
    costs.take("image")
    with pytest.raises(costs.OverLimit, match="AI 그림 상한 2장"):
        costs.take("image")
    costs.take("places", 3)
    assert costs.left("places") == 0 and not costs.can_take("places")
    assert costs.today_counts() == {"image": 2, "places": 3, "youtube": 0}
    costs.set_limits(daily_youtube=1)
    costs.take("youtube")
    with pytest.raises(costs.OverLimit, match="YouTube 검색"):
        costs.take("youtube")
    # YouTube does not burn the monthly ¥ budget
    costs.set_limits(daily_youtube=10, daily_places=10)
    monkeypatch.setattr(costs, "month_paid_yen", lambda: 1200)      # month budget used up
    with pytest.raises(costs.OverLimit, match="이번 달"):
        costs.take("places")
    costs.take("youtube")  # still ok


def test_billing_numbers_are_shaped_for_the_card():
    raw = {"month": "2026-10", "currency": "JPY", "updated_at": "2026-10-21T10:00:00",
           "gemini": {"cost": 100.4}, "imagen": {"cost": 30}, "banana": {"cost": 0}, "places": {"cost": 12},
           "total": {"cost": 922, "daily": [{"date": "2026-10-01", "cost": 40.6}]}}
    s = costs._shape(raw)
    assert s["cats"] == {"gemini": 100, "imagen": 30, "banana": 0, "places": 12}
    assert s["paid"] == 42 and s["other"] == 922 - 142 and s["daily"][0]["yen"] == 41
    assert costs._prev("2026-01", 2) == "2025-11"
