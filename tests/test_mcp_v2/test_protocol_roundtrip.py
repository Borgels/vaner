from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("mcp")

from vaner.mcp.server import build_server
from vaner.models.scenario import Scenario
from vaner.store.scenarios import ScenarioStore


async def _seed(repo_root) -> None:
    store = ScenarioStore(repo_root / ".vaner" / "scenarios.db")
    await store.initialize()
    await store.upsert(
        Scenario(
            id="scn_roundtrip_v2",
            kind="change",
            score=0.9,
            confidence=0.8,
            entities=["auth", "pipeline", "route"],
            prepared_context="Auth in middleware",
        )
    )


def test_mcp_v2_protocol_roundtrip(temp_repo, monkeypatch) -> None:
    async def _run() -> None:
        memory = pytest.importorskip("mcp.shared.memory")
        await _seed(temp_repo)
        (temp_repo / ".vaner" / "config.toml").write_text(
            '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr("vaner.mcp.server.aprecompute", lambda *args, **kwargs: asyncio.sleep(0, result=1))
        server = build_server(temp_repo)
        async with memory.create_connected_server_and_client_session(server) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            expected = [
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
            ]
            for tool_name in expected:
                assert tool_name in names
            status = await session.call_tool("vaner.status", {})
            assert json.loads(status.content[0].text)["ready"] is True

            suggestion = await session.call_tool("vaner.suggest", {"query": "auth middleware", "limit": 1})
            suggestion_payload = json.loads(suggestion.content[0].text)
            assert "suggestions" in suggestion_payload

            resolved = await session.call_tool("vaner.resolve", {"query": "auth middleware"})
            resolved_payload = json.loads(resolved.content[0].text)
            assert ("resolution_id" in resolved_payload) or (resolved_payload.get("abstained") is True)

            expanded = await session.call_tool("vaner.expand", {"target_id": "scn_roundtrip_v2", "mode": "details"})
            expanded_text = expanded.content[0].text
            try:
                expanded_payload = json.loads(expanded_text)
                assert "scn_roundtrip_v2" in json.dumps(expanded_payload)
            except json.JSONDecodeError:
                assert "scn_roundtrip_v2" in expanded_text

            searched = await session.call_tool("vaner.search", {"query": "auth", "mode": "hybrid", "limit": 3})
            search_payload = json.loads(searched.content[0].text)
            assert ("items" in search_payload) or ("results" in search_payload)

            explained = await session.call_tool("vaner.explain", {"resolution_id": "dec_non_existent"})
            explain_payload = json.loads(explained.content[0].text)
            assert ("status" in explain_payload) or ("code" in explain_payload)

            feedback = await session.call_tool(
                "vaner.feedback",
                {"resolution_id": "dec_non_existent", "rating": "useful"},
            )
            feedback_payload = json.loads(feedback.content[0].text)
            assert ("accepted" in feedback_payload) or ("code" in feedback_payload)

            warmed = await session.call_tool("vaner.warm", {"targets": ["auth"]})
            assert "queued" in json.loads(warmed.content[0].text)

            inspected = await session.call_tool("vaner.inspect", {"item_id": "scn_roundtrip_v2"})
            inspect_payload = json.loads(inspected.content[0].text)
            assert ("id" in inspect_payload) or ("code" in inspect_payload)

            traced = await session.call_tool("vaner.debug.trace", {})
            trace_payload = json.loads(traced.content[0].text)
            assert ("status" in trace_payload) or ("code" in trace_payload)

    asyncio.run(_run())
