from __future__ import annotations

import asyncio
import json

import aiosqlite
import pytest

pytest.importorskip("mcp")
from mcp.types import CallToolRequest, ListToolsRequest

from vaner.mcp.server import build_server
from vaner.models.scenario import Scenario
from vaner.store.scenarios import ScenarioStore


async def _seed_scenario(repo_root) -> None:
    store = ScenarioStore(repo_root / ".vaner" / "scenarios.db")
    await store.initialize()
    await store.upsert(
        Scenario(
            id="scn_mcp_1",
            kind="change",
            score=0.9,
            confidence=0.8,
            entities=["src/main.py"],
            evidence=[],
            prepared_context="prepared",
            coverage_gaps=[],
            freshness="fresh",
            cost_to_expand="medium",
        )
    )


def test_mcp_tools_list_and_scenario_flow(temp_repo, monkeypatch):
    async def _run() -> None:
        await _seed_scenario(temp_repo)
        (temp_repo / ".vaner" / "config.toml").write_text(
            "[backend]\nbase_url = \"http://127.0.0.1:11434/v1\"\nmodel = \"llama3.2:3b\"\n",
            encoding="utf-8",
        )
        server = build_server(temp_repo)
        list_handler = server.request_handlers[ListToolsRequest]
        call_handler = server.request_handlers[CallToolRequest]

        listed = await list_handler(ListToolsRequest(method="tools/list"))
        names = {tool.name for tool in listed.root.tools}
        assert "list_scenarios" in names
        assert "legacy_get_context" in names

        listed_scenarios = await call_handler(
            CallToolRequest(method="tools/call", params={"name": "list_scenarios", "arguments": {"limit": 5}})
        )
        payload = json.loads(listed_scenarios.root.content[0].text)
        assert payload["count"] >= 1

        fetched = await call_handler(
            CallToolRequest(method="tools/call", params={"name": "get_scenario", "arguments": {"id": "scn_mcp_1"}})
        )
        scenario = json.loads(fetched.root.content[0].text)
        assert scenario["id"] == "scn_mcp_1"

        monkeypatch.setattr("vaner.mcp.server.aprecompute", lambda *args, **kwargs: asyncio.sleep(0, result=1))
        expanded = await call_handler(
            CallToolRequest(method="tools/call", params={"name": "expand_scenario", "arguments": {"id": "scn_mcp_1"}})
        )
        assert json.loads(expanded.root.content[0].text)["id"] == "scn_mcp_1"

        compared = await call_handler(
            CallToolRequest(
                method="tools/call",
                params={"name": "compare_scenarios", "arguments": {"ids": ["scn_mcp_1", "scn_mcp_1"]}},
            )
        )
        assert json.loads(compared.root.content[0].text)["recommended_scenario_id"] == "scn_mcp_1"

        outcome = await call_handler(
            CallToolRequest(
                method="tools/call",
                params={"name": "report_outcome", "arguments": {"id": "scn_mcp_1", "result": "useful"}},
            )
        )
        assert json.loads(outcome.root.content[0].text)["ok"] is True

        legacy_metrics = await call_handler(
            CallToolRequest(method="tools/call", params={"name": "legacy_get_metrics", "arguments": {"last_n": 10}})
        )
        assert "count" in legacy_metrics.root.content[0].text or "No metrics yet" in legacy_metrics.root.content[0].text

        legacy_context = await call_handler(CallToolRequest(method="tools/call", params={"name": "legacy_get_context", "arguments": {}}))
        assert legacy_context.root.isError is True

    asyncio.run(_run())


def test_mcp_unknown_tool_records_error_telemetry(temp_repo):
    async def _run() -> int:
        await _seed_scenario(temp_repo)
        server = build_server(temp_repo)
        call_handler = server.request_handlers[CallToolRequest]
        result = await call_handler(CallToolRequest(method="tools/call", params={"name": "unknown_tool", "arguments": {}}))
        assert result.root.isError is True

        async with aiosqlite.connect(temp_repo / ".vaner" / "metrics.db") as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM mcp_tool_calls WHERE tool_name = ? AND status = ?",
                ("unknown_tool", "error"),
            )
            return int((await cur.fetchone())[0])

    assert asyncio.run(_run()) == 1
