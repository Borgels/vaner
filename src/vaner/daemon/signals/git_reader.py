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


def read_head_sha(repo_root: Path) -> str:
    """Return the current HEAD SHA (empty string if not a git repo)."""
    return _run_git(repo_root, ["rev-parse", "HEAD"])


def read_commit_subjects(repo_root: Path, last_n: int = 20) -> list[str]:
    """Return the subject lines of the last ``last_n`` commits on HEAD.

    Used by WS7 (Workspace Goals) to cluster recurring themes in the user's
    recent work. Empty list when the repo has no history or git isn't
    available.
    """
    count = max(1, int(last_n))
    output = _run_git(repo_root, ["log", f"-n{count}", "--pretty=%s"])
    if not output:
        return []
    return [line.strip() for line in output.splitlines() if line.strip()]


def read_content_hashes(repo_root: Path, paths: list[str]) -> dict[str, str]:
    """Return a ``{path: content_sha}`` mapping for the given paths.

    Used by WS6's invalidation sweep: the registry compares these against
    the hashes captured at briefing-synthesis time to decide whether a
    ``ready`` prediction is still valid after file edits.

    Content source, in order of preference:
      1. ``git hash-object`` for the working-tree file — picks up uncommitted
         edits so a prediction whose files changed on disk invalidates
         immediately, without waiting for a commit.
      2. A SHA-256 of the file bytes if git hash-object fails.
      3. Path missing from the result if the file doesn't exist.

    The hash algorithm doesn't need to match git's — it just needs to be
    stable per content. We use whatever git gives us when available so the
    hashes line up with native git identities for easier debugging.
    """
    import hashlib

    result: dict[str, str] = {}
    for path in paths:
        if not path:
            continue
        abs_path = repo_root / path
        try:
            if not abs_path.exists() or not abs_path.is_file():
                continue
        except OSError:
            continue
        sha = _run_git(repo_root, ["hash-object", "--", str(path)])
        if sha and len(sha) >= 16:
            result[path] = sha
            continue
        # Fallback: SHA-256 of file bytes. Used when git isn't available or
        # the repo lives outside a git checkout (rare but possible for
        # corpus-style workspaces).
        try:
            data = abs_path.read_bytes()
        except OSError:
            continue
        result[path] = hashlib.sha256(data).hexdigest()[:40]
    return result
