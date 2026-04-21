from __future__ import annotations

from .conftest import call_tool, parse_content, seed_scenario


def test_inspect_returns_memory_meta(temp_repo, mcp_server) -> None:
    seed_scenario(temp_repo, scenario_id="scn_inspect", memory_state="trusted")
    result = call_tool(mcp_server, "vaner.inspect", {"item_id": "scn_inspect"})
    payload = parse_content(result)
    assert payload["id"] == "scn_inspect"
    assert payload["memory"]["state"] == "trusted"
