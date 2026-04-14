# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import subprocess
from pathlib import Path


def _run_git(repo_root: Path, args: list[str]) -> str:
    try:
        process = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return ""
    if process.returncode != 0:
        return ""
    return process.stdout.strip()


def read_git_state(repo_root: Path) -> dict[str, str]:
    commit_count_output = _run_git(repo_root, ["rev-list", "--count", "HEAD"])
    has_history = False
    if commit_count_output.isdigit():
        has_history = int(commit_count_output) > 1
    return {
        "branch": _run_git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"]),
        "recent_diff": _run_git(repo_root, ["diff", "--name-only", "HEAD~1", "HEAD"]) if has_history else "",
        "staged": _run_git(repo_root, ["diff", "--cached", "--name-only"]),
    }


def read_git_diff(repo_root: Path, relative_path: str) -> str:
    commit_count_output = _run_git(repo_root, ["rev-list", "--count", "HEAD"])
    if not commit_count_output.isdigit() or int(commit_count_output) <= 1:
        return ""
    return _run_git(repo_root, ["diff", "--unified=0", "HEAD~1", "HEAD", "--", relative_path])
