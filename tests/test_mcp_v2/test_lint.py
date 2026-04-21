from __future__ import annotations

import asyncio

from vaner.mcp.lint import run_lint
from vaner.models.scenario import Scenario
from vaner.store.scenarios import ScenarioStore


def test_memory_state_counts_present(tmp_path) -> None:
    async def _run() -> None:
        store = ScenarioStore(tmp_path / ".vaner" / "scenarios.db")
        await store.initialize()
        await store.upsert(
            Scenario(
                id="s1",
                kind="change",
                entities=["auth", "route", "policy"],
                memory_state="trusted",
                last_outcome="useful",
            )
        )
        await store.upsert(
            Scenario(
                id="s2",
                kind="change",
                entities=["auth", "route", "policy"],
                memory_state="demoted",
                last_outcome="wrong",
            )
        )
        report = await run_lint(store)
        assert report.trusted_count >= 1
        assert report.demoted_count >= 1
        assert report.contradictions

    asyncio.run(_run())
