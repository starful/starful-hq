"""Job bubble errors must stay human-readable."""
from __future__ import annotations

from app.office import jobs
from app.work import workers


def test_bubble_err_hides_json_fence():
    assert "```" not in jobs._bubble_err(RuntimeError("```json"))
    assert "읽지 못" in jobs._bubble_err(RuntimeError("```json\n{"))
    assert "한도" in jobs._bubble_err(RuntimeError("Claude session/usage limit — try again later"))
    assert "JSON" in jobs._bubble_err(RuntimeError("Claude returned no JSON"))


def test_ko_hides_json_fence():
    assert "```" not in workers._ko("```json")
    assert "읽지 못" in workers._ko("```json")
    assert "한도" in workers._ko("rate limit exceeded")
