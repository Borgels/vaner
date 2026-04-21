from __future__ import annotations

from .conftest import call_tool, parse_content


def test_debug_trace_disabled_by_default(mcp_server) -> None:
    result = call_tool(mcp_server, "vaner.debug.trace")
    payload = parse_content(result)
    assert payload["code"] == "debug_disabled"
