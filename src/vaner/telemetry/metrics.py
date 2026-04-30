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

import asyncio
import json
import math
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

from vaner.models.cost import CostLedgerEntry, PredictionCostSummary, PricingSnapshot, TurnCostSummary

_LEAD_TIME_BUCKETS: tuple[tuple[str, float], ...] = (
    ("lt_1s", 1.0),
    ("lt_3s", 3.0),
    ("lt_10s", 10.0),
    ("lt_30s", 30.0),
    ("lt_60s", 60.0),
    ("lt_300s", 300.0),
    ("lt_900s", 900.0),
    ("gte_900s", float("inf")),
)

# 0.8.7 hardening (H2): coarse buckets for composer-event length_chars in
# telemetry rows. Storing exact char counts alongside even an irreversible
# hash gives a post-compromise adversary the ranged input domain for a
# brute-force sha256 inversion. The buckets keep the histogram useful for
# debugging without pinning short prompts to enumerable bands.
_COMPOSER_LENGTH_BUCKETS: tuple[tuple[str, int], ...] = (
    ("0_9", 10),
    ("10_49", 50),
    ("50_249", 250),
    ("250_999", 1_000),
    ("1000_4999", 5_000),
    ("5000_plus", -1),
)


def _length_bucket(length_chars: int) -> str:
    n = max(0, int(length_chars))
    for label, ceiling in _COMPOSER_LENGTH_BUCKETS:
        if ceiling < 0 or n < ceiling:
            return label
    return _COMPOSER_LENGTH_BUCKETS[-1][0]


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
    injected_context_tokens: int = 0
    expected_incremental_primary_cost_usd: float = 0.0
    primary_llm_input_tokens: int = 0
    primary_llm_output_tokens: int = 0
    primary_llm_thinking_tokens: int = 0
    primary_llm_cost_usd: float = 0.0
    primary_llm_usage_known: bool = False
    total_known_cloud_cost_usd: float = 0.0
    total_estimated_cloud_cost_usd: float = 0.0
    pricing_snapshot_id: str = "unknown-zero"
    usage_source_summary: str = "unknown"
    primary_usage_record_error: str = ""

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "timestamp": self.timestamp,
            "cache_tier": self.cache_tier,
            "partial_similarity": self.partial_similarity,
            "context_tokens": self.context_tokens,
            "prompt_tokens": self.prompt_tokens,
            "is_stream": self.is_stream,
            "injected_context_tokens": self.injected_context_tokens,
            "expected_incremental_primary_cost_usd": self.expected_incremental_primary_cost_usd,
            "primary_llm_input_tokens": self.primary_llm_input_tokens,
            "primary_llm_output_tokens": self.primary_llm_output_tokens,
            "primary_llm_thinking_tokens": self.primary_llm_thinking_tokens,
            "primary_llm_cost_usd": self.primary_llm_cost_usd,
            "primary_llm_usage_known": self.primary_llm_usage_known,
            "total_known_cloud_cost_usd": self.total_known_cloud_cost_usd,
            "total_estimated_cloud_cost_usd": self.total_estimated_cloud_cost_usd,
            "pricing_snapshot_id": self.pricing_snapshot_id,
            "usage_source_summary": self.usage_source_summary,
            "context_retrieval_ms": self.context_retrieval_ms,
            "llm_first_token_ms": self.llm_first_token_ms,
            "llm_total_ms": self.llm_total_ms,
            "total_e2e_ms": self.total_e2e_ms,
        }


class MetricsStore:
    """SQLite-backed store for per-request metrics."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        # 0.8.7 hardening: serialize concurrent ``initialize()`` calls
        # within the same process. SQLite WAL setup takes an exclusive
        # lock on the WAL file's first write — two parallel async tasks
        # creating fresh connections will race that step, and the
        # connection-time ``timeout=`` only kicks in AFTER the lock
        # attempt fails. The asyncio lock guarantees only one initialize
        # writes the schema; the second waits and finds it complete.
        # Production runs ``initialize()`` once at daemon-lifespan start;
        # this lock is defense-in-depth for tests + future call sites.
        self._init_lock = asyncio.Lock()
        self._initialized = False

    @staticmethod
    async def _ensure_column(db: aiosqlite.Connection, table: str, column: str, column_def: str) -> None:
        cursor = await db.execute(f"PRAGMA table_info({table})")
        rows = await cursor.fetchall()
        names = {str(row[1]) for row in rows}
        if column in names:
            return
        # 0.8.7 hardening (H3): two concurrent first-time initialize()
        # calls (e.g. two ingest requests on a fresh DB) can both pass
        # the PRAGMA check, both attempt ALTER, and the second errors
        # with "duplicate column name". The post-state is correct (the
        # column exists once) — swallow the race and trust the PRAGMA
        # snapshot on the next call. Any other OperationalError still
        # propagates so genuine schema problems surface loudly.
        try:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")
        except aiosqlite.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise

    async def initialize(self) -> None:
        # 0.8.7 hardening: in-process serialization. Two concurrent
        # initialize() calls in the same daemon raced on SQLite WAL
        # setup before this lock was added. The ``_initialized``
        # short-circuit means the second caller does no SQL work; the
        # first holds the lock through the full DDL pass. timeout=5.0
        # + PRAGMA busy_timeout below remain as belt-and-braces for
        # cross-process or cross-MetricsStore-instance contention.
        async with self._init_lock:
            if self._initialized:
                return
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            async with aiosqlite.connect(self.db_path, timeout=5.0) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA busy_timeout = 5000")
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
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS shadow_comparisons (
                        shadow_pair_id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL,
                        timestamp REAL NOT NULL,
                        with_context_total_ms REAL NOT NULL,
                        without_context_total_ms REAL NOT NULL,
                        with_context_tokens INTEGER NOT NULL,
                        without_context_tokens INTEGER NOT NULL,
                        latency_delta_ms REAL NOT NULL,
                        token_delta INTEGER NOT NULL,
                        metadata_json TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS integration_usage (
                        mode TEXT PRIMARY KEY,
                        count INTEGER NOT NULL DEFAULT 0,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS mcp_tool_calls (
                        id TEXT PRIMARY KEY,
                        tool_name TEXT NOT NULL,
                        status TEXT NOT NULL,
                        latency_ms REAL NOT NULL,
                        scenario_id TEXT,
                        skill TEXT,
                        timestamp REAL NOT NULL
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS scenario_outcomes (
                        id TEXT PRIMARY KEY,
                        scenario_id TEXT NOT NULL,
                        result TEXT NOT NULL,
                        note TEXT NOT NULL DEFAULT '',
                        skill TEXT,
                        timestamp REAL NOT NULL
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS memory_quality_counters (
                        name TEXT PRIMARY KEY,
                        value REAL NOT NULL DEFAULT 0,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS prediction_events (
                        id TEXT PRIMARY KEY,
                        timestamp REAL NOT NULL,
                        top1_label TEXT NOT NULL,
                        top1_confidence REAL NOT NULL,
                        probs_json TEXT NOT NULL
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS draft_events (
                        id TEXT PRIMARY KEY,
                        timestamp REAL NOT NULL,
                        status TEXT NOT NULL,
                        predicted_prompt_similarity REAL NOT NULL DEFAULT 0.0,
                        evidence_overlap REAL NOT NULL DEFAULT 0.0,
                        answer_reuse_ratio REAL NOT NULL DEFAULT 0.0,
                        directional_correct INTEGER NOT NULL DEFAULT 0,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        event_type TEXT NOT NULL DEFAULT 'legacy_draft'
                    )
                    """
                )
                # 0.8.7 WS6: idempotent ALTER for pre-0.8.7 DBs that already
                # had ``draft_events`` without an ``event_type`` column. Existing
                # rows default to 'legacy_draft' so the post-migration view of
                # the existing 0.8.6 dashboard counters is byte-identical.
                await self._ensure_column(db, "draft_events", "event_type", "TEXT NOT NULL DEFAULT 'legacy_draft'")
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS counterfactual_misses (
                        id TEXT PRIMARY KEY,
                        timestamp REAL NOT NULL,
                        prompt TEXT NOT NULL,
                        miss_type TEXT NOT NULL,
                        helpful_context_json TEXT NOT NULL DEFAULT '[]',
                        wasted_branches_json TEXT NOT NULL DEFAULT '[]',
                        metadata_json TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pricing_snapshots (
                        pricing_snapshot_id TEXT PRIMARY KEY,
                        source TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        effective_at REAL NOT NULL,
                        content_hash TEXT NOT NULL DEFAULT '',
                        raw_snapshot_json TEXT NOT NULL DEFAULT '{}',
                        notes TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS llm_usage_events (
                        id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL DEFAULT '',
                        turn_id TEXT NOT NULL DEFAULT '',
                        prediction_id TEXT NOT NULL DEFAULT '',
                        cycle_id TEXT NOT NULL DEFAULT '',
                        client_id TEXT NOT NULL DEFAULT '',
                        app TEXT NOT NULL DEFAULT '',
                        project TEXT NOT NULL DEFAULT '',
                        workspace TEXT NOT NULL DEFAULT '',
                        provider TEXT NOT NULL DEFAULT '',
                        model TEXT NOT NULL DEFAULT '',
                        endpoint TEXT NOT NULL DEFAULT '',
                        call_role TEXT NOT NULL,
                        local_or_cloud TEXT NOT NULL DEFAULT 'unknown',
                        usage_source TEXT NOT NULL DEFAULT 'unknown',
                        usage_estimated INTEGER NOT NULL DEFAULT 1,
                        prompt_tokens INTEGER NOT NULL DEFAULT 0,
                        completion_tokens INTEGER NOT NULL DEFAULT 0,
                        thinking_tokens INTEGER NOT NULL DEFAULT 0,
                        cached_input_tokens INTEGER NOT NULL DEFAULT 0,
                        total_tokens INTEGER NOT NULL DEFAULT 0,
                        provider_usage_raw TEXT NOT NULL DEFAULT '{}',
                        pricing_source TEXT NOT NULL DEFAULT 'unknown_zero',
                        pricing_snapshot_id TEXT NOT NULL DEFAULT 'unknown-zero',
                        pricing_effective_at REAL NOT NULL DEFAULT 0.0,
                        input_cost_usd REAL NOT NULL DEFAULT 0.0,
                        output_cost_usd REAL NOT NULL DEFAULT 0.0,
                        thinking_cost_usd REAL NOT NULL DEFAULT 0.0,
                        cached_input_cost_usd REAL NOT NULL DEFAULT 0.0,
                        total_cost_usd REAL NOT NULL DEFAULT 0.0,
                        cost_estimated INTEGER NOT NULL DEFAULT 1,
                        latency_ms REAL NOT NULL DEFAULT 0.0,
                        created_at REAL NOT NULL
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS prediction_costs (
                        prediction_id TEXT PRIMARY KEY,
                        cycle_id TEXT NOT NULL DEFAULT '',
                        turn_id TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT '',
                        final_outcome TEXT NOT NULL DEFAULT '',
                        model_calls INTEGER NOT NULL DEFAULT 0,
                        local_tokens INTEGER NOT NULL DEFAULT 0,
                        cloud_tokens INTEGER NOT NULL DEFAULT 0,
                        prompt_tokens INTEGER NOT NULL DEFAULT 0,
                        completion_tokens INTEGER NOT NULL DEFAULT 0,
                        thinking_tokens INTEGER NOT NULL DEFAULT 0,
                        total_tokens INTEGER NOT NULL DEFAULT 0,
                        estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
                        adopted INTEGER NOT NULL DEFAULT 0,
                        ignored INTEGER NOT NULL DEFAULT 0,
                        dropped INTEGER NOT NULL DEFAULT 0,
                        invalidated INTEGER NOT NULL DEFAULT 0,
                        false_ready INTEGER NOT NULL DEFAULT 0,
                        created_at REAL NOT NULL,
                        finalized_at REAL NOT NULL DEFAULT 0.0
                    )
                    """
                )
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS turn_costs (
                        turn_id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL DEFAULT '',
                        client_id TEXT NOT NULL DEFAULT '',
                        app TEXT NOT NULL DEFAULT '',
                        project TEXT NOT NULL DEFAULT '',
                        workspace TEXT NOT NULL DEFAULT '',
                        vaner_speculative_tokens INTEGER NOT NULL DEFAULT 0,
                        vaner_speculative_cost_usd REAL NOT NULL DEFAULT 0.0,
                        local_prep_tokens INTEGER NOT NULL DEFAULT 0,
                        cloud_prep_tokens INTEGER NOT NULL DEFAULT 0,
                        injected_context_tokens INTEGER NOT NULL DEFAULT 0,
                        expected_incremental_primary_cost_usd REAL NOT NULL DEFAULT 0.0,
                        primary_llm_input_tokens INTEGER NOT NULL DEFAULT 0,
                        primary_llm_output_tokens INTEGER NOT NULL DEFAULT 0,
                        primary_llm_thinking_tokens INTEGER NOT NULL DEFAULT 0,
                        primary_llm_cost_usd REAL NOT NULL DEFAULT 0.0,
                        primary_llm_usage_known INTEGER NOT NULL DEFAULT 0,
                        judge_llm_tokens INTEGER NOT NULL DEFAULT 0,
                        judge_llm_cost_usd REAL NOT NULL DEFAULT 0.0,
                        total_known_cloud_cost_usd REAL NOT NULL DEFAULT 0.0,
                        total_estimated_cloud_cost_usd REAL NOT NULL DEFAULT 0.0,
                        net_estimated_cloud_delta_usd REAL NOT NULL DEFAULT 0.0,
                        created_at REAL NOT NULL
                    )
                    """
                )
                await db.commit()
            self._initialized = True

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
                    json.dumps(
                        {
                            "injected_context_tokens": m.injected_context_tokens,
                            "expected_incremental_primary_cost_usd": m.expected_incremental_primary_cost_usd,
                            "primary_llm_input_tokens": m.primary_llm_input_tokens,
                            "primary_llm_output_tokens": m.primary_llm_output_tokens,
                            "primary_llm_thinking_tokens": m.primary_llm_thinking_tokens,
                            "primary_llm_cost_usd": m.primary_llm_cost_usd,
                            "primary_llm_usage_known": m.primary_llm_usage_known,
                            "total_known_cloud_cost_usd": m.total_known_cloud_cost_usd,
                            "total_estimated_cloud_cost_usd": m.total_estimated_cloud_cost_usd,
                            "pricing_snapshot_id": m.pricing_snapshot_id,
                            "usage_source_summary": m.usage_source_summary,
                        }
                    ),
                ),
            )
            await db.commit()

    async def record_pricing_snapshot(self, snapshot: PricingSnapshot) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO pricing_snapshots
                    (pricing_snapshot_id, source, created_at, effective_at,
                     content_hash, raw_snapshot_json, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.pricing_snapshot_id,
                    snapshot.source,
                    snapshot.created_at,
                    snapshot.effective_at,
                    snapshot.content_hash,
                    json.dumps(snapshot.raw_snapshot_json, sort_keys=True),
                    snapshot.notes,
                ),
            )
            await db.commit()

    async def record_llm_usage(self, entry: CostLedgerEntry) -> None:
        usage = entry.usage
        cost = entry.cost
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO llm_usage_events
                    (id, request_id, turn_id, prediction_id, cycle_id,
                     client_id, app, project, workspace, provider, model,
                     endpoint, call_role, local_or_cloud, usage_source,
                     usage_estimated, prompt_tokens, completion_tokens,
                     thinking_tokens, cached_input_tokens, total_tokens,
                     provider_usage_raw, pricing_source, pricing_snapshot_id,
                     pricing_effective_at, input_cost_usd, output_cost_usd,
                     thinking_cost_usd, cached_input_cost_usd, total_cost_usd,
                     cost_estimated, latency_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.request_id,
                    entry.turn_id,
                    entry.prediction_id,
                    entry.cycle_id,
                    entry.client_id,
                    entry.app,
                    entry.project,
                    entry.workspace,
                    entry.provider,
                    entry.model,
                    entry.endpoint,
                    entry.call_role,
                    entry.local_or_cloud,
                    usage.usage_source,
                    int(usage.usage_estimated),
                    usage.prompt_tokens,
                    usage.completion_tokens,
                    usage.thinking_tokens,
                    usage.cached_input_tokens,
                    usage.total_tokens,
                    json.dumps(usage.provider_usage_raw, sort_keys=True),
                    cost.pricing_source,
                    cost.pricing_snapshot_id,
                    cost.pricing_effective_at,
                    cost.input_cost_usd,
                    cost.output_cost_usd,
                    cost.thinking_cost_usd,
                    cost.cached_input_cost_usd,
                    cost.total_cost_usd,
                    int(cost.estimated),
                    entry.latency_ms,
                    entry.created_at,
                ),
            )
            await db.commit()

    async def rollup_prediction_cost(
        self,
        prediction_id: str,
        *,
        cycle_id: str = "",
        turn_id: str = "",
        status: str = "",
        final_outcome: str = "",
    ) -> PredictionCostSummary:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM llm_usage_events
                WHERE prediction_id = ?
                ORDER BY created_at ASC
                """,
                (prediction_id,),
            )
            rows = [dict(row) for row in await cursor.fetchall()]
            summary = PredictionCostSummary(
                prediction_id=prediction_id,
                cycle_id=cycle_id,
                turn_id=turn_id,
                status=status,
                final_outcome=final_outcome,
                model_calls=len(rows),
                local_tokens=sum(int(r["total_tokens"]) for r in rows if r["local_or_cloud"] == "local"),
                cloud_tokens=sum(int(r["total_tokens"]) for r in rows if r["local_or_cloud"] == "cloud"),
                prompt_tokens=sum(int(r["prompt_tokens"]) for r in rows),
                completion_tokens=sum(int(r["completion_tokens"]) for r in rows),
                thinking_tokens=sum(int(r["thinking_tokens"]) for r in rows),
                total_tokens=sum(int(r["total_tokens"]) for r in rows),
                estimated_cost_usd=sum(float(r["total_cost_usd"]) for r in rows),
                adopted=final_outcome == "adopted",
                ignored=final_outcome == "ignored",
                dropped=final_outcome == "dropped",
                invalidated=final_outcome == "invalidated",
                false_ready=final_outcome == "false_ready",
                created_at=float(rows[0]["created_at"]) if rows else time.time(),
                finalized_at=time.time() if final_outcome else 0.0,
            )
            await db.execute(
                """
                INSERT OR REPLACE INTO prediction_costs
                    (prediction_id, cycle_id, turn_id, status, final_outcome,
                     model_calls, local_tokens, cloud_tokens, prompt_tokens,
                     completion_tokens, thinking_tokens, total_tokens,
                     estimated_cost_usd, adopted, ignored, dropped,
                     invalidated, false_ready, created_at, finalized_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    summary.prediction_id,
                    summary.cycle_id,
                    summary.turn_id,
                    summary.status,
                    summary.final_outcome,
                    summary.model_calls,
                    summary.local_tokens,
                    summary.cloud_tokens,
                    summary.prompt_tokens,
                    summary.completion_tokens,
                    summary.thinking_tokens,
                    summary.total_tokens,
                    summary.estimated_cost_usd,
                    int(summary.adopted),
                    int(summary.ignored),
                    int(summary.dropped),
                    int(summary.invalidated),
                    int(summary.false_ready),
                    summary.created_at,
                    summary.finalized_at,
                ),
            )
            await db.commit()
        return summary

    async def record_turn_cost(self, summary: TurnCostSummary) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO turn_costs
                    (turn_id, request_id, client_id, app, project, workspace,
                     vaner_speculative_tokens, vaner_speculative_cost_usd,
                     local_prep_tokens, cloud_prep_tokens, injected_context_tokens,
                     expected_incremental_primary_cost_usd, primary_llm_input_tokens,
                     primary_llm_output_tokens, primary_llm_thinking_tokens,
                     primary_llm_cost_usd, primary_llm_usage_known,
                     judge_llm_tokens, judge_llm_cost_usd,
                     total_known_cloud_cost_usd, total_estimated_cloud_cost_usd,
                     net_estimated_cloud_delta_usd, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    summary.turn_id,
                    summary.request_id,
                    summary.client_id,
                    summary.app,
                    summary.project,
                    summary.workspace,
                    summary.vaner_speculative_tokens,
                    summary.vaner_speculative_cost_usd,
                    summary.local_prep_tokens,
                    summary.cloud_prep_tokens,
                    summary.injected_context_tokens,
                    summary.expected_incremental_primary_cost_usd,
                    summary.primary_llm_input_tokens,
                    summary.primary_llm_output_tokens,
                    summary.primary_llm_thinking_tokens,
                    summary.primary_llm_cost_usd,
                    int(summary.primary_llm_usage_known),
                    summary.judge_llm_tokens,
                    summary.judge_llm_cost_usd,
                    summary.total_known_cloud_cost_usd,
                    summary.total_estimated_cloud_cost_usd,
                    summary.net_estimated_cloud_delta_usd,
                    summary.created_at,
                ),
            )
            await db.commit()

    async def cost_summary(self, last_n: int = 1000) -> dict[str, Any]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM llm_usage_events ORDER BY created_at DESC LIMIT ?",
                (last_n,),
            )
            events = [dict(row) for row in await cursor.fetchall()]
            cursor = await db.execute(
                "SELECT * FROM turn_costs ORDER BY created_at DESC LIMIT ?",
                (last_n,),
            )
            turns = [dict(row) for row in await cursor.fetchall()]
        cost_by_model: dict[str, float] = {}
        cost_by_provider: dict[str, float] = {}
        for event in events:
            cost = float(event.get("total_cost_usd") or 0.0)
            model = str(event.get("model") or "unknown")
            provider = str(event.get("provider") or "unknown")
            cost_by_model[model] = cost_by_model.get(model, 0.0) + cost
            cost_by_provider[provider] = cost_by_provider.get(provider, 0.0) + cost
        return {
            "event_count": len(events),
            "turn_count": len(turns),
            "total_estimated_cloud_cost_usd": sum(float(e.get("total_cost_usd") or 0.0) for e in events),
            "local_tokens": sum(int(e.get("total_tokens") or 0) for e in events if e.get("local_or_cloud") == "local"),
            "cloud_tokens": sum(int(e.get("total_tokens") or 0) for e in events if e.get("local_or_cloud") == "cloud"),
            "injected_context_tokens": sum(int(t.get("injected_context_tokens") or 0) for t in turns),
            "cost_by_model": cost_by_model,
            "cost_by_provider": cost_by_provider,
        }

    async def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM request_metrics ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def summary(self, last_n: int = 1000) -> dict[str, Any]:
        """Aggregate statistics over the last *last_n* requests."""
        rows = await self.recent(last_n)
        if not rows:
            return {"count": 0}

        def _avg(key: str) -> float:
            vals = [r[key] for r in rows if r[key] > 0]
            return round(float(sum(vals) / len(vals)), 2) if vals else 0.0

        def _p95(key: str) -> float:
            vals = sorted(r[key] for r in rows if r[key] > 0)
            if not vals:
                return 0.0
            idx = max(0, int(len(vals) * 0.95) - 1)
            return round(float(vals[idx]), 2)

        tiers: dict[str, int] = {}
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

    async def record_shadow_pair(
        self,
        *,
        request_id: str,
        with_context_total_ms: float,
        without_context_total_ms: float,
        with_context_tokens: int,
        without_context_tokens: int,
    ) -> None:
        latency_delta = without_context_total_ms - with_context_total_ms
        token_delta = with_context_tokens - without_context_tokens
        payload = {
            "request_id": request_id,
            "with_context_total_ms": with_context_total_ms,
            "without_context_total_ms": without_context_total_ms,
            "with_context_tokens": with_context_tokens,
            "without_context_tokens": without_context_tokens,
            "latency_delta_ms": latency_delta,
            "token_delta": token_delta,
        }
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO shadow_comparisons
                    (shadow_pair_id, request_id, timestamp, with_context_total_ms,
                     without_context_total_ms, with_context_tokens, without_context_tokens,
                     latency_delta_ms, token_delta, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    request_id,
                    time.time(),
                    with_context_total_ms,
                    without_context_total_ms,
                    with_context_tokens,
                    without_context_tokens,
                    latency_delta,
                    token_delta,
                    json.dumps(payload),
                ),
            )
            await db.commit()

    async def shadow_summary(self, last_n: int = 500) -> dict[str, Any]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM shadow_comparisons ORDER BY timestamp DESC LIMIT ?",
                (last_n,),
            )
            rows = await cursor.fetchall()
        if not rows:
            return {"count": 0}
        pairs = [dict(row) for row in rows]
        wins = [row for row in pairs if row["latency_delta_ms"] > 0]
        avg_latency_gain = round(sum(row["latency_delta_ms"] for row in pairs) / len(pairs), 2)
        avg_token_delta = round(sum(row["token_delta"] for row in pairs) / len(pairs), 2)
        return {
            "count": len(pairs),
            "win_rate": round(len(wins) / len(pairs), 3),
            "avg_latency_gain_ms": avg_latency_gain,
            "avg_token_delta": avg_token_delta,
        }

    async def increment_mode_usage(self, mode: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO integration_usage (mode, count, updated_at)
                VALUES (?, 1, ?)
                ON CONFLICT(mode) DO UPDATE SET
                    count = count + 1,
                    updated_at = excluded.updated_at
                """,
                (mode, time.time()),
            )
            await db.commit()

    async def mode_usage_summary(self) -> dict[str, int]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT mode, count FROM integration_usage ORDER BY count DESC")
            rows = await cursor.fetchall()
        return {str(row["mode"]): int(row["count"]) for row in rows}

    async def record_mcp_tool_call(
        self,
        *,
        tool_name: str,
        status: str,
        latency_ms: float,
        scenario_id: str | None = None,
        skill: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO mcp_tool_calls (id, tool_name, status, latency_ms, scenario_id, skill, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), tool_name, status, latency_ms, scenario_id, skill, time.time()),
            )
            await db.commit()

    async def record_scenario_outcome(
        self,
        *,
        scenario_id: str,
        result: str,
        note: str = "",
        skill: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO scenario_outcomes (id, scenario_id, result, note, skill, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), scenario_id, result, note, skill, time.time()),
            )
            await db.commit()

    async def increment_counter(self, name: str, delta: float = 1.0) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO memory_quality_counters (name, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    value = value + excluded.value,
                    updated_at = excluded.updated_at
                """,
                (name, float(delta), time.time()),
            )
            await db.commit()

    async def set_counter(self, name: str, value: float) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO memory_quality_counters (name, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (name, float(value), time.time()),
            )
            await db.commit()

    async def _counters_map(self) -> dict[str, float]:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute("SELECT name, value FROM memory_quality_counters")
                rows = await cur.fetchall()
            return {str(row["name"]): float(row["value"]) for row in rows}
        except Exception:
            return {}

    async def memory_quality_snapshot(self) -> dict[str, float]:
        counters = await self._counters_map()
        resolves = max(1.0, counters.get("resolves_total", 0.0))
        promotions = max(1.0, counters.get("promotions_total", 0.0))
        corrections = max(1.0, counters.get("corrections_submitted", 0.0))
        demotions = max(1.0, counters.get("demotions_total", 0.0))
        snapshot: dict[str, float] = {
            "predictive_hit_rate": counters.get("predictive_hit_total", 0.0) / resolves,
            "stale_hit_rate": counters.get("stale_hit_total", 0.0) / resolves,
            "promotion_precision": counters.get("promotions_still_trusted_total", 0.0) / promotions,
            "contradiction_rate": counters.get("conflict_total", 0.0) / resolves,
            "correction_survival_rate": counters.get("corrections_survived_total", 0.0) / corrections,
            "demotion_recovery_rate": counters.get("demotion_recovery_total", 0.0) / demotions,
            "trusted_evidence_avg": counters.get("trusted_evidence_total", 0.0) / max(1.0, counters.get("trusted_scenarios_count", 0.0)),
            "abstain_rate": counters.get("abstain_total", 0.0) / resolves,
            "next_prompt_top1_rate": counters.get("next_prompt_top1_correct_total", 0.0)
            / max(1.0, counters.get("next_prompt_predictions_total", 0.0)),
            "next_prompt_top3_rate": counters.get("next_prompt_top3_correct_total", 0.0)
            / max(1.0, counters.get("next_prompt_predictions_total", 0.0)),
            "next_prompt_logloss": counters.get("next_prompt_logloss_total", 0.0)
            / max(1.0, counters.get("next_prompt_predictions_total", 0.0)),
            "next_prompt_brier": counters.get("next_prompt_brier_total", 0.0)
            / max(1.0, counters.get("next_prompt_predictions_total", 0.0)),
            "draft_usefulness_rate": counters.get("draft_useful_total", 0.0) / max(1.0, counters.get("draft_served_total", 0.0)),
            "budget_utilization": counters.get("cycle_budget_used_ms_total", 0.0)
            / max(1.0, counters.get("cycle_budget_allocated_ms_total", 0.0)),
            "predictive_lead_seconds_avg": counters.get("predictive_lead_seconds_total", 0.0)
            / max(1.0, counters.get("predictive_lead_events_total", 0.0)),
            "confidence_conditioned_utility": counters.get("confidence_conditioned_utility_total", 0.0)
            / max(1.0, counters.get("next_prompt_predictions_total", 0.0)),
            "cycle_budget_allocated_ms_total": counters.get("cycle_budget_allocated_ms_total", 0.0),
            "cycle_budget_used_ms_total": counters.get("cycle_budget_used_ms_total", 0.0),
            "bucket_budget_exploit_allocated_ms_total": counters.get("bucket_budget_exploit_allocated_ms_total", 0.0),
            "bucket_budget_hedge_allocated_ms_total": counters.get("bucket_budget_hedge_allocated_ms_total", 0.0),
            "bucket_budget_invest_allocated_ms_total": counters.get("bucket_budget_invest_allocated_ms_total", 0.0),
            "bucket_budget_no_regret_allocated_ms_total": counters.get("bucket_budget_no_regret_allocated_ms_total", 0.0),
            "bucket_budget_exploit_used_ms_total": counters.get("bucket_budget_exploit_used_ms_total", 0.0),
            "bucket_budget_hedge_used_ms_total": counters.get("bucket_budget_hedge_used_ms_total", 0.0),
            "bucket_budget_invest_used_ms_total": counters.get("bucket_budget_invest_used_ms_total", 0.0),
            "bucket_budget_no_regret_used_ms_total": counters.get("bucket_budget_no_regret_used_ms_total", 0.0),
            "draft_predicted_prompt_similarity_total": counters.get("draft_predicted_prompt_similarity_total", 0.0),
            "draft_evidence_overlap_total": counters.get("draft_evidence_overlap_total", 0.0),
            "draft_answer_reuse_ratio_total": counters.get("draft_answer_reuse_ratio_total", 0.0),
            "draft_directionally_correct_total": counters.get("draft_directionally_correct_total", 0.0),
        }
        for bucket_name, _ in _LEAD_TIME_BUCKETS:
            snapshot[f"predictive_lead_hist_{bucket_name}"] = counters.get(f"predictive_lead_hist_{bucket_name}", 0.0)
        return snapshot

    async def calibration_snapshot(self) -> list[dict[str, float]]:
        counters = await self._counters_map()
        rows: list[dict[str, float]] = []
        for bucket_idx in range(10):
            total = counters.get(f"calibration_bucket_{bucket_idx}_total", 0.0)
            correct = counters.get(f"calibration_bucket_{bucket_idx}_correct", 0.0)
            confidence_mid = (bucket_idx + 0.5) / 10.0
            rows.append(
                {
                    "bucket": float(bucket_idx),
                    "confidence_mid": confidence_mid,
                    "count": total,
                    "accuracy": (correct / total) if total > 0 else 0.0,
                }
            )
        return rows

    async def record_next_prompt_prediction(
        self,
        *,
        probabilities: dict[str, float],
        actual_label: str,
    ) -> None:
        if not probabilities:
            return
        total = sum(max(0.0, float(v)) for v in probabilities.values())
        if total <= 0.0:
            return
        normalized = {k: max(0.0, float(v)) / total for k, v in probabilities.items()}
        ranked = sorted(normalized.items(), key=lambda item: item[1], reverse=True)
        top1_label, top1_conf = ranked[0]
        top3_labels = {label for label, _ in ranked[:3]}
        p_actual = max(1e-9, normalized.get(actual_label, 0.0))
        logloss = -math.log(p_actual)
        labels = set(normalized.keys())
        labels.add(actual_label)
        brier = 0.0
        for label in labels:
            y = 1.0 if label == actual_label else 0.0
            p = normalized.get(label, 0.0)
            brier += (p - y) ** 2
        brier /= max(1, len(labels))
        confidence = float(top1_conf)
        confidence_utility = confidence * (1.0 if top1_label == actual_label else -1.0)
        bucket_idx = min(9, max(0, int(confidence * 10.0)))

        async with aiosqlite.connect(self.db_path) as db:
            now = time.time()
            await db.execute(
                """
                INSERT INTO prediction_events (id, timestamp, top1_label, top1_confidence, probs_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), now, top1_label, confidence, json.dumps(normalized, sort_keys=True)),
            )

            async def _inc(name: str, delta: float) -> None:
                await db.execute(
                    """
                    INSERT INTO memory_quality_counters (name, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        value = value + excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (name, float(delta), now),
                )

            await _inc("next_prompt_predictions_total", 1.0)
            await _inc("next_prompt_top1_correct_total", 1.0 if top1_label == actual_label else 0.0)
            await _inc("next_prompt_top3_correct_total", 1.0 if actual_label in top3_labels else 0.0)
            await _inc("next_prompt_logloss_total", logloss)
            await _inc("next_prompt_brier_total", brier)
            await _inc("confidence_conditioned_utility_total", confidence_utility)
            await _inc(f"calibration_bucket_{bucket_idx}_total", 1.0)
            await _inc(f"calibration_bucket_{bucket_idx}_correct", 1.0 if top1_label == actual_label else 0.0)
            await db.commit()

    async def record_cycle_budget(
        self,
        *,
        allocated_ms: float,
        used_ms: float,
        bucket: str | None = None,
    ) -> None:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:

            async def _inc(name: str, delta: float) -> None:
                await db.execute(
                    """
                    INSERT INTO memory_quality_counters (name, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        value = value + excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (name, float(delta), now),
                )

            await _inc("cycle_budget_allocated_ms_total", max(0.0, float(allocated_ms)))
            await _inc("cycle_budget_used_ms_total", max(0.0, float(used_ms)))
            if bucket:
                await _inc(f"bucket_budget_{bucket}_allocated_ms_total", max(0.0, float(allocated_ms)))
                await _inc(f"bucket_budget_{bucket}_used_ms_total", max(0.0, float(used_ms)))
            await db.commit()

    async def record_predictive_lead_seconds(self, seconds: float) -> None:
        value = max(0.0, float(seconds))
        await self.increment_counter("predictive_lead_seconds_total", delta=value)
        await self.increment_counter("predictive_lead_events_total", delta=1.0)
        for bucket_name, ceiling in _LEAD_TIME_BUCKETS:
            if value < ceiling:
                await self.increment_counter(f"predictive_lead_hist_{bucket_name}", delta=1.0)
                break

    async def record_draft_event(
        self,
        *,
        status: str,
        predicted_prompt_similarity: float = 0.0,
        evidence_overlap: float = 0.0,
        answer_reuse_ratio: float = 0.0,
        directional_correct: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        status_key = status.strip().lower()
        if status_key not in {"served", "useful", "wrong", "unused"}:
            status_key = "served"
        payload = metadata or {}
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO draft_events (
                    id, timestamp, status, predicted_prompt_similarity, evidence_overlap,
                    answer_reuse_ratio, directional_correct, metadata_json, event_type
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    now,
                    status_key,
                    max(0.0, min(1.0, float(predicted_prompt_similarity))),
                    max(0.0, min(1.0, float(evidence_overlap))),
                    max(0.0, min(1.0, float(answer_reuse_ratio))),
                    int(bool(directional_correct)),
                    json.dumps(payload, sort_keys=True),
                    "legacy_draft",
                ),
            )
            await db.execute(
                """
                INSERT INTO memory_quality_counters (name, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    value = value + excluded.value,
                    updated_at = excluded.updated_at
                """,
                (f"draft_{status_key}_total", 1.0, now),
            )
            if status_key == "served":
                await db.execute(
                    """
                    INSERT INTO memory_quality_counters (name, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        value = value + excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    ("draft_predicted_prompt_similarity_total", max(0.0, min(1.0, float(predicted_prompt_similarity))), now),
                )
                await db.execute(
                    """
                    INSERT INTO memory_quality_counters (name, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        value = value + excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    ("draft_evidence_overlap_total", max(0.0, min(1.0, float(evidence_overlap))), now),
                )
                await db.execute(
                    """
                    INSERT INTO memory_quality_counters (name, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        value = value + excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    ("draft_answer_reuse_ratio_total", max(0.0, min(1.0, float(answer_reuse_ratio))), now),
                )
                if directional_correct:
                    await db.execute(
                        """
                        INSERT INTO memory_quality_counters (name, value, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(name) DO UPDATE SET
                            value = value + excluded.value,
                            updated_at = excluded.updated_at
                        """,
                        ("draft_directionally_correct_total", 1.0, now),
                    )
            await db.commit()

    async def record_composer_lifecycle_event(
        self,
        *,
        session_id: str,
        snapshot_id: str,
        lifecycle_state: str,
        text_hash: str,
        length_chars: int,
        composer_event_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a composer-lifecycle observation (0.8.7 WS6).

        Composer-lifecycle rows write to the *separate* counter prefix
        ``composer_lifecycle_{lifecycle_state}_total`` so they never
        corrupt the existing ``draft_{served|useful|wrong|unused}_total``
        counters that 0.8.6 dashboards depend on. The ``event_type``
        column on ``draft_events`` distinguishes these rows from legacy
        ``record_draft_event`` writes.

        0.8.7 hardening (H2 — short-prompt brute-force): the telemetry
        row stores ``length_bucket`` (coarse band) instead of the exact
        ``length_chars``, and DROPS the redundant ``text_hash`` copy that
        existed alongside the unbucketed length. The signal_events row
        retains the full snapshot for audit (single source of truth).
        Reducing the cross-store correlation surface defeats the trivial
        case where a post-compromise adversary brute-forces sha256 over
        all candidate strings of an exact known length.
        """
        # Reject the audit-noise pair (text_hash, length_chars) entirely
        # from the telemetry surface. snapshot_id + composer_event_id
        # already pin the row to its source signal_events record without
        # leaking the privacy-sensitive cross-store correlation.
        del text_hash
        payload = dict(metadata or {})
        payload["session_id"] = session_id
        payload["snapshot_id"] = snapshot_id
        payload["composer_event_id"] = composer_event_id
        payload["length_bucket"] = _length_bucket(length_chars)
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO draft_events (
                    id, timestamp, status, predicted_prompt_similarity, evidence_overlap,
                    answer_reuse_ratio, directional_correct, metadata_json, event_type
                )
                VALUES (?, ?, ?, 0.0, 0.0, 0.0, 0, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    now,
                    lifecycle_state,
                    json.dumps(payload, sort_keys=True),
                    "composer_lifecycle",
                ),
            )
            await db.execute(
                """
                INSERT INTO memory_quality_counters (name, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    value = value + excluded.value,
                    updated_at = excluded.updated_at
                """,
                (f"composer_lifecycle_{lifecycle_state}_total", 1.0, now),
            )
            await db.commit()

    async def record_counterfactual_miss(
        self,
        *,
        prompt: str,
        miss_type: str,
        helpful_context: list[str],
        wasted_branches: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO counterfactual_misses (
                    id, timestamp, prompt, miss_type, helpful_context_json, wasted_branches_json, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    now,
                    prompt[:4000],
                    miss_type[:128],
                    json.dumps(helpful_context[:50]),
                    json.dumps(wasted_branches[:50]),
                    json.dumps(metadata or {}, sort_keys=True),
                ),
            )
            await db.execute(
                """
                INSERT INTO memory_quality_counters (name, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    value = value + excluded.value,
                    updated_at = excluded.updated_at
                """,
                ("counterfactual_miss_total", 1.0, now),
            )
            await db.commit()
