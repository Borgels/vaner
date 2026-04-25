# SPDX-License-Identifier: Apache-2.0

"""MCP server resource-list + read_resource integration for the UI bundle."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

if importlib.util.find_spec("mcp") is None:  # pragma: no cover - CI matrix dependent
    pytest.skip("mcp package is unavailable in this test environment", allow_module_level=True)


def _build(tmp_path: Path, *, apps_ui_enabled: bool = True):
    (tmp_path / ".vaner").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".vaner" / "config.toml").write_text(
        '[backend]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "llama3.2:3b"\n'
        f"[mcp]\napps_ui_enabled = {str(apps_ui_enabled).lower()}\n",
        encoding="utf-8",
    )
    from vaner.mcp.server import build_server

    return build_server(tmp_path)


def test_list_resources_includes_ui_bundle_when_enabled(tmp_path: Path) -> None:
    server = _build(tmp_path, apps_ui_enabled=True)

    async def _run() -> list:
        from mcp.types import ListResourcesRequest

        handler = server.request_handlers[ListResourcesRequest]
        result = await handler(ListResourcesRequest(method="resources/list"))
        return list(result.root.resources)

    resources = asyncio.run(_run())
    uris = {str(r.uri) for r in resources}
    assert "ui://vaner/active-predictions" in uris
    assert "vaner://guidance/current" in uris


def test_list_resources_omits_ui_bundle_when_disabled(tmp_path: Path) -> None:
    server = _build(tmp_path, apps_ui_enabled=False)

    async def _run() -> list:
        from mcp.types import ListResourcesRequest

        handler = server.request_handlers[ListResourcesRequest]
        result = await handler(ListResourcesRequest(method="resources/list"))
        return list(result.root.resources)

    resources = asyncio.run(_run())
    uris = {str(r.uri) for r in resources}
    assert "ui://vaner/active-predictions" not in uris
    assert "vaner://guidance/current" in uris  # guidance resource still advertised


def test_read_resource_returns_ui_html(tmp_path: Path) -> None:
    server = _build(tmp_path)

    async def _run() -> str:
        from mcp.types import (
            ReadResourceRequest,
            ReadResourceRequestParams,
        )
        from pydantic import AnyUrl

        handler = server.request_handlers[ReadResourceRequest]
        req = ReadResourceRequest(
            method="resources/read",
            params=ReadResourceRequestParams(uri=AnyUrl("ui://vaner/active-predictions")),
        )
        result = await handler(req)
        return result.root.contents[0].text

    html = asyncio.run(_run())
    assert "<!doctype html>" in html.lower()
    assert "Vaner" in html
