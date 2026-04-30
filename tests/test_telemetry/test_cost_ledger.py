# SPDX-License-Identifier: Apache-2.0

import pytest

from vaner.models.cost import CostLedgerEntry, ModelPricing, TurnCostSummary, estimate_cost, estimate_usage
from vaner.telemetry.metrics import MetricsStore


@pytest.mark.asyncio
async def test_cost_ledger_records_usage_prediction_rollup_and_turn(tmp_path) -> None:
    store = MetricsStore(tmp_path / "metrics.db")
    await store.initialize()

    usage = estimate_usage("prompt words", "completion words")
    cost = estimate_cost(usage, ModelPricing(input_cost_per_1k=1.0, output_cost_per_1k=2.0))
    await store.record_llm_usage(
        CostLedgerEntry(
            request_id="req-1",
            turn_id="turn-1",
            prediction_id="pred-1",
            cycle_id="7",
            provider="test",
            model="model-a",
            call_role="precompute",
            local_or_cloud="cloud",
            usage=usage,
            cost=cost,
        )
    )

    pred = await store.rollup_prediction_cost("pred-1", cycle_id="7", final_outcome="dropped")
    assert pred.prediction_id == "pred-1"
    assert pred.model_calls == 1
    assert pred.total_tokens == usage.total_tokens
    assert pred.cloud_tokens == usage.total_tokens
    assert pred.dropped is True

    await store.record_turn_cost(
        TurnCostSummary(
            turn_id="turn-1",
            request_id="req-1",
            injected_context_tokens=123,
            primary_llm_input_tokens=456,
            primary_llm_output_tokens=78,
            primary_llm_usage_known=True,
            total_estimated_cloud_cost_usd=cost.total_cost_usd,
        )
    )
    summary = await store.cost_summary()
    assert summary["event_count"] == 1
    assert summary["turn_count"] == 1
    assert summary["cloud_tokens"] == usage.total_tokens
    assert summary["injected_context_tokens"] == 123
