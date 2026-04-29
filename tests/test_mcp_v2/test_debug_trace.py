from __future__ import annotations

from .conftest import call_tool, parse_content


def test_debug_trace_disabled_by_default(mcp_server) -> None:
    result = call_tool(mcp_server, "vaner.debug.trace")
    payload = parse_content(result)
    assert payload["code"] == "debug_disabled"


def test_debug_trace_includes_prediction_health_when_enabled(mcp_server, monkeypatch) -> None:
    monkeypatch.setenv("VANER_MCP_DEBUG", "1")
    result = call_tool(mcp_server, "vaner.debug.trace")
    payload = parse_content(result)
    assert "prediction_health" in payload
    assert payload["prediction_health"]["diagnostic_status"] in {"healthy", "cold", "engine_unavailable"}
