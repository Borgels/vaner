from __future__ import annotations

import asyncio

from .conftest import call_tool, parse_content, seed_scenario


def test_resolve_returns_resolution_or_abstain(temp_repo, mcp_server, monkeypatch) -> None:
    seed_scenario(temp_repo, scenario_id="scn_resolve")
    monkeypatch.setattr("vaner.mcp.server.aprecompute", lambda *args, **kwargs: asyncio.sleep(0, result=1))
    result = call_tool(mcp_server, "vaner.resolve", {"query": "where auth is enforced"})
    payload = parse_content(result)
    assert ("resolution_id" in payload) or payload.get("abstained") is True
