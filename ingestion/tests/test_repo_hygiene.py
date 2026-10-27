"""Repository checks that protect the deploy: scripts the VM runs directly must be executable in
git. On Windows, git doesn't track the executable flag by default, so it is easy to lose: that
happened once, and the deploy's sudo rule (which runs deploy.sh directly) would have failed."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def git_modes(pattern: str) -> dict[str, str]:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run(
        ["git", "ls-files", "-s", "--", pattern], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return {line.split("\t", 1)[1]: line.split()[0] for line in out.stdout.splitlines()}


def test_deploy_scripts_are_executable_in_git():
    modes = git_modes("deploy/*.sh")
    assert {"deploy/deploy.sh", "deploy/smoke_check.sh"} <= set(modes)
    not_executable = sorted(path for path, mode in modes.items() if mode != "100755")
    assert not not_executable, f"run: git update-index --chmod=+x {' '.join(not_executable)}"


def test_shell_scripts_use_unix_line_endings():
    for path in [*ROOT.glob("deploy/*.sh"), ROOT / "airflow" / "include" / "entrypoint.sh"]:
        committed = subprocess.run(
            ["git", "show", f"HEAD:{path.relative_to(ROOT).as_posix()}"], cwd=ROOT, capture_output=True
        )
        if committed.returncode != 0:
            continue  # not committed yet
        assert b"\r" not in committed.stdout, f"{path.name} has Windows line endings in git"
