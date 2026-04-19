# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio

from vaner.telemetry.metrics import MetricsStore


def test_shadow_summary_aggregates_pairs(temp_repo):
    db_path = temp_repo / ".vaner" / "metrics.db"
    store = MetricsStore(db_path)

    async def _run() -> dict:
        await store.initialize()
        await store.record_shadow_pair(
            request_id="r1",
            with_context_total_ms=100.0,
            without_context_total_ms=140.0,
            with_context_tokens=120,
            without_context_tokens=90,
        )
        await store.record_shadow_pair(
            request_id="r2",
            with_context_total_ms=200.0,
            without_context_total_ms=150.0,
            with_context_tokens=150,
            without_context_tokens=130,
        )
        return await store.shadow_summary(last_n=10)

    summary = asyncio.run(_run())
    assert summary["count"] == 2
    assert 0.0 <= summary["win_rate"] <= 1.0
    assert "avg_latency_gain_ms" in summary


def test_mode_usage_counts(temp_repo):
    db_path = temp_repo / ".vaner" / "metrics.db"
    store = MetricsStore(db_path)

    async def _run() -> dict[str, int]:
        await store.initialize()
        await store.increment_mode_usage("proxy")
        await store.increment_mode_usage("proxy")
        await store.increment_mode_usage("mcp")
        return await store.mode_usage_summary()

    usage = asyncio.run(_run())
    assert usage["proxy"] == 2
    assert usage["mcp"] == 1
