"""Git rule: every tracked file name is English (ASCII letters, digits, - _ . /), no spaces."""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OK = re.compile(r"^[A-Za-z0-9._/-]+$")


def test_tracked_file_names_are_english():
    try:
        out = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    bad = [p for p in out.splitlines() if not OK.match(p)]
    assert not bad, "파일 이름은 영어로만 (git 운영 규칙): " + ", ".join(bad)
