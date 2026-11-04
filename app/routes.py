"""New okadmin. Local back office. okpy is the first site.

Work is handed to people (office.ZONES). Each person has their own job slot, so several
can work at once; git work (이슈·머지·올리기) takes turns inside jobs.GIT_LOCK.
"""

from __future__ import annotations
import os
from pathlib import Path

import secrets

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session, url_for

from app.paths import APP_DIR, HQ_ROOT
from app.work import actions
from app.money import affiliate
from app.money import affsync
from app.work import autodev
from app.office import boss
from app.work import devwork
from app.office import jobs
from app.work import marketing
from app.office import meeting
from app import office
from app.work import planning
from app import sites
from app import snapshot
from app import tools
from app.work import workers

ROOT = HQ_ROOT  # starful-hq/ (data/ · start.sh)
PORT = int(os.environ.get("PORT", "8091"))
STEPS = (
    ("status", "현황"),
    ("meet", "회의"),  # /meetings only — not a tab; open from 현황「회의 결과 보기」
    ("write", "글"),
    ("polish", "다듬기"),
    ("search", "검색"),
    ("images", "이미지"),
    ("ship", "반영"),
)

# Right column tabs. 현황 + teams. 회의 결과는 탭이 아니라 /meetings 페이지.
TEAM_TABS = (
    ("status", "📊", "현황", ("status",)),
    ("plan", "🗂️", "기획", ("write",)),
    ("mkt", "📈", "마케", ("search",)),  # polish lives in the search drawer (write-flow)
    ("design", "🎨", "디자인", ("images",)),
    ("dev", "🛠️", "개발", ("ship",)),
)
STEP_TAB = {step: tab for tab, _, _, steps in TEAM_TABS for step in steps}
STEP_TAB["polish"] = "mkt"  # 직접 고치기 URL · editor only
STEP_TAB["meet"] = ""  # no tab selected on the meetings page

app = Flask(__name__, template_folder=str(APP_DIR / "templates"), static_folder=str(APP_DIR / "static"))
app.config["TEMPLATES_AUTO_RELOAD"] = True  # template edits show without a restart
app.secret_key = boss.secret_key()


# ---------- 사이트: /site/<site>/... — every page and every job works on one site ----------

@app.url_value_preprocessor
def _pick_site(endpoint, values):
    if values and "site" in values:
        sid = values.pop("site")
        site = sites.get(sid)
        if not site:
            abort(404)
        sites.set_current(sid)
        request.site_not_ready = not site.enabled
    else:   # pages without a site in the URL (home, /office, login): the last site you looked at
        sites.set_current(request.args.get("s") or request.cookies.get("okn_site") or sites.DEFAULT_SITE)


@app.url_defaults
def _keep_site(endpoint, values):
    if "site" not in values and app.url_map.is_endpoint_expecting(endpoint, "site"):
        values["site"] = sites.current_id()


_OPEN_BEFORE_CONNECT = {"site_status", "site_costs_card", "site_affiliate_card"}   # not about the site itself


@app.before_request
def _reset_job_result():
    jobs.reset_request()


@app.before_request
def _site_ready():
    if getattr(request, "site_not_ready", False):
        # A site that isn't connected yet: same page as the others (its floor, people, office map), read-only.
        # Only the status page opens; everything that would touch the repo waits until the site is connected.
        site = sites.current()
        if request.method == "GET" and request.endpoint in _OPEN_BEFORE_CONNECT:
            return None
        if request.method != "GET" and request.headers.get("X-Requested-With") != "fetch":
            return redirect(url_for("site_status"))
        if request.method != "GET" or request.headers.get("X-Requested-With") == "fetch" or request.endpoint == "site_panel":
            return jsonify({"ok": False, "error": f"{site.label}는 아직 연결 전이에요"}), 409
        return redirect(url_for("site_status"))
    return None


@app.after_request
def _remember_site(resp):
    if request.path.startswith("/site/") and not getattr(request, "site_not_ready", False):
        resp.set_cookie("okn_site", sites.current_id(), max_age=60 * 60 * 24 * 365, samesite="Lax")
    return resp


@app.after_request
def _track_usage(resp):
    """Count HQ POSTs (clicks that change something). UI-only clicks use /usage/track."""
    if request.method != "POST" or resp.status_code >= 400:
        return resp
    ep = request.endpoint or ""
    if not ep or ep in ("usage_track", "static"):
        return resp
    try:
        from app.money import usage
        detail = ""
        args = request.view_args or {}
        if ep == "site_run":
            detail = str(args.get("task") or "")
        elif ep == "site_action":
            detail = str(args.get("aid") or "")
        elif ep == "site_meeting":
            detail = str(request.form.get("mode") or "all")
        usage.track(f"post:{ep}" + (f":{detail}" if detail else ""), site=sites.current_id(), detail="")
    except Exception:  # noqa: BLE001
        pass
    return resp


def _flow_now() -> dict:
    try:
        return office.read_flow()
    except Exception:  # noqa: BLE001
        return {}


@app.context_processor
def _site_ctx():
    cur = sites.current()
    from app.work import autoship
    return {"staff_map": dict(office.ALL_ROSTER), "flow_now": _flow_now(),
            "kind_of": sites.kind_of, "picks_text": sites.picks_text,"cur_site": cur, "cur_office": sites.office_of(cur.id), "offices": sites.OFFICES, "all_sites": sites.SITES,
            "office_sites": [s for s in sites.office_sites(cur.office) if s.enabled],
            "office_autoship": autoship.office_states()}


_ICON_COLORS = ("#2f6fd6", "#d9622b", "#2e9e6a", "#8a4fd0", "#c43d6b", "#2a8fa6", "#b08a1e", "#5b6b85")


app.jinja_env.filters["josa"] = sites.josa   # {{ word | josa('을/를') }}


@app.get("/site-icon/<sid>")
def site_icon(sid: str):
    """A site's favicon; a letter badge when it has none (or isn't checked out here)."""
    site = sites.get(sid)
    if site is None:
        abort(404)
    path = site.icon
    if path is not None:
        return send_file(path, max_age=3600)
    color = _ICON_COLORS[sum(map(ord, sid)) % len(_ICON_COLORS)]
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="8" fill="{color}"/>'
           f'<text x="16" y="22" font-family="sans-serif" font-size="17" font-weight="700" fill="#fff" text-anchor="middle">'
           f'{site.label[:1]}</text></svg>')
    return app.response_class(svg, mimetype="image/svg+xml", headers={"Cache-Control": "max-age=3600"})
app.config["SESSION_COOKIE_NAME"] = "okn_session"   # okadmin (8090) on the same host keeps its own cookie


# ---------- 사장 로그인 (Google) ----------
# Not logging in = the owner on this Mac, as before. Logging in with a boss account adds Taekki.
BOSS_BLOCKED_ENDPOINTS = {"site_ship_issue", "site_ship_review", "site_ship_merge", "site_ship_push", "site_ship_run",
                          "site_ship_fix", "site_restart"}
BOSS_BLOCKED_ACTIONS = {"issue", "review", "push", "deploy"}


def is_boss() -> bool:
    return boss.is_boss(session.get("boss_email"))


@app.context_processor
def _boss_ctx():
    return {"boss": boss.state(is_boss()), "boss_desk": office.BOSS_DESK,
            "boss_blocked": BOSS_BLOCKED_ACTIONS if is_boss() else set()}


@app.before_request
def _boss_limits():
    """The boss hands out work but doesn't make PRs, merge or deploy (that stays with the owner)."""
    if request.method != "POST" or not is_boss():
        return None
    ep = request.endpoint or ""
    blocked = ep in BOSS_BLOCKED_ENDPOINTS
    if ep == "site_action" and (request.view_args or {}).get("aid") in BOSS_BLOCKED_ACTIONS:
        blocked = True
    if ep == "site_run" and (request.view_args or {}).get("task") in BOSS_BLOCKED_ACTIONS:
        blocked = True
    if not blocked:
        return None
    msg = "사장님이 퇴근하시면 작업할게요 (PR·머지·배포)"
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": False, "who": None, "message": msg}), 403
    return msg, 403


def _callback_url() -> str:
    return url_for("oauth_callback", _external=True)


@app.get("/login")
def login():
    cid, sec = boss.oauth_client()
    if not cid or not sec:
        return "Google OAuth 설정이 없어요 (okadmin/.env의 GOOGLE_OAUTH_CLIENT_ID / _SECRET)", 500
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    session["after_login"] = request.args.get("next") or url_for("site_status")
    return redirect(boss.auth_url(_callback_url(), state))


@app.get("/oauth/callback")
def oauth_callback():
    if not request.args.get("state") or request.args.get("state") != session.pop("oauth_state", None):
        return "로그인 확인값이 맞지 않아요. 다시 로그인해 주세요.", 400
    if request.args.get("error"):
        return f"로그인을 못 했어요: {request.args.get('error')}", 400
    try:
        email = boss.email_from_code(request.args.get("code") or "", _callback_url())
    except Exception as exc:  # noqa: BLE001
        return f"Google 로그인 실패: {exc}", 502
    if not boss.is_boss(email):
        session.pop("boss_email", None)
        return f"{email} 계정은 사장 계정으로 등록되어 있지 않아요 (data/boss.json).", 403
    session["boss_email"] = email
    session.permanent = True
    boss.heartbeat()
    dest = session.pop("after_login", "") or url_for("site_status")
    return redirect(dest if str(dest).startswith("/") else url_for("site_status"))


@app.get("/logout")
def logout():
    if is_boss():
        boss.leave()
    session.pop("boss_email", None)
    return redirect(request.args.get("next") or url_for("site_status"))


@app.post("/boss/move")
def boss_move():
    if not is_boss():
        return jsonify({"ok": False}), 403
    data = request.get_json(silent=True) or {}
    try:
        boss.move(float(data.get("x")), float(data.get("y")))
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    return jsonify({"ok": True})


_EMPTY_SITE = {"sentence": "아직 연결 전이에요", "content": {"posts": 0, "sections": {}, "newest": [], "langs": [], "translations": 0},
               "search": {"rows": [], "shown_count": 0}, "images": {"count": 0}, "git": {}, "ship": {}, "seo_last": {}, "refreshed_at": ""}


def _site():
    if not sites.current().enabled:   # not connected yet: never build a snapshot for it
        return snapshot.read_snapshot() or {}, _EMPTY_SITE
    snap = snapshot.load_or_refresh()
    return snap, snapshot.site_data(snap)


def _glance(snap: dict) -> list[dict]:
    """한눈에: every enabled site on every floor (snapshot numbers per site)."""
    from app.work import workqueue
    rows = []
    for off in sorted(sites.OFFICES.values(), key=lambda o: o.floor):
        for s in sites.office_sites(off.id):
            if not s.enabled:
                continue
            with sites.use(s.id):
                blk = snap.get(s.id) or {}
                flow = office.read_flow()
                pr = flow.get("pr") if flow.get("pr") and not flow.get("merged") else None
                search_rows = (blk.get("search") or {}).get("rows") or []
                pulse = workqueue.site_pulse(s.id)
                rows.append({
                    "site": s, "ready": bool(blk),
                    "office": off, "floor": off.floor, "floor_label": off.floor_label,
                    "picks": sum(1 for p in (workers.read_research().get("picks") or []) if p.get("status") == "new"),
                    "search": len({str(r.get("url") or "").split("#")[0] for r in search_rows}),
                    "images": (blk.get("images") or {}).get("count") or 0,
                    "posts": (blk.get("content") or {}).get("posts") or 0,
                    "translations": (blk.get("content") or {}).get("translations") or 0,
                    "kinds": snapshot.kinds_of(blk.get("content") or {}),
                    "langs": (blk.get("content") or {}).get("langs") or [],
                    "pr": pr, "review": ("리뷰 OK" if flow.get("verdict_ok") else ("고칠 것 있음" if flow.get("reviewed") else "리뷰 전")) if pr else "",
                    "ship": ((blk.get("ship") or {}).get("when") or "")[5:16],
                    "files": (blk.get("git") or {}).get("file_count") or 0,
                    "pulse": pulse,
                })
    return rows


_UI_TEAM = {"research": "plan", "write": "plan", "issue": "plan", "propose": "plan", "search": "mkt", "polish": "mkt",
            "analyze": "mkt", "fix": "mkt", "images": "design", "refactor": "dev", "review": "dev", "push": "dev", "ship": "dev"}


def _next_up() -> dict[str, str]:
    """task → whoever on its team is next in turn (and free, if anyone is): the face shown on that step."""
    out = {}
    for task, team in _UI_TEAM.items():
        members = office.ordered_members(task) or list(office.team_members(team))
        out[task] = next((m for m in members if not jobs.running(m)), members[0])
    return out


def _cost_cached():
    """The 💰 card from memory (no BigQuery / Claude read): (data, stale, time) or (None, True, '')."""
    from app.money import costs
    data, stale = costs.cached()
    return {"data": data, "stale": stale, "at": costs.card_age()}


def _ship_stage(site: dict) -> dict:
    from app.work import autoship
    try:
        return autoship.stage(site.get("git") or {})
    except Exception as exc:  # noqa: BLE001
        return {"key": "idle", "label": "", "detail": str(exc)[:120], "auto": False, "url": "", "needs_person": False}


def _render_site(step: str, **extra):
    from app.work import workqueue
    pending = workqueue.pending_tasks()
    extra.setdefault("queue_pending", pending)
    extra.setdefault("queue_pending_label", {t: workqueue.pending_label(st) for t, st in pending.items()})
    snap, site = _site()
    all_jobs = jobs.all_jobs()
    flow = office.read_flow()
    return render_template(
        "site.html",
        step=step,
        steps=STEPS,
        snap=snap,
        site=site,
        jobs=all_jobs,
        flow=flow,
        categories=workers.categories(),
        teams=office.teams_here(),
        team_info=office.TEAM_INFO,
        waiting=office.waiting_work(snap, flow),
        office=office.build_office(snap, all_jobs, flow),  # same map as home, on every step
        tabs=TEAM_TABS,
        new_picks=sum(1 for p in (workers.read_research().get("picks") or []) if p.get("status") == "new"),
        tab=STEP_TAB.get(step, "status"),
        quick=actions.panel(STEP_TAB[step]) if STEP_TAB.get(step) not in (None, "", "status", "meet") else None,
        auto=autodev.summary() if step == "ship" else None,
        group_busy=jobs.busy_groups(),
        task_group=office.TASK_GROUP,
        items_busy={t: jobs.busy_items(t) for t in office.ITEM_TASKS},
        affiliate=affiliate.summary(),
        aff_sync=affsync.summary(),
        glance=_glance(snap),
        ship_stage=_ship_stage(site) if step in ("status", "ship") else None,
        next_up=_next_up(),
        cost_cached=_cost_cached(),
        meet_on={m: meeting.running(m) for m in meeting.MODES},
        boss_on=meeting.running("boss"),
        free_team={t: [m for m in office.TEAMS[t] if not jobs.running(m)] for t in office.TEAMS},
        free_people=[p["id"] for p in office.PEOPLE if not jobs.running(p["id"])],
        free_leaders=[m for m in office.LEADERS if not jobs.running(m)],
        **extra,
    )


def _fetch() -> bool:
    return request.headers.get("X-Requested-With") == "fetch"


def _done_json(ok_msg: str = "") -> Any:
    """Buttons sent with fetch (no page reload): who took the work, or why nobody did."""
    r = jobs.request_result()
    names = [office.staff_by_role(p)["name"] for p in r["who"]]
    if r["tried"] and not names:
        return jsonify({"ok": False, "msg": r["why"] or "지금은 맡길 수 없어요"})
    if names:
        return jsonify({"ok": True, "who": r["who"], "msg": f"{', '.join(names)}에게 맡겼어요"})
    return jsonify({"ok": True, "msg": ok_msg or "했어요"})


def _back(step: str, **args):
    """Back to the step page. Team details open the drawer (#d); 현황·회의는 인라인이라 해시 없음."""
    if _fetch():
        return _done_json("저장했어요" if args.get("saved") else "")
    url = url_for(f"site_{step}", **args)
    if step not in ("status", "meet"):
        url += "#d"
    return redirect(url)


def _meet_page_ctx() -> dict:
    """Date chips + that day's meetings for the 회의 tab."""
    hist = planning.history(80)
    days = sorted({str(h.get("at") or "")[:10] for h in hist if h.get("at")}, reverse=True)
    pid = (request.args.get("proposal") or "").strip()
    day = (request.args.get("date") or "").strip()[:10]
    proposal = planning.read(pid) if pid else {}
    if pid and proposal.get("at"):
        day = str(proposal["at"])[:10]
    elif not day and days:
        day = days[0]
    if day and days and day not in days:
        day = days[0]
    day_hist = [h for h in hist if str(h.get("at") or "")[:10] == day]
    day_meetings = [p for p in (planning.read(h["id"]) for h in day_hist) if p]
    if not proposal and day_meetings:
        proposal = day_meetings[0]
    day_i = days.index(day) if day in days else -1
    return {
        "proposal": proposal,
        "proposals": hist,
        "viewing_proposal": pid,
        "proposal_days": days,
        "proposal_day": day,
        "proposal_day_newer": days[day_i - 1] if day_i > 0 else "",
        "proposal_day_older": days[day_i + 1] if 0 <= day_i < len(days) - 1 else "",
        "day_meetings": day_meetings,
        "day_counts": {d: sum(1 for h in hist if str(h.get("at") or "")[:10] == d) for d in days[:30]},
    }


@app.get("/")
def home():
    snap = snapshot.load_or_refresh()
    all_jobs = jobs.all_jobs()
    return render_template("home.html", snap=snap, office=office.build_office(snap, all_jobs))


@app.get("/office")
def office_state():
    # a floor whose sites aren't connected yet (2층 before its first site): never build a snapshot for it
    snap = snapshot.load_or_refresh() if sites.current().enabled else (snapshot.read_snapshot() or {})
    all_jobs = jobs.all_jobs()
    me = is_boss()
    if me:
        boss.heartbeat()
    return jsonify({"jobs": all_jobs, "office": office.build_office(snap, all_jobs), "refreshed_at": snap.get("refreshed_at"),
                    "boss": boss.state(me)})


@app.get("/job")
def job():
    return jsonify(jobs.all_jobs())


@app.get("/staff")
def staff_page():
    """👥 사원: every floor's people and what they're doing now (a small card opened from the header)."""
    floors = office.staff_directory(jobs.all_jobs())
    if request.args.get("format") == "json":
        return jsonify([{**f, "sites": [s.id for s in f["sites"]]} for f in floors])
    return render_template("_staff_pop.html", floors=floors, here=sites.current().office)


@app.post("/usage/track")
def usage_track():
    """UI click beacon (data-track). Body: {event, detail?}."""
    from app.money import usage
    body = request.get_json(silent=True) or {}
    event = str(body.get("event") or request.form.get("event") or "").strip()
    detail = str(body.get("detail") or request.form.get("detail") or "").strip()
    if not event.startswith("ui:"):
        return jsonify({"ok": False}), 400
    usage.track(event, site=sites.current_id(), detail=detail)
    return jsonify({"ok": True})


@app.get("/usage")
def usage_card():
    """👆 HQ 클릭 popup (header, next to 사원)."""
    from app.money import usage
    return render_template("_usage_card.html", usage=usage.summary(14))


@app.get("/site/<site>/analytics/card")
def site_analytics_card():
    """GA4 chart + GSC queries for the status tab (async; cache-first).

    First paint uses soft=True (no live GA/GSC refetch). ↻ sets fresh=1 for a full refresh.
    """
    fresh = request.args.get("fresh") == "1"
    try:
        traffic = marketing.traffic_card(refresh=fresh, soft=not fresh)
    except Exception as exc:  # noqa: BLE001 — shown on the card
        traffic = {
            "days": 28, "period": "최근 28일", "cache_hit": False,
            "site_label": sites.current().label,
            "ga4": {"error": str(exc)[:120], "ok": False, "kpis": [], "rows": [], "prior_rows": [],
                    "start": "", "end": ""},
            "gsc": {"error": str(exc)[:120], "clicks": 0, "impressions": 0, "queries": [],
                    "d_clicks": {}, "d_impressions": {}, "start": "", "end": ""},
        }
    return render_template("_analytics_card.html", traffic=traffic)

@app.post("/refresh")
def refresh():
    jobs.refresh_snapshot()
    dest = request.form.get("next") or url_for("home")
    if not str(dest).startswith("/"):
        dest = url_for("home")
    return redirect(dest)


# ---------- 1. 현황 · 회의 ----------

@app.get("/site/<site>")
def site_status():
    """현황: 전체 회의 열기 + 트래픽. 회의 결과는 /meetings."""
    from app.work import workqueue
    # Old bookmarks: ?proposal= / ?date= lived on status drawer → 회의 탭
    if request.args.get("proposal") or request.args.get("date"):
        return redirect(url_for(
            "site_meet",
            proposal=request.args.get("proposal") or None,
            date=request.args.get("date") or None,
        ))
    hist = planning.history(8)
    proposal: dict = {}
    for h in hist:
        if h.get("id"):
            proposal = planning.read(h["id"]) or {}
            if proposal:
                break
    return _render_site("status", tool_rows=tools.status(), proposal=proposal,
                        focus_opts=planning.FOCUS,
                        meeting_on=meeting.running("all"), leaders_on=meeting.running("leaders"),
                        site_prefs=workqueue.prefs())


@app.get("/site/<site>/meetings")
def site_meet():
    """회의 결과 페이지 (탭 아님). 현황「회의 결과 보기」→ 여기 → 「현황으로」."""
    return _render_site("meet", focus_opts=planning.FOCUS, **_meet_page_ctx())

@app.post("/site/<site>/meeting")
def site_meeting():
    if office.is_borrowed():   # B1 has no meeting room
        return _back("status")
    focus = request.form.get("focus") or "all"
    ask = (request.form.get("ask") or "").strip()[:300]
    topic = (request.form.get("topic") or "").strip()[:200]
    mode = request.form.get("mode") or "all"
    if mode == "all":  # optional aside topic; does not replace the main FOCUS
        topic = (request.form.get("all_topic") or topic or "").strip()[:200]
    people = [p for p in request.form.getlist("people") if p][: meeting.BOSS_MAX]
    # 사장실 호출 can be about another site of this office (the result is kept on that site)
    target = request.form.get("site") or sites.current_id()
    target_site = sites.get(target)
    if target_site is None or not target_site.enabled or target_site.office != sites.current().office:
        target = sites.current_id()
    with sites.use(target):
        came = meeting.start(focus, ask, mode, topic, people)
        if came and mode == "boss":
            boss.move(office.BOSS_TABLE[-1]["x"], office.BOSS_TABLE[-1]["y"], forced=True)   # Taekki takes the last chair
    if _fetch():
        if not came:
            return jsonify({"ok": False, "msg": "지금은 회의를 열 수 없어요 · 다른 회의 중이거나 참석할 사람이 모두 일하는 중이에요"})
        return jsonify({"ok": True, "who": came, "msg": f"회의 시작 · {len(came)}명 모였어요", "site": target})
    return redirect(url_for("site_status", site=target))


def _aff_card(**kw):
    return render_template("_affiliate_card.html", affiliate=affiliate.summary(), aff_sync=affsync.summary(), **kw)


@app.get("/site/<site>/costs")
def site_costs_card():
    """💰 GCP 비용 card (loaded after the page: the billing read can take a few seconds)."""
    from app.money import costs
    cost = costs.summary_cached(fresh=request.args.get("fresh") == "1")
    return render_template("_costs_card.html", cost=cost, cost_at=costs.card_age())


@app.post("/site/<site>/costs/limits")
def site_costs_limits():
    from app.money import costs
    costs.set_limits(**{k: request.form.get(k) for k in costs.DEFAULT_LIMITS})
    html = render_template("_costs_card.html", cost=costs.summary_cached(), cost_at=costs.card_age(), cost_msg="상한을 저장했어요")
    return html if request.headers.get("X-Requested-With") == "fetch" else redirect(url_for("site_status"))


@app.get("/site/<site>/affiliate")
def site_affiliate_card():
    """The 💴 어필리에이트 card alone (it reloads itself while a fetch runs)."""
    return _aff_card()


@app.post("/site/<site>/affiliate/<what>")
def site_affiliate_do(what: str):
    """자동 취득: fetch now · open the login window · one-time browser install."""
    if what == "fetch":
        ok, msg = affsync.fetch_now(), "가져오는 중이에요 (1~2분)"
    elif what == "login":
        ok, msg = affsync.open_login(), "Mac에 로그인 창을 열었어요. 3곳 로그인 후 창을 닫으면 바로 가져와요"
    elif what == "install":
        ok, msg = affsync.install(), "브라우저를 설치하는 중이에요 (몇 분)"
    else:
        abort(404)
    if not ok:
        msg = "지금은 할 수 없어요 (다른 작업 중이거나 준비 전)"
    html = _aff_card(aff_msg=msg, aff_bad=not ok)
    return html if request.headers.get("X-Requested-With") == "fetch" else redirect(url_for("site_status"))


@app.post("/site/<site>/affiliate")
def site_affiliate_save():
    """This month's numbers for each platform → okadmin's affiliate store (same file/history as 8090)."""
    values: dict[str, dict[str, str]] = {}
    for key, val in request.form.items():
        pid, _, field = key.partition(".")
        if field in ("clicks", "orders", "commission"):
            values.setdefault(pid, {})[field] = val
    month = __import__("datetime").datetime.now().strftime("%Y-%m")
    try:
        n = affiliate.save(month, values)
        msg, bad = (f"{n}곳 저장했어요" if n else "바뀐 숫자가 없어요"), False
    except Exception as exc:  # noqa: BLE001 - shown on the card
        msg, bad = f"저장 못 했어요: {str(exc)[:80]}", True
    html = _aff_card(aff_msg=msg, aff_bad=bad)
    if request.headers.get("X-Requested-With") == "fetch":
        return html
    return redirect(url_for("site_status"))


@app.post("/site/<site>/autoship")
def site_autoship():
    """Toggle autoship for the current site's floor (office)."""
    from app.work import autoship
    on = request.form.get("on") == "1"
    oid = autoship.set_enabled(on)
    off = sites.OFFICES.get(oid)
    label = off.floor_label if off else oid
    if on:
        for s in sites.office_sites(oid):
            if s.enabled:
                autoship.kick(s.id)
    msg = (f"{label} 자동 진행을 켰어요 · 이 층 사이트 PR·리뷰·머지·배포를 알아서 해요"
           if on else f"{label} 자동 진행을 껐어요 · 버튼으로 진행해요")
    return jsonify({"ok": True, "msg": msg, "office": oid, "on": on, "floor_label": label}) if _fetch() \
        else redirect(url_for("site_status"))


@app.post("/office/<office_id>/autoship")
def office_autoship(office_id: str):
    """Toggle autoship for a floor without entering a site."""
    from app.work import autoship
    if office_id not in sites.OFFICES:
        abort(404)
    on = request.form.get("on") == "1"
    autoship.set_enabled(on, office_id=office_id)
    off = sites.OFFICES[office_id]
    if on:
        for s in sites.office_sites(office_id):
            if s.enabled:
                autoship.kick(s.id)
    msg = (f"{off.floor_label} 자동 진행을 켰어요 · 이 층 사이트 PR·리뷰·머지·배포를 알아서 해요"
           if on else f"{off.floor_label} 자동 진행을 껐어요 · 버튼으로 진행해요")
    return jsonify({"ok": True, "msg": msg, "office": office_id, "on": on, "floor_label": off.floor_label}) if _fetch() \
        else redirect(request.referrer or url_for("home"))


@app.post("/site/<site>/checked")
def site_checked():
    from app.work import autoship
    at = autoship.mark_checked()
    return jsonify({"ok": True, "msg": f"사이트 확인 완료 · {at[5:16]}"}) if _fetch() else redirect(url_for("site_status"))


@app.post("/site/<site>/meeting/top")
def site_meeting_top():
    """Enqueue every runnable idea from this meeting (whole result → execution queue)."""
    from app.work import workqueue
    pid = (request.form.get("pid") or "").strip()
    out = actions.proposal_enqueue_all(pid) if pid else {"ok": False, "message": "제안을 찾지 못했어요", "n": 0}
    if not pid:
        # latest meeting on this site
        prop = planning.read()
        if prop.get("meeting") and prop.get("id"):
            out = actions.proposal_enqueue_all(prop["id"])
    return jsonify({"ok": out.get("ok"), "msg": out.get("message"), "n": out.get("n", 0)}) if _fetch() \
        else redirect(url_for("site_meet", proposal=pid or None))


@app.post("/site/<site>/meeting/run")
def site_meeting_run():
    """Enqueue one meeting idea (queue drains when someone is free)."""
    try:
        idx = int(request.form.get("idx") or -1)
    except ValueError:
        idx = -1
    out = actions.proposal_run((request.form.get("pid") or "").strip(), idx)
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": out.get("ok"), "message": out.get("message"), "msg": out.get("message")})
    return _back("meet", proposal=request.form.get("pid") or "")


@app.post("/site/<site>/queue/pause")
def site_queue_pause():
    from app.work import workqueue
    on = request.form.get("on") == "1"
    workqueue.set_paused(on)
    msg = "실행 큐를 멈췄어요" if on else "실행 큐를 다시 돌렸어요"
    return jsonify({"ok": True, "msg": msg}) if _fetch() else redirect(url_for("site_status"))


@app.post("/site/<site>/queue/start")
def site_queue_start():
    """한눈에 큐 팝업: 한 건 「지금 시작」또는 사이트 대기 한 건 drain."""
    from app.work import workqueue
    item_id = (request.form.get("id") or "").strip()
    if item_id:
        out = workqueue.start_now(item_id, sites.current_id())
    else:
        out = workqueue.drain_now(sites.current_id())
    if _fetch():
        return jsonify(out)
    return redirect(url_for("site_status"))


@app.post("/site/<site>/search/clear")
def site_search_clear():
    from app.work import workqueue
    workqueue.clear_search_targets()
    jobs.refresh_snapshot()
    return jsonify({"ok": True, "msg": "검색 고칠 목록을 비웠어요"}) if _fetch() else redirect(url_for("site_search"))


# ---------- 지도에서 바로 맡기기 (방·책상을 누르면 나오는 패널) ----------

@app.get("/site/<site>/panel/<team>")
def site_panel(team: str):
    return jsonify(actions.panel(team))


@app.post("/site/<site>/action/<aid>")
def site_action(aid: str):
    return jsonify(actions.run(aid))


@app.post("/site/<site>/propose")
def site_propose():
    focus = request.form.get("focus") or "all"
    ask = (request.form.get("ask") or "").strip()[:300]
    jobs.start_task("propose", "사업·기능 제안 정리 시작", planning.propose, focus if focus in planning.FOCUS else "all", ask)
    return _back("status")


@app.get("/site/<site>/proposal/<pid>.md")
def site_proposal_file(pid: str):
    pid = marketing._safe_id(pid)
    path = planning.file_of(pid, ".md") or planning.PROPOSAL_DIR / f"{pid}.md"
    if not pid or not path.is_file():
        abort(404)
    return send_file(path, mimetype="text/markdown", as_attachment=True, download_name=f"{sites.current_id()}-proposal-{pid}.md")


@app.post("/site/<site>/tools/claude-login")
def site_claude_login():
    tools.claude_login()  # okadmin's start_claude_login: opens the browser OAuth
    return _back("status")


@app.post("/site/<site>/tools/check")
def site_tools_check():
    tools.status(refresh=True)
    return _back("status")


# ---------- 2. 글 (서연) ----------

@app.get("/site/<site>/write")
def site_write():
    if request.args.get("path"):  # old links: editing lives in 다듬기 now
        return _back("polish", path=request.args.get("path"))
    workers.ensure_analysis_picks()
    return _render_site("write", research=workers.read_research(), seed_chips=workers.seed_chips(20))


@app.post("/site/<site>/write/research")
def site_write_research():
    seeds = [x.strip() for x in request.form.getlist("seed") if x.strip()][:20]
    extra = (request.form.get("seed_extra") or "").strip()
    if extra:
        seeds = (seeds + [w.strip() for w in extra.split(",") if w.strip()])[:20]
    def num(name: str, low: int, high: int, default: int) -> int:
        try:
            return max(low, min(high, int(request.form.get(name) or default)))
        except ValueError:
            return default

    n_topics = num("n_topics", 3, MAX_BATCH, 10)
    # no auto-write after research — picks stay on the list for the owner to choose
    jobs.start_task("research", "트렌드 리서치 시작", workers.research, seeds, 0, n_topics)
    return _back("write")


@app.post("/site/<site>/write/picks")
def site_write_picks():
    ids = [x.strip() for x in request.form.getlist("pick") if x.strip()][:MAX_BATCH]
    if ids:
        jobs.start_task_split("write", ids, lambda n: f"{sites.current().words['picks']} {n}{sites.current().words['unit']} {sites.current().words['verb']} 시작", workers.write_picks)
    return _back("write")


@app.post("/site/<site>/write")
def site_write_save():
    rel = (request.form.get("path") or "").strip()
    path = snapshot.safe_post(rel)
    if path is None:
        abort(404)
    path.write_text(request.form.get("body") or "", encoding="utf-8")
    jobs.refresh_snapshot()
    return _back("polish", path=rel, saved=1)


MAX_BATCH = 15


@app.post("/site/<site>/write/new")
def site_write_new():
    """Manual topics, one per line (up to 15), split across the writing team."""
    category = (request.form.get("category") or "").strip()
    if category not in workers.categories():
        return _back("write")
    topics = [t.strip()[:200] for t in (request.form.get("topics") or request.form.get("topic") or "").splitlines() if t.strip()]
    items = [[category, t] for t in topics[:MAX_BATCH]]
    jobs.start_task_split("write", items, lambda n: f"직접 고른 {sites.current().words['noun']} {n}{sites.current().words['unit']} {sites.current().words['verb']} 시작", workers.write_topics)
    return _back("write")


# ---------- 3. 다듬기 (마케팀) ----------

@app.get("/site/<site>/polish")
def site_polish():
    """직접 고치기 editor. 목록·맡기기는 검색 상세 플로에 합쳐져 있어요."""
    pick = (request.args.get("pick") or "").strip()
    rel = (request.args.get("path") or "").strip()
    if not rel:
        kw = {"pick": pick} if pick else {}
        return redirect(url_for("site_search", **kw) + ("#polish" if pick else "#d"))
    path = snapshot.safe_post(rel)
    if path is None:
        abort(404)
    post = {"rel": rel, "title": path.stem, "body": path.read_text(encoding="utf-8"),
            "saved": bool(request.args.get("saved"))}
    return _render_site("polish", post=post, pick=pick)


@app.post("/site/<site>/polish")
def site_polish_run():
    rels = [r.strip() for r in request.form.getlist("rel") if r.strip() and snapshot.safe_post(r.strip())][:MAX_BATCH]
    ask = (request.form.get("ask") or "").strip()[:400]
    if rels:
        jobs.start_task_split("polish", rels, lambda n: f"글 {n}편 다듬기 시작", marketing.polish_many, ask)
    return _back("search")


# ---------- 4. 검색 · 분석 · 다듬기 (마케팀) ----------

@app.get("/site/<site>/search")
def site_search():
    marketing.import_legacy_analysis()
    analyses = marketing.list_analyses()
    days = []
    seen_days: set[str] = set()
    for h in analyses:
        d = str(h.get("at") or "")[:10]
        if d and d not in seen_days:
            seen_days.add(d)
            days.append(d)
    day = (request.args.get("date") or "").strip()[:10]
    aid = (request.args.get("analysis") or "").strip()
    if day and day in days and not aid:
        aid = next((h["id"] for h in analyses if str(h.get("at") or "").startswith(day)), "")
    elif aid:
        day = next((str(h.get("at") or "")[:10] for h in analyses if h["id"] == aid), day)
    if not day and days:
        day = days[0]
    snap, site = _site()
    # Old snaps may still say backlog_off from the removed toggle — rebuild once.
    if (site.get("search") or {}).get("backlog_off"):
        jobs.refresh_snapshot()
        snap, site = _site()
    analysis = marketing.read_analysis(aid)
    targets = marketing.mkt_targets(site, analysis)
    pick = (request.args.get("pick") or "").strip()
    day_i = days.index(day) if day in days else -1
    return _render_site(
        "search", analysis=analysis, analyses=analyses, viewing=aid, targets=targets, pick=pick,
        analysis_days=days, analysis_day=day,
        analysis_day_newer=days[day_i - 1] if day_i > 0 else "",
        analysis_day_older=days[day_i + 1] if 0 <= day_i < len(days) - 1 else "",
        analysis_day_counts={d: sum(1 for h in analyses if str(h.get("at") or "").startswith(d)) for d in days},
    )


@app.get("/site/<site>/analysis/<aid>.md")
def site_analysis_file(aid: str):
    aid = marketing._safe_id(aid)
    path = marketing.ANALYSIS_DIR / f"{aid}.md"
    if not aid or not path.is_file():
        abort(404)
    return send_file(path, mimetype="text/markdown", as_attachment=True, download_name=f"{sites.current_id()}-analysis-{aid}.md")


@app.post("/site/<site>/search/analyze")
def site_search_analyze():
    jobs.start_task("analyze", "GA4·GSC 분석 시작", marketing.analyze)
    return _back("search")


@app.post("/site/<site>/search")
def site_search_run():
    """One submit from the unified 마케팀 list: split into SEO fix and/or polish automatically."""
    raw = [t for t in request.form.getlist("target") if t][:MAX_BATCH]
    urls: list[str] = []
    rels: list[str] = []
    for item in raw:
        parts = str(item).split("\t")
        url = (parts[0] if len(parts) > 0 else "").strip()
        rel = (parts[1] if len(parts) > 1 else "").strip()
        job_bits = {j.strip() for j in (parts[2] if len(parts) > 2 else "").split(",") if j.strip()}
        if "search" in job_bits and url and url not in urls:
            urls.append(url)
        if "polish" in job_bits and rel and rel not in rels and snapshot.safe_post(rel):
            rels.append(rel)
    if not urls and not rels:
        return _back("search")
    _, site = _site()
    if urls:
        by_url = {r["url"]: r for r in (site.get("search") or {}).get("rows") or []}
        rows = [
            {
                "url": url,
                "impressions": (by_url.get(url) or {}).get("impressions") or 0,
                "ctr": (by_url.get(url) or {}).get("ctr") or 0,
                "position": (by_url.get(url) or {}).get("position") or 0,
                "pattern": (by_url.get(url) or {}).get("pattern") or "low_ctr",
            }
            for url in urls
        ]
        jobs.start_task_split("search", urls, lambda n: f"글 {n}편 고치기 시작", workers.search_fix, rows)
    if rels and sites.current().can_polish:
        jobs.start_task_split("polish", rels, lambda n: f"글 {n}편 다듬기 시작", marketing.polish_many, "")
    return _back("search")


# ---------- 대기 중인 일 → 실행 큐 ----------

@app.post("/site/<site>/run/<task>")
def site_run(task: str):
    """Put waiting work on the execution queue (drains when a seat is free)."""
    from app.work import workqueue
    waiting = office.waiting_work(snapshot.load_or_refresh(), office.read_flow())
    label = waiting.get(task) or task
    if task not in waiting and task not in ("write", "search", "issue", "review", "deploy"):
        msg = "지금 할 일이 없어요"
        return jsonify({"ok": False, "msg": msg}) if _fetch() else redirect(url_for("site_status"))
    row = workqueue.enqueue_task(task, label)
    if not row:
        msg = workqueue.pending_label(workqueue.pending_tasks().get(task, "queued"))
        return jsonify({"ok": False, "msg": f"이미 {msg}", "queued": True}) if _fetch() else redirect(request.form.get("next") or url_for("site_status"))
    msg = f"큐에 담았어요 · 곧 시작 · {label}"
    return jsonify({"ok": True, "msg": msg, "queued": True, "label": workqueue.pending_label("queued")}) if _fetch() \
        else redirect(request.form.get("next") or url_for("site_status"))


# ---------- 5. 이미지 · YouTube (디자인팀) ----------

@app.get("/site/<site>/youtube")
def site_youtube():
    """Old URL → design tab YouTube mode (hq.youtube.enabled sites)."""
    cur = sites.current()
    if not cur.youtube_on:
        abort(404)
    return redirect(url_for("site_images") + "#youtube")


@app.route("/site/<site>/youtube/api/", defaults={"path": ""}, methods=["GET", "POST"])
@app.route("/site/<site>/youtube/api/<path:path>", methods=["GET", "POST"])
def site_youtube_api(path: str = ""):
    """JSON API for the design-team YouTube UI (same capabilities as okadmin /api/youtube)."""
    from app.content import youtube_queue as ytq
    from app.content.youtube_api import YouTubeApiError, youtube_api_key
    from app.money import costs
    cur = sites.current()
    if not cur.youtube_on:
        return jsonify({"error": "이 사이트는 YouTube 연결이 꺼져 있어요 · sites.yaml hq.youtube.enabled"}), 404
    sid = cur.id
    parts = [p for p in (path or "").split("/") if p]
    head = parts[0] if parts else ""

    if head == "status" and request.method == "GET":
        key = youtube_api_key()
        return jsonify({
            "site_id": sid,
            "site_label": ytq.site_label(sid),
            "noun": ytq.site_noun(sid),
            "sites": sorted(ytq.youtube_site_ids()),
            "api_key_configured": bool(key),
            "api_key_prefix": (key[:4] + "…") if key else "",
        })

    if head == "shops" and len(parts) == 1 and request.method == "GET":
        raw_filter = (request.args.get("filter") or "").strip().lower()
        filter_mode = raw_filter if raw_filter in ("missing", "attached", "all") else "missing"
        limit_raw = (request.args.get("limit") or "100").strip()
        limit = int(limit_raw) if limit_raw.isdigit() else 100
        limit = max(1, min(limit, 200))
        shops = ytq.list_shops(site_id=sid, filter_mode=filter_mode, limit=0)
        gsc = ytq.load_shop_gsc(site_id=sid)
        shops = ytq.apply_shop_gsc(shops, gsc)[:limit]
        gsc_out = {
            "days": gsc.get("days"), "start": gsc.get("start"), "end": gsc.get("end"),
            "cache_hit": bool(gsc.get("cache_hit")), "shop_count": gsc.get("shop_count"),
        }
        if gsc.get("error"):
            gsc_out["error"] = gsc["error"]
        return jsonify({
            "site_id": sid, "site_label": ytq.site_label(sid), "noun": ytq.site_noun(sid),
            "filter": filter_mode, "shops": shops, "count": len(shops), "gsc": gsc_out,
        })

    if head == "shops" and len(parts) >= 3:
        base_id = parts[1]
        action = parts[2]
        if action == "candidates" and request.method == "GET":
            entry = ytq.get_cached_candidates(base_id, site_id=sid)
            if not entry:
                return jsonify({"error": "no cached candidates — fetch first", "base_id": base_id}), 404
            return jsonify(entry)
        if action == "fetch" and request.method == "POST":
            body = request.get_json(silent=True) or {}
            query = (body.get("query") or request.args.get("query") or "").strip() or None
            max_raw = body.get("max_results") or request.args.get("max_results") or 5
            try:
                max_results = int(max_raw)
            except (TypeError, ValueError):
                max_results = 5
            max_results = max(3, min(max_results, 5))
            try:
                return jsonify(ytq.fetch_candidates(base_id, site_id=sid, query=query, max_results=max_results))
            except FileNotFoundError as exc:
                return jsonify({"error": str(exc)}), 404
            except costs.OverLimit as exc:
                return jsonify({"error": str(exc)}), 429
            except YouTubeApiError as exc:
                return jsonify({"error": str(exc)}), 502
        if action == "attach" and request.method == "POST":
            body = request.get_json(silent=True) or {}
            video_id = (body.get("video_id") or "").strip()
            if not video_id:
                return jsonify({"error": "video_id required"}), 400
            try:
                return jsonify(ytq.attach_video(base_id, video_id, site_id=sid))
            except (FileNotFoundError, ValueError) as exc:
                return jsonify({"error": str(exc)}), 400
        if action == "clear" and request.method == "POST":
            return jsonify(ytq.clear_video(base_id, site_id=sid))

    if head == "ship" and request.method == "GET":
        return jsonify(ytq.ship_status(sid))
    if head == "ship" and request.method == "POST":
        if is_boss():
            return jsonify({"ok": False, "error": "사장님이 퇴근하시면 작업할게요 (PR·머지·배포)", "message": "사장님이 퇴근하시면 작업할게요 (PR·머지·배포)"}), 403
        out = ytq.enqueue_ship(sid)
        code = 200 if out.get("ok") else 409
        return jsonify(out), code

    if head == "default-query" and request.method == "GET":
        shop = (request.args.get("shop_name") or "").strip()
        address = (request.args.get("address") or "").strip()
        activity = (request.args.get("activity") or "").strip()
        return jsonify({
            "query": ytq.default_query(shop, address, site_id=sid, activity=activity),
            "site_id": sid,
        })

    return jsonify({"error": "unknown youtube api path", "path": path}), 404


@app.get("/site/<site>/images")
def site_images():
    from app.content import jpcontent
    from app.content import cdbcontent
    from app.content import poicontent
    campus = jpcontent.is_site()
    poi = poicontent.is_site()
    from app.content import placepick
    from app.content import gcsimage
    snap, site = _site()
    live = snapshot._images() if sites.current().enabled else None   # fresh list: kinds, same-photo marks, names
    pickable = placepick.eligible_names([f.get("name", "") for f in ((live or site.get("images")) or {}).get("files") or []])
    from app.content import contentkit
    can_ai = not bool(contentkit.redraw_blocked())
    return _render_site("images", places=jpcontent.has_places(), campus=campus, cdb=cdbcontent.is_site(), poi=poi, pickable=pickable,
                        live_images=live, gcs=gcsimage.enabled(), can_ai=can_ai,
                        poi_noun=poicontent.kit().noun_ko if poi else "",
                        no_photo=len(poicontent.shops_without_photo()) if poi else 0,
                        stock_n=len(jpcontent.stock_cover_slugs()) if campus else 0)


@app.post("/site/<site>/images/covers")
def site_image_covers():
    """Campus sites: guides still on Unsplash → Google Places / school photo covers (40 per run)."""
    jobs.start_task("images", "가이드 커버를 실제 사진으로 바꾸기 시작", workers.images_replace_stock)
    return _back("images")


@app.post("/site/<site>/images/logos")
def site_image_logos():
    """companydb: fill company logos (the site's own logo step)."""
    jobs.start_task("images", "회사 로고 채우기 시작", workers.images_fill_logos)
    return _back("images")


@app.post("/site/<site>/images/cleanup")
def site_image_cleanup():
    """Delete images nothing uses (pages, templates, data, other sites) — locally and in GCS."""
    from app.content import imagecleanup
    jobs.start_task("images", "안 쓰는 이미지 정리 시작", imagecleanup.clean)
    return _back("images")


@app.post("/site/<site>/images/sync")
def site_image_sync():
    """Send photos that differ from the site's GCS copy (okadmin's upload), e.g. ones replaced before uploads existed."""
    from app.content import gcsimage
    jobs.start_task("images", "바뀐 사진 사이트 저장소에 올리기 시작", gcsimage.sync)
    return _back("images")


@app.post("/site/<site>/images/places")
def site_image_places():
    """krcampus: fetch Google Places photos for school/university pages that have none."""
    jobs.start_task("images", "빠진 학교·대학 사진 받기 시작", workers.images_places_missing)
    return _back("images")


@app.get("/site/<site>/images/candidates")
def site_image_candidates():
    """Google Places candidate photos for one selected image (the owner picks the right one)."""
    from app.money import costs
    from app.content import placepick
    name = request.args.get("name") or ""
    if snapshot.safe_image(name) is None or not placepick.eligible(name):
        return jsonify({"ok": False, "msg": "이 이미지는 Places 후보를 고를 수 없어요"})
    try:
        out = placepick.candidates(name, request.args.get("q") or "")
    except costs.OverLimit as exc:
        return jsonify({"ok": False, "msg": str(exc)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "msg": f"후보를 찾지 못했어요 · {str(exc)[:120]}"})
    return jsonify({"ok": True, **out})


@app.get("/site/<site>/images/candidate")
def site_image_candidate():
    from app.content import placepick
    try:
        raw = placepick.thumb(request.args.get("ref") or "")
    except Exception:  # noqa: BLE001 - over the limit / gone: an empty tile
        abort(404)
    resp = app.response_class(raw, mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@app.post("/site/<site>/images/pick")
def site_image_pick():
    from app.content import placepick
    name = request.form.get("name") or ""
    try:
        msg = placepick.apply(name, request.form.get("ref") or "")
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "msg": str(exc)[:200]})
    jobs.refresh_snapshot()
    return jsonify({"ok": True, "msg": msg, "name": name})


@app.get("/site/<site>/images/file/<name>")
def site_image_file(name: str):
    path = snapshot.safe_image(name)
    if path is None:
        abort(404)
    return send_file(path, max_age=0)


@app.post("/site/<site>/images")
def site_image_replace():
    name = request.form.get("name") or ""
    path = snapshot.safe_image(name)
    upload = request.files.get("file")
    if path is None or upload is None or not upload.filename:
        abort(400)
    upload.save(path)
    note = workers._gcs_push([path.name])
    jobs.refresh_snapshot()
    if _fetch():
        return jsonify({"ok": True, "msg": f"{path.name}을 내 파일로 바꿨어요" + (f" · {note}" if note else "")})
    return _back("images")


@app.post("/site/<site>/images/redraw")
def site_image_redraw():
    name = request.form.get("name") or ""
    if snapshot.safe_image(name) is None:
        abort(400)
    hint = (request.form.get("hint") or "").strip()[:300]
    jobs.start_task("images", f"{name} AI로 그리기 시작", workers.images_redraw, name, hint)
    return _back("images")


# ---------- 6. 반영 (개발팀) ----------

@app.get("/site/<site>/ship")
def site_ship():
    return _render_site("ship", refactor_kinds={k: devwork.KINDS[k] for k in devwork.UI_KINDS}, refactor_areas=devwork.AREAS,
                        refactor_last=devwork.last_result())


@app.post("/site/<site>/ship/issue")
def site_ship_issue():
    hint = (request.form.get("hint") or "").strip()[:300]
    jobs.start_task("issue", "이슈·PR 만들기 시작", workers.issue_pr, hint)
    return _back("ship")


@app.post("/site/<site>/ship/review")
def site_ship_review():
    jobs.start_task("review", "PR 리뷰·머지 시작", workers.review_and_merge)
    return _back("ship")


@app.post("/site/<site>/ship/fix")
def site_ship_fix():
    workers.start_fix_for_review()
    return _back("ship")


@app.post("/site/<site>/ship/push")
def site_ship_push():
    jobs.start_task("push", "PR 브랜치에 푸시 시작", workers.push_fixes)
    return _back("ship")


@app.post("/site/<site>/ship/merge")
def site_ship_merge():
    # after a "고칠 게 있어요" verdict: merge anyway, still 다은
    jobs.start_task("review", "그래도 머지 시작", workers.review_and_merge, True)
    return _back("ship")


@app.post("/site/<site>/ship/refactor")
def site_ship_refactor():
    kind = request.form.get("kind") or "tidy"
    area = request.form.get("area") or "back"
    ask = (request.form.get("ask") or "").strip()[:400]
    hand_off = bool(request.form.get("hand_off"))
    if kind == "optimize":
        jobs.start_task("optimize", "최적화 시작", devwork.optimize, ask, hand_off)
        return _back("ship")
    label = devwork.KINDS.get(kind, ("소스정리",))[0]
    jobs.start_task(devwork.area_task(area), f"{label} 시작", devwork.refactor, kind, area, ask, hand_off)
    return _back("ship")


@app.post("/site/<site>/ship/auto")
def site_ship_auto():
    autodev.set_on(request.form.get("on") == "1")
    return _back("ship")


@app.post("/site/<site>/ship")
def site_ship_run():
    jobs.start_task("deploy", "사이트 올리기 시작", workers.deploy)
    return _back("ship")


@app.post("/site/<site>/tools/restart")
def site_restart():
    """Restart 8091 via start.sh (it stops this server first). Refused while someone is working."""
    import subprocess

    if jobs.any_running():
        return _back("status")
    # WERKZEUG_* would make the new server think it is this server's reloader child (bad fd)
    env = {k: v for k, v in os.environ.items() if not k.startswith("WERKZEUG_")}
    subprocess.Popen(
        ["bash", "-c", f"sleep 1; exec ./start.sh {PORT} >> data/logs/server.log 2>&1"],
        cwd=str(ROOT), start_new_session=True, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return "<meta http-equiv='refresh' content='4;url=" + url_for("site_status") + "'><p style='font-family:sans-serif'>다시 켜는 중… 4초 뒤에 돌아가요.</p>"


@app.get("/healthz")
def healthz():
    return "ok", 200


# 자동 공통화·소스정리: only in the serving process (the reloader's parent only watches files).
if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or os.environ.get("NEXT_AUTODEV") == "1":
    autodev.start_loop()
    affsync.start_loop()   # 어필리에이트: every few hours
    from app.work import autoship
    from app.work import workqueue
    autoship.start_loop()  # 자동 진행: PR → review → merge → deploy
    workqueue.start_loop()  # 실행 큐: 회의·할 일 순서대로 시작
