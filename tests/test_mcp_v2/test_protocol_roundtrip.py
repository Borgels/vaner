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
            assert names == [
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
            status = await session.call_tool("vaner.status", {})
            assert json.loads(status.content[0].text)["ready"] is True

    asyncio.run(_run())
