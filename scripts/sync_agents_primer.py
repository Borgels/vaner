#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Sync the AGENTS.md primer block to the canonical guidance asset.

AGENTS.md contains an inline primer between machine-parseable markers:

    <!-- vaner-primer:start v=1 -->
    ...body...
    <!-- vaner-primer:end -->

The `v=` marker tracks the **canonical guidance asset's `guidance_version`
field** (the integer at the top of `vaner_guidance_v1.md`'s frontmatter),
NOT the Vaner release tag. The asset only bumps when the guidance content
materially changes — most Vaner releases leave it untouched. So a
machine reading `v=1` in a 0.8.5 install is the *expected* state, not a
sign of stale primer. The 0.8.5 plan briefly conflated the two; see the
"WS13 plan correction" note in we-will-now-move-cozy-harbor.md.

This script rewrites the marker + body to match
`src/vaner/integrations/guidance/vaner_guidance_v1.md`. Run in CI with
`--check` to enforce lockstep.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_MD = REPO_ROOT / "AGENTS.md"
PRIMER_START_PREFIX = "<!-- vaner-primer:start"
PRIMER_END = "<!-- vaner-primer:end -->"


def _load_canonical_body() -> tuple[str, int]:
    # Import the loader directly by file path to avoid triggering the top-level
    # `vaner` package __init__ (which pulls pydantic + the whole engine). This
    # script must run in a plain-stdlib CI step.
    import importlib.util

    loader_path = REPO_ROOT / "src" / "vaner" / "integrations" / "guidance" / "loader.py"
    mod_name = "_vaner_guidance_loader"
    spec = importlib.util.spec_from_file_location(mod_name, loader_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Python 3.14's dataclass machinery needs the module in sys.modules before exec.
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    doc = mod.load_guidance("canonical")  # type: ignore[attr-defined]
    return doc.as_text(), doc.version


def _render_primer_block(body: str, version: int) -> str:
    """Render the primer block.

    `version` is the canonical guidance asset's `guidance_version` (an
    integer). It is **not** the Vaner release tag — the marker decouples
    primer freshness from release cadence so most releases don't churn
    AGENTS.md unnecessarily. See module docstring for rationale.
    """
    start_marker = f"<!-- vaner-primer:start v={version} -->"
    heading = "# Using Vaner"
    return f"{start_marker}\n{heading}\n\n{body}\n{PRIMER_END}"


def _splice(original: str, replacement: str) -> str:
    start_idx = original.find(PRIMER_START_PREFIX)
    end_idx = original.find(PRIMER_END)
    if start_idx < 0 or end_idx < 0:
        raise SystemExit(
            "AGENTS.md is missing <!-- vaner-primer:start ... --> / end markers. "
            "Add them around the primer block before running this script."
        )
    end_after = end_idx + len(PRIMER_END)
    return original[:start_idx] + replacement + original[end_after:]


def sync(*, check: bool) -> int:
    body, version = _load_canonical_body()
    new_block = _render_primer_block(body, version)
    original = AGENTS_MD.read_text(encoding="utf-8")
    new_text = _splice(original, new_block)
    if original == new_text:
        return 0
    if check:
        sys.stderr.write(
            "AGENTS.md primer block is out of sync with "
            "src/vaner/integrations/guidance/vaner_guidance_v1.md.\n"
            "Run: python scripts/sync_agents_primer.py\n"
        )
        return 1
    AGENTS_MD.write_text(new_text, encoding="utf-8")
    sys.stderr.write(f"Synced AGENTS.md primer to guidance v{version}.\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync AGENTS.md primer to canonical guidance asset.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if AGENTS.md is out of sync (do not modify).",
    )
    args = parser.parse_args()
    return sync(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
