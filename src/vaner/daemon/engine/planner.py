# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import fnmatch
from pathlib import Path

from vaner.models.signal import SignalEvent
from vaner.policy.privacy import path_is_allowed


def _matches_allowed_path(path: str, pattern: str) -> bool:
    normalized = pattern.strip()
    if normalized in {".", "./"}:
        return True
    return fnmatch.fnmatch(path, normalized) or path.startswith(normalized.rstrip("/") + "/")


def plan_targets(
    repo_root: Path,
    signals: list[SignalEvent],
    allowed_paths: list[str],
    excluded_patterns: list[str],
) -> list[Path]:
    targets: list[Path] = []
    for signal in signals:
        path_value = signal.payload.get("path")
        if not path_value:
            continue
        rel = path_value
        if allowed_paths:
            if not any(_matches_allowed_path(rel, pattern) for pattern in allowed_paths):
                continue
        if not path_is_allowed(rel, excluded_patterns):
            continue
        abs_path = (repo_root / rel).resolve()
        if abs_path.exists() and abs_path.is_file():
            targets.append(abs_path)
    return sorted(set(targets))
