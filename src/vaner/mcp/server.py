# SPDX-License-Identifier: Apache-2.0
"""Vaner MCP server.

Exposes Vaner as a speculative context engine over MCP (Model Context Protocol):
the model pulls pre-computed scenarios on demand as a cheat sheet before or
during a response.

The optional OpenAI-compatible proxy remains available as a capability, but the
default integration path is MCP-first.

Tools exposed
-------------
``list_scenarios(kind?, limit?)``
    List top-ranked scenarios for the current repository.

``get_scenario(id)``
    Return full prepared context and evidence for a scenario.

``expand_scenario(id, depth?)``
    Trigger deeper precompute and return the refreshed scenario.

``compare_scenarios(ids)``
    Compare overlap/divergence across candidate scenarios.

``report_outcome(id, result, note?)``
    Record whether a scenario was useful, partial, or irrelevant.

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
# mypy: ignore-errors

from __future__ import annotations

import json
import time
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

from vaner.api import aprecompute
from vaner.cli.commands.config import load_config
from vaner.models.scenario import Scenario
from vaner.store.scenarios import ScenarioStore
from vaner.telemetry.metrics import MetricsStore


def _make_text(content: str) -> list[TextContent]:
    return [TextContent(type="text", text=content)]


BACKEND_NOT_CONFIGURED_MESSAGE = (
    "No LLM backend configured for Vaner.\n"
    "Fix it with one command:\n"
    "  vaner init --backend-preset ollama          # local, free\n"
    "  vaner init --backend-preset openrouter --backend-api-key-env OPENROUTER_API_KEY\n"
    "Docs: https://docs.vaner.ai/mcp"
)


class BackendNotConfiguredError(RuntimeError):
    code = "backend_not_configured"

    def __init__(self) -> None:
        super().__init__(BACKEND_NOT_CONFIGURED_MESSAGE)


def _ensure_backend(config: Any) -> None:
    backend = getattr(config, "backend", None)
    base_url = str(getattr(backend, "base_url", "") or "").strip()
    model = str(getattr(backend, "model", "") or "").strip()
    if not base_url or not model:
        raise BackendNotConfiguredError()


def _scenario_to_summary(scenario: Scenario) -> dict[str, Any]:
    return {
        "id": scenario.id,
        "kind": scenario.kind,
        "score": scenario.score,
        "confidence": scenario.confidence,
        "entities": scenario.entities[:8],
        "freshness": scenario.freshness,
        "cost_to_expand": scenario.cost_to_expand,
        "prepared_context_preview": scenario.prepared_context[:220],
    }


def build_server(repo_root: Path) -> Server:
    """Build and return the Vaner MCP Server instance."""
    config = load_config(repo_root)
    metrics_store = MetricsStore(repo_root / ".vaner" / "metrics.db")
    scenario_store = ScenarioStore(repo_root / ".vaner" / "scenarios.db")
    server: Server = Server("vaner")

    @server.list_tools()
    async def list_tools() -> ListToolsResult:
        return ListToolsResult(
            tools=[
                Tool(
                    name="list_scenarios",
                    description=(
                        "List top-ranked Vaner scenarios for the current repository. "
                        "Use this first to discover candidate context opportunities."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "description": (
                                    "Optional filter by scenario kind "
                                    "(debug | explain | change | research). "
                                    "Unknown values return an empty list."
                                ),
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of scenarios to return (default: 10)",
                                "default": 10,
                            },
                        },
                    },
                ),
                Tool(
                    name="get_scenario",
                    description="Retrieve a full scenario payload, including prepared context, evidence, and coverage gaps.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Scenario id returned by list_scenarios",
                            }
                        },
                        "required": ["id"],
                    },
                ),
                Tool(
                    name="expand_scenario",
                    description="Expand a scenario (targeted deeper precompute) and return the refreshed scenario payload.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "depth": {
                                "type": "integer",
                                "default": 1,
                                "description": "Expansion depth hint (currently best-effort)",
                            },
                        },
                        "required": ["id"],
                    },
                ),
                Tool(
                    name="compare_scenarios",
                    description="Compare competing scenarios and show overlap/divergence for disambiguation.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Two or more scenario ids",
                            }
                        },
                        "required": ["ids"],
                    },
                ),
                Tool(
                    name="report_outcome",
                    description="Record feedback outcome for a scenario: useful, partial, or irrelevant.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "result": {"type": "string", "enum": ["useful", "irrelevant", "partial"]},
                            "note": {"type": "string"},
                        },
                        "required": ["id", "result"],
                    },
                ),
            ]
        )

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> CallToolResult:
        args = arguments or {}
        started = time.perf_counter()
        await metrics_store.initialize()
        await scenario_store.initialize()

        async def _record(status: str, *, tool_name: str | None = None, scenario_id: str | None = None) -> None:
            latency_ms = (time.perf_counter() - started) * 1000.0
            try:
                await metrics_store.increment_mode_usage("mcp")
                await metrics_store.increment_mode_usage("mcp_tools_total")
                await metrics_store.record_mcp_tool_call(
                    tool_name=tool_name or name,
                    status=status,
                    latency_ms=latency_ms,
                    scenario_id=scenario_id,
                )
            except Exception:
                # Metrics are best-effort; tool execution must remain non-fatal.
                return

        async def _error(
            message: str,
            *,
            tool_name: str | None = None,
            scenario_id: str | None = None,
            code: str | None = None,
        ) -> CallToolResult:
            await _record("error", tool_name=tool_name, scenario_id=scenario_id)
            structured: dict[str, Any] | None = None
            if code:
                structured = {"code": code, "message": message}
            return CallToolResult(content=_make_text(message), isError=True, structuredContent=structured)

        if name == "list_scenarios":
            limit = int(args.get("limit", 10))
            kind = args.get("kind")
            if kind is not None:
                kind = str(kind)
            scenarios = await scenario_store.list_top(kind=kind, limit=limit)
            payload = {"count": len(scenarios), "scenarios": [_scenario_to_summary(s) for s in scenarios]}
            await _record("ok")
            return CallToolResult(content=_make_text(json.dumps(payload, indent=2)))

        if name == "get_scenario":
            scenario_id = str(args.get("id", "")).strip()
            if not scenario_id:
                return await _error("ERROR: id is required")
            scenario = await scenario_store.get(scenario_id)
            if scenario is None:
                return await _error(f"ERROR: Scenario '{scenario_id}' not found", scenario_id=scenario_id)
            await _record("ok", scenario_id=scenario_id)
            return CallToolResult(content=_make_text(json.dumps(scenario.model_dump(mode="json"), indent=2)))

        if name == "expand_scenario":
            scenario_id = str(args.get("id", "")).strip()
            if not scenario_id:
                return await _error("ERROR: id is required")
            try:
                _ensure_backend(config)
            except BackendNotConfiguredError as exc:
                return await _error(str(exc), scenario_id=scenario_id, code=exc.code)
            try:
                await aprecompute(repo_root, config=config)
            except Exception as exc:
                return await _error(f"ERROR: {exc}", scenario_id=scenario_id)
            await scenario_store.record_expansion(scenario_id)
            scenario = await scenario_store.get(scenario_id)
            if scenario is None:
                return await _error(f"ERROR: Scenario '{scenario_id}' not found", scenario_id=scenario_id)
            await _record("ok", scenario_id=scenario_id)
            return CallToolResult(content=_make_text(json.dumps(scenario.model_dump(mode="json"), indent=2)))

        if name == "compare_scenarios":
            raw_ids = args.get("ids", [])
            if not isinstance(raw_ids, list) or len(raw_ids) < 2:
                return await _error("ERROR: ids must contain at least 2 scenario ids")
            scenarios: list[Scenario] = []
            for item in raw_ids:
                candidate = await scenario_store.get(str(item))
                if candidate is not None:
                    scenarios.append(candidate)
            if len(scenarios) < 2:
                return await _error("ERROR: could not resolve at least two scenarios")
            sets = {s.id: set(s.entities) for s in scenarios}
            shared_entities = sorted(set.intersection(*(entity_set for entity_set in sets.values())))
            per_scenario = {}
            for s in scenarios:
                others = set().union(*(value for sid, value in sets.items() if sid != s.id))
                per_scenario[s.id] = {
                    "score": s.score,
                    "kind": s.kind,
                    "unique_entities": sorted(sets[s.id] - others),
                    "evidence_keys": [e.key for e in s.evidence[:8]],
                }
            recommendation = max(scenarios, key=lambda s: s.score)
            payload = {
                "shared_entities": shared_entities,
                "scenarios": per_scenario,
                "recommended_scenario_id": recommendation.id,
                "recommended_next_tool": "get_scenario",
            }
            await _record("ok", scenario_id=recommendation.id)
            return CallToolResult(content=_make_text(json.dumps(payload, indent=2)))

        if name == "report_outcome":
            scenario_id = str(args.get("id", "")).strip()
            result = str(args.get("result", "")).strip()
            note = str(args.get("note", "")).strip()
            if not scenario_id:
                return await _error("ERROR: id is required")
            if result not in {"useful", "irrelevant", "partial"}:
                return await _error("ERROR: result must be useful|irrelevant|partial", scenario_id=scenario_id)
            await scenario_store.record_outcome(scenario_id, result)
            try:
                await metrics_store.record_scenario_outcome(scenario_id=scenario_id, result=result, note=note)
            except Exception:
                # Outcome telemetry should not fail the tool response.
                pass
            await _record("ok", scenario_id=scenario_id)
            return CallToolResult(content=_make_text(json.dumps({"ok": True, "scenario_id": scenario_id, "result": result})))

        return await _error(f"ERROR: Unknown tool '{name}'")

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
                server_version="0.2.0",
                capabilities=server.get_capabilities(
                    notification_options=None,
                    experimental_capabilities={},
                ),
            ),
        )


async def run_sse(repo_root: Path, host: str, port: int) -> None:
    """Run the MCP server over HTTP+SSE (for remote/network access)."""
    import uvicorn
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Mount, Route

    server = build_server(repo_root)
    sse_transport = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse_transport.connect_sse(request.scope, request.receive, request._send) as streams:
            await server.run(
                streams[0],
                streams[1],
                InitializationOptions(
                    server_name="vaner",
                    server_version="0.2.0",
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
