"""Vaner proxy — intercepts LLM API calls and injects artifact context."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
from aiohttp import web

from vaner_tools.artefact_store import list_artefacts

logger = logging.getLogger("vaner.proxy")


def _make_context_string(artefacts: list) -> str:
    top5 = sorted(artefacts, key=lambda a: a.generated_at, reverse=True)[:5]
    parts = [f"### {a.source_path}\n{a.content}" for a in top5]
    return "## Vaner context\n" + "\n---\n".join(parts) + "\n---"


def _inject_context(messages: list[dict], context: str) -> list[dict]:
    """Prepend context into system message, or insert one if absent."""
    for msg in messages:
        if msg.get("role") == "system":
            msg["content"] = context + "\n" + msg["content"]
            return messages
    return [{"role": "system", "content": context}] + messages


class VanerProxy:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 11435,
        upstream: str = "http://localhost:11434",
        timeout: int = 120,
    ):
        self._host = host
        self._port = port
        self._upstream = upstream.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._runner: web.AppRunner | None = None
        self._running = False

    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/api/chat", self._handle_chat)
        app.router.add_post("/v1/chat/completions", self._handle_chat)
        app.router.add_route("*", "/{tail:.*}", self._handle_passthrough)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        self._running = True
        logger.info("VanerProxy listening on %s:%d → %s", self._host, self._port, self._upstream)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
        self._running = False
        logger.info("VanerProxy stopped")

    async def _get_context(self) -> str:
        try:
            artefacts = list_artefacts(kind="file_summary")
            if not artefacts:
                return ""
            return _make_context_string(artefacts)
        except Exception:
            return ""

    async def _handle_chat(self, request: web.Request) -> web.Response:
        try:
            data: dict[str, Any] = await request.json()
        except Exception:
            return await self._forward_raw(request)

        context = await self._get_context()
        if context and "messages" in data and isinstance(data["messages"], list):
            data["messages"] = _inject_context(data["messages"], context)

        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(
                    f"{self._upstream}{request.path}",
                    json=data,
                    headers={k: v for k, v in request.headers.items()
                             if k.lower() not in ("host", "content-length")},
                ) as resp:
                    body = await resp.read()
                    return web.Response(
                        status=resp.status,
                        body=body,
                        content_type=resp.content_type,
                    )
        except aiohttp.ClientConnectorError:
            return web.Response(status=502, text="Upstream unavailable")
        except Exception as exc:
            logger.error("Proxy error: %s", exc)
            return web.Response(status=502, text=str(exc))

    async def _handle_passthrough(self, request: web.Request) -> web.Response:
        return await self._forward_raw(request)

    async def _forward_raw(self, request: web.Request) -> web.Response:
        try:
            body = await request.read()
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.request(
                    method=request.method,
                    url=f"{self._upstream}{request.path}",
                    headers={k: v for k, v in request.headers.items()
                             if k.lower() not in ("host", "content-length")},
                    data=body,
                ) as resp:
                    return web.Response(
                        status=resp.status,
                        body=await resp.read(),
                        content_type=resp.content_type,
                    )
        except aiohttp.ClientConnectorError:
            return web.Response(status=502, text="Upstream unavailable")
        except Exception as exc:
            return web.Response(status=502, text=str(exc))
