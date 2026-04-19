# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from vaner.paths import resolve_repo_path


def test_resolve_repo_path_rejects_escape(temp_repo):
    with pytest.raises(ValueError):
        resolve_repo_path(temp_repo, "../outside.txt")


def test_resolve_repo_path_accepts_relative_and_absolute(temp_repo):
    relative = resolve_repo_path(temp_repo, "sample.py")
    absolute = resolve_repo_path(temp_repo, str(temp_repo / "sample.py"))
    assert relative == temp_repo / "sample.py"
    assert absolute == temp_repo / "sample.py"
