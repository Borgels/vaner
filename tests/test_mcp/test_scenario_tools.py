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


async def _seed_scenario(repo_root, scenario_id: str, kind: str = "change", state: str = "candidate") -> None:
    store = ScenarioStore(repo_root / ".vaner" / "scenarios.db")
    await store.initialize()
    await store.upsert(
        Scenario(
            id=scenario_id,
            kind=kind,
            score=0.9,
            confidence=0.8,
            entities=["auth", "pipeline", "route"],
            evidence=[],
            prepared_context="auth is enforced in middleware",
            coverage_gaps=[],
            freshness="fresh",
            cost_to_expand="medium",
            memory_state=state,
            memory_confidence=0.8,
        )
    )


def test_mcp_tools_list_and_scenario_flow(temp_repo, monkeypatch):
    async def _run() -> None:
        await _seed_scenario(temp_repo, "scn_mcp_1", kind="change")
        await _seed_scenario(temp_repo, "scn_mcp_2", kind="debug", state="trusted")
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
            "vaner.status",
            "vaner.suggest",
            "vaner.resolve",
            "vaner.expand",
            "vaner.search",
            "vaner.explain",
            "vaner.feedback",
            "vaner.warm",
            "vaner.inspect",
            "vaner.debug.trace",
            "vaner.predictions.active",
            "vaner.predictions.adopt",
            # WS7: workspace goals.
            "vaner.goals.list",
            "vaner.goals.declare",
            "vaner.goals.update_status",
            "vaner.goals.delete",
        }

        monkeypatch.setattr("vaner.mcp.server.aprecompute", lambda *args, **kwargs: asyncio.sleep(0, result=1))
        status = await call_handler(CallToolRequest(method="tools/call", params={"name": "vaner.status", "arguments": {}}))
        status_payload = json.loads(status.root.content[0].text)
        assert status_payload["ready"] is True

        suggest = await call_handler(
            CallToolRequest(method="tools/call", params={"name": "vaner.suggest", "arguments": {"query": "where auth happens"}})
        )
        suggest_payload = json.loads(suggest.root.content[0].text)
        assert "suggestions" in suggest_payload

        resolved = await call_handler(
            CallToolRequest(
                method="tools/call",
                params={"name": "vaner.resolve", "arguments": {"query": "where auth happens", "context": {"domain": "code"}}},
            )
        )
        resolve_payload = json.loads(resolved.root.content[0].text)
        assert "resolution_id" in resolve_payload or resolve_payload.get("abstained") is True

        expanded = await call_handler(
            CallToolRequest(
                method="tools/call",
                params={"name": "vaner.expand", "arguments": {"target_id": "scn_mcp_1", "mode": "details"}},
            )
        )
        expanded_payload = json.loads(expanded.root.content[0].text)
        assert expanded_payload["target_id"] == "scn_mcp_1"

        feedback = await call_handler(
            CallToolRequest(
                method="tools/call",
                params={
                    "name": "vaner.feedback",
                    "arguments": {
                        "resolution_id": resolve_payload.get("resolution_id"),
                        "rating": "partial",
                        "query": "where auth happens",
                    },
                },
            )
        )
        feedback_payload = json.loads(feedback.root.content[0].text)
        assert feedback_payload["accepted"] is True

        async with aiosqlite.connect(temp_repo / ".vaner" / "metrics.db") as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM mcp_tool_calls WHERE tool_name = ? AND status = ?",
                ("vaner.status", "ok"),
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
