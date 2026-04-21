from __future__ import annotations

import asyncio

from .conftest import call_tool, parse_content, seed_scenario


def test_expand_returns_payload(temp_repo, mcp_server, monkeypatch) -> None:
    seed_scenario(temp_repo, scenario_id="scn_expand")
    monkeypatch.setattr("vaner.mcp.server.aprecompute", lambda *args, **kwargs: asyncio.sleep(0, result=1))
    result = call_tool(mcp_server, "vaner.expand", {"target_id": "scn_expand", "mode": "details"})
    payload = parse_content(result)
    assert payload["target_id"] == "scn_expand"
