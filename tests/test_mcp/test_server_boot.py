from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("mcp")

from vaner.mcp.server import build_server


def test_server_boot_initialize_lists_tools_and_status(temp_repo) -> None:
    async def _run() -> None:
        memory = pytest.importorskip("mcp.shared.memory")
        server = build_server(temp_repo)
        async with memory.create_connected_server_and_client_session(server) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = [tool.name for tool in listed.tools]
            schemas = {tool.name: tool.inputSchema for tool in listed.tools}
            # WS7 added 4 goal tools → 16. 0.8.2 WS1 adds 4 artefact
            # tools + 1 sources.status → 21. 0.8.3 WS4 adds 5 deep_run
            # tools → 26. 0.8.5 WS5 adds vaner.predictions.dashboard → 27.
            # 0.8.6 WS7 adds 5 vaner.setup.* / vaner.policy.show tools → 32.
            # 0.8.6 WS9 adds vaner.deep_run.defaults → 33.
            # Exact set is asserted in test_protocol_roundtrip.
            assert len(names) == 33
            assert "vaner.status" in names
            assert "vaner.predictions.active" in names
            assert "vaner.predictions.adopt" in names
            assert "vaner.predictions.dashboard" in names
            assert "vaner.goals.declare" in names
            assert "vaner.artefacts.list" in names
            assert "vaner.artefacts.influence" in names
            assert "vaner.sources.status" in names
            assert "vaner.deep_run.start" in names
            assert "vaner.setup.questions" in names
            assert "vaner.setup.recommend" in names
            assert "vaner.setup.apply" in names
            assert "vaner.setup.status" in names
            assert "vaner.policy.show" in names
            resolve_schema = schemas["vaner.resolve"]
            assert set(resolve_schema["properties"]) >= {
                "query",
                "suggestion_id",
                "context",
                "budget",
                "max_evidence_items",
                "include_briefing",
                "include_predicted_response",
                "include_metrics",
                "estimated_cost_per_1k_tokens",
            }
            status = await session.call_tool("vaner.status", {})
            assert json.loads(status.content[0].text)["ready"] is True

    asyncio.run(_run())
