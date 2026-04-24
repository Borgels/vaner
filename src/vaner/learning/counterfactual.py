# SPDX-License-Identifier: Apache-2.0
"""Counterfactual miss analysis.

When a real prompt arrives and Vaner served a cold_miss or warm_start, this
module classifies the root cause so the scoring policy and next bundle can
learn from it.

Root cause taxonomy:
  taxonomy   — right files, wrong predicted category steered exploration away
  retrieval  — right category, wrong files retrieved for it
  ranking    — right files, wrong ordering / package composition
  timing     — right package, but not ready before the prompt arrived
  abstain_too_eager — abstain threshold fired too low; breadth beat depth here

Records are written to .vaner/decisions/ as JSON lines, one per miss.
The training pipeline ingests these to update priors.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

_MISS_CAUSES = frozenset({"taxonomy", "retrieval", "ranking", "timing", "abstain_too_eager"})


@dataclass(frozen=True, slots=True)
class CounterfactualRecord:
    record_id: str
    ts: float
    miss_type: str
    prompt_snippet: str
    helpful_paths: list[str]
    wasted_paths: list[str]
    root_cause: str
    metadata: dict[str, object]


def _infer_root_cause(
    miss_type: str,
    helpful_paths: list[str],
    wasted_paths: list[str],
    *,
    abstain_was_active: bool,
) -> str:
    if abstain_was_active:
        return "abstain_too_eager"
    if miss_type == "cold_miss":
        if not helpful_paths:
            return "taxonomy"
        if not wasted_paths:
            return "retrieval"
        return "ranking"
    if miss_type == "warm_start":
        return "timing"
    if miss_type in ("taxonomy_miss", "retrieval_miss"):
        return miss_type.replace("_miss", "")
    return "retrieval"


class CounterfactualAnalyzer:
    def __init__(self, decisions_dir: Path) -> None:
        self._dir = decisions_dir

    def analyze(
        self,
        *,
        prompt: str,
        miss_type: str,
        helpful_paths: list[str],
        wasted_paths: list[str],
        abstain_was_active: bool = False,
        metadata: dict[str, object] | None = None,
    ) -> CounterfactualRecord:
        root_cause = _infer_root_cause(
            miss_type,
            helpful_paths,
            wasted_paths,
            abstain_was_active=abstain_was_active,
        )
        record = CounterfactualRecord(
            record_id=str(uuid.uuid4()),
            ts=time.time(),
            miss_type=miss_type,
            prompt_snippet=prompt[:120],
            helpful_paths=helpful_paths[:20],
            wasted_paths=wasted_paths[:20],
            root_cause=root_cause,
            metadata=metadata or {},
        )
        self._write(record)
        return record

    def _write(self, record: CounterfactualRecord) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path = self._dir / f"{record.record_id}.json"
            payload = {
                "record_id": record.record_id,
                "ts": record.ts,
                "miss_type": record.miss_type,
                "prompt_snippet": record.prompt_snippet,
                "helpful_paths": record.helpful_paths,
                "wasted_paths": record.wasted_paths,
                "root_cause": record.root_cause,
                "metadata": record.metadata,
            }
            path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        except Exception:
            pass
