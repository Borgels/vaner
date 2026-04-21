# SPDX-License-Identifier: Apache-2.0

"""Multi-endpoint LLM pool for exploration parallelism (PR #135 P5).

Vaner's exploration loop ships one scenario to one LLM at a time. With
``exploration_concurrency`` wired to a semaphore (P1), multiple scenarios run
concurrently — but they all hit the same endpoint. This module provides a
small pool that dispatches calls across multiple endpoints via weighted
round-robin, with per-endpoint health tracking.

Design constraints:

- **Drop-in** for the existing ``LLMCallable`` callable protocol
  (``async def __call__(prompt: str) -> str``). The engine does not need to
  learn a new interface.
- **Round-robin, not routed.** Scenarios are not sticky to endpoints. The
  pool's purpose is throughput via parallelism, not per-scenario affinity.
- **Health tracking, not SLA.** The goal is "don't keep hitting a broken
  endpoint while N-1 healthy ones sit idle." After three consecutive failures
  an endpoint goes into a 60-second cooldown; on the next attempt after the
  cooldown it's tried again (half-open) and either recovers or re-arms the
  timer.
- **Asyncio-safe.** All state mutations happen under a single ``asyncio.Lock``
  so concurrent callers don't corrupt the round-robin cursor or the health
  counters.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vaner.models.config import ExplorationEndpoint


# Health policy constants. Kept module-level (not config) until we have
# evidence a real deployment needs to tune them.
FAILURE_COOLDOWN_SECONDS = 60.0
FAILURE_THRESHOLD = 3

LLMCallable = Callable[[str], Awaitable[str]]


@dataclass(slots=True)
class EndpointHealth:
    """Per-endpoint health counters maintained by the pool."""

    consecutive_failures: int = 0
    last_success_at: float = 0.0
    last_failure_at: float = 0.0
    total_calls: int = 0
    total_failures: int = 0
    cooldown_until: float = 0.0

    def as_dict(self) -> dict[str, float | int]:
        """Snapshot for status readers (cockpit, tests)."""
        return {
            "consecutive_failures": self.consecutive_failures,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "total_calls": self.total_calls,
            "total_failures": self.total_failures,
            "cooldown_until": self.cooldown_until,
        }


@dataclass(slots=True)
class _PoolEntry:
    """One live entry in the pool."""

    url: str
    model: str
    weight: float
    client: LLMCallable
    health: EndpointHealth = field(default_factory=EndpointHealth)


class ExplorationEndpointPool:
    """Weighted round-robin dispatcher across multiple exploration endpoints.

    Implements the ``LLMCallable`` protocol: ``await pool(prompt) -> str``.
    Internally tracks per-endpoint health and skips endpoints in cooldown.

    When *every* endpoint is in cooldown the pool still attempts one (the
    least-recently-failed) rather than refusing the call — pragmatic fallback
    so the caller sees a real error, not a spurious pool-unavailable one.
    """

    def __init__(self, entries: list[_PoolEntry]) -> None:
        if not entries:
            raise ValueError("ExplorationEndpointPool requires at least one endpoint")
        self._entries = entries
        self._lock = asyncio.Lock()
        # Expanded index: each entry appears weight times, for a simple
        # weighted round-robin via a rotating cursor.
        self._expanded_index: list[int] = []
        for idx, entry in enumerate(entries):
            count = max(1, int(round(entry.weight)))
            self._expanded_index.extend([idx] * count)
        self._cursor = 0

    @classmethod
    def from_endpoints(
        cls,
        endpoints: list[ExplorationEndpoint],
        *,
        env_getter: Callable[[str], str | None] | None = None,
        timeout: float = 120.0,
    ) -> ExplorationEndpointPool:
        """Build a pool from config entries, wiring up the right client per backend.

        ``env_getter`` is injected for testability so unit tests can supply
        fake env vars without mutating ``os.environ``.
        """
        import os

        from vaner.clients.ollama import ollama_llm
        from vaner.clients.openai import openai_llm

        _env = env_getter or os.environ.get
        live: list[_PoolEntry] = []
        for ep in endpoints:
            if ep.weight <= 0:
                continue
            if ep.backend == "ollama":
                client: LLMCallable = ollama_llm(model=ep.model, base_url=ep.url, timeout=timeout)
            else:
                api_key = (_env(ep.api_key_env) if ep.api_key_env else None) or "EMPTY"
                client = openai_llm(model=ep.model, api_key=api_key, base_url=ep.url, timeout=timeout)
            live.append(_PoolEntry(url=ep.url, model=ep.model, weight=ep.weight, client=client))
        if not live:
            raise ValueError("no live endpoints after filtering zero-weight entries")
        return cls(live)

    async def _pick(self) -> _PoolEntry:
        """Choose the next endpoint via weighted round-robin, skipping cooldowns."""
        now = time.monotonic()
        async with self._lock:
            n = len(self._expanded_index)
            # Try up to `n` advances looking for a healthy endpoint.
            for _ in range(n):
                idx = self._expanded_index[self._cursor % n]
                self._cursor = (self._cursor + 1) % n
                entry = self._entries[idx]
                if entry.health.cooldown_until <= now:
                    return entry
            # Every endpoint is in cooldown. Fall back to the one whose
            # cooldown expires soonest — giving it a chance to recover rather
            # than hard-failing the call.
            best = min(self._entries, key=lambda e: e.health.cooldown_until)
            return best

    async def __call__(self, prompt: str) -> str:
        entry = await self._pick()
        try:
            result = await entry.client(prompt)
        except Exception:
            await self._record_failure(entry)
            raise
        await self._record_success(entry)
        return result

    async def _record_success(self, entry: _PoolEntry) -> None:
        async with self._lock:
            entry.health.consecutive_failures = 0
            entry.health.cooldown_until = 0.0
            entry.health.last_success_at = time.monotonic()
            entry.health.total_calls += 1

    async def _record_failure(self, entry: _PoolEntry) -> None:
        async with self._lock:
            entry.health.consecutive_failures += 1
            entry.health.last_failure_at = time.monotonic()
            entry.health.total_calls += 1
            entry.health.total_failures += 1
            if entry.health.consecutive_failures >= FAILURE_THRESHOLD:
                entry.health.cooldown_until = time.monotonic() + FAILURE_COOLDOWN_SECONDS

    def snapshot(self) -> list[dict[str, object]]:
        """Return a point-in-time health summary for status/debug surfaces."""
        return [
            {
                "url": entry.url,
                "model": entry.model,
                "weight": entry.weight,
                "health": entry.health.as_dict(),
            }
            for entry in self._entries
        ]
