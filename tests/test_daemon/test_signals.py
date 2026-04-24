# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import subprocess

from vaner.daemon.signals.fs_watcher import scan_repo_files
from vaner.daemon.signals.git_reader import (
    read_commit_subjects,
    read_content_hashes,
    read_git_state,
    read_head_sha,
)


def test_scan_repo_files_skips_internal_dirs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "ok.py").write_text("x=1\n", encoding="utf-8")
    (repo / ".vaner").mkdir()
    (repo / ".vaner" / "hidden.py").write_text("y=1\n", encoding="utf-8")
    (repo / ".git").mkdir()
    (repo / ".git" / "ignored").write_text("z=1\n", encoding="utf-8")

    files = scan_repo_files(repo)
    rel_paths = {str(path.relative_to(repo)) for path in files}

    assert "ok.py" in rel_paths
    assert ".vaner/hidden.py" not in rel_paths


def test_read_git_state_handles_missing_git(monkeypatch, tmp_path):
    def _raise(*args, **kwargs):  # noqa: ANN002, ANN003
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", _raise)
    state = read_git_state(tmp_path)

    assert state == {"branch": "", "recent_diff": "", "staged": ""}


def test_read_content_hashes_uses_sha256_fallback_when_git_unavailable(monkeypatch, tmp_path):
    """WS6: with git absent we still produce a stable per-file hash so the
    invalidation sweep works on non-git workspaces."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("content-a", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("content-b", encoding="utf-8")

    def _raise(*args, **kwargs):  # noqa: ANN002, ANN003
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", _raise)
    hashes = read_content_hashes(tmp_path, ["src/a.py", "src/b.py", "missing.py"])
    assert set(hashes.keys()) == {"src/a.py", "src/b.py"}
    assert hashes["src/a.py"] != hashes["src/b.py"]
    # Same content → same hash (stability).
    (tmp_path / "src" / "c.py").write_text("content-a", encoding="utf-8")
    again = read_content_hashes(tmp_path, ["src/c.py"])
    assert again["src/c.py"] == hashes["src/a.py"]


def test_read_head_sha_empty_outside_git_repo(tmp_path):
    sha = read_head_sha(tmp_path)
    assert sha == ""


def test_read_commit_subjects_empty_outside_git_repo(tmp_path):
    subjects = read_commit_subjects(tmp_path, last_n=5)
    assert subjects == []
