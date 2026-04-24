# SPDX-License-Identifier: Apache-2.0
"""Typed HTTP client for the Vaner daemon's REST surface.

This is the **internal** client — the one Vaner's own tools (CLI, cockpit
server-side helpers, the MCP subprocess when forwarding predictions tools)
use to reach the daemon over HTTP. It is not an LLM client and not an MCP
client; despite living under ``src/vaner/clients/`` alongside ``openai.py``
and ``ollama.py``, those serve LLM backends while this module exists so
every in-tree Vaner surface can talk to the daemon through one typed
contract rather than hand-rolled httpx calls each time.

Public contract (keep stable across 0.8.x):
    get_status() -> dict
    get_predictions_active() -> dict                          # {"predictions": [...]}
    get_prediction(id, *, include=["draft", "briefing", ...])  # may include content
    adopt_prediction(id) -> Resolution                         # parsed pydantic model
    resolve(query, *, context=..., include_briefing=...,
            include_predicted_response=...) -> Resolution      # 0.8.1: MCP/HTTP forward

Errors surface as exceptions so callers can distinguish "daemon is down"
from "daemon rejected the request". Fire-and-forget style callers
(the MCP forwarder) wrap these calls in try/except themselves.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from vaner.mcp.contracts import Resolution

DEFAULT_BASE_URL = "http://127.0.0.1:8473"
DEFAULT_TIMEOUT = 5.0


class VanerDaemonError(Exception):
    """Base class for daemon communication errors."""


class VanerDaemonUnavailable(VanerDaemonError):
    """Raised when the daemon is unreachable (connection refused, timeout,
    5xx) OR when the daemon reports its own engine is unavailable (409).

    Both cases are recoverable by the caller — start the daemon or wait for
    its first cycle.
    """


class VanerDaemonNotFound(VanerDaemonError):
    """Raised when the daemon returned 404 for a specific resource.

    Distinct from ``VanerDaemonUnavailable`` — the daemon is up but the
    specific prediction/resource doesn't exist (e.g. stale id, wrong repo).
    """


class VanerDaemonClient:
    """HTTP client for ``http://127.0.0.1:8473``-style daemon targets.

    Construct once and reuse; each call acquires an ``httpx.AsyncClient``
    via an async context manager unless a pre-built client is injected
    (tests). Instances are safe to share across coroutines because each
    call opens its own short-lived connection.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            yield client

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    async def get_status(self) -> dict[str, Any]:
        """GET /status — returns the daemon's health + config snapshot."""
        async with self._session() as client:
            try:
                response = await client.get(f"{self._base}/status")
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                raise VanerDaemonUnavailable(f"daemon unreachable at {self._base}: {exc}") from exc
            if response.status_code >= 500:
                raise VanerDaemonUnavailable(f"daemon returned {response.status_code}")
            response.raise_for_status()
            return response.json()

    async def get_predictions_active(self) -> dict[str, Any]:
        """GET /predictions/active — snapshot of currently-active PredictedPrompts.

        Returns the daemon's response body verbatim: ``{"predictions": [...]}``.
        An empty list is a valid success (not an error).

        A 404 response is treated as :class:`VanerDaemonUnavailable` — that
        means the daemon is running but predates the Phase 4 endpoints, which
        is operationally the same as the engine being unavailable.
        """
        async with self._session() as client:
            try:
                response = await client.get(f"{self._base}/predictions/active")
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                raise VanerDaemonUnavailable(f"daemon unreachable at {self._base}: {exc}") from exc
            if response.status_code == 404:
                raise VanerDaemonUnavailable(f"daemon at {self._base} does not expose /predictions/active (pre-0.8.0 daemon?)")
            if response.status_code >= 500:
                raise VanerDaemonUnavailable(f"daemon returned {response.status_code}")
            response.raise_for_status()
            return response.json()

    async def get_prediction(
        self,
        prediction_id: str,
        *,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        """GET /predictions/{id} — one prediction's detail.

        ``include`` optionally requests inline artifact content. Supported
        values are ``"draft"``, ``"briefing"``, ``"thinking"``. When supplied,
        the response carries an ``artifacts_content`` dict alongside the
        summary.
        """
        params: dict[str, Any] = {}
        if include:
            params["include"] = ",".join(include)
        async with self._session() as client:
            try:
                response = await client.get(
                    f"{self._base}/predictions/{prediction_id}",
                    params=params,
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                raise VanerDaemonUnavailable(f"daemon unreachable at {self._base}: {exc}") from exc
            if response.status_code == 404:
                raise VanerDaemonNotFound(f"no such prediction: {prediction_id}")
            if response.status_code >= 500:
                raise VanerDaemonUnavailable(f"daemon returned {response.status_code}")
            response.raise_for_status()
            return response.json()

    async def adopt_prediction(self, prediction_id: str) -> Resolution:
        """POST /predictions/{id}/adopt — returns a parsed Resolution.

        Error mapping:
            - 400 (whitespace id etc.) → ``ValueError``
            - 404 (unknown id) → :class:`VanerDaemonNotFound`
            - 409 (engine unavailable) → :class:`VanerDaemonUnavailable`
            - 5xx / transport → :class:`VanerDaemonUnavailable`
        """
        async with self._session() as client:
            try:
                response = await client.post(f"{self._base}/predictions/{prediction_id}/adopt")
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                raise VanerDaemonUnavailable(f"daemon unreachable at {self._base}: {exc}") from exc
            if response.status_code == 400:
                try:
                    body = response.json()
                except Exception:
                    body = {}
                raise ValueError(body.get("message", "invalid adopt request"))
            if response.status_code == 404:
                raise VanerDaemonNotFound(f"no such prediction: {prediction_id}")
            if response.status_code == 409:
                try:
                    body = response.json()
                except Exception:
                    body = {}
                raise VanerDaemonUnavailable(body.get("message", "daemon engine unavailable"))
            if response.status_code >= 500:
                raise VanerDaemonUnavailable(f"daemon returned {response.status_code}")
            response.raise_for_status()
            return Resolution.model_validate(response.json())

    async def resolve(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        include_briefing: bool = False,
        include_predicted_response: bool = False,
    ) -> Resolution:
        """POST /resolve — forward a query to the daemon's engine.resolve_query.

        The daemon wraps :meth:`VanerEngine.resolve_query` so the MCP
        surface ``vaner.resolve`` can delegate to the engine without
        running its own scenario-store path (0.8.1 convergence — see
        :func:`VanerEngine.resolve_query` for the canonical query →
        Resolution contract).

        Error mapping:
            - 400 (empty query) → ``ValueError``
            - 409 (engine unavailable) → :class:`VanerDaemonUnavailable`
            - 5xx / transport → :class:`VanerDaemonUnavailable`
        """
        payload: dict[str, Any] = {
            "query": query,
            "include_briefing": include_briefing,
            "include_predicted_response": include_predicted_response,
        }
        if context is not None:
            payload["context"] = context
        async with self._session() as client:
            try:
                response = await client.post(f"{self._base}/resolve", json=payload)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                raise VanerDaemonUnavailable(f"daemon unreachable at {self._base}: {exc}") from exc
            if response.status_code == 400:
                try:
                    body = response.json()
                except Exception:
                    body = {}
                raise ValueError(body.get("message", "invalid resolve request"))
            if response.status_code == 409:
                try:
                    body = response.json()
                except Exception:
                    body = {}
                raise VanerDaemonUnavailable(body.get("message", "daemon engine unavailable"))
            if response.status_code >= 500:
                raise VanerDaemonUnavailable(f"daemon returned {response.status_code}")
            response.raise_for_status()
            return Resolution.model_validate(response.json())
