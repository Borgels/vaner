from __future__ import annotations

import asyncio

import aiosqlite

from vaner.telemetry.metrics import MetricsStore


def test_record_scenario_outcome_and_mcp_tool_call(temp_repo):
    db_path = temp_repo / ".vaner" / "metrics.db"
    store = MetricsStore(db_path)

    async def _run() -> tuple[int, int]:
        await store.initialize()
        await store.record_mcp_tool_call(
            tool_name="list_scenarios",
            status="ok",
            latency_ms=12.3,
            scenario_id="scn_1",
        )
        await store.record_scenario_outcome(scenario_id="scn_1", result="useful", note="high quality")
        async with aiosqlite.connect(db_path) as db:
            mcp_cur = await db.execute("SELECT COUNT(*) FROM mcp_tool_calls")
            mcp_count = int((await mcp_cur.fetchone())[0])
            out_cur = await db.execute("SELECT COUNT(*) FROM scenario_outcomes")
            outcome_count = int((await out_cur.fetchone())[0])
        return mcp_count, outcome_count

    mcp_count, outcome_count = asyncio.run(_run())
    assert mcp_count == 1
    assert outcome_count == 1


def test_mcp_tools_total_mode_bucket(temp_repo):
    db_path = temp_repo / ".vaner" / "metrics.db"
    store = MetricsStore(db_path)

    async def _run() -> dict[str, int]:
        await store.initialize()
        await store.increment_mode_usage("mcp")
        await store.increment_mode_usage("mcp_tools_total")
        await store.increment_mode_usage("mcp_tools_total")
        return await store.mode_usage_summary()

    usage = asyncio.run(_run())
    assert usage["mcp"] == 1
    assert usage["mcp_tools_total"] == 2
