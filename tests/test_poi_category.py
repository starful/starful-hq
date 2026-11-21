"""poi write: guide-shaped topics must not default to the place noun."""

from app import sites
from app.content import contentkit, poicontent


def test_guide_topic_detection():
    assert poicontent.is_guide_topic("富士山周辺キャンプ場 レンタル比較")
    assert poicontent.is_guide_topic("苗場スキー場 アクセス方法 完全ガイド")
    assert poicontent.is_guide_topic("오키나와 첫 다이빙 준비")
    assert poicontent.is_guide_topic("How to get to Naeba Ski Resort")
    assert not poicontent.is_guide_topic("하쿠바 핫포네 스키장")
    assert not poicontent.is_guide_topic("Naeba Ski Resort")


def test_resolve_forces_guide_even_if_place_category():
    with sites.use("jpfun"):
        assert poicontent.resolve_category("레저 스팟", "富士山周辺キャンプ場 レンタル比較") == "가이드"
        assert poicontent.resolve_category("", "苗場スキー場 アクセス方法 完全ガイド") == "가이드"
        assert poicontent.resolve_category("레저 스팟", "하쿠바 핫포네 스키장") == "레저 스팟"
        assert contentkit.resolve_write_category(None, "타투 OK 온천 고르는 법") == "가이드"


def test_okonsen_resolve_uses_noun():
    with sites.use("okonsen"):
        assert poicontent.resolve_category("", "하코네 유모토 온천 료칸") == "온천"
        assert poicontent.resolve_category("온천", "타투 OK 온천 고르는 법") == "가이드"
