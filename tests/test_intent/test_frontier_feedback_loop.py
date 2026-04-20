from __future__ import annotations

import asyncio

from vaner.models.scenario import Scenario
from vaner.store.scenarios import ScenarioStore


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
        await store.record_outcome("scn_processed", "useful", skill="vaner-feedback", source="skill")
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
        await store.record_outcome("scn_feedback", "partial", skill="vaner-feedback", source="skill")
        return await store.consume_feedback(limit=10)

    feedback = asyncio.run(_run())
    assert feedback == [("skill", True)]
