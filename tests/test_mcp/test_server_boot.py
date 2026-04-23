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
            assert len(names) == 12
            assert "vaner.status" in names
            assert "vaner.predictions.active" in names
            assert "vaner.predictions.adopt" in names
            status = await session.call_tool("vaner.status", {})
            assert json.loads(status.content[0].text)["ready"] is True

    asyncio.run(_run())
