# SPDX-License-Identifier: Apache-2.0
"""End-to-end request metrics for the Vaner proxy.

Captures the full round-trip latency that an end user experiences:

    t0  -- request received by proxy
    t1  -- context assembled by VanerEngine (end of aquery)
    t2  -- enriched payload sent to backend LLM
    t3  -- first token received from backend (streaming only)
    t4  -- response complete / last byte received

Derived metrics:

    context_retrieval_ms  = t1 - t0  (Vaner's overhead)
    llm_first_token_ms    = t3 - t2  (time-to-first-token; streaming only)
    llm_total_ms          = t4 - t2  (total LLM generation time)
    total_e2e_ms          = t4 - t0  (wall-clock for the full request)
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import aiosqlite


@dataclass
class RequestMetrics:
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)

    # Timing checkpoints (seconds since epoch; 0 = not yet recorded)
    t0_received: float = 0.0
    t1_context_ready: float = 0.0
    t2_forwarded: float = 0.0
    t3_first_token: float = 0.0
    t4_complete: float = 0.0

    # Context metadata
    cache_tier: str = "unknown"  # "full_hit" | "partial_hit" | "miss"
    partial_similarity: float = 0.0  # 0-1 similarity score for partial hits
    context_tokens: int = 0  # tokens of context injected
    prompt_tokens: int = 0  # tokens in user prompt
    is_stream: bool = False

    # Derived metrics (populated by finalize())
    context_retrieval_ms: float = 0.0
    llm_first_token_ms: float = 0.0
    llm_total_ms: float = 0.0
    total_e2e_ms: float = 0.0

    def finalize(self) -> None:
        """Compute derived ms values from recorded checkpoints."""
        if self.t1_context_ready and self.t0_received:
            self.context_retrieval_ms = (self.t1_context_ready - self.t0_received) * 1000.0
        if self.t3_first_token and self.t2_forwarded:
            self.llm_first_token_ms = (self.t3_first_token - self.t2_forwarded) * 1000.0
        if self.t4_complete and self.t2_forwarded:
            self.llm_total_ms = (self.t4_complete - self.t2_forwarded) * 1000.0
        if self.t4_complete and self.t0_received:
            self.total_e2e_ms = (self.t4_complete - self.t0_received) * 1000.0

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "timestamp": self.timestamp,
            "cache_tier": self.cache_tier,
            "partial_similarity": self.partial_similarity,
            "context_tokens": self.context_tokens,
            "prompt_tokens": self.prompt_tokens,
            "is_stream": self.is_stream,
            "context_retrieval_ms": self.context_retrieval_ms,
            "llm_first_token_ms": self.llm_first_token_ms,
            "llm_total_ms": self.llm_total_ms,
            "total_e2e_ms": self.total_e2e_ms,
        }


class MetricsStore:
    """SQLite-backed store for per-request metrics."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS request_metrics (
                    request_id TEXT PRIMARY KEY,
                    timestamp REAL NOT NULL,
                    cache_tier TEXT NOT NULL,
                    partial_similarity REAL NOT NULL DEFAULT 0.0,
                    context_tokens INTEGER NOT NULL DEFAULT 0,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    is_stream INTEGER NOT NULL DEFAULT 0,
                    context_retrieval_ms REAL NOT NULL DEFAULT 0.0,
                    llm_first_token_ms REAL NOT NULL DEFAULT 0.0,
                    llm_total_ms REAL NOT NULL DEFAULT 0.0,
                    total_e2e_ms REAL NOT NULL DEFAULT 0.0,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            await db.commit()

    async def record(self, m: RequestMetrics) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO request_metrics
                    (request_id, timestamp, cache_tier, partial_similarity,
                     context_tokens, prompt_tokens, is_stream,
                     context_retrieval_ms, llm_first_token_ms, llm_total_ms,
                     total_e2e_ms, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    m.request_id,
                    m.timestamp,
                    m.cache_tier,
                    m.partial_similarity,
                    m.context_tokens,
                    m.prompt_tokens,
                    int(m.is_stream),
                    m.context_retrieval_ms,
                    m.llm_first_token_ms,
                    m.llm_total_ms,
                    m.total_e2e_ms,
                    json.dumps({}),
                ),
            )
            await db.commit()

    async def recent(self, limit: int = 100) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM request_metrics ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def summary(self, last_n: int = 1000) -> dict:
        """Aggregate statistics over the last *last_n* requests."""
        rows = await self.recent(last_n)
        if not rows:
            return {"count": 0}

        def _avg(key: str) -> float:
            vals = [r[key] for r in rows if r[key] > 0]
            return round(sum(vals) / len(vals), 2) if vals else 0.0

        def _p95(key: str) -> float:
            vals = sorted(r[key] for r in rows if r[key] > 0)
            if not vals:
                return 0.0
            idx = max(0, int(len(vals) * 0.95) - 1)
            return round(vals[idx], 2)

        tiers = {}
        for r in rows:
            tiers[r["cache_tier"]] = tiers.get(r["cache_tier"], 0) + 1

        return {
            "count": len(rows),
            "cache_tiers": tiers,
            "context_retrieval_ms": {"avg": _avg("context_retrieval_ms"), "p95": _p95("context_retrieval_ms")},
            "llm_first_token_ms": {"avg": _avg("llm_first_token_ms"), "p95": _p95("llm_first_token_ms")},
            "llm_total_ms": {"avg": _avg("llm_total_ms"), "p95": _p95("llm_total_ms")},
            "total_e2e_ms": {"avg": _avg("total_e2e_ms"), "p95": _p95("total_e2e_ms")},
            "avg_context_tokens": _avg("context_tokens"),
        }
