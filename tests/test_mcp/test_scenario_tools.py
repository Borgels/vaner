from __future__ import annotations

import asyncio
import json
from typing import cast

import aiosqlite
import pytest

pytest.importorskip("mcp")
from mcp.types import CallToolRequest, ListToolsRequest

from vaner.mcp.server import build_server
from vaner.models.scenario import Scenario, ScenarioKind
from vaner.store.scenarios import ScenarioStore


async def _seed_scenario(repo_root, scenario_id: str, kind: str = "change") -> None:
    store = ScenarioStore(repo_root / ".vaner" / "scenarios.db")
    await store.initialize()
    await store.upsert(
        Scenario(
            id=scenario_id,
            kind=cast(ScenarioKind, kind),
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
        await _seed_scenario(temp_repo, "scn_mcp_1", kind="change")
        await _seed_scenario(temp_repo, "scn_mcp_2", kind="debug")
        (temp_repo / ".vaner" / "config.toml").write_text(
            '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
            encoding="utf-8",
        )
        server = build_server(temp_repo)
        list_handler = server.request_handlers[ListToolsRequest]
        call_handler = server.request_handlers[CallToolRequest]

        listed = await list_handler(ListToolsRequest(method="tools/list"))
        names = {tool.name for tool in listed.root.tools}
        assert names == {
            "list_scenarios",
            "get_scenario",
            "expand_scenario",
            "compare_scenarios",
            "report_outcome",
        }
        assert "legacy_get_context" not in names
        assert "legacy_precompute" not in names
        assert "legacy_get_metrics" not in names

        listed_scenarios = await call_handler(
            CallToolRequest(
                method="tools/call",
                params={"name": "list_scenarios", "arguments": {"limit": 5, "skill": "vaner-feedback"}},
            )
        )
        payload = json.loads(listed_scenarios.root.content[0].text)
        assert payload["count"] >= 1

        filtered = await call_handler(
            CallToolRequest(method="tools/call", params={"name": "list_scenarios", "arguments": {"kind": "debug", "limit": 5}})
        )
        filtered_payload = json.loads(filtered.root.content[0].text)
        assert filtered_payload["count"] == 1
        assert filtered_payload["scenarios"][0]["kind"] == "debug"

        unknown_kind = await call_handler(
            CallToolRequest(method="tools/call", params={"name": "list_scenarios", "arguments": {"kind": "bogus", "limit": 5}})
        )
        unknown_payload = json.loads(unknown_kind.root.content[0].text)
        assert unknown_payload["count"] == 0

        fetched = await call_handler(
            CallToolRequest(
                method="tools/call",
                params={"name": "get_scenario", "arguments": {"id": "scn_mcp_1", "skill": "vaner-feedback"}},
            )
        )
        scenario = json.loads(fetched.root.content[0].text)
        assert scenario["id"] == "scn_mcp_1"

        missing_id = await call_handler(CallToolRequest(method="tools/call", params={"name": "get_scenario", "arguments": {}}))
        assert missing_id.root.isError is True

        unknown_id = await call_handler(
            CallToolRequest(method="tools/call", params={"name": "get_scenario", "arguments": {"id": "does_not_exist"}})
        )
        assert unknown_id.root.isError is True

        monkeypatch.setattr("vaner.mcp.server.aprecompute", lambda *args, **kwargs: asyncio.sleep(0, result=1))
        expanded = await call_handler(
            CallToolRequest(method="tools/call", params={"name": "expand_scenario", "arguments": {"id": "scn_mcp_1"}})
        )
        assert json.loads(expanded.root.content[0].text)["id"] == "scn_mcp_1"

        expand_missing_id = await call_handler(CallToolRequest(method="tools/call", params={"name": "expand_scenario", "arguments": {}}))
        assert expand_missing_id.root.isError is True

        expand_unknown = await call_handler(
            CallToolRequest(method="tools/call", params={"name": "expand_scenario", "arguments": {"id": "does_not_exist"}})
        )
        assert expand_unknown.root.isError is True

        async def _boom(*_args, **_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr("vaner.mcp.server.aprecompute", _boom)
        expand_failure = await call_handler(
            CallToolRequest(method="tools/call", params={"name": "expand_scenario", "arguments": {"id": "scn_mcp_1"}})
        )
        assert expand_failure.root.isError is True

        compared = await call_handler(
            CallToolRequest(
                method="tools/call",
                params={"name": "compare_scenarios", "arguments": {"ids": ["scn_mcp_1", "scn_mcp_1"]}},
            )
        )
        assert json.loads(compared.root.content[0].text)["recommended_scenario_id"] == "scn_mcp_1"

        compare_short = await call_handler(
            CallToolRequest(method="tools/call", params={"name": "compare_scenarios", "arguments": {"ids": ["scn_mcp_1"]}})
        )
        assert compare_short.root.isError is True

        compare_unknown = await call_handler(
            CallToolRequest(
                method="tools/call",
                params={"name": "compare_scenarios", "arguments": {"ids": ["does_not_exist", "other_missing"]}},
            )
        )
        assert compare_unknown.root.isError is True

        outcome = await call_handler(
            CallToolRequest(
                method="tools/call",
                params={
                    "name": "report_outcome",
                    "arguments": {"id": "scn_mcp_1", "result": "useful", "skill": "vaner-feedback"},
                },
            )
        )
        assert json.loads(outcome.root.content[0].text)["ok"] is True

        invalid_outcome = await call_handler(
            CallToolRequest(
                method="tools/call",
                params={"name": "report_outcome", "arguments": {"id": "scn_mcp_1", "result": "bad"}},
            )
        )
        assert invalid_outcome.root.isError is True

        async with aiosqlite.connect(temp_repo / ".vaner" / "metrics.db") as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM mcp_tool_calls WHERE tool_name = ? AND status = ?",
                ("list_scenarios", "ok"),
            )
            assert int((await cur.fetchone())[0]) >= 1

    asyncio.run(_run())


def test_mcp_unknown_tool_records_error_telemetry(temp_repo):
    async def _run() -> int:
        await _seed_scenario(temp_repo, "scn_mcp_1")
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
