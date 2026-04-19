# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from vaner import api


def forget_state(repo_root: Path) -> int:
    return api.forget(repo_root)
