# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from vaner.daemon import preflight


def test_check_repo_root_refuses_home_without_force() -> None:
    result = preflight.check_repo_root(Path.home(), force=False)
    assert result["ok"] is False
    assert result["reason"] == "unsafe_root"


def test_check_repo_root_allows_home_with_force() -> None:
    result = preflight.check_repo_root(Path.home(), force=True)
    assert result["ok"] is True


def test_check_repo_root_rejects_large_non_git(monkeypatch, temp_repo) -> None:
    monkeypatch.setattr(preflight, "_is_git_repo", lambda _path: False)
    monkeypatch.setattr(preflight, "_count_files_limited", lambda _path, limit=100_000: 100_000)
    result = preflight.check_repo_root(temp_repo, force=False)
    assert result["ok"] is False
    assert result["reason"] == "non_git_large_root"


def test_check_inotify_budget_warns_when_headroom_low(monkeypatch, temp_repo) -> None:
    monkeypatch.setattr(preflight, "_read_int_file", lambda _path: 1000)
    monkeypatch.setattr(preflight, "_estimate_dir_count", lambda _path, limit=200_000: 800)
    result = preflight.check_inotify_budget(temp_repo)
    assert result["ok"] is False
    assert result["level"] == "warn"
    assert "headroom" in str(result["detail"])
