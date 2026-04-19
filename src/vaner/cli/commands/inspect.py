# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from vaner import api


def inspect_cache(repo_root: Path) -> str:
    return api.inspect(repo_root)


def inspect_last(repo_root: Path) -> str:
    return api.inspect_last(repo_root)
