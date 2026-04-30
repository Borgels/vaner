# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path


def test_prediction_engine_does_not_embed_benchmark_prompts_or_harness_imports() -> None:
    root = Path("src/vaner")
    forbidden = [
        "scenario_match_bench",
        "v2_hardening",
        "benchmark/runs",
        "exploration frontier decide which scenario",
        "tiered prediction cache",
        "reward computation work",
        "IntentScorer use GBDT",
    ]
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle in forbidden:
            if needle in text:
                offenders.append(f"{path}:{needle}")

    assert offenders == []


def test_prediction_engine_source_does_not_embed_absolute_local_paths() -> None:
    root = Path("src/vaner")
    forbidden = ["/" + part + "/" for part in ("home", "Users", "mnt", "media")]
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle in forbidden:
            if needle in text:
                offenders.append(f"{path}:{needle}")

    assert offenders == []
