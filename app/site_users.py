"""사이트별 「이용자 보기」— SiteKPI visitlog+설문 · visitlog 사이트 · 나머지 GA4."""

from __future__ import annotations

from typing import Any

from app import sites

PERIODS = (7, 30, 90)


def button_label(site_id: str | None = None) -> str:
    sid = site_id or sites.current_id()
    return "이용자 · 설문 보기" if sid == "sitekpi" else "이용자 보기"


def fetch(site_id: str | None = None, days: int = 7, fresh: bool = False) -> dict[str, Any]:
    sid = (site_id or sites.current_id() or "").strip()
    days = days if days in PERIODS else 7
    site = sites.SITES.get(sid)

    if sid == "sitekpi":
        from app import sitekpi_users
        data = sitekpi_users.fetch(days, fresh=fresh)
        return {
            **data,
            "kind": "visitlog",
            "survey_on": True,
            "site_id": sid,
            "site_label": (site or sites.current()).label,
            "title": "SiteKPI 이용자",
            "btn": "이용자 · 설문 보기",
            "subtitle": "sitekpi.net의 행동 로그 · 30초 설문 (익명)",
        }

    if site and site.visitlog:
        from app import visitlog_users
        data = visitlog_users.fetch(sid, days, fresh=fresh)
        label = site.label
        return {
            **data,
            "kind": "visitlog",
            "survey_on": False,
            "site_id": sid,
            "site_label": label,
            "title": f"{label} 이용자",
            "btn": "이용자 보기",
            "subtitle": f"{site.domain or sid} 행동 로그 (익명 · 설문 없음)",
            "note": data.get("note") or (
                "남기는 것: 익명 방문자 ID · 경로 · 클릭/CTA · 유입 도메인 · UTM(있을 때) · 언어. "
                "남기지 않는 것: IP · 계정. 약 90일 후 삭제."
            ),
        }

    from app import ga4_users
    data = ga4_users.fetch(sid, days, fresh=fresh)
    return {
        **data,
        "title": f"{data.get('site_label') or sid} 이용자",
        "btn": "이용자 보기",
        "subtitle": "GA4 행동 요약 (방문자별 흐름은 visitlog 연결 후)",
    }
