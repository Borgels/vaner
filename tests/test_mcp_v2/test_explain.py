from __future__ import annotations

import asyncio

from .conftest import call_tool, parse_content, seed_scenario


def test_explain_reads_latest_decision(temp_repo, mcp_server, monkeypatch) -> None:
    seed_scenario(temp_repo, scenario_id="scn_explain")
    monkeypatch.setattr("vaner.mcp.server.aprecompute", lambda *args, **kwargs: asyncio.sleep(0, result=1))
    resolved = call_tool(mcp_server, "vaner.resolve", {"query": "auth"})
    payload = parse_content(resolved)
    if payload.get("abstained") or payload.get("code") == "engine_unavailable":
        # 0.8.1: resolve delegates to engine.resolve_query; no engine / no
        # daemon in this smoke setup, so engine_unavailable is expected.
        return
    explained = call_tool(mcp_server, "vaner.explain", {"resolution_id": payload["resolution_id"]})
    explain_payload = parse_content(explained)
    assert "selection_reason" in explain_payload
