# SPDX-License-Identifier: Apache-2.0
"""Assert that src/vaner/ does not contain Vaner-train moat markers."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

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
    src = Path(__file__).resolve().parent.parent / "src" / "vaner"
    offenders: list[str] = []
    for entry in list(src.rglob("*.py")) + list(src.rglob("*.json")):
        text = entry.read_text(encoding="utf-8", errors="ignore")
        if MOAT_MARKERS.search(text):
            offenders.append(str(entry))
    assert not offenders, f"Moat markers present in: {offenders}"


def test_no_vaner_train_imports() -> None:
    result = subprocess.run(
        ["python", "scripts/check_no_vaner_train_imports.py"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
