# SPDX-License-Identifier: Apache-2.0
"""Vaner MCP server.

Exposes Vaner's context-retrieval capability as an MCP (Model Context Protocol)
server so that tools with native MCP support (Cursor, Claude Desktop, etc.) can
explicitly request repository context before generating a response.

Unlike the proxy mode (which enriches every request transparently), the MCP
mode gives the AI model explicit control: it can call ``get_context`` when it
decides that codebase context would help.

Tools exposed
-------------
``get_context(prompt)``
    Retrieve the most relevant repository context for a given prompt.
    Returns the injected context string plus metadata (cache tier, token count).

``precompute()``
    Trigger a background precompute cycle to warm the cache.

``get_metrics()``
    Return recent proxy/engine metrics as a JSON summary.

Usage
-----
    # Start the MCP server (stdio transport, for Claude Desktop / Cursor)
    vaner mcp --path /your/repo

    # Or run as an HTTP+SSE server (for remote access)
    vaner mcp --path /your/repo --transport sse --host 127.0.0.1 --port 8472

Claude Desktop config (~/.claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "vaner": {
          "command": "vaner",
          "args": ["mcp", "--path", "/path/to/your/repo"]
        }
      }
    }

Cursor MCP config (.cursor/mcp.json in your project):
    {
      "mcpServers": {
        "vaner": {
          "command": "vaner",
          "args": ["mcp", "--path", "."]
        }
      }
    }
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.types import (
    CallToolResult,
    ListToolsResult,
    TextContent,
    Tool,
)

from vaner.api import aprecompute, aquery
from vaner.cli.commands.config import load_config


def _make_text(content: str) -> list[TextContent]:
    return [TextContent(type="text", text=content)]


def build_server(repo_root: Path) -> Server:
    """Build and return the Vaner MCP Server instance."""
    config = load_config(repo_root)
    server: Server = Server("vaner")

    @server.list_tools()
    async def list_tools() -> ListToolsResult:
        return ListToolsResult(
            tools=[
                Tool(
                    name="get_context",
                    description=(
                        "Retrieve the most relevant repository context for a given prompt. "
                        "Returns file contents, summaries, and relevant code snippets that "
                        "Vaner has pre-computed for this repository. Use this before answering "
                        "questions that require knowledge of the codebase."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "prompt": {
                                "type": "string",
                                "description": "The user's question or task description",
                            },
                            "top_n": {
                                "type": "integer",
                                "description": "Maximum number of context snippets to return (default: 8)",
                                "default": 8,
                            },
                        },
                        "required": ["prompt"],
                    },
                ),
                Tool(
                    name="precompute",
                    description=(
                        "Trigger a Vaner precompute cycle to explore the repository and warm "
                        "the context cache. Call this after significant code changes to ensure "
                        "get_context returns up-to-date results."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {},
                    },
                ),
                Tool(
                    name="get_metrics",
                    description="Return recent Vaner performance metrics (cache hit rates, latency).",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "last_n": {
                                "type": "integer",
                                "description": "Number of recent requests to summarize (default: 100)",
                                "default": 100,
                            }
                        },
                    },
                ),
            ]
        )

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> CallToolResult:
        args = arguments or {}

        if name == "get_context":
            prompt = str(args.get("prompt", ""))
            top_n = int(args.get("top_n", 8))
            if not prompt:
                return CallToolResult(
                    content=_make_text("ERROR: prompt is required"),
                    isError=True,
                )
            try:
                package = await aquery(prompt, repo_root, config=config, top_n=top_n)
            except Exception as exc:
                return CallToolResult(
                    content=_make_text(f"ERROR: {exc}"),
                    isError=True,
                )
            metadata = {
                "cache_tier": package.cache_tier,
                "partial_similarity": round(package.partial_similarity, 3),
                "token_used": package.token_used,
                "selections": len(package.selections),
                "paths": [s.source_path for s in package.selections],
            }
            output = (
                f"<!-- vaner:metadata {json.dumps(metadata)} -->\n\n"
                + package.injected_context
            )
            return CallToolResult(content=_make_text(output))

        if name == "precompute":
            try:
                written = await aprecompute(repo_root, config=config)
            except Exception as exc:
                return CallToolResult(
                    content=_make_text(f"ERROR: {exc}"),
                    isError=True,
                )
            return CallToolResult(
                content=_make_text(f"Precompute cycle complete. Artefacts written: {written}")
            )

        if name == "get_metrics":
            last_n = int(args.get("last_n", 100))
            try:
                from vaner.telemetry.metrics import MetricsStore
                metrics_db = repo_root / ".vaner" / "metrics.db"
                if not metrics_db.exists():
                    return CallToolResult(
                        content=_make_text("No metrics yet. Run `vaner proxy` and make some requests first.")
                    )
                store = MetricsStore(metrics_db)
                await store.initialize()
                summary = await store.summary(last_n=last_n)
                return CallToolResult(content=_make_text(json.dumps(summary, indent=2)))
            except Exception as exc:
                return CallToolResult(
                    content=_make_text(f"ERROR: {exc}"),
                    isError=True,
                )

        return CallToolResult(
            content=_make_text(f"ERROR: Unknown tool '{name}'"),
            isError=True,
        )

    return server


async def run_stdio(repo_root: Path) -> None:
    """Run the MCP server on stdio (for Claude Desktop / Cursor local config)."""
    from mcp.server.stdio import stdio_server

    server = build_server(repo_root)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="vaner",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=None,
                    experimental_capabilities={},
                ),
            ),
        )


async def run_sse(repo_root: Path, host: str, port: int) -> None:
    """Run the MCP server over HTTP+SSE (for remote/network access)."""
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Mount, Route

    import uvicorn

    server = build_server(repo_root)
    sse_transport = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await server.run(
                streams[0],
                streams[1],
                InitializationOptions(
                    server_name="vaner",
                    server_version="0.1.0",
                    capabilities=server.get_capabilities(
                        notification_options=None,
                        experimental_capabilities={},
                    ),
                ),
            )

    starlette_app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse_transport.handle_post_message),
        ]
    )
    config = uvicorn.Config(starlette_app, host=host, port=port)
    await uvicorn.Server(config).serve()
