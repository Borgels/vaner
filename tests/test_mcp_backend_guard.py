from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("mcp")
from mcp.types import CallToolRequest

from vaner.mcp.server import build_server


def test_mcp_backend_guard_for_expand(temp_repo):
    async def _run() -> None:
        server = build_server(temp_repo)
        call_handler = server.request_handlers[CallToolRequest]

        expand_result = await call_handler(
            CallToolRequest(
                method="tools/call",
                params={"name": "expand_scenario", "arguments": {"id": "scn_missing"}},
            )
        )
        assert expand_result.root.isError is True
        assert "vaner init --backend-preset" in expand_result.root.content[0].text
        assert expand_result.root.structuredContent["code"] == "backend_not_configured"

    asyncio.run(_run())
