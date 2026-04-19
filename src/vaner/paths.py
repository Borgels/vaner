# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path


def resolve_repo_path(repo_root: Path, path: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    resolved = candidate.resolve()
    if resolved != repo_root and repo_root not in resolved.parents:
        raise ValueError(f"Path escapes repository root: {path}")
    return resolved
