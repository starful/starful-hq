from app.work import devwork
from app.work import workers
def test_only_code_files_may_change():
    assert devwork.blocked_files(["app/routes.py", "app/templates/base.html"]) == []
    assert devwork.blocked_files(["app/content/posts/python/x.md"]) == ["app/content/posts/python/x.md"]
    assert devwork.blocked_files(["requirements.txt", "app/static/images/posts/a.jpg"]) == ["requirements.txt", "app/static/images/posts/a.jpg"]


def test_changed_by_job_ignores_files_dirty_before():
    before = {"a.py": "1", "b.py": "2"}
    after = {"a.py": "1", "b.py": "3", "c.py": "4"}
    assert devwork.changed_by_job(before, after) == ["b.py", "c.py"]


def test_image_model_pick_skips_retired_models():
    rows = [["imagen-4.0-generate-001", ["predict"]], ["gemini-2.5-flash-image", ["generateContent"]],
            ["gemini-2.5-flash", ["generateContent"]]]
    got = workers.pick_image_models(rows, "imagen-4.0-fast-generate-001")
    assert got == {"imagen": "imagen-4.0-generate-001", "gemini_image": "gemini-2.5-flash-image"}
    assert workers.pick_image_models([["gemini-2.5-flash-image", ["generateContent"]]])["imagen"] == ""


def test_other_teams_article_edits_are_not_the_jobs_and_are_never_reverted():
    before = {"app/content/posts/a.md": "1"}
    after = {"app/content/posts/a.md": "2", "app/content/posts/b.md": "9", "app/routes.py": "x", "data/x.json": "y"}
    assert devwork.changed_by_job(before, after) == ["app/routes.py"]


def test_agent_is_refused_articles_before_it_writes():
    assert "Edit(./app/content/**)" in devwork.DENY_RULES and "Edit(./app/static/images/**)" in devwork.DENY_RULES
    assert "Edit(./requirements.txt)" in devwork.DENY_RULES


def test_optimize_tidies_back_and_front_together_then_hands_off(tmp_path, monkeypatch):
    import subprocess
    import threading
    import time
    from app.office import jobs
    from app import office
    repo = tmp_path / "okpy"
    (repo / "app" / "templates").mkdir(parents=True)
    (repo / "app" / "content").mkdir()
    (repo / "app" / "x.py").write_text("import os\nprint(1)\n")
    (repo / "app" / "templates" / "a.html").write_text("<p>a</p>\n")
    (repo / "app" / "content" / "z.md").write_text("post\n")
    for cmd in (["init", "-q"], ["add", "."], ["-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init"]):
        subprocess.run(["git", *cmd], cwd=repo, check=True)
    monkeypatch.setattr(devwork, "OKPY", repo)
    monkeypatch.setattr(devwork, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(devwork.tools, "need", lambda *a: None)
    monkeypatch.setattr(office, "FLOW_PATH", tmp_path / "flow.json")
    seen_areas, started = [], []

    class P:
        returncode, stderr = 0, ""

    def fake_agent(area, prompt):
        seen_areas.append((area, time.time()))
        time.sleep(0.2)
        if area == "back":
            (repo / "app" / "x.py").write_text("print(1)\n")
        else:
            (repo / "app" / "templates" / "a.html").write_text("<p>a</p>")
            (repo / "app" / "content" / "z.md").write_text("marketing edited this meanwhile\n")
        return P(), "바꾼 것:\n- 정리"
    monkeypatch.setattr(devwork, "_agent", fake_agent)
    real_start_task = jobs.start_task
    monkeypatch.setattr(jobs, "start_task", lambda task, *a, **k: started.append(task) or "yuna")
    jobs.JOBS.clear()
    out = devwork.optimize(jobs.Work("liam"), "", True)
    assert {a for a, _ in seen_areas} == {"back", "front"}
    assert abs(seen_areas[0][1] - seen_areas[1][1]) < 0.15            # at the same time
    assert "백엔드 1개 · 프론트 1개" in out and started == ["issue"]
    assert not office.read_flow().get("then_common")   # 공통화 is never chained after a tidy-up
    assert (repo / "app" / "content" / "z.md").read_text().startswith("marketing")   # other team's edit kept
    helper = next(r for r, j in jobs.JOBS.items() if r != "liam")
    for _ in range(20):
        if not jobs.running(helper):
            break
        time.sleep(0.05)
    assert jobs.get(helper)["line"].startswith("프론트")
    jobs.start_task = real_start_task


def test_each_side_may_only_edit_its_own_files():
    assert devwork.in_area("app/routes/blog.py", "back") and not devwork.in_area("app/templates/base.html", "back")
    assert devwork.in_area("app/templates/base.html", "front") and devwork.in_area("app/static/css/a.css", "front")
    assert "Edit(./app/templates/**)" in devwork.AREA_DENY["back"] and "Edit(./app/**/*.py)" in devwork.AREA_DENY["front"]


def test_tests_that_already_failed_do_not_block_a_job(tmp_path, monkeypatch):
    import subprocess
    repo = tmp_path / "site"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_a.py").write_text(
        "import unittest\n"
        "def test_old_broken():\n    assert False\n"
        "def test_ok():\n    assert True\n"
        "class T(unittest.TestCase):\n"
        "    def test_sub(self):\n"
        "        for x in (1, 2):\n"
        "            with self.subTest(x=x):\n                self.assertEqual(x, 1)\n")
    for cmd in (["init", "-q"], ["add", "."], ["-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init"]):
        subprocess.run(["git", *cmd], cwd=repo, check=True)
    monkeypatch.setattr(devwork, "OKPY", repo)
    monkeypatch.setattr(devwork, "_BASELINE", {})
    base = devwork.baseline_failures()
    assert "tests/test_a.py::test_old_broken" in base and not any("test_ok" in b for b in base)
    assert devwork._checks([]) == []                       # same failures as before: fine
    (repo / "tests" / "test_a.py").write_text((repo / "tests" / "test_a.py").read_text().replace("assert True", "assert 0"))
    problems = devwork._checks([])
    assert problems and "test_ok" in problems[0] and "새로 깨짐 1개" in problems[0]


def test_code_pr_review_is_fixed_by_the_dev_team(tmp_path, monkeypatch):
    import subprocess

    from app import office
    from app.work import workers
    repo = tmp_path / "site"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "x.py").write_text("print(1)\n")
    for cmd in (["init", "-q", "-b", "feat/1"], ["add", "."], ["-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init"]):
        subprocess.run(["git", *cmd], cwd=repo, check=True)
    monkeypatch.setattr(devwork, "OKPY", repo)
    monkeypatch.setattr(devwork, "_BASELINE", {})
    monkeypatch.setattr(devwork.tools, "need", lambda *a: None)
    monkeypatch.setattr(office, "FLOW_PATH", tmp_path / "flow.json")
    office.write_flow(pr={"number": 1}, branch="feat/1", reviewed=True, verdict_ok=False, required=["x.py: print 2"])
    seen = {}
    switched = []

    class P:
        returncode, stderr = 0, ""

    def fake_agent(area, prompt):
        seen["prompt"] = prompt
        (repo / "app" / "x.py").write_text("print(2)\n")
        return P(), "바꾼 것:\n- x.py"
    monkeypatch.setattr(devwork, "_agent", fake_agent)
    monkeypatch.setattr(workers, "push_fixes", lambda work, fixed: f"pushed {fixed}")
    monkeypatch.setattr(workers, "sync_merged_pr_from_github", lambda: None)
    monkeypatch.setattr(workers, "ensure_pr_branch", lambda b: switched.append(b))

    class W:
        role = "hajun"

        def say(self, m):
            pass

        def git_turn(self):
            import contextlib
            return contextlib.nullcontext()
    out = devwork.fix_review(W())
    assert switched == ["feat/1"]
    assert "x.py: print 2" in seen["prompt"] and out.endswith("pushed ['app/x.py']")


def test_fix_review_stops_when_pr_already_merged_on_github(tmp_path, monkeypatch):
    """Outside merge left flow.merged=false — sync instead of 'wrong branch' forever."""
    from app import office

    monkeypatch.setattr(devwork.tools, "need", lambda *a: None)
    monkeypatch.setattr(office, "FLOW_PATH", tmp_path / "flow.json")
    office.write_flow(pr={"number": 19}, branch="feat/18-x", reviewed=True, verdict_ok=False,
                      required=["script/x.py: add pages"], merged=False)
    monkeypatch.setattr(workers, "sync_merged_pr_from_github",
                        lambda: "PR #19은 이미 머지됐어요 · 배포 차례로 넘길게요")
    monkeypatch.setattr(workers, "ensure_pr_branch", lambda b: (_ for _ in ()).throw(AssertionError("no checkout")))

    class W:
        role = "ren"

        def say(self, m):
            pass

        def git_turn(self):
            import contextlib
            return contextlib.nullcontext()

    out = devwork.fix_review(W())
    assert "이미 머지" in out


def test_ensure_pr_branch_checks_out(tmp_path, monkeypatch):
    import subprocess

    repo = tmp_path / "site"
    repo.mkdir()
    for cmd in (["init", "-q", "-b", "main"],
                ["commit", "--allow-empty", "-qm", "init"],
                ["checkout", "-b", "feat/1"]):
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a", *cmd], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True)
    monkeypatch.setattr(workers, "OKPY", repo)
    workers.ensure_pr_branch("feat/1")
    cur = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo,
                         capture_output=True, text=True, check=True).stdout.strip()
    assert cur == "feat/1"


def test_ensure_on_main_when_no_open_pr(tmp_path, monkeypatch):
    import subprocess

    from app import office

    repo = tmp_path / "site"
    repo.mkdir()
    for cmd in (["init", "-q", "-b", "main"],
                ["commit", "--allow-empty", "-qm", "init"],
                ["checkout", "-b", "feat/stale"]):
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a", *cmd], cwd=repo, check=True)
    monkeypatch.setattr(workers, "OKPY", repo)
    monkeypatch.setattr(office, "FLOW_PATH", tmp_path / "flow.json")
    office.write_flow(pr=None, merged=True, deployed=True, branch="feat/stale")
    assert workers.ensure_on_main_if_idle() is True
    cur = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo,
                         capture_output=True, text=True, check=True).stdout.strip()
    assert cur == "main"


def test_sync_clears_stale_merged_false_without_pr(tmp_path, monkeypatch):
    from app import office

    monkeypatch.setattr(office, "FLOW_PATH", tmp_path / "flow.json")
    monkeypatch.setattr(workers, "ensure_on_main_if_idle", lambda: False)
    office.write_flow(pr=None, merged=False, deployed=True, last_pr={"number": 54}, branch="feat/x")
    assert workers.sync_merged_pr_from_github() is None
    assert office.read_flow().get("merged") is True
