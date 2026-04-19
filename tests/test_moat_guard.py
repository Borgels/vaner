# SPDX-License-Identifier: Apache-2.0
"""Assert that src/vaner/ does not contain Vaner-train moat markers."""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_DIRS = ("infra", "scripts/data", "scripts/remote")
FORBIDDEN_FILENAMES = (
    "eval-at-scale",
    "bundle-workflow",
    "vaner_report",
    "production-roadmap",
    "release-audit",
)

MOAT_MARKERS = re.compile(
    r"lmsys_[a-z0-9_-]+_patterns\.json|"
    r"allenai_[A-Za-z0-9_-]+_patterns\.json|"
    r"conversation_pattern_adapter|"
    r"run_public_training|"
    r"train_arc_model|"
    r"simulate_repo_sessions|"
    r"validate_bundle|"
    r"promote_bundle|"
    r"Vaner-train",
    re.IGNORECASE,
)


def test_no_moat_markers_under_src_vaner() -> None:
    src = REPO_ROOT / "src" / "vaner"
    offenders: list[str] = []
    for entry in list(src.rglob("*.py")) + list(src.rglob("*.json")):
        text = entry.read_text(encoding="utf-8", errors="ignore")
        if MOAT_MARKERS.search(text):
            offenders.append(str(entry))
    assert not offenders, f"Moat markers present in: {offenders}"


def test_no_vaner_train_imports() -> None:
    offenders: list[str] = []
    src = REPO_ROOT / "src" / "vaner"
    for entry in src.rglob("*.py"):
        tree = ast.parse(entry.read_text(encoding="utf-8"), filename=str(entry))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "vaner_train" in alias.name:
                        offenders.append(f"{entry}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if "vaner_train" in module:
                    offenders.append(f"{entry}:{node.lineno} from {module}")
    assert not offenders, f"Vaner-train imports detected: {offenders}"


def test_no_forbidden_root_directories() -> None:
    offenders = [path for path in FORBIDDEN_DIRS if (REPO_ROOT / path).exists()]
    assert not offenders, f"Forbidden directories found: {offenders}"


def test_no_moat_sensitive_filenames() -> None:
    offenders: list[str] = []
    for entry in REPO_ROOT.rglob("*"):
        if not entry.is_file():
            continue
        lowered = entry.name.lower()
        if any(marker in lowered for marker in FORBIDDEN_FILENAMES):
            offenders.append(str(entry.relative_to(REPO_ROOT)))
            continue
        if entry.suffix == ".parquet":
            size_bytes = entry.stat().st_size
            if size_bytes < 1_000_000:
                offenders.append(str(entry.relative_to(REPO_ROOT)))
    assert not offenders, f"Moat-sensitive filenames present: {offenders}"
