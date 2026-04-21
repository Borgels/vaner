from __future__ import annotations

import asyncio

from .conftest import call_tool, parse_content, seed_scenario


def test_feedback_returns_memory_transition(temp_repo, mcp_server, monkeypatch) -> None:
    seed_scenario(temp_repo, scenario_id="scn_feedback")
    monkeypatch.setattr("vaner.mcp.server.aprecompute", lambda *args, **kwargs: asyncio.sleep(0, result=1))
    resolved = call_tool(mcp_server, "vaner.resolve", {"query": "auth"})
    payload = parse_content(resolved)
    if payload.get("abstained"):
        return
    feedback = call_tool(
        mcp_server,
        "vaner.feedback",
        {"resolution_id": payload["resolution_id"], "rating": "partial", "query": "auth"},
    )
    feedback_payload = parse_content(feedback)
    assert feedback_payload["accepted"] is True
    assert "memory_transition" in feedback_payload
