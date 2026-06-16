# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import inspect
import json
import re

import pytest

pytest.importorskip("mcp")

from vaner.mcp import server as mcp_server


def test_listed_mcp_tools_have_dispatch_branches(temp_repo) -> None:
    """Catch drift between the public tool list and the call dispatcher."""

    async def _run() -> None:
        memory = pytest.importorskip("mcp.shared.memory")
        server = mcp_server.build_server(temp_repo)
        async with memory.create_connected_server_and_client_session(server) as session:
            await session.initialize()
            listed = {tool.name for tool in (await session.list_tools()).tools}

        source = inspect.getsource(mcp_server.build_server)
        handled = set(re.findall(r'if name == "(vaner\.[^"]+)"', source))

        assert listed <= handled

    asyncio.run(_run())


def test_unknown_mcp_tool_uses_structured_error(temp_repo) -> None:
    async def _run() -> None:
        memory = pytest.importorskip("mcp.shared.memory")
        server = mcp_server.build_server(temp_repo)
        async with memory.create_connected_server_and_client_session(server) as session:
            await session.initialize()
            result = await session.call_tool("vaner.not_a_tool", {})

        assert result.isError is True
        payload = json.loads(result.content[0].text)
        assert payload["code"] == "unknown_tool"
        assert result.structuredContent == payload

    asyncio.run(_run())


def test_unknown_artefacts_tool_error_names_subsystem(temp_repo) -> None:
    source = inspect.getsource(mcp_server.build_server)
    assert "unknown artefacts/sources tool" in source
    assert "unknown goals tool: {name}" not in source


def test_basic_mcp_success_sets_structured_content(temp_repo) -> None:
    async def _run() -> None:
        memory = pytest.importorskip("mcp.shared.memory")
        server = mcp_server.build_server(temp_repo)
        async with memory.create_connected_server_and_client_session(server) as session:
            await session.initialize()
            result = await session.call_tool("vaner.status", {})

        assert result.isError is not True
        payload = json.loads(result.content[0].text)
        assert result.structuredContent == payload
        assert payload["ready"] is True

    asyncio.run(_run())


def test_strict_mcp_tool_names_are_regex_safe_and_dispatch(temp_repo) -> None:
    async def _run() -> None:
        memory = pytest.importorskip("mcp.shared.memory")
        server = mcp_server.build_server(temp_repo, tool_name_format="strict")
        async with memory.create_connected_server_and_client_session(server) as session:
            await session.initialize()
            listed = {tool.name for tool in (await session.list_tools()).tools}
            result = await session.call_tool("vaner__status", {})

        assert "vaner__status" in listed
        assert "vaner.status" not in listed
        assert all(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name) for name in listed)
        assert result.isError is not True
        payload = json.loads(result.content[0].text)
        assert payload["ready"] is True

    asyncio.run(_run())
