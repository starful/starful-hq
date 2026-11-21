"""Hand-off note for unfinished HQ work left in a site repo.

Added 2026-11-21. On 2026-10-27 an HQ content job stopped half way and left its
edits uncommitted on a feature branch. Later another tool (Cursor) picked those
edits up as "interrupted work" and shipped them, including ~70 pages whose only
change was the date. Nothing told it what the leftovers were.

When an HQ job fails, or HQ finds leftovers after a restart, this writes
HQ_HANDOFF.md at the site repo root: what was running, which files changed and
which of them only changed dates or titles. The file is listed in
.git/info/exclude so it is never committed. It is removed once the working tree
is clean again.
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from app import sites

NAME = "HQ_HANDOFF.md"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=60)


def _exclude(repo: Path) -> None:
    ex = repo / ".git" / "info" / "exclude"
    try:
        ex.parent.mkdir(parents=True, exist_ok=True)
        text = ex.read_text(encoding="utf-8") if ex.exists() else ""
        if NAME not in text.split():
            ex.write_text(text + ("" if text.endswith("\n") or not text else "\n") + NAME + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"handoff exclude: {exc}")


def _dirty(repo: Path) -> list[tuple[str, str]]:
    out = _git(repo, "status", "--porcelain").stdout.splitlines()
    rows = [(ln[:2].strip() or "?", ln[3:].strip()) for ln in out if ln.strip()]
    return [(st, p) for st, p in rows if p != NAME and "__pycache__" not in p and not p.endswith((".pyc", ".DS_Store"))]


def _classify(repo: Path, path: str) -> str:
    from app.work import seo_guard as g
    if not path.lower().endswith(g.CONTENT_EXT):
        return ""
    old = _git(repo, "show", f"HEAD:{path}").stdout
    try:
        new = (repo / path).read_text(encoding="utf-8")
    except OSError:
        return ""
    if not old:
        return "새 페이지"
    fo, fn = g._front(old), g._front(new)
    changed = {k for k in set(fo) | set(fn) if fo.get(k) != fn.get(k)}
    same_body = g._body(old) == g._body(new)
    tags = []
    if changed & set(g.DATE_KEYS) and same_body and changed <= set(g.DATE_KEYS):
        tags.append("⚠️ 날짜만 바뀜 — 되돌릴 것")
    elif changed & set(g.DATE_KEYS):
        tags.append("⚠️ 날짜 바뀜")
    if changed & set(g.TITLE_KEYS):
        tags.append("제목·설명 바뀜")
    if not same_body:
        tags.append("본문 바뀜")
    return " · ".join(tags)


def write(reason: str, task: str = "", who: str = "") -> Path | None:
    """Write the note for the current site. Returns its path, or None when nothing is left over."""
    site = sites.current()
    repo = Path(site.repo)
    rows = _dirty(repo)
    note = repo / NAME
    if not rows:
        clear()
        return None
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    lines = [
        "# ⚠️ HQ가 끝내지 못한 작업이 남아 있어요 / Unfinished HQ work",
        "",
        "이 폴더에는 Starful HQ 작업이 중간에 멈추면서 남은 **커밋되지 않은 변경**이 있어요.",
        "**이 변경을 그대로 커밋하거나 PR로 올리지 마세요.** 파일마다 확인하고, 필요한 것만 남기세요.",
        "날짜만 바뀐 페이지는 `git checkout -- <파일>`로 되돌리세요 (검색 순위가 떨어질 수 있어요).",
        "",
        "Do NOT commit or open a PR with these changes as they are. Review each file. Revert pages whose",
        "only change is the date (`git checkout -- <file>`): bumping dates without new content hurts search.",
        "",
        f"- 시각 / at: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 사이트 / site: {site.id}",
        f"- 브랜치 / branch: {branch}",
        f"- 작업 / task: {task or '알 수 없음'}" + (f" ({who})" if who else ""),
        f"- 이유 / reason: {reason}",
        "",
        "| 상태 | 파일 | 내용 |",
        "|---|---|---|",
    ]
    for st, path in rows[:300]:
        lines.append(f"| {st} | `{path}` | {_classify(repo, path)} |")
    if len(rows) > 300:
        lines.append(f"| | … 외 {len(rows) - 300}개 | |")
    lines += ["", "이 파일은 커밋되지 않아요 (.git/info/exclude). 작업 트리가 깨끗해지면 HQ가 지워요.", ""]
    try:
        note.write_text("\n".join(lines), encoding="utf-8")
        _exclude(repo)
    except OSError as exc:
        print(f"handoff write: {exc}")
        return None
    return note


def clear() -> None:
    """Remove the note once the working tree is clean."""
    repo = Path(sites.current().repo)
    note = repo / NAME
    if note.exists() and not _dirty(repo):
        try:
            note.unlink()
        except OSError as exc:
            print(f"handoff clear: {exc}")


def scan_all(reason: str = "HQ가 다시 시작됐을 때 끝나지 않은 변경이 남아 있었어요") -> None:
    """At HQ start: leave a note in every site repo that still has leftovers."""
    for s in sites.enabled():
        try:
            with sites.use(s.id):
                write(reason)
        except Exception as exc:  # noqa: BLE001
            print(f"handoff scan {s.id}: {exc}")
