#!/usr/bin/env python3
# SPDX-License-Identifier: MIT

from __future__ import annotations

import argparse
import pathlib
import subprocess
from dataclasses import dataclass

FORBIDDEN_PREFIXES = (
    ".vaner/",
    ".vaner_data/",
    "eval/",
    "infra/",
    "scripts/",
    "__pycache__/",
)
FORBIDDEN_SEGMENTS = ("/__pycache__/",)
FORBIDDEN_SUFFIXES = (
    ".docx",
    ".pptx",
    ".xlsx",
)
MAX_TRACKED_FILE_BYTES = 1_000_000


@dataclass(frozen=True)
class Violation:
    path: str
    reason: str


def _tracked_files(repo_root: pathlib.Path) -> list[str]:
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=repo_root, text=False)
    return [entry.decode("utf-8") for entry in raw.split(b"\0") if entry]


def collect_violations(repo_root: pathlib.Path) -> tuple[list[str], list[Violation]]:
    tracked = _tracked_files(repo_root)
    violations: list[Violation] = []

    for rel in tracked:
        if rel.startswith(FORBIDDEN_PREFIXES):
            violations.append(Violation(path=rel, reason="tracked from generated/data directory"))
            continue
        if any(segment in rel for segment in FORBIDDEN_SEGMENTS):
            violations.append(Violation(path=rel, reason="contains generated cache segment"))
            continue

        abs_path = repo_root / rel
        if abs_path.suffix.lower() in FORBIDDEN_SUFFIXES:
            violations.append(Violation(path=rel, reason="tracked binary/office document"))
            continue
        try:
            size = abs_path.stat().st_size
        except OSError:
            continue
        if size > MAX_TRACKED_FILE_BYTES:
            violations.append(
                Violation(
                    path=rel,
                    reason=f"{size} bytes exceeds {MAX_TRACKED_FILE_BYTES} byte policy",
                )
            )

    return tracked, violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Check repository hygiene for release readiness.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when violations are found.")
    args = parser.parse_args()

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    tracked, violations = collect_violations(repo_root)

    if violations:
        print("Repository hygiene check failed.")
        for violation in violations:
            print(f" - {violation.path}: {violation.reason}")
        return 1 if args.strict else 0

    print(
        "Repository hygiene check passed "
        f"({len(tracked)} tracked files; max tracked file size <= {MAX_TRACKED_FILE_BYTES} bytes)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
