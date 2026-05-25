# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

from vaner.integrations.mcp_consumer.safety import McpToolDescriptor, ReadOnlyToolPolicy, ToolSafetyDecision


@dataclass(frozen=True, slots=True)
class McpToolCallResult:
    ok: bool
    payload: Any = None
    error: str = ""
    safety_reason: str = ""


class McpConsumerClient:
    """Managed MCP client for proactive read-only external-state calls."""

    def __init__(
        self,
        *,
        server_name: str,
        transport: str,
        command: str = "",
        args: list[str] | None = None,
        url: str = "",
        env: dict[str, str] | None = None,
        timeout_ms: int = 10000,
        policy: ReadOnlyToolPolicy,
    ) -> None:
        self.server_name = server_name
        self.transport = transport
        self.command = command
        self.args = list(args or [])
        self.url = url
        self.env = dict(env or {})
        self.timeout_ms = timeout_ms
        self.policy = policy
        self._session: Any = None
        self._stdio_cm: Any = None
        self._streams_cm: Any = None
        self._tool_descriptors: dict[str, McpToolDescriptor] = {}

    async def start(self) -> None:
        try:
            from mcp import ClientSession
            from mcp.client.stdio import StdioServerParameters, stdio_client
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError as exc:  # pragma: no cover - optional dependency guard
            raise RuntimeError("mcp consumer support requires the 'mcp' optional dependency") from exc

        if self.transport == "stdio":
            params = StdioServerParameters(command=self.command, args=self.args, env=_merged_env(self.env))
            self._stdio_cm = stdio_client(params)
            read, write = await self._stdio_cm.__aenter__()
        elif self.transport == "streamable_http":
            self._streams_cm = streamablehttp_client(self.url)
            read, write, _ = await self._streams_cm.__aenter__()
        else:
            raise ValueError(f"unsupported MCP consumer transport: {self.transport}")
        try:
            self._session = ClientSession(read, write)
            await self._session.__aenter__()
            await self._session.initialize()
            decisions = await self.refresh_tools()
            missing = sorted(self.policy.allowed_tools - set(self._tool_descriptors))
            if missing:
                raise RuntimeError(f"MCP consumer {self.server_name} allowlist references unknown tools: {', '.join(missing)}")
            unsafe = [decision for decision in decisions if decision.tool_name in self.policy.allowed_tools and not decision.allowed]
            if unsafe:
                details = ", ".join(f"{decision.tool_name}:{decision.reason}" for decision in unsafe)
                raise RuntimeError(f"MCP consumer {self.server_name} allowlist contains unsafe tools: {details}")
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        if self._session is not None:
            await self._session.__aexit__(None, None, None)
            self._session = None
        if self._stdio_cm is not None:
            await self._stdio_cm.__aexit__(None, None, None)
            self._stdio_cm = None
        if self._streams_cm is not None:
            await self._streams_cm.__aexit__(None, None, None)
            self._streams_cm = None

    async def refresh_tools(self) -> list[ToolSafetyDecision]:
        if self._session is None:
            raise RuntimeError("MCP consumer client is not started")
        response = await asyncio.wait_for(self._session.list_tools(), timeout=self.timeout_ms / 1000)
        descriptors: dict[str, McpToolDescriptor] = {}
        for tool in getattr(response, "tools", []) or []:
            annotations = getattr(tool, "annotations", None)
            if annotations is None:
                annotation_payload: dict[str, Any] = {}
            elif hasattr(annotations, "model_dump"):
                annotation_payload = annotations.model_dump(exclude_none=True)
            else:
                annotation_payload = dict(annotations)
            descriptors[str(getattr(tool, "name", ""))] = McpToolDescriptor(
                name=str(getattr(tool, "name", "")),
                annotations=annotation_payload,
            )
        self._tool_descriptors = descriptors
        return self.policy.validate_all(list(descriptors.values()))

    async def call_tool(self, tool_name: str, args: dict[str, Any] | None = None) -> McpToolCallResult:
        if self._session is None:
            return McpToolCallResult(ok=False, error="client_not_started")
        descriptor = self._tool_descriptors.get(tool_name, McpToolDescriptor(name=tool_name))
        decision = self.policy.validate(descriptor)
        if not decision.allowed:
            return McpToolCallResult(ok=False, error="tool_blocked", safety_reason=decision.reason)
        try:
            payload = await asyncio.wait_for(self._session.call_tool(tool_name, args or {}), timeout=self.timeout_ms / 1000)
        except Exception as exc:  # pragma: no cover - transport boundary
            return McpToolCallResult(ok=False, error=str(exc), safety_reason=decision.reason)
        return McpToolCallResult(ok=True, payload=payload, safety_reason=decision.reason)


async def discover_mcp_tools(
    *,
    transport: str,
    command: str = "",
    args: list[str] | None = None,
    url: str = "",
    env: dict[str, str] | None = None,
    timeout_ms: int = 10000,
) -> list[McpToolDescriptor]:
    """Connect to an MCP server long enough to list tools.

    Discovery never calls provider tools and intentionally does not apply a
    consumer allowlist. The caller is responsible for running the returned
    descriptors through :class:`ReadOnlyToolPolicy` before persisting any tool
    as callable external state.
    """

    try:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError as exc:  # pragma: no cover - optional dependency guard
        raise RuntimeError("mcp consumer support requires the 'mcp' optional dependency") from exc

    stdio_cm: Any = None
    streams_cm: Any = None
    session: Any = None
    try:
        if transport == "stdio":
            params = StdioServerParameters(command=command, args=list(args or []), env=_merged_env(env))
            stdio_cm = stdio_client(params)
            read, write = await stdio_cm.__aenter__()
        elif transport == "streamable_http":
            streams_cm = streamablehttp_client(url)
            read, write, _ = await streams_cm.__aenter__()
        else:
            raise ValueError(f"unsupported MCP consumer transport: {transport}")

        session = ClientSession(read, write)
        await session.__aenter__()
        await session.initialize()
        response = await asyncio.wait_for(session.list_tools(), timeout=timeout_ms / 1000)
        descriptors: list[McpToolDescriptor] = []
        for tool in getattr(response, "tools", []) or []:
            annotations = getattr(tool, "annotations", None)
            if annotations is None:
                annotation_payload: dict[str, Any] = {}
            elif hasattr(annotations, "model_dump"):
                annotation_payload = annotations.model_dump(exclude_none=True)
            else:
                annotation_payload = dict(annotations)
            name = str(getattr(tool, "name", "")).strip()
            if name:
                descriptors.append(McpToolDescriptor(name=name, annotations=annotation_payload))
        return descriptors
    finally:
        if session is not None:
            await session.__aexit__(None, None, None)
        if stdio_cm is not None:
            await stdio_cm.__aexit__(None, None, None)
        if streams_cm is not None:
            await streams_cm.__aexit__(None, None, None)


def _merged_env(env: dict[str, str] | None) -> dict[str, str]:
    merged = dict(os.environ)
    merged.update(dict(env or {}))
    return merged
