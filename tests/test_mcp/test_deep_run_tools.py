# SPDX-License-Identifier: Apache-2.0
"""WS4 — MCP `vaner.deep_run.*` tool tests (0.8.3)."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

pytest.importorskip("mcp")
from mcp.types import CallToolRequest, ListToolsRequest

from vaner.intent.deep_run_gates import (
    reset_cost_gate,
    set_active_session_for_routing,
)
from vaner.mcp.server import build_server


@pytest.fixture(autouse=True)
def _isolate_singletons():
    set_active_session_for_routing(None)
    reset_cost_gate(None)
    yield
    set_active_session_for_routing(None)
    reset_cost_gate(None)


def _payload(result) -> dict:
    return json.loads(result.root.content[0].text)


def _seed_repo(repo_root) -> None:
    (repo_root / ".vaner").mkdir(parents=True, exist_ok=True)
    (repo_root / ".vaner" / "config.toml").write_text(
        '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n',
        encoding="utf-8",
    )


def test_deep_run_lifecycle_through_mcp(temp_repo, monkeypatch) -> None:
    async def _run() -> None:
        _seed_repo(temp_repo)
        server = build_server(temp_repo)
        list_handler = server.request_handlers[ListToolsRequest]
        call_handler = server.request_handlers[CallToolRequest]

        # All five tools advertised
        listed = await list_handler(ListToolsRequest(method="tools/list"))
        names = {t.name for t in listed.root.tools}
        for tool_name in (
            "vaner.deep_run.start",
            "vaner.deep_run.stop",
            "vaner.deep_run.status",
            "vaner.deep_run.list",
            "vaner.deep_run.show",
        ):
            assert tool_name in names

        # Start a session
        start_resp = await call_handler(
            CallToolRequest(
                method="tools/call",
                params={
                    "name": "vaner.deep_run.start",
                    "arguments": {
                        "ends_at": time.time() + 3600,
                        "preset": "balanced",
                        "locality": "local_only",
                    },
                },
            )
        )
        started = _payload(start_resp)
        assert started["status"] == "active"
        assert started["preset"] == "balanced"
        assert started["locality"] == "local_only"

        # Status reflects the new session
        status_resp = await call_handler(
            CallToolRequest(
                method="tools/call",
                params={"name": "vaner.deep_run.status", "arguments": {}},
            )
        )
        status_payload = _payload(status_resp)
        assert status_payload["session"] is not None
        assert status_payload["session"]["id"] == started["id"]

        # vaner.status should also surface deep_run state
        monkeypatch.setattr(
            "vaner.mcp.server.aprecompute",
            lambda *args, **kwargs: asyncio.sleep(0, result=1),
        )
        global_status = await call_handler(
            CallToolRequest(
                method="tools/call",
                params={"name": "vaner.status", "arguments": {}},
            )
        )
        global_payload = _payload(global_status)
        assert "deep_run" in global_payload
        assert global_payload["deep_run"]["active"] is True
        assert global_payload["deep_run"]["session"]["id"] == started["id"]

        # List + show
        list_resp = await call_handler(
            CallToolRequest(
                method="tools/call",
                params={"name": "vaner.deep_run.list", "arguments": {"limit": 5}},
            )
        )
        listed_sessions = _payload(list_resp)["sessions"]
        assert any(s["id"] == started["id"] for s in listed_sessions)

        show_resp = await call_handler(
            CallToolRequest(
                method="tools/call",
                params={
                    "name": "vaner.deep_run.show",
                    "arguments": {"session_id": started["id"]},
                },
            )
        )
        shown = _payload(show_resp)
        assert shown["id"] == started["id"]

        # Stop
        stop_resp = await call_handler(
            CallToolRequest(
                method="tools/call",
                params={"name": "vaner.deep_run.stop", "arguments": {}},
            )
        )
        summary = _payload(stop_resp)["summary"]
        assert summary is not None
        assert summary["session_id"] == started["id"]
        assert summary["final_status"] == "ended"
        # Honest 4-counter discipline
        for k in ("matured_kept", "matured_discarded", "matured_rolled_back", "matured_failed"):
            assert k in summary

    asyncio.run(_run())


def test_deep_run_start_rejects_missing_ends_at(temp_repo) -> None:
    async def _run() -> None:
        _seed_repo(temp_repo)
        server = build_server(temp_repo)
        call_handler = server.request_handlers[CallToolRequest]
        resp = await call_handler(
            CallToolRequest(
                method="tools/call",
                params={"name": "vaner.deep_run.start", "arguments": {}},
            )
        )
        assert resp.root.isError is True

    asyncio.run(_run())


def test_deep_run_show_unknown_session_returns_not_found(temp_repo) -> None:
    async def _run() -> None:
        _seed_repo(temp_repo)
        server = build_server(temp_repo)
        call_handler = server.request_handlers[CallToolRequest]
        resp = await call_handler(
            CallToolRequest(
                method="tools/call",
                params={
                    "name": "vaner.deep_run.show",
                    "arguments": {"session_id": "ffffffffffffffff"},
                },
            )
        )
        assert resp.root.isError is True

    asyncio.run(_run())


def test_deep_run_stop_with_no_session_returns_null_summary(temp_repo) -> None:
    async def _run() -> None:
        _seed_repo(temp_repo)
        server = build_server(temp_repo)
        call_handler = server.request_handlers[CallToolRequest]
        resp = await call_handler(
            CallToolRequest(
                method="tools/call",
                params={"name": "vaner.deep_run.stop", "arguments": {}},
            )
        )
        body = _payload(resp)
        assert body.get("summary") is None

    asyncio.run(_run())
