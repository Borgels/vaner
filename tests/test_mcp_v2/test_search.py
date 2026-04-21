from __future__ import annotations

from .conftest import call_tool, parse_content, seed_scenario


def test_search_returns_results(temp_repo, mcp_server) -> None:
    seed_scenario(temp_repo, scenario_id="scn_search")
    result = call_tool(mcp_server, "vaner.search", {"query": "auth policy", "mode": "hybrid"})
    payload = parse_content(result)
    assert "results" in payload
