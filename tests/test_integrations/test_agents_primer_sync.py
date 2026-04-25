# SPDX-License-Identifier: Apache-2.0

"""Enforce that AGENTS.md primer block matches the canonical guidance asset.

If this test fails, run `python scripts/sync_agents_primer.py` locally.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync_agents_primer.py"
AGENTS_MD = REPO_ROOT / "AGENTS.md"


def test_sync_script_exists() -> None:
    assert SYNC_SCRIPT.exists()


def test_agents_md_in_sync_with_canonical_guidance() -> None:
    result = subprocess.run(
        [sys.executable, str(SYNC_SCRIPT), "--check"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"AGENTS.md primer drifted from canonical guidance.\n"
        f"stderr: {result.stderr}\n"
        f"Fix: run `python scripts/sync_agents_primer.py` and commit."
    )


def test_agents_md_has_primer_markers() -> None:
    text = AGENTS_MD.read_text(encoding="utf-8")
    assert "<!-- vaner-primer:start" in text
    assert "<!-- vaner-primer:end -->" in text
