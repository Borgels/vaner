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
import math
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

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

    def to_dict(self) -> dict[str, Any]:
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

    @staticmethod
    async def _ensure_column(db: aiosqlite.Connection, table: str, column: str, column_def: str) -> None:
        cursor = await db.execute(f"PRAGMA table_info({table})")
        rows = await cursor.fetchall()
        names = {str(row[1]) for row in rows}
        if column not in names:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")

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
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
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
                    answer_reuse_ratio, directional_correct, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
