# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

from vaner import api


def run_query(repo_root: Path, prompt: str) -> str:
    return api.query(prompt, repo_root).injected_context
