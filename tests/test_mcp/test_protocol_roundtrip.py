from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from vaner.mcp.server import build_server
from vaner.models.scenario import Scenario
from vaner.store.scenarios import ScenarioStore


async def _seed_scenario(repo_root: Path) -> None:
    store = ScenarioStore(repo_root / ".vaner" / "scenarios.db")
    await store.initialize()
    await store.upsert(
        Scenario(
            id="scn_roundtrip_1",
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


def test_mcp_protocol_roundtrip(temp_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _run() -> None:
        memory = pytest.importorskip("mcp.shared.memory")
        await _seed_scenario(temp_repo)
        (temp_repo / ".vaner" / "config.toml").write_text(
            '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr("vaner.mcp.server.aprecompute", lambda *args, **kwargs: asyncio.sleep(0, result=1))

        server = build_server(temp_repo)
        async with memory.create_connected_server_and_client_session(server) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = {tool.name for tool in listed.tools}
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
            }

            status = await session.call_tool("vaner.status", {})
            assert status.isError is not True
            assert json.loads(status.content[0].text)["ready"] is True

            suggested = await session.call_tool("vaner.suggest", {"query": "main flow"})
            assert suggested.isError is not True

            resolved = await session.call_tool("vaner.resolve", {"query": "main flow"})
            assert resolved.isError is not True

            expanded = await session.call_tool("vaner.expand", {"target_id": "scn_roundtrip_1", "mode": "details"})
            assert expanded.isError is not True
            assert json.loads(expanded.content[0].text)["target_id"] == "scn_roundtrip_1"

            feedback = await session.call_tool(
                "vaner.feedback",
                {
                    "resolution_id": json.loads(resolved.content[0].text).get("resolution_id"),
                    "rating": "partial",
                    "query": "main flow",
                },
            )
            assert feedback.isError is not True
            assert json.loads(feedback.content[0].text)["accepted"] is True

            inspected = await session.call_tool("vaner.inspect", {"item_id": "scn_roundtrip_1"})
            assert inspected.isError is not True
            assert json.loads(inspected.content[0].text)["id"] == "scn_roundtrip_1"

    asyncio.run(_run())
