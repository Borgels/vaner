from __future__ import annotations

import asyncio
import inspect

import pytest

from vaner.models.scenario import Scenario
from vaner.store.scenarios import ScenarioStore

if not hasattr(ScenarioStore, "consume_feedback"):
    pytest.skip("consume_feedback unavailable on this store surface", allow_module_level=True)


async def _record_outcome_compat(store: ScenarioStore, scenario_id: str, result: str) -> None:
    params = inspect.signature(store.record_outcome).parameters
    if "skill" in params and "source" in params:
        await store.record_outcome(scenario_id, result, skill="vaner-feedback", source="skill")
        return
    await store.record_outcome(scenario_id, result)


def test_consume_feedback_marks_processed(temp_repo):
    async def _run() -> tuple[int, int]:
        store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(
            Scenario(
                id="scn_processed",
                kind="change",
                score=0.5,
                confidence=0.5,
                entities=[],
                evidence=[],
                prepared_context="ctx",
                coverage_gaps=[],
                freshness="fresh",
                cost_to_expand="low",
            )
        )
        await _record_outcome_compat(store, "scn_processed", "useful")
        first = await store.consume_feedback(limit=10)
        second = await store.consume_feedback(limit=10)
        return len(first), len(second)

    first_count, second_count = asyncio.run(_run())
    assert first_count == 1
    assert second_count == 0


def test_consume_feedback_returns_entries_for_known_scenario(temp_repo):
    async def _run() -> list[tuple[str, bool]]:
        store = ScenarioStore(temp_repo / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(
            Scenario(
                id="scn_feedback",
                kind="change",
                score=0.6,
                confidence=0.6,
                entities=[],
                evidence=[],
                prepared_context="ctx",
                coverage_gaps=[],
                freshness="fresh",
                cost_to_expand="low",
            )
        )
        await _record_outcome_compat(store, "scn_feedback", "partial")
        return await store.consume_feedback(limit=10)

    feedback = asyncio.run(_run())
    assert len(feedback) == 1
