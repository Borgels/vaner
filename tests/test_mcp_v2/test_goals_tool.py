# SPDX-License-Identifier: Apache-2.0
"""WS7 — vaner.goals.* MCP tool tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from tests.test_mcp_v2.conftest import call_tool, parse_content


def _make_server(temp_repo: Path):
    if importlib.util.find_spec("mcp") is None:  # pragma: no cover
        pytest.skip("mcp package is unavailable in this test environment")
    from vaner.mcp.server import build_server

    (temp_repo / ".vaner").mkdir(parents=True, exist_ok=True)
    (temp_repo / ".vaner" / "config.toml").write_text(
        '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
        encoding="utf-8",
    )
    return build_server(temp_repo)


# ---------------------------------------------------------------------------
# declare → list round-trip
# ---------------------------------------------------------------------------


def test_goals_declare_then_list_round_trip(temp_repo):
    server = _make_server(temp_repo)

    declared = parse_content(
        call_tool(
            server,
            "vaner.goals.declare",
            {
                "title": "JWT migration",
                "description": "Replace session tokens with JWT",
                "related_files": ["src/auth.py", "tests/test_auth.py"],
            },
        )
    )
    assert "goal_id" in declared
    assert declared["status"] == "active"

    listed = parse_content(call_tool(server, "vaner.goals.list", {"status": "active"}))
    goals = listed["goals"]
    assert len(goals) == 1
    goal = goals[0]
    assert goal["title"] == "JWT migration"
    assert goal["description"] == "Replace session tokens with JWT"
    assert goal["source"] == "user_declared"
    assert goal["confidence"] == 1.0
    assert goal["status"] == "active"
    assert goal["related_files"] == ["src/auth.py", "tests/test_auth.py"]
    # evidence is parsed to a list (empty when nothing attached yet).
    assert goal["evidence"] == []


def test_goals_declare_requires_title(temp_repo):
    server = _make_server(temp_repo)
    result = parse_content(call_tool(server, "vaner.goals.declare", {"title": "   "}))
    assert result["code"] == "invalid_input"


def test_goals_update_status_changes_the_row(temp_repo):
    server = _make_server(temp_repo)

    declared = parse_content(call_tool(server, "vaner.goals.declare", {"title": "Retire legacy API"}))
    goal_id = declared["goal_id"]

    updated = parse_content(
        call_tool(
            server,
            "vaner.goals.update_status",
            {"goal_id": goal_id, "status": "achieved"},
        )
    )
    assert updated["status"] == "achieved"

    listed_active = parse_content(call_tool(server, "vaner.goals.list", {"status": "active"}))
    assert listed_active["goals"] == []
    listed_done = parse_content(call_tool(server, "vaner.goals.list", {"status": "achieved"}))
    assert len(listed_done["goals"]) == 1


def test_goals_update_status_rejects_unknown(temp_repo):
    """Schema validation rejects invalid status values before the handler
    runs — we just verify the surface doesn't accept the bad value as an
    ok update."""
    server = _make_server(temp_repo)
    declared = parse_content(call_tool(server, "vaner.goals.declare", {"title": "g"}))
    result = call_tool(
        server,
        "vaner.goals.update_status",
        {"goal_id": declared["goal_id"], "status": "not-a-status"},
    )
    # Either schema validation rejected it (content isn't JSON — a plain
    # error string) or our handler rejected it with code=invalid_input.
    # Both are acceptable outcomes; we reject the case where the update
    # went through.
    text = result.root.content[0].text
    assert "invalid" in text.lower() or "validation" in text.lower() or "not one of" in text.lower()

    # Post-condition: the goal's status remains 'active'.
    listed = parse_content(call_tool(server, "vaner.goals.list", {"status": "active"}))
    assert any(g["id"] == declared["goal_id"] for g in listed["goals"])


def test_goals_update_status_404_on_missing_id(temp_repo):
    server = _make_server(temp_repo)
    missing = parse_content(
        call_tool(
            server,
            "vaner.goals.update_status",
            {"goal_id": "nonexistent", "status": "paused"},
        )
    )
    assert missing["code"] == "not_found"


def test_goals_delete_removes_the_row(temp_repo):
    server = _make_server(temp_repo)
    declared = parse_content(call_tool(server, "vaner.goals.declare", {"title": "Delete me"}))
    goal_id = declared["goal_id"]

    deleted = parse_content(call_tool(server, "vaner.goals.delete", {"goal_id": goal_id}))
    assert deleted["deleted"] is True

    listed = parse_content(call_tool(server, "vaner.goals.list"))
    assert listed["goals"] == []


def test_goals_delete_404_on_missing_id(temp_repo):
    server = _make_server(temp_repo)
    missing = parse_content(call_tool(server, "vaner.goals.delete", {"goal_id": "nope"}))
    assert missing["code"] == "not_found"


def test_goals_list_filters_by_status(temp_repo):
    server = _make_server(temp_repo)

    # Declare two goals; mark one as achieved.
    g1 = parse_content(call_tool(server, "vaner.goals.declare", {"title": "alpha"}))["goal_id"]
    parse_content(call_tool(server, "vaner.goals.declare", {"title": "beta"}))
    parse_content(
        call_tool(
            server,
            "vaner.goals.update_status",
            {"goal_id": g1, "status": "achieved"},
        )
    )

    active = parse_content(call_tool(server, "vaner.goals.list", {"status": "active"}))
    assert {g["title"] for g in active["goals"]} == {"beta"}

    achieved = parse_content(call_tool(server, "vaner.goals.list", {"status": "achieved"}))
    assert {g["title"] for g in achieved["goals"]} == {"alpha"}
