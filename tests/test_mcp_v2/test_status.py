from __future__ import annotations

from .conftest import call_tool, parse_content, seed_scenario


def test_status_returns_health_payload(temp_repo, mcp_server) -> None:
    seed_scenario(temp_repo, scenario_id="scn_status")
    result = call_tool(mcp_server, "vaner.status")
    payload = parse_content(result)
    assert payload["ready"] is True
    assert "memory" in payload
