from __future__ import annotations

import asyncio

from vaner.telemetry.metrics import MetricsStore


def test_memory_quality_snapshot_keys_present(tmp_path) -> None:
    async def _run() -> None:
        store = MetricsStore(tmp_path / ".vaner" / "metrics.db")
        await store.initialize()
        snapshot = await store.memory_quality_snapshot()
        expected = {
            "predictive_hit_rate",
            "stale_hit_rate",
            "promotion_precision",
            "contradiction_rate",
            "correction_survival_rate",
            "demotion_recovery_rate",
            "trusted_evidence_avg",
            "abstain_rate",
        }
        assert expected.issubset(snapshot)

    asyncio.run(_run())


def test_predictive_hit_rate_updates_on_hit_and_miss(tmp_path) -> None:
    async def _run() -> None:
        store = MetricsStore(tmp_path / ".vaner" / "metrics.db")
        await store.initialize()
        await store.increment_counter("resolves_total", 2)
        await store.increment_counter("predictive_hit_total", 1)
        snapshot = await store.memory_quality_snapshot()
        assert snapshot["predictive_hit_rate"] == 0.5

    asyncio.run(_run())


def test_stale_hit_rate_increments_on_stale_resolve(tmp_path) -> None:
    async def _run() -> None:
        store = MetricsStore(tmp_path / ".vaner" / "metrics.db")
        await store.initialize()
        await store.increment_counter("resolves_total", 4)
        await store.increment_counter("stale_hit_total", 1)
        snapshot = await store.memory_quality_snapshot()
        assert snapshot["stale_hit_rate"] == 0.25

    asyncio.run(_run())
