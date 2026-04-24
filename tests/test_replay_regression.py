# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from vaner.defaults.loader import load_defaults_bundle
from vaner.intent.arcs import ConversationArcModel, classify_query_category
from vaner.telemetry.metrics import MetricsStore

_FLOORS_PATH = Path(__file__).parent / "fixtures" / "replay" / "floors.json"
_REPLAY_PATH = Path(__file__).parent / "fixtures" / "replay" / "sample_replay.json"


def _load_floors() -> dict[str, float]:
    try:
        raw = json.loads(_FLOORS_PATH.read_text(encoding="utf-8"))
        return {k: float(v) for k, v in raw.items() if isinstance(v, (int, float))}
    except Exception:
        return {}


def test_defaults_bundle_loads_with_split_manifests() -> None:
    bundle = load_defaults_bundle()
    assert bundle.behavior.arc_transitions is not None
    assert bundle.behavior.phase_classifier is not None
    assert bundle.search.scorer_metadata is not None


def test_metrics_snapshot_contains_regression_floors(tmp_path: Path) -> None:
    turns = json.loads(_REPLAY_PATH.read_text(encoding="utf-8"))
    queries = [str(row.get("query", "")).strip() for row in turns if isinstance(row, dict) and row.get("query")]
    assert len(queries) >= 6

    store = MetricsStore(tmp_path / "metrics.db")

    async def _run() -> dict[str, float]:
        await store.initialize()
        for idx in range(1, len(queries)):
            history = queries[:idx]
            arc = ConversationArcModel()
            arc.rebuild_from_history(history)
            previous_category = classify_query_category(history[-1])
            ranked = arc.predict_next(previous_category, top_k=5, recent_queries=history[-5:])
            if not ranked:
                continue
            scores = {label: max(0.0, float(score)) for label, score in ranked}
            total = sum(scores.values())
            if total <= 0.0:
                continue
            probabilities = {label: value / total for label, value in scores.items()}
            await store.record_next_prompt_prediction(
                probabilities=probabilities,
                actual_label=classify_query_category(queries[idx]),
            )
            await store.record_predictive_lead_seconds(max(0.1, float(idx) * 1.5))
            await store.record_cycle_budget(allocated_ms=1000, used_ms=700, bucket="exploit")
        return await store.memory_quality_snapshot()

    snapshot = asyncio.run(_run())
    floors = _load_floors()
    assert snapshot["next_prompt_top1_rate"] >= floors.get("next_prompt_top1_rate", 0.10), (
        f"top1_rate {snapshot['next_prompt_top1_rate']:.3f} below floor {floors.get('next_prompt_top1_rate', 0.10)}"
    )
    assert snapshot["next_prompt_brier"] <= floors.get("next_prompt_brier", 1.0), (
        f"brier {snapshot['next_prompt_brier']:.3f} above floor {floors.get('next_prompt_brier', 1.0)}"
    )
    assert snapshot["predictive_lead_seconds_avg"] >= floors.get("predictive_lead_seconds_avg", 0.10), (
        f"lead_seconds {snapshot['predictive_lead_seconds_avg']:.3f} below floor {floors.get('predictive_lead_seconds_avg', 0.10)}"
    )
    assert snapshot["budget_utilization"] >= floors.get("budget_utilization", 0.50), (
        f"budget_utilization {snapshot['budget_utilization']:.3f} below floor {floors.get('budget_utilization', 0.50)}"
    )
