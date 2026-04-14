# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path


def score_paths(paths: list[Path], prioritized_paths: set[str] | None = None) -> list[tuple[Path, float]]:
    prioritized_paths = prioritized_paths or set()
    scored: list[tuple[Path, float]] = []
    for path in paths:
        score = 1.0
        if "test" not in path.name.lower():
            score += 0.5
        if str(path) in prioritized_paths:
            score += 1.0
        score += max(0.0, 2.0 - (len(path.parts) * 0.1))
        scored.append((path, score))
    return sorted(scored, key=lambda item: item[1], reverse=True)
