"""개발팀: 공통화 · 소스정리 in the okpy repo.

Claude Code edits the code itself (Read/Edit/Write/Glob/Grep only; no shell, no web) inside okpy.
Afterwards we check the result: only code files may change, Python must still compile and okpy's
tests must pass. Otherwise everything this job changed is put back. A good result is handed to
기획팀 for an issue/PR, so 다은 reviews it like any other change.
"""

from __future__ import annotations
from app.paths import HQ_ROOT

import fnmatch
import hashlib
import threading
import json
import os
import py_compile
import re
import subprocess
from pathlib import Path
from typing import Any

from app import office
from app import sites
from app import snapshot
from app import tools
ROOT = HQ_ROOT  # starful-hq/ (data lives next to app/)
OKPY = snapshot.OKPY_ROOT
LOG_DIR = ROOT / "data" / "logs" / "refactor"

KINDS = {
    "common": ("공통화", "여러 곳에 반복되는 코드·템플릿 조각을 찾아 공통 함수/모듈/매크로로 묶어라. 동작은 바꾸지 마라."),
    "tidy": ("소스정리", "안 쓰는 import·함수·변수·파일 조각을 지우고, 이름과 구조를 읽기 쉽게 정돈하라. "
                       "주석도 정리하라: 틀리거나 오래된 주석은 고치고, 주석 처리된 죽은 코드와 뻔한 주석은 지우고, "
                       "이해하기 어려운 곳에만 짧은 주석·docstring을 더하라. 동작은 바꾸지 마라."),
    "optimize": ("최적화", "소스정리(주석 포함)를 백엔드·프론트 동시에 한다. 공통화는 따로 맡긴다."),
    "feature": ("기능 추가", "아래 '추가 요청'의 기능을 이 저장소에 작게 구현하라. 기존 페이지와 URL은 그대로 두고, 새 것만 더하라."),
}
UI_KINDS = ("optimize", "common")  # 반영 화면의 선택지 (기능 추가는 회의 제안에서 실행)
AREAS = {
    "back": ("백엔드 (Python)", ["app/*.py", "app/**/*.py", "scripts/*.py", "script/*.py", "run.py", "wsgi.py"], "refactor_back"),
    "front": ("프론트 (템플릿·CSS·JS)", ["app/templates/**", "app/static/**/*.css", "app/static/**/*.js"], "refactor_front"),
    "all": ("전체", ["app/**", "scripts/**", "script/**", "run.py", "wsgi.py"], "refactor"),
}
# Never touched by this job: articles, images, data, deploy settings, dependencies.
BLOCKED = ("app/content/", "app/static/images/", "data/", "output/", ".venv/", "venv/", "cloudbuild.yaml", "deploy.sh",
           "Dockerfile", "requirements.txt", ".env", ".git/")
# Claude Code permission rules: the agent is refused before it can edit these (not only checked afterwards)
DENY_RULES = tuple(f"Edit(./{path}**)" for path in ("app/content/", "app/static/images/", "data/", "output/", ".venv/", "venv/", ".git/")) \
    + tuple(f"Edit(./{name})" for name in ("cloudbuild.yaml", "deploy.sh", "Dockerfile", "requirements.txt", ".env"))   # Edit rules cover Write/MultiEdit too
# While back and front are tidied at the same time, each agent may only edit its own side.
AREA_DENY = {
    "back": ("Edit(./app/templates/**)", "Edit(./app/static/**)"),
    "front": ("Edit(./app/**/*.py)", "Edit(./app/*.py)", "Edit(./scripts/**)", "Edit(./script/**)", "Edit(./run.py)", "Edit(./wsgi.py)"),
}
CODE_EXT = (".py", ".html", ".jinja", ".j2", ".css", ".js", ".md", ".txt", ".toml", ".cfg", ".ini", ".json", ".yaml", ".yml")


def _git(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(OKPY), *args], capture_output=True, text=True, timeout=timeout)


def _dirty() -> dict[str, str]:
    """path → content hash for every changed or new file (so we can tell what this job touched)."""
    out: dict[str, str] = {}
    snapshot.revert_generated()   # .pyc etc. written by running code are nobody's change
    proc = _git("status", "--porcelain", "-uall")
    for line in proc.stdout.splitlines():
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if snapshot.is_generated(path):
            continue
        full = OKPY / path
        out[path] = hashlib.sha1(full.read_bytes()).hexdigest() if full.is_file() else "-"
    return out


def _pre_dirty_msg(pre: list[str]) -> str:
    wip = snapshot.owner_wip()
    mine = [p for p in pre if p in wip]
    if mine:
        return f"사장님이 커밋하지 않은 작업 파일이 있어서 손대지 않았어요 ({mine[0]} 등 {len(mine)}개). 커밋하거나 정리한 뒤 맡겨 주세요"
    return f"아직 올리지 않은 코드 변경이 있어요 ({pre[0]} 등). 먼저 PR로 올려 주세요"


def changed_by_job(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Code files that changed while the job ran. Articles, images and data are left out: other teams
    (마케팀 검색 고치기 · 기획팀 글쓰기) edit those at the same time, and the agent can't write there
    (DENY_RULES), so they are never this job's — and never reverted by it."""
    return sorted(p for p, h in after.items() if before.get(p) != h and not p.startswith(BLOCKED))


def blocked_files(paths: list[str]) -> list[str]:
    return [p for p in paths if p.startswith(BLOCKED) or not p.endswith(CODE_EXT)]


def _restore(paths: list[str], before: dict[str, str]) -> None:
    """Put back what this job changed. Files that were already dirty before are left as they are now
    only if we cannot restore them safely (we never had their old content), so we refuse those earlier."""
    tracked = set(_git("ls-files", *paths).stdout.split()) if paths else set()
    for p in paths:
        if p in tracked:
            _git("checkout", "--", p)
        elif p not in before:
            try:
                (OKPY / p).unlink()
            except OSError:
                pass


def _python() -> str:
    venv = next((OKPY / d / "bin" / "python" for d in (".venv", "venv") if (OKPY / d / "bin" / "python").exists()), OKPY / ".venv" / "bin" / "python")
    return str(venv) if venv.exists() else "python3"


def _checks(paths: list[str]) -> list[str]:
    problems = []
    for p in paths:
        if p.endswith(".py") and (OKPY / p).is_file():
            try:
                py_compile.compile(str(OKPY / p), doraise=True)
            except py_compile.PyCompileError as exc:
                problems.append(f"{p}: 문법 오류 {str(exc).splitlines()[-1][:120]}")
    if problems:
        return problems
    after = _run_tests()
    if after is None:
        return problems
    failed, tail = after
    if failed is None:   # collection error etc.: not something a baseline can excuse
        problems.append(f"{sites.current().label} 테스트 실패: " + tail)
        return problems
    new = sorted(failed - baseline_failures())
    if new:
        problems.append(f"{sites.current().label} 테스트 실패 (이번 변경으로 새로 깨짐 {len(new)}개): "
                        + ", ".join(new[:3])[:260])
    return problems


_FAIL_RE = re.compile(r"^(?:SUB)?FAILED(?:\(.*?\))?\s+(\S+::\S+)", re.M)
_BASELINE: dict[tuple[str, str], set[str]] = {}


def _run_tests() -> tuple[set[str] | None, str] | None:
    """(failing test ids, last lines). None: the repo has no pytest/tests. ids None: the run itself broke."""
    has_pytest = subprocess.run([_python(), "-c", "import pytest"], cwd=str(OKPY), capture_output=True).returncode == 0
    if not has_pytest or not (OKPY / "tests").is_dir():
        return None
    proc = subprocess.run([_python(), "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider", "tests"], cwd=str(OKPY),
                          capture_output=True, text=True, timeout=900)
    out = proc.stdout + proc.stderr
    tail = " / ".join([ln for ln in out.splitlines() if ln.strip()][-3:])[:300]
    if proc.returncode in (0, 5):   # 5 = no tests collected
        return set(), tail
    if proc.returncode != 1:        # 2 interrupted · 3 internal · 4 usage: e.g. an import broke
        return None, tail
    ids = {m.group(1).split(" ")[0] for m in _FAIL_RE.finditer(out)}
    return (ids or None), tail


def baseline_failures() -> set[str]:
    """Tests already failing before this job touched anything: those don't block it (e.g. krcampus had one
    failing since August). Measured at the start of the job, when the code is still HEAD (a job never starts
    on uncommitted code); cached per site + HEAD so it runs once per commit."""
    key = (sites.current_id(), _git("rev-parse", "HEAD").stdout.strip())
    if key not in _BASELINE:
        res = _run_tests()
        _BASELINE[key] = (res[0] or set()) if res else set()
    return _BASELINE[key]


def _prompt(kind: str, area: str, ask: str) -> str:
    kind_label, kind_rule = KINDS[kind]
    area_label, globs, _ = AREAS[area]
    return (
        f"너는 {sites.current().about} 저장소의 개발자다. 작업: {kind_label}.\n{kind_rule}\n"
        f"대상: {area_label} — {', '.join(globs)}\n"
        + (f"추가 요청: {ask}\n" if ask else "")
        + "규칙:\n"
        "- 글(app/content), 이미지(app/static/images), data/, 배포 설정, requirements.txt는 절대 건드리지 마라.\n"
        + ("- 새 라이브러리를 추가하지 말고, 지금 있는 코드 스타일을 따라라. 완성 못 할 만큼 크면 가장 작은 첫 단계만 만들어라.\n"
           if kind == "feature" else
           "- 공개 URL, 템플릿이 받는 변수 이름, 함수의 바깥 동작을 바꾸지 마라.\n"
           "- 한 번에 너무 많이 바꾸지 말고, 확실히 안전한 변경만 5~15곳 정도 하라.\n")
        + "- 끝나면 마지막에 한국어로 '바꾼 것:' 목록(파일 · 한 줄 설명)만 짧게 써라.\n"
    )


def refactor(work, kind: str, area: str, ask: str = "", hand_off: bool = True) -> str:
    if kind not in KINDS or area not in AREAS:
        raise RuntimeError("작업 종류를 골라 주세요")
    tools.need("claude")
    flow = office.read_flow()
    if flow.get("pr") and not flow.get("merged"):
        raise RuntimeError(f"열린 PR #{(flow.get('pr') or {}).get('number')}이 있어요. 머지한 뒤에 맡겨 주세요")
    from app.work import workers
    workers._okadmin_import()
    from llm_claude import MODEL_HEAVY, claude_bin

    kind_label = KINDS[kind][0]
    with work.git_turn():  # nobody switches branches while the code is being edited
        before = _dirty()
        pre_dirty_code = [p for p in before if not p.startswith(BLOCKED) and p.endswith(CODE_EXT)]
        if pre_dirty_code:
            raise RuntimeError(_pre_dirty_msg(pre_dirty_code))
        work.say("지금 테스트 상태를 먼저 확인하는 중")
        baseline_failures()   # tests that already fail on this commit won't count against the job
        work.say(f"{kind_label}: 코드를 읽고 고치는 중 ({AREAS[area][0]})")
        # The prompt goes in on stdin: --allowedTools/--disallowedTools take several values and would
        # otherwise swallow a prompt given as the last argument.
        cmd = _cmd()
        try:
            proc = subprocess.run(cmd, cwd=str(OKPY), input=_prompt(kind, area, ask.strip()), capture_output=True,
                                  text=True, timeout=60 * 30, env={**os.environ})
        except subprocess.TimeoutExpired:
            proc = None
        out = (proc.stdout if proc else "") or ""
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        (LOG_DIR / f"{work.role}.log").write_text(out + "\n---\n" + ((proc.stderr if proc else "timeout") or ""), encoding="utf-8")
        after = _dirty()
        changed = changed_by_job(before, after)
        if proc is None:
            _restore(changed, before)
            raise RuntimeError("30분 안에 끝나지 않아서 바꾼 것을 되돌렸어요")
        if not changed:
            err = (proc.stderr or "").strip()
            low = (out + err).lower()
            if "limit" in low:
                raise RuntimeError("Claude 사용 한도에 걸렸어요. 잠시 뒤에 다시 맡겨 주세요")
            if not out.strip():  # Claude never got to work: say why instead of "nothing to change"
                raise RuntimeError("Claude가 일을 시작하지 못했어요: " + (err.splitlines()[0][:160] if err else f"종료 코드 {proc.returncode}"))
            reason = [ln.strip() for ln in out.strip().splitlines() if ln.strip()][-1][:160]
            return f"{kind_label}: 고칠 곳이 없다고 했어요 — {reason}"
        bad = blocked_files(changed)
        if bad:
            _restore(changed, before)
            raise RuntimeError(f"건드리면 안 되는 파일을 바꿔서 모두 되돌렸어요 ({bad[0]})")
        work.say(f"{len(changed)}개 파일 확인하는 중 (문법 · 테스트)")
        problems = _checks(changed)
        if problems:
            _restore(changed, before)
            raise RuntimeError(f"확인에 실패해서 되돌렸어요: {problems[0]}")
    summary = out.split("바꾼 것:", 1)[-1].strip().splitlines() if "바꾼 것:" in out else []
    hint = f"{kind_label} ({AREAS[area][0]}): " + "; ".join(x.strip("-• ").strip() for x in summary[:6] if x.strip())
    (LOG_DIR / "last.json").write_text(json.dumps({"kind": kind, "area": area, "files": changed, "summary": summary},
                                                   ensure_ascii=False, indent=1), encoding="utf-8")
    msg = f"{kind_label}: {len(changed)}개 파일을 고쳤어요 (문법·테스트 확인)"
    if hand_off:
        from app.office import jobs
        who = jobs.start_task("issue", f"{kind_label} 변경 PR 만들기 시작", workers.issue_pr, hint[:280])
        if who:
            msg += f" · {office.staff_by_role(who)['name']}에게 PR을 넘겼어요"
    return msg


def in_area(path: str, area: str) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in AREAS[area][1])


def _cmd(area: str = "") -> list[str]:
    from app.work import workers
    workers._okadmin_import()
    from llm_claude import MODEL_HEAVY, claude_bin

    deny = ("Bash", "WebFetch", "WebSearch", "Agent", "NotebookEdit") + DENY_RULES + AREA_DENY.get(area, ())
    return [claude_bin(), "-p", "--model", MODEL_HEAVY, "--output-format", "text", "--permission-mode", "acceptEdits",
            "--allowedTools=Read,Edit,MultiEdit,Write,Glob,Grep", "--disallowedTools=" + ",".join(deny)]


def _agent(area: str, prompt: str) -> tuple[subprocess.CompletedProcess | None, str]:
    try:
        proc = subprocess.run(_cmd(area), cwd=str(OKPY), input=prompt, capture_output=True, text=True,
                              timeout=60 * 30, env={**os.environ})
    except subprocess.TimeoutExpired:
        return None, ""
    return proc, proc.stdout or ""


def _summary(out: str) -> list[str]:
    lines = out.split("바꾼 것:", 1)[-1].strip().splitlines() if "바꾼 것:" in out else []
    return [x.strip("-• ").strip() for x in lines if x.strip()][:6]


def _helper(work, done: threading.Event, box: dict[str, str]) -> str:
    """The second developer of an optimize run: shows on the map until the shared run is over."""
    done.wait(timeout=60 * 60)
    if box.get("error"):
        raise RuntimeError(box["error"])
    return box.get("line") or "최적화 끝"


def optimize(work, ask: str = "", hand_off: bool = True) -> str:
    """⚙️ 최적화: two people of the dev team (whoever is free) tidy up the back end and the front — code, names and comments — at the same time,
    each only on their own side. One PR for both. 공통화 is a separate job on purpose (never chained right
    after a tidy-up: both rewrite the same code)."""
    from app.office import jobs
    from app.work import workers
    tools.need("claude")
    flow = office.read_flow()
    if flow.get("pr") and not flow.get("merged"):
        raise RuntimeError(f"열린 PR #{(flow.get('pr') or {}).get('number')}이 있어요. 머지한 뒤에 맡겨 주세요")
    me = work.role
    other = next((m for m in office.ordered_members("optimize") if m != me and not jobs.running(m)), "")   # anyone free on the dev team
    sides = {"back": me, "front": other or me}
    done, box = threading.Event(), {}
    helper = sides["front"] if sides["front"] != me else (sides["back"] if sides["back"] != me else "")
    if helper and not jobs.start(helper, "최적화: 소스정리 시작", _helper, done, box, _task="refactor_front"):
        helper = ""
        sides = {"back": me, "front": me}
    try:
        with work.git_turn():
            before = _dirty()
            pre = [p for p in before if not p.startswith(BLOCKED) and p.endswith(CODE_EXT)]
            if pre:
                raise RuntimeError(_pre_dirty_msg(pre))
            work.say("지금 테스트 상태를 먼저 확인하는 중")
            baseline_failures()
            for area, who in sides.items():
                line = f"최적화: {AREAS[area][0]} 소스정리 중 (주석 포함)"
                work.say(line) if who == me else jobs._set(who, line=line, state="work")
            results: dict[str, tuple] = {}

            sid = sites.current_id()

            def run(area: str) -> None:
                with sites.use(sid):   # a new thread starts without the job's site
                    results[area] = _agent(area, _prompt("tidy", area, ask.strip()))
            if sides["back"] == sides["front"]:   # nobody free to help: one after the other
                run("back")
                run("front")
            else:
                threads = [threading.Thread(target=run, args=(a,)) for a in ("back", "front")]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            for area, (proc, out) in results.items():
                (LOG_DIR / f"optimize-{area}.log").write_text(out + "\n---\n" + ((proc.stderr if proc else "timeout") or ""), encoding="utf-8")
            changed = changed_by_job(before, _dirty())
            mine = {a: [p for p in changed if in_area(p, a)] for a in ("back", "front")}
            stray = [p for p in changed if p not in mine["back"] and p not in mine["front"]]
            if stray:   # neither side's files (the deny rules should prevent this): put back
                _restore(stray, before)
            notes: dict[str, str] = {}
            for area in ("back", "front"):
                proc, out = results.get(area, (None, ""))
                files = mine[area]
                bad = blocked_files(files)
                if proc is None:
                    _restore(files, before)
                    mine[area], notes[area] = [], "30분 안에 못 끝내서 되돌렸어요"
                elif bad:
                    _restore(files, before)
                    mine[area], notes[area] = [], f"건드리면 안 되는 파일이라 되돌렸어요 ({bad[0]})"
                elif files:
                    work.say(f"최적화: {AREAS[area][0]} {len(files)}개 파일 확인 중 (문법)")
                    problems = [x for x in _checks_compile(files)]
                    if problems:
                        _restore(files, before)
                        mine[area], notes[area] = [], f"문법 확인 실패로 되돌렸어요: {problems[0]}"
                    else:
                        notes[area] = f"{len(files)}개 파일 정리 — " + "; ".join(_summary(out)[:3])
                else:
                    notes[area] = "고칠 곳이 없다고 했어요"
            both = mine["back"] + mine["front"]
            if both:
                work.say(f"최적화: {len(both)}개 파일 테스트 확인 중")
                problems = _checks(both)
                if problems:
                    _restore(both, before)
                    raise RuntimeError(f"테스트가 깨져서 백엔드·프론트 모두 되돌렸어요: {problems[0]}")
        (LOG_DIR / "last.json").write_text(json.dumps({"kind": "optimize", "area": "all", "files": both,
                                                       "summary": [f"{AREAS[a][0]}: {n}" for a, n in notes.items()]},
                                                      ensure_ascii=False, indent=1), encoding="utf-8")
        box["line"] = f"프론트: {notes.get('front', '')}"[:200] if helper == sides.get("front") else f"백엔드: {notes.get('back', '')}"[:200]
    except Exception as exc:
        box["error"] = str(exc)[:200]
        raise
    finally:
        done.set()
    msg = f"최적화: 백엔드 {len(mine['back'])}개 · 프론트 {len(mine['front'])}개 파일 정리"
    if not hand_off:
        return msg
    if both:
        hint = "최적화(소스정리·주석): " + " / ".join(f"{AREAS[a][0]} {notes[a]}" for a in ("back", "front"))
        who = jobs.start_task("issue", "최적화 PR 만들기 시작", workers.issue_pr, hint[:280], _from=work.role)
        if who:
            msg += f" · {office.staff_by_role(who)['name']}에게 PR을 넘겼어요"
        return msg
    return msg + " · 정리할 곳이 없었어요"


def _checks_compile(paths: list[str]) -> list[str]:
    problems = []
    for p in paths:
        if p.endswith(".py") and (OKPY / p).is_file():
            try:
                py_compile.compile(str(OKPY / p), doraise=True)
            except py_compile.PyCompileError as exc:
                problems.append(f"{p}: 문법 오류 {str(exc).splitlines()[-1][:120]}")
    return problems


def area_task(area: str) -> str:
    return AREAS.get(area, AREAS["all"])[2]


def last_result() -> dict[str, Any]:
    try:
        return json.loads((LOG_DIR / "last.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}



def fix_review(work) -> str:
    """개발팀: fix what the review of an open code PR asks for (required items only), on the PR branch,
    then commit + push to the same PR and hand it back for review (workers.push_fixes)."""
    from app.work import workers
    tools.need("claude", "gh")
    flow = office.read_flow()
    required = [str(x) for x in (flow.get("required") or [])]
    review = ("\n".join(f"- {x}" for x in required)) if required else str(flow.get("review") or "").strip()
    branch = str(flow.get("branch") or "").strip()
    if not flow.get("pr") or flow.get("merged") or not review:
        raise RuntimeError("고칠 리뷰가 없어요")
    prompt = (
        f"너는 {sites.current().about} 저장소의 개발자다. 열린 PR #{(flow.get('pr') or {}).get('number')}의 리뷰에서 "
        "꼭 고치라고 한 것만 고쳐라. 그 밖의 코드는 바꾸지 마라.\n"
        f"리뷰에서 꼭 고칠 것:\n{review[:4000]}\n"
        "규칙:\n- 글(app/content), 이미지, data/, output/, 배포 설정, requirements.txt는 절대 건드리지 마라.\n"
        "- 새 라이브러리를 추가하지 마라. 지금 코드 스타일을 따라라.\n"
        "- 끝나면 마지막에 '바꾼 것:' 아래에 한 줄씩 적어라.\n"
    )
    with work.git_turn():
        # Outside merges leave flow.merged=false; sync first or autoship retries this forever.
        synced = workers.sync_merged_pr_from_github()
        if synced:
            return synced
        workers.ensure_pr_branch(branch)
        before = _dirty()
        work.say("지금 테스트 상태를 먼저 확인하는 중")
        baseline_failures()
        work.say("리뷰에서 지적한 코드를 고치는 중")
        proc, out = _agent("", prompt)
        after = _dirty()
        changed = changed_by_job(before, after)
        if proc is None:
            _restore(changed, before)
            raise RuntimeError("30분 안에 끝나지 않아서 바꾼 것을 되돌렸어요")
        if not changed:
            reason = " ".join(ln.strip() for ln in out.strip().splitlines() if ln.strip())[-400:] or "답이 없었어요"
            # already on the branch / only blocked paths: hand back for re-review instead of failing forever
            if workers._looks_already_fixed(reason) or workers._required_only_blocked(required):
                from app.office import jobs as office_jobs
                office.write_flow(
                    reviewed=False, verdict_ok=None,
                    review_round=int(flow.get("review_round") or 0) + 1,
                    fixed_at=snapshot.now_stamp(), last_fixed=[],
                )
                who = office_jobs.start_task("review", "고칠 코드 없음 · 다시 리뷰 시작", workers.review_and_merge, False, False, _from=work.role)
                name = office.staff_by_role(who)["name"] if who else "개발팀"
                return f"추가로 고칠 코드가 없어요 · {name}에게 다시 리뷰를 넘겼어요"
            raise RuntimeError(f"고친 파일이 없어요 — {reason[:160]}")
        bad = blocked_files(changed)
        if bad:
            _restore(changed, before)
            raise RuntimeError(f"건드리면 안 되는 파일을 바꿔서 모두 되돌렸어요 ({bad[0]})")
        work.say(f"{len(changed)}개 파일 확인하는 중 (문법 · 테스트)")
        problems = _checks(changed)
        if problems:
            _restore(changed, before)
            raise RuntimeError(f"확인에 실패해서 되돌렸어요: {problems[0]}")
    return f"리뷰 지적 {len(changed)}개 파일을 고쳤어요 · " + workers.push_fixes(work, changed)
