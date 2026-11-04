import threading
import time

from app.office import jobs
from app import office
def _quiet(monkeypatch, tmp_path):
    monkeypatch.setattr(office, "EVENTS_PATH", tmp_path / "events.jsonl")
    monkeypatch.setattr(jobs, "refresh_snapshot", lambda: None)
    jobs.JOBS.clear()


def _wait_idle(*roles, timeout=5):
    end = time.time() + timeout
    while time.time() < end and any(jobs.running(r) for r in roles):
        time.sleep(0.02)


def test_two_people_run_at_the_same_time(monkeypatch, tmp_path):
    _quiet(monkeypatch, tmp_path)
    gate = threading.Event()

    def slow(work):
        gate.wait(2)
        return "끝"

    assert jobs.start("write", "시작", slow)
    assert jobs.start("images", "시작", slow)
    assert jobs.running("write") and jobs.running("images")
    assert not jobs.start("write", "또", slow)  # same person can't take two jobs
    gate.set()
    _wait_idle("write", "images")
    assert jobs.get("write")["line"] == "끝"


def test_git_work_takes_turns(monkeypatch, tmp_path):
    _quiet(monkeypatch, tmp_path)
    gate = threading.Event()
    order = []

    def first(work):
        with work.git_turn():
            order.append("issue")
            gate.wait(2)
        return "ok"

    def second(work):
        with work.git_turn():
            order.append("merge")
        return "ok"

    jobs.start("issue", "시작", first)
    time.sleep(0.05)
    jobs.start("merge", "시작", second)
    time.sleep(0.1)
    assert jobs.get("merge")["state"] == "think"  # waiting for its turn
    gate.set()
    _wait_idle("issue", "merge")
    assert order == ["issue", "merge"]


def test_failure_shows_on_the_bubble(monkeypatch, tmp_path):
    _quiet(monkeypatch, tmp_path)

    def boom(work):
        raise RuntimeError("GitHub 로그인이 필요해요")

    jobs.start("issue", "시작", boom)
    _wait_idle("issue")
    j = jobs.get("issue")
    assert not j["running"] and j["state"] == "wait" and "로그인" in j["line"]


def test_team_splits_work_across_free_members(monkeypatch, tmp_path):
    _quiet(monkeypatch, tmp_path)
    gate = threading.Event()
    got = {}

    def write(work, chunk):
        got[work.role] = list(chunk)
        gate.wait(2)
        return "ok"

    started = jobs.start_team(("write", "write2", "write3"), list(range(15)), lambda n: f"{n}편", write, stagger=0)
    assert sorted(r for r, _ in started) == ["write", "write2", "write3"]
    assert sorted(n for _, n in started) == [5, 5, 5]
    time.sleep(0.1)
    # a busy member is skipped next time
    assert jobs.free_members(("write", "write2", "write3")) == []
    gate.set()
    _wait_idle("write", "write2", "write3")
    assert sorted(sum(got.values(), [])) == list(range(15))


def test_same_kind_of_work_is_not_handed_out_twice():
    import threading
    from app.office import jobs
    gate = threading.Event()

    def slow(work, *a):
        gate.wait(5)
        return "ok"

    jobs.JOBS.clear()
    first = jobs.start_task("refactor_front", "소스정리 시작", slow)
    assert first in office.TEAMS["dev"]
    assert jobs.start_task("refactor", "소스정리 시작", slow) is None  # nobody may start a second one
    second = jobs.start_task("refactor_back", "소스정리 시작", slow, _from=first)   # hand-off is ok
    assert second in office.TEAMS["dev"] and second != first
    gate.set()


def test_items_in_progress_are_skipped():
    import threading
    from app.office import jobs
    gate = threading.Event()
    jobs.JOBS.clear()

    def slow(work, chunk):
        gate.wait(5)
        return "ok"

    got = jobs.start_task_split("search", ["u1", "u2"], lambda n: "go", slow)
    assert sorted(n for _, n in got) == [1, 1]
    assert jobs.start_task_split("search", ["u1", "u2"], lambda n: "go", slow) == []
    assert set(jobs.busy_items("search")) == {"u1", "u2"}
    gate.set()


def test_extract_keeps_code_blocks_inside_the_post():
    from app.work import workers
    original = "---\ntitle: a\n---\n# A\n\n```python\nimport os\n```\n\ntext\n"
    reply = "고친 파일입니다:\n```markdown\n---\ntitle: b\n---\n# A\n\n```python\nimport os\n```\n\ntext more\n```\n"
    body, why = workers._extract_file(reply, original)
    assert why == "" and "```python" in body and body.count("```") == 2 and body.startswith("---")
    broken = "---\ntitle: b\n---\n# A\n\nimport os\n\ntext more and more more\n"
    assert workers._extract_file(broken, original)[1].startswith("코드 블록이 바뀜")


def test_review_splits_blocking_from_suggestions():
    from app.work import workers
    req, sug = workers.split_review("판정: 머지 좋아요\n요약: 글 3편\n필수: 없음\n제안:\n- 제목이 길어요\n- 빈 줄 정리")
    assert req == [] and sug == ["제목이 길어요", "빈 줄 정리"]
    req, sug = workers.split_review("판정: 고칠 게 있어요\n필수:\n- 코드 블록이 깨짐\n제안: 없음")
    assert req == ["코드 블록이 깨짐"] and sug == []
    req, sug = workers.split_review("판정: 머지 좋아요\n필수: 없음 (이전 리뷰의 a.md 지적은 고쳐졌어요)\n제안:\n- b.md 문장 중복")
    assert req == [] and sug == ["b.md 문장 중복"]   # '없음 (…)' is still none: it once looped 자동 진행 forever
    # '필수: 없음' then a resolved-note bullet must not block (okonsen PR #48)
    req, sug = workers.split_review(
        "판정: 머지 좋아요\n필수: 없음\n"
        "- (이전 필수였던 app/content/guides/x_ko.md의 구체적 사실은 빠져 해결됨을 확인했습니다.)\n"
        "제안:\n- 숫자 통일"
    )
    assert req == [] and sug == ["숫자 통일"]
    old = workers.split_review("판정: 고칠 게 있어요\n- a\n- b")   # old free-form review: everything blocks
    assert old == (["a", "b"], [])


def test_required_code_routes_over_posts():
    from app.work import workers
    assert workers._required_points_at_code([
        "`app/routes/assets.py` — PNG 투명도",
        "`app/routes/internal.py` — 토큰 비교",
    ])
    assert not workers._required_points_at_code(["app/content/guides/x_ko.md — 사실 오류"])
    assert workers._clean_path("app/content/guides/x_ko.md의") == "app/content/guides/x_ko.md"
    assert workers._paths_in("이전 app/content/guides/x_ko.md의 YAML") == ["app/content/guides/x_ko.md"]
    assert workers._required_only_blocked([
        "`output/assets/logos/042700.png`와 `output/assets/logos/128940.png` — 동일 해시"
    ])
    assert not workers._required_only_blocked(["`app/analytics.py` — HMAC"])
    assert workers._looks_already_fixed("이미 c3237a1 커밋에 반영되어 있어 추가 수정이 필요하지 않았음")


def test_failed_review_hands_fixes_on_until_the_round_limit(monkeypatch, tmp_path):
    from app.work import workers
    monkeypatch.setattr(office, "FLOW_PATH", tmp_path / "flow.json")
    started = []
    monkeypatch.setattr(jobs, "start_task", lambda task, line, fn, *a, _from="": started.append(task) or "seoyeon")
    monkeypatch.setattr(workers, "_pr_has_posts", lambda flow: True)

    class W:
        role = "daeun"

    def bad_review(work):
        office.write_flow(verdict_ok=False)
        return "꼭 고칠 게 1개 있어요"
    monkeypatch.setattr(workers, "review_pr", bad_review)
    office.write_flow(pr={"number": 9}, review_round=0)
    assert "고치기를 넘겼어요" in workers.review_and_merge(W())
    assert started == ["fix"]
    office.write_flow(review_round=workers.MAX_FIX_ROUNDS)
    assert "멈췄어요" in workers.review_and_merge(W())
    assert started == ["fix"]


def test_code_required_on_mixed_pr_goes_to_dev(monkeypatch, tmp_path):
    """okcaddie: PR has posts + code, but 필수 names .py → fix_code, not marketing fix."""
    from app.work import workers
    monkeypatch.setattr(office, "FLOW_PATH", tmp_path / "flow.json")
    started = []
    monkeypatch.setattr(jobs, "start_task", lambda task, line, fn, *a, _from="": started.append(task) or "doyoon")
    monkeypatch.setattr(workers, "_pr_has_posts", lambda flow: True)

    class W:
        role = "daeun"

    def bad_review(work):
        office.write_flow(
            verdict_ok=False,
            required=["`app/routes/assets.py` — PNG", "`app/routes/internal.py` — token"],
        )
        return "꼭 고칠 게 2개 있어요"
    monkeypatch.setattr(workers, "review_pr", bad_review)
    office.write_flow(pr={"number": 50}, review_round=0)
    assert "코드 고치기" in workers.review_and_merge(W())
    assert started == ["fix_code"]


def test_fix_finds_files_from_a_shortened_review_path():
    from app.work import workers
    posts = ["app/content/posts/python/pythonで光速web-api開発-starlette入門.md", "app/content/posts/cloud/aws-waf.md"]
    item = "`app/content/posts/python/python...starlette...md`의 frontmatter `summary` 필드가 내부 메모"
    got = workers.match_review_files([item], posts)
    assert [g["path"] for g in got] == [posts[0]]
    assert workers.match_review_files(["app/content/posts/cloud/aws-waf.md 링크 깨짐"], posts)[0]["path"] == posts[1]


def test_seo_note_in_summary_is_put_back(tmp_path, monkeypatch):
    import subprocess
    from app.work import workers
    repo = tmp_path / "okpy"
    post = repo / "app" / "content" / "posts" / "a.md"
    post.parent.mkdir(parents=True)
    post.write_text("---\ntitle: A\nsummary: 本当の要約です。\n---\nbody\n")
    for cmd in (["init", "-q"], ["add", "."], ["-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "i"]):
        subprocess.run(["git", *cmd], cwd=repo, check=True)
    post.write_text("---\ntitle: A2\nsummary: タイトルを問題解決型に強化。位置7.6での低CTR改善を狙う。\n---\nbody\n")
    monkeypatch.setattr(workers, "OKPY", repo)
    n = workers._undo_memo_summaries([{"status": "applied", "files": ["okpy/app/content/posts/a.md"]}])
    assert n == 1 and post.read_text() == "---\ntitle: A2\nsummary: 本当の要約です。\n---\nbody\n"


def test_no_waiting_bubble_while_someone_works_on_it():
    jobs.JOBS.clear()
    flow = {"pr": {"number": 9}, "merged": False, "reviewed": True, "verdict_ok": False}
    built = office.build_office({}, {"minjae": {"running": True, "task": "fix", "line": "리뷰 지적 고치는 중", "state": "work"}}, flow)
    assert all(not (z["person"] or {}).get("task") == "review" for z in built["zones"])   # Minjae is fixing: no "대기 중"
    built = office.build_office({}, {}, flow)
    nxt = office.ordered_members("review")[0]
    assert next(z for z in built["zones"] if z["id"] == nxt)["person"]["state"] == "idle"


def test_fix_finds_the_file_by_a_quoted_phrase_or_article_text(tmp_path, monkeypatch):
    from app.work import workers
    root = tmp_path / "okpy"
    a = root / "app/content/posts/python/pythonで光速web-api開発.md"
    b = root / "app/content/posts/python/numba.md"
    a.parent.mkdir(parents=True)
    a.write_text("---\nsummary: タイトルを問題解決型・乗り換え動機ベースに強化。\n---\nStarlette を使う\n")
    b.write_text("---\nsummary: numba\n---\n")
    monkeypatch.setattr(workers, "OKPY", root)
    posts = ["app/content/posts/python/pythonで光速web-api開発.md", "app/content/posts/python/numba.md"]
    item = '`app/content/posts/python/python...starlette...md`의 summary가 내부 메모("タイトルを問題解決型・乗り換え動機ベースに強化。...")'
    assert [x["path"] for x in workers.match_review_files([item], posts)] == [posts[0]]
