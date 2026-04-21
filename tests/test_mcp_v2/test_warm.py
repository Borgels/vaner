from __future__ import annotations

import asyncio

from .conftest import call_tool, parse_content


def test_warm_accepts_targets(mcp_server, monkeypatch) -> None:
    monkeypatch.setattr("vaner.mcp.server.aprecompute", lambda *args, **kwargs: asyncio.sleep(0, result=1))
    result = call_tool(mcp_server, "vaner.warm", {"targets": ["auth", "billing"]})
    payload = parse_content(result)
    assert payload["queued"] == 2
