# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import subprocess

from vaner.daemon.signals.fs_watcher import scan_repo_files
from vaner.daemon.signals.git_reader import read_git_state


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
