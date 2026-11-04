"""Per-person job slots. Several people can work at once; git work takes turns."""

from __future__ import annotations

import threading
import traceback
from typing import Any, Callable

from app import office
from app import sites
_LOCK = threading.Lock()
GIT_LOCK = sites.git_lock(sites.DEFAULT_SITE)   # okpy's; every site has its own (sites.git_lock)
_REQ = threading.local()            # per web request: who got the work handed out (or why nobody did)
_IN_JOB = threading.local()        # set inside a job thread: hand-offs pass the concurrency cap
REFRESH_LOCK = threading.Lock()    # one snapshot rewrite at a time
JOBS: dict[str, dict[str, Any]] = {}


def get(role: str) -> dict[str, Any]:
    with _LOCK:
        return dict(JOBS.get(role) or {"running": False, "kind": role, "line": ""})


def all_jobs() -> dict[str, dict[str, Any]]:
    with _LOCK:
        return {r: dict(j) for r, j in JOBS.items()}


def running(role: str) -> bool:
    return bool(get(role).get("running"))


def any_running() -> bool:
    with _LOCK:
        return any(j.get("running") for j in JOBS.values())


def _set(role: str, **kw: Any) -> None:
    with _LOCK:
        JOBS.setdefault(role, {"running": False, "kind": role, "line": "", "state": ""}).update(kw)


class Work:
    """Handle passed to a worker: say(line) updates the bubble and the feed line."""

    def __init__(self, role: str):
        self.role = role
        self.person = office.staff_by_role(role)
        self.site = sites.current_id()

    def say(self, line: str, state: str = "work") -> None:
        _set(self.role, line=line, state=state)

    def event(self, text: str, state: str = "done") -> None:
        office.append_event(who=self.person["name"], dept=self.person["dept"], text=text, state=state, site=self.site)

    def git_turn(self):
        """Context manager: wait for the git lock, showing 'waiting' meanwhile."""
        work = self

        class _Turn:
            lock = sites.git_lock(work.site)   # one repo, one git job at a time

            def __enter__(self_inner):
                if not self_inner.lock.acquire(blocking=False):
                    work.say("앞 git 작업이 끝나길 기다리는 중", state="think")
                    self_inner.lock.acquire()
                work.say("작업 중")
                return work

            def __exit__(self_inner, *exc):
                self_inner.lock.release()
                return False

        return _Turn()


def refresh_snapshot() -> None:
    from app import snapshot
    with REFRESH_LOCK:
        try:
            snapshot.refresh_snapshot()
        except Exception:
            traceback.print_exc()


def _same_site(j: dict[str, Any], site: str) -> bool:
    return j.get("site", sites.DEFAULT_SITE) == site


def busy_groups(site: str | None = None) -> dict[str, str]:
    """Work kinds someone is doing right now on this site: group → person id (see office.TASK_GROUP).
    Meetings count everywhere (people, not a repo)."""
    site = site or sites.current_id()
    with _LOCK:
        out = {}
        for role, j in JOBS.items():
            if j.get("running") and j.get("task"):
                group = office.task_group(j["task"])
                if group.startswith("meeting") or _same_site(j, site):
                    out.setdefault(group, role)
        return out


def busy_items(task: str, site: str | None = None) -> dict[str, str]:
    """Items (post, URL, topic id) of this task someone is working on (this site): item → person id."""
    site = site or sites.current_id()
    with _LOCK:
        out = {}
        for role, j in JOBS.items():
            if j.get("running") and j.get("task") == task and _same_site(j, site):
                for item in j.get("items") or []:
                    out[str(item)] = role
        return out


def start(role: str, start_line: str, target: Callable[..., str], *args: Any,
          _task: str = "", _items: list | None = None, **kwargs: Any) -> bool:
    """Run target(work, *args) in a thread. Returns False if this person is already busy.

    target returns the finished line ("3편을 고쳤어요"). Raising marks the person as needing a check.
    _task/_items record what is being done so nobody else is handed the same work meanwhile.
    """
    site = sites.current_id()
    with _LOCK:
        if (JOBS.get(role) or {}).get("running"):
            return False
        if not _task.startswith("meeting") and not getattr(_IN_JOB, "on", False) and _heavy_count() >= sites.MAX_CONCURRENT:
            return False   # usage limit guard; a hand-off from a running job still goes through
        JOBS[role] = {"running": True, "kind": role, "line": start_line, "state": "work", "site": site,
                      "task": _task, "items": [str(i) if not isinstance(i, (list, tuple)) else str(i[-1]) for i in (_items or [])]}
        if not _task.startswith("meeting"):
            office.note_assigned(role)   # the next task of this team goes to someone else first
    work = Work(role)
    work.event(start_line, state="work")

    def run() -> None:
        sites.set_current(site)   # the job keeps its site; hand-offs started here stay on it
        _IN_JOB.on = True
        try:
            done = target(work, *args, **kwargs) or "끝났어요"
            _set(role, running=False, line=done, state="done")
            work.event(done, state="done")
        except Exception as exc:  # noqa: BLE001 - surface any failure on the bubble
            traceback.print_exc()
            msg = f"못 했어요. {str(exc)[:200]}"
            _set(role, running=False, line=msg, state="wait")
            work.event(msg, state="wait")
        finally:
            refresh_snapshot()
            if not _task.startswith("meeting"):
                try:   # 자동 진행: changes → PR → review → merge → deploy
                    from app.work import autoship
                    autoship.kick(site)
                except Exception as exc:  # noqa: BLE001
                    print(f"autoship kick failed: {exc}")
                try:   # 실행 큐: 다음 대기 건
                    from app.work import workqueue
                    workqueue.kick(site)
                except Exception as exc:  # noqa: BLE001
                    print(f"workqueue kick failed: {exc}")

    threading.Thread(target=run, name=f"job-{role}", daemon=True).start()
    return True


def _heavy_count() -> int:
    """Running jobs that use Claude (meetings are one call for many people: not counted). Call with _LOCK held."""
    return sum(1 for j in JOBS.values() if j.get("running") and not str(j.get("task") or "").startswith("meeting"))


def at_capacity() -> bool:
    with _LOCK:
        return _heavy_count() >= sites.MAX_CONCURRENT


def free_members(team: tuple[str, ...] | list[str]) -> list[str]:
    with _LOCK:
        return [r for r in team if not (JOBS.get(r) or {}).get("running")]


def start_team(
    team: tuple[str, ...] | list[str],
    items: list,
    start_line: Callable[[int], str],
    target: Callable[..., str],
    *extra: Any,
    stagger: float = 2.0,
    task: str = "",
) -> list[tuple[str, int]]:
    """Split items across the free members of a team (round-robin) and start one job each.

    target(work, chunk, *extra). Returns [(role, n_items)]. Members start `stagger` seconds
    apart so files named by timestamp (covers) don't collide.
    """
    free = free_members(team)
    if task:  # items someone is already doing are not handed out twice
        taken = busy_items(task)
        items = [i for i in items if (str(i) if not isinstance(i, (list, tuple)) else str(i[-1])) not in taken]
    if not free or not items:
        return []
    chunks: dict[str, list] = {r: [] for r in free}
    for i, item in enumerate(items):
        chunks[free[i % len(free)]].append(item)
    started: list[tuple[str, int]] = []
    for n, (role, chunk) in enumerate((r, c) for r, c in chunks.items() if c):
        delay = n * stagger

        def run_later(work, chunk=chunk, delay=delay):
            if delay:
                import time

                time.sleep(delay)
            return target(work, chunk, *extra)

        if start(role, start_line(len(chunk)), run_later, _task=task, _items=chunk):
            started.append((role, len(chunk)))
    return started


WIP_TASKS = ("issue", "push", "deploy", "optimize", "refactor", "refactor_front", "refactor_back")   # wait for the owner's uncommitted work
CONTENT_TASKS = ("research", "write", "images")   # need the site's own post generator (okpy only for now)


def reset_request() -> None:
    _REQ.tried, _REQ.who, _REQ.why = False, [], ""


def request_result() -> dict[str, Any]:
    """What the last start_task / start_task_split of this web request did (for buttons sent with fetch)."""
    return {"tried": bool(getattr(_REQ, "tried", False)), "who": list(getattr(_REQ, "who", []) or []),
            "why": str(getattr(_REQ, "why", "") or "")}


def _refused(why: str) -> None:
    if not getattr(_IN_JOB, "on", False):
        _REQ.tried, _REQ.why = True, why


_GROUP_WHY = {   # work that goes one at a time per site, and why
    "refactor": "{who}이(가) {site} 코드를 고치는 중이에요 · 코드 작업은 사이트마다 한 번에 한 명만 해요 (변경이 한 PR에 섞이지 않게)",
    "pr": "{who}이(가) {site} PR 작업(이슈·리뷰·푸시) 중이에요 · PR 일은 한 번에 하나씩 해요",
    "deploy": "{who}이(가) {site}를 올리는 중이에요 · 배포는 한 번에 하나씩 해요",
}


def _busy_why(holder: str, group: str) -> str:
    who, site = office.staff_by_role(holder)["name"], sites.current().label
    return _GROUP_WHY.get(group, "{who}이(가) 이 일을 하는 중이에요").format(who=who, site=site) + " · 끝나면 다시 맡겨 주세요"


def start_task(task: str, start_line: str, target: Callable[..., str], *args: Any, _from: str = "") -> str | None:
    """Give a task to the first free person of the team that owns it (preferred people first).

    Returns the person id, or None when the whole team is busy or someone already does this kind of
    work (e.g. a second 소스정리 while one is running).
    """
    if task in CONTENT_TASKS and not sites.current().can_write:
        _refused(f"{sites.current().label}는 아직 이 일이 연결되지 않았어요")
        return None   # this site's posts/images are not connected yet: nobody is handed work that can only fail
    if task == "polish" and not sites.current().can_polish:
        _refused("이 사이트 페이지는 다듬지 않아요")
        return None   # company pages are built from filings, not written: nothing to polish
    if task in WIP_TASKS:
        from app import snapshot
        if snapshot.owner_wip():
            _refused("사장님이 커밋하지 않은 파일이 있어서 기다려요 · 커밋하거나 정리한 뒤 다시 맡겨 주세요")
            return None   # the owner's uncommitted files would end up in the PR / on the live site: wait
    holder = busy_groups().get(office.task_group(task))
    if holder and holder != _from:  # a hand-off from the person doing it (fix → push → review) is fine
        _refused(_busy_why(holder, office.task_group(task)))
        return None
    for person in office.ordered_members(task):
        if start(person, start_line, target, *args, _task=task):
            with _LOCK:
                taken = [j.get("seat") for r, j in JOBS.items() if r != person and j.get("running") and j.get("seat")]
            seat = office.work_seat(task, person, taken)
            if seat:
                _set(person, seat=seat)
            if not getattr(_IN_JOB, "on", False):
                _REQ.tried, _REQ.who = True, [*getattr(_REQ, "who", []), person]
            return person
    _refused("팀 모두 일하는 중이에요 · 끝나면 다시 맡겨 주세요")
    return None


def start_task_split(task: str, items: list, start_line: Callable[[int], str], target: Callable[..., str], *extra: Any):
    """Split items across the free members of the task's team (see start_team)."""
    if task in CONTENT_TASKS and not sites.current().can_write:
        _refused(f"{sites.current().label}는 아직 이 일이 연결되지 않았어요")
        return []
    if task == "polish" and not sites.current().can_polish:
        _refused("이 사이트 페이지는 다듬지 않아요")
        return []
    started = start_team(office.ordered_members(task), items, start_line, target, *extra, task=task)
    if started:
        if not getattr(_IN_JOB, "on", False):
            _REQ.tried, _REQ.who = True, [*getattr(_REQ, "who", []), *[r for r, _ in started]]
    elif items:
        _refused("팀 모두 일하는 중이거나 같은 일이 진행 중이에요 · 끝나면 다시 맡겨 주세요")
    return started
