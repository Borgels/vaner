# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ast
from pathlib import Path

from vaner.intent.adapter import QualityIssue


def run_code_quality_scan(repo_root: Path, max_files: int = 400) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    count = 0
    for path in repo_root.rglob("*"):
        if count >= max_files:
            break
        if not path.is_file():
            continue
        rel_parts = path.relative_to(repo_root).parts
        if any(part in {".git", ".venv", "__pycache__", ".vaner"} for part in rel_parts):
            continue
        count += 1
        rel = str(path.relative_to(repo_root))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lowered = text.lower()
        if "todo" in lowered or "fixme" in lowered or "hack" in lowered:
            issues.append(
                QualityIssue(
                    key=f"file:{rel}",
                    severity="info",
                    message="Contains TODO/FIXME/HACK markers",
                    metadata={"path": rel},
                )
            )
        if path.suffix == ".py":
            try:
                ast.parse(text)
            except SyntaxError as exc:
                issues.append(
                    QualityIssue(
                        key=f"file:{rel}",
                        severity="high",
                        message=f"Python syntax error: {exc.msg}",
                        metadata={"path": rel, "line": str(exc.lineno or 0)},
                    )
                )
    return issues
