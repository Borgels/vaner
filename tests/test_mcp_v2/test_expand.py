from __future__ import annotations

from .conftest import call_tool, parse_content, seed_scenario


def test_expand_returns_payload(temp_repo, mcp_server) -> None:
    seed_scenario(temp_repo, scenario_id="scn_expand")
    result = call_tool(mcp_server, "vaner.expand", {"target_id": "scn_expand", "mode": "details"})
    payload = parse_content(result)
    assert payload["target_id"] == "scn_expand"
    assert payload["background_refresh"]["reason"] == "expand:scn_expand"
