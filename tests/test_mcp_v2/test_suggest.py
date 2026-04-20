from __future__ import annotations

from .conftest import call_tool, parse_content, seed_scenario


def test_suggest_returns_candidates(temp_repo, mcp_server) -> None:
    seed_scenario(temp_repo, scenario_id="scn_suggest")
    result = call_tool(mcp_server, "vaner.suggest", {"query": "where auth is enforced"})
    payload = parse_content(result)
    assert "suggestions" in payload
