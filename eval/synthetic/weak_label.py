#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
from pathlib import Path

from vaner import api


def _label_case(repo_root: Path, case: dict) -> dict:
    question = str(case.get("question", ""))
    package = api.query(question, repo_root)
    selected_paths = [selection.source_path for selection in package.selections]
    expected_paths = [str(path) for path in case.get("expected_paths", [])]

    matched = [path for path in expected_paths if path in selected_paths]
    top_3 = selected_paths[:3]
    hit_at_3 = any(path in top_3 for path in expected_paths)
    confidence = "high" if hit_at_3 else ("medium" if matched else "low")

    secondary = [path for path in selected_paths if path not in expected_paths][:5]
    flags: list[str] = []
    if not hit_at_3:
        flags.append("drifted")
    if confidence == "low":
        flags.append("low_confidence")

    enriched = dict(case)
    enriched["secondary_units"] = secondary
    enriched["label_confidence"] = confidence
    enriched["flags"] = flags
    return enriched


def main() -> None:
    parser = argparse.ArgumentParser(description="Weak-label synthetic benchmark cases using current retrieval behavior.")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--input", type=Path, default=None, help="Input synthetic raw cases JSON")
    parser.add_argument("--output", type=Path, default=None, help="Output labeled cases JSON")
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    input_path = args.input or (repo_root / "eval" / "synthetic" / "cases" / "raw.json")
    output_path = args.output or (repo_root / "eval" / "synthetic" / "cases" / "labeled.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    raw = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit(f"Expected JSON array in {input_path}")

    labeled = [_label_case(repo_root, case) for case in raw if isinstance(case, dict)]
    output_path.write_text(json.dumps(labeled, indent=2), encoding="utf-8")
    print(f"Wrote {len(labeled)} labeled cases to {output_path}")


if __name__ == "__main__":
    main()
