from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import aiosqlite

from vaner.intent.scenario_lifecycle import apply_scenario_lifecycle
from vaner.intent.scenario_scorer import scenario_score
from vaner.mcp.contracts import MemoryMeta, MemorySection, MemoryState
from vaner.memory.policy import InvalidationContext, decide_invalidation, validate_transition
from vaner.models.scenario import (
    EvidenceRef,
    Scenario,
    ScenarioCost,
    ScenarioFreshness,
    ScenarioKind,
    ScenarioLifecycleMotion,
    ScenarioOutcome,
    ScenarioReadiness,
    ScenarioVisibility,
)

SCENARIO_SAMPLE_MIN_INTERVAL_SECONDS = 15.0
SCENARIO_SAMPLE_KEEP_ROWS = 50_000


@dataclass(frozen=True)
class ScenarioSample:
    ts: float
    scenario_id: str
    relevance: float
    readiness: str
    confidence: float
    freshness: str
    visible_priority: float
    visibility: str
    lifecycle_motion: str
    status: str
    pinned: bool
    active: bool
    cycle_id: str | None = None
    job_id: str | None = None
    source_event_id: str | None = None


class ScenarioStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS scenarios (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    score REAL NOT NULL,
                    confidence REAL NOT NULL,
                    entities_json TEXT NOT NULL,
                    prepared_context TEXT NOT NULL,
                    coverage_gaps_json TEXT NOT NULL,
                    freshness TEXT NOT NULL,
                    cost_to_expand TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expanded_at REAL,
                    last_refreshed_at REAL NOT NULL,
                    last_outcome TEXT
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS scenario_evidence (
                    scenario_id TEXT NOT NULL,
                    evidence_key TEXT NOT NULL,
                    source_path TEXT NOT NULL DEFAULT '',
                    excerpt TEXT NOT NULL DEFAULT '',
                    weight REAL NOT NULL DEFAULT 0.0,
                    PRIMARY KEY (scenario_id, evidence_key),
                    FOREIGN KEY (scenario_id) REFERENCES scenarios(id) ON DELETE CASCADE
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS prompt_macro_clusters (
                    macro_key TEXT PRIMARY KEY,
                    centroid_label TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.0,
                    support_count INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS scenario_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    scenario_id TEXT NOT NULL,
                    relevance REAL NOT NULL,
                    readiness TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    freshness TEXT NOT NULL,
                    visible_priority REAL NOT NULL,
                    visibility TEXT NOT NULL,
                    lifecycle_motion TEXT NOT NULL,
                    status TEXT NOT NULL,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 0,
                    cycle_id TEXT,
                    job_id TEXT,
                    source_event_id TEXT,
                    FOREIGN KEY (scenario_id) REFERENCES scenarios(id) ON DELETE CASCADE
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_scenarios_kind ON scenarios(kind)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_scenarios_score ON scenarios(score DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_scenarios_freshness ON scenarios(freshness)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_scenario_evidence_sid ON scenario_evidence(scenario_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_prompt_macro_clusters_centroid ON prompt_macro_clusters(centroid_label)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_scenario_samples_sid_ts ON scenario_samples(scenario_id, ts)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_scenario_samples_ts ON scenario_samples(ts)")
            await self._add_column_if_missing(db, "ALTER TABLE scenarios ADD COLUMN context_envelope_json TEXT NOT NULL DEFAULT '{}'")
            await self._add_column_if_missing(db, "ALTER TABLE scenarios ADD COLUMN memory_state TEXT NOT NULL DEFAULT 'candidate'")
            await self._add_column_if_missing(db, "ALTER TABLE scenarios ADD COLUMN memory_confidence REAL NOT NULL DEFAULT 0.0")
            await self._add_column_if_missing(db, "ALTER TABLE scenarios ADD COLUMN memory_last_validated_at REAL")
            await self._add_column_if_missing(db, "ALTER TABLE scenarios ADD COLUMN memory_evidence_hashes_json TEXT NOT NULL DEFAULT '[]'")
            await self._add_column_if_missing(db, "ALTER TABLE scenarios ADD COLUMN prior_successes INTEGER NOT NULL DEFAULT 0")
            await self._add_column_if_missing(db, "ALTER TABLE scenarios ADD COLUMN contradiction_signal REAL NOT NULL DEFAULT 0.0")
            await self._add_column_if_missing(db, "ALTER TABLE scenarios ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
            await self._add_column_if_missing(db, "ALTER TABLE scenarios ADD COLUMN relevance REAL NOT NULL DEFAULT 0.0")
            await self._add_column_if_missing(db, "ALTER TABLE scenarios ADD COLUMN visible_priority REAL NOT NULL DEFAULT 0.0")
            await self._add_column_if_missing(db, "ALTER TABLE scenarios ADD COLUMN readiness TEXT NOT NULL DEFAULT 'unprepared'")
            await self._add_column_if_missing(db, "ALTER TABLE scenarios ADD COLUMN visibility TEXT NOT NULL DEFAULT 'warming'")
            await self._add_column_if_missing(db, "ALTER TABLE scenarios ADD COLUMN lifecycle_motion TEXT NOT NULL DEFAULT 'stable'")
            await self._add_column_if_missing(db, "ALTER TABLE scenarios ADD COLUMN last_reinforced_at REAL")
            await self._add_column_if_missing(db, "ALTER TABLE scenarios ADD COLUMN archived_at REAL")
            await self._add_column_if_missing(db, "ALTER TABLE scenarios ADD COLUMN visibility_reason TEXT NOT NULL DEFAULT ''")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_scenarios_memory_state ON scenarios(memory_state)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_scenarios_visibility ON scenarios(visibility)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_scenarios_relevance ON scenarios(relevance DESC)")
            await db.execute("UPDATE scenarios SET last_reinforced_at = COALESCE(last_reinforced_at, last_refreshed_at)")
            await db.execute("UPDATE scenarios SET relevance = score WHERE relevance = 0.0 AND score > 0.0")
            await db.execute("UPDATE scenarios SET visible_priority = relevance WHERE visible_priority = 0.0 AND relevance > 0.0")
            await db.commit()

    async def upsert_prompt_macro_cluster(
        self,
        *,
        macro_key: str,
        centroid_label: str,
        confidence: float,
        support_count: int,
    ) -> None:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO prompt_macro_clusters (macro_key, centroid_label, confidence, support_count, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(macro_key) DO UPDATE SET
                    centroid_label=excluded.centroid_label,
                    confidence=excluded.confidence,
                    support_count=excluded.support_count,
                    updated_at=excluded.updated_at
                """,
                (macro_key, centroid_label, float(confidence), int(support_count), now),
            )
            await db.commit()

    async def list_prompt_macro_clusters(self, limit: int = 200) -> list[dict[str, object]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT macro_key, centroid_label, confidence, support_count, updated_at
                FROM prompt_macro_clusters
                ORDER BY support_count DESC, confidence DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            )
            rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def upsert(self, scenario: Scenario) -> None:
        scenario = apply_scenario_lifecycle(scenario)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO scenarios (
                    id, kind, score, confidence, entities_json, prepared_context,
                    coverage_gaps_json, freshness, cost_to_expand, created_at,
                    expanded_at, last_refreshed_at, last_outcome, context_envelope_json,
                    memory_state, memory_confidence, memory_last_validated_at, memory_evidence_hashes_json,
                    prior_successes, contradiction_signal, pinned, relevance, visible_priority,
                    readiness, visibility, lifecycle_motion, last_reinforced_at, archived_at,
                    visibility_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind=excluded.kind,
                    score=excluded.score,
                    confidence=excluded.confidence,
                    entities_json=excluded.entities_json,
                    prepared_context=excluded.prepared_context,
                    coverage_gaps_json=excluded.coverage_gaps_json,
                    freshness=excluded.freshness,
                    cost_to_expand=excluded.cost_to_expand,
                    created_at=MIN(scenarios.created_at, excluded.created_at),
                    expanded_at=excluded.expanded_at,
                    last_refreshed_at=excluded.last_refreshed_at,
                    last_outcome=excluded.last_outcome,
                    context_envelope_json=excluded.context_envelope_json,
                    memory_state=excluded.memory_state,
                    memory_confidence=excluded.memory_confidence,
                    memory_last_validated_at=excluded.memory_last_validated_at,
                    memory_evidence_hashes_json=excluded.memory_evidence_hashes_json,
                    prior_successes=excluded.prior_successes,
                    contradiction_signal=excluded.contradiction_signal,
                    pinned=MAX(scenarios.pinned, excluded.pinned),
                    relevance=excluded.relevance,
                    visible_priority=excluded.visible_priority,
                    readiness=excluded.readiness,
                    visibility=excluded.visibility,
                    lifecycle_motion=excluded.lifecycle_motion,
                    last_reinforced_at=excluded.last_reinforced_at,
                    archived_at=excluded.archived_at,
                    visibility_reason=excluded.visibility_reason
                """,
                (
                    scenario.id,
                    scenario.kind,
                    scenario.score,
                    scenario.confidence,
                    json.dumps(scenario.entities),
                    scenario.prepared_context,
                    json.dumps(scenario.coverage_gaps),
                    scenario.freshness,
                    scenario.cost_to_expand,
                    scenario.created_at,
                    scenario.expanded_at,
                    scenario.last_refreshed_at,
                    scenario.last_outcome,
                    scenario.context_envelope_json,
                    scenario.memory_state,
                    scenario.memory_confidence,
                    scenario.memory_last_validated_at,
                    scenario.memory_evidence_hashes_json,
                    scenario.prior_successes,
                    scenario.contradiction_signal,
                    int(scenario.pinned),
                    scenario.relevance,
                    scenario.visible_priority,
                    scenario.readiness,
                    scenario.visibility,
                    scenario.lifecycle_motion,
                    scenario.last_reinforced_at,
                    scenario.archived_at,
                    scenario.visibility_reason,
                ),
            )
            await db.execute("DELETE FROM scenario_evidence WHERE scenario_id = ?", (scenario.id,))
            for evidence in scenario.evidence:
                await db.execute(
                    """
                    INSERT INTO scenario_evidence (scenario_id, evidence_key, source_path, excerpt, weight)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (scenario.id, evidence.key, evidence.source_path, evidence.excerpt, evidence.weight),
                )
            await self._refresh_lifecycle_for_ids(db, [scenario.id])
            await db.commit()

    async def list_top(self, *, kind: str | None = None, limit: int = 10, visibility: str = "live") -> list[Scenario]:
        await self.mark_stale()
        query = "SELECT * FROM scenarios"
        params: list[object] = []
        predicates: list[str] = []
        if kind:
            predicates.append("kind = ?")
            params.append(kind)
        if visibility == "live":
            predicates.append("(visibility != 'archived' OR pinned = 1)")
        elif visibility == "history":
            predicates.append("visibility = 'archived' AND pinned = 0")
        elif visibility != "all":
            predicates.append("(visibility != 'archived' OR pinned = 1)")
        if predicates:
            query += " WHERE " + " AND ".join(predicates)
        query += " ORDER BY visible_priority DESC, relevance DESC, last_reinforced_at DESC, last_refreshed_at DESC LIMIT ?"
        params.append(max(1, limit))
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(query, params)
            rows = await cur.fetchall()
            evidence_map = await self._load_evidence_for_scenarios(db, [str(row["id"]) for row in rows])
        return [self._row_to_scenario(row, evidence_map.get(str(row["id"]), [])) for row in rows]

    async def list_samples(
        self,
        *,
        scenario_ids: list[str] | None = None,
        start_ts: float | None = None,
        end_ts: float | None = None,
        limit: int = 20_000,
    ) -> list[ScenarioSample]:
        query = "SELECT * FROM scenario_samples"
        predicates: list[str] = []
        params: list[object] = []
        if scenario_ids:
            placeholders = ", ".join("?" for _ in scenario_ids)
            predicates.append(f"scenario_id IN ({placeholders})")
            params.extend(scenario_ids)
        if start_ts is not None:
            predicates.append("ts >= ?")
            params.append(float(start_ts))
        if end_ts is not None:
            predicates.append("ts <= ?")
            params.append(float(end_ts))
        if predicates:
            query += " WHERE " + " AND ".join(predicates)
        query += " ORDER BY ts ASC LIMIT ?"
        params.append(max(1, min(100_000, int(limit))))
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(query, params)
            rows = await cur.fetchall()
        return [self._sample_from_row(row) for row in rows]

    async def get(self, scenario_id: str) -> Scenario | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,))
            row = await cur.fetchone()
            if row is None:
                return None
            evidence_map = await self._load_evidence_for_scenarios(db, [scenario_id])
        return self._row_to_scenario(row, evidence_map.get(scenario_id, []))

    async def record_expansion(self, scenario_id: str) -> None:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE scenarios
                SET freshness = 'fresh', expanded_at = ?, last_refreshed_at = ?,
                    last_reinforced_at = ?, archived_at = NULL
                WHERE id = ?
                """,
                (now, now, now, scenario_id),
            )
            await self._refresh_lifecycle_for_ids(db, [scenario_id], now=now)
            await db.commit()

    async def record_outcome(self, scenario_id: str, outcome: str, **_: object) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            now = time.time()
            await db.execute("UPDATE scenarios SET last_outcome = ? WHERE id = ?", (outcome, scenario_id))
            if outcome == "useful":
                await db.execute(
                    """
                    UPDATE scenarios
                    SET prior_successes = prior_successes + 1, freshness = 'fresh',
                        last_reinforced_at = ?, archived_at = NULL
                    WHERE id = ?
                    """,
                    (now, scenario_id),
                )
            if outcome == "partial":
                await db.execute(
                    "UPDATE scenarios SET freshness = 'fresh', last_reinforced_at = ?, archived_at = NULL WHERE id = ?",
                    (now, scenario_id),
                )
            if outcome == "irrelevant":
                await db.execute(
                    "UPDATE scenarios SET contradiction_signal = MIN(1.0, contradiction_signal + 0.12), freshness = 'stale' WHERE id = ?",
                    (scenario_id,),
                )
            if outcome == "wrong":
                await db.execute(
                    "UPDATE scenarios SET contradiction_signal = MIN(1.0, contradiction_signal + 0.25), freshness = 'stale' WHERE id = ?",
                    (scenario_id,),
                )
            row_cur = await db.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,))
            row = await row_cur.fetchone()
            if row is not None:
                evidence_map = await self._load_evidence_for_scenarios(db, [scenario_id])
                scenario = self._row_to_scenario(row, evidence_map.get(scenario_id, []))
                score = scenario_score(scenario)
                await db.execute("UPDATE scenarios SET score = ? WHERE id = ?", (score, scenario_id))
            await self._refresh_lifecycle_for_ids(db, [scenario_id], now=now)
            await db.commit()

    async def mark_stale(self) -> None:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE scenarios
                SET freshness = CASE
                    WHEN freshness = 'stale' THEN 'stale'
                    WHEN ? - last_refreshed_at > 1800 THEN 'stale'
                    WHEN ? - last_refreshed_at > 300 THEN 'recent'
                    ELSE freshness
                END
                """,
                (now, now),
            )
            cur = await db.execute("SELECT id FROM scenarios")
            rows = await cur.fetchall()
            await self._refresh_lifecycle_for_ids(db, [str(row[0]) for row in rows], now=now)
            await db.commit()

    async def mark_absent_stale(self, active_ids: set[str]) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            if not active_ids:
                await db.execute("UPDATE scenarios SET freshness = 'stale' WHERE pinned = 0")
                cur = await db.execute("SELECT id FROM scenarios WHERE pinned = 0")
            else:
                placeholders = ", ".join("?" for _ in active_ids)
                await db.execute(
                    f"UPDATE scenarios SET freshness = 'stale' WHERE pinned = 0 AND id NOT IN ({placeholders})",
                    tuple(sorted(active_ids)),
                )
                cur = await db.execute(
                    f"SELECT id FROM scenarios WHERE pinned = 0 AND id NOT IN ({placeholders})",
                    tuple(sorted(active_ids)),
                )
            rows = await cur.fetchall()
            await self._refresh_lifecycle_for_ids(db, [str(row[0]) for row in rows])
            await db.commit()

    async def set_pinned(self, scenario_id: str, pinned: bool) -> None:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE scenarios
                SET pinned = ?, archived_at = CASE WHEN ? = 1 THEN NULL ELSE archived_at END,
                    last_reinforced_at = CASE WHEN ? = 1 THEN COALESCE(last_reinforced_at, last_refreshed_at, ?) ELSE last_reinforced_at END
                WHERE id = ?
                """,
                (1 if pinned else 0, 1 if pinned else 0, 1 if pinned else 0, now, scenario_id),
            )
            await self._refresh_lifecycle_for_ids(db, [scenario_id], now=now)
            await db.commit()

    async def freshness_counts(self) -> dict[str, int]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT freshness, COUNT(*) AS count
                FROM scenarios
                GROUP BY freshness
                """
            )
            rows = await cur.fetchall()
        counts = {"fresh": 0, "recent": 0, "stale": 0, "total": 0}
        for row in rows:
            freshness = str(row["freshness"])
            count = int(row["count"])
            if freshness in counts:
                counts[freshness] = count
            counts["total"] += count
        return counts

    async def memory_state_counts(self) -> dict[str, int]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT memory_state, COUNT(*) AS count
                FROM scenarios
                GROUP BY memory_state
                """
            )
            rows = await cur.fetchall()
        counts = {"candidate": 0, "trusted": 0, "stale": 0, "demoted": 0, "total": 0}
        for row in rows:
            state = str(row["memory_state"] or "candidate")
            count = int(row["count"])
            if state in counts:
                counts[state] = count
            counts["total"] += count
        return counts

    async def promote_scenario(
        self,
        scenario_id: str,
        *,
        new_state: MemoryState,
        confidence: float,
        evidence_hashes: list[str],
        at: float,
    ) -> None:
        scenario = await self.get(scenario_id)
        if scenario is None:
            return
        validate_transition(cast(MemoryState, scenario.memory_state), new_state)
        pinned = 1 if new_state == "trusted" else 0
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE scenarios
                SET memory_state = ?, memory_confidence = ?, memory_last_validated_at = ?,
                    memory_evidence_hashes_json = ?, pinned = ?, freshness='fresh',
                    last_refreshed_at=?, last_reinforced_at=?, archived_at=NULL
                WHERE id = ?
                """,
                (
                    new_state,
                    max(0.0, min(1.0, confidence)),
                    at,
                    json.dumps(evidence_hashes),
                    pinned,
                    at,
                    at,
                    scenario_id,
                ),
            )
            await self._refresh_lifecycle_for_ids(db, [scenario_id], now=at)
            await db.commit()

    async def demote_scenario(
        self,
        scenario_id: str,
        *,
        new_state: MemoryState = "demoted",
        score_penalty: float = 0.30,
        contradiction_delta: float = 0.25,
    ) -> None:
        scenario = await self.get(scenario_id)
        if scenario is None:
            return
        validate_transition(cast(MemoryState, scenario.memory_state), new_state)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE scenarios
                SET memory_state = ?, pinned = CASE WHEN ? = 'trusted' THEN 1 ELSE 0 END,
                    score = MAX(0.0, score - ?),
                    contradiction_signal = MIN(1.0, contradiction_signal + ?),
                    freshness = 'stale'
                WHERE id = ?
                """,
                (new_state, new_state, score_penalty, contradiction_delta, scenario_id),
            )
            await self._refresh_lifecycle_for_ids(db, [scenario_id])
            await db.commit()

    async def mark_stale_by_evidence(self, scenario_id: str, *, evidence_hashes_now: list[str] | None = None) -> None:
        scenario = await self.get(scenario_id)
        if scenario is None:
            return
        previous = list(json.loads(scenario.memory_evidence_hashes_json or "[]"))
        current = evidence_hashes_now if evidence_hashes_now is not None else previous
        decision = decide_invalidation(
            InvalidationContext(
                fingerprints_at_validation=previous,
                fingerprints_now=current,
                memory_confidence=float(scenario.memory_confidence),
            ),
            cast(MemoryState, scenario.memory_state),
        )
        if decision is None or decision.to_state == decision.from_state:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE scenarios SET memory_state = ?, pinned = CASE WHEN ?='trusted' THEN 1 ELSE 0 END WHERE id = ?",
                (decision.to_state, decision.to_state, scenario_id),
            )
            await self._refresh_lifecycle_for_ids(db, [scenario_id])
            await db.commit()

    async def merge_memory_section(
        self,
        scenario_id: str,
        *,
        section: MemorySection,
        body: str,
        evidence_hashes: list[str],
        mark_stale_older: bool = True,
    ) -> None:
        aliases = {
            "decision_digest": "decision_digests",
            "hotspots": "hotspot_notes",
            "decision_digests": "decision_digests",
            "hotspot_notes": "hotspot_notes",
            "invariants": "invariants",
            "conventions": "conventions",
            "feedback": "feedback",
        }
        normalized_section = aliases.get(str(section))
        if normalized_section is None:
            raise ValueError(f"Unknown memory section: {section}")
        scenario = await self.get(scenario_id)
        if scenario is None:
            return
        prepared = scenario.prepared_context or ""
        start_marker = f"<!-- vaner:memory:{normalized_section}:start"
        end_marker = f"<!-- vaner:memory:{normalized_section}:end -->"
        pattern = re.compile(
            rf"<!-- vaner:memory:{re.escape(normalized_section)}:start.*?-->.*?<!-- vaner:memory:{re.escape(normalized_section)}:end -->",
            re.DOTALL,
        )
        header = f"<!-- vaner:memory:{normalized_section}:start fingerprints={','.join(evidence_hashes)} validated_at={time.time():.3f} -->"
        block = f"{header}\n{body.strip()}\n{end_marker}"
        if normalized_section != "feedback":
            if start_marker in prepared:
                prepared = pattern.sub(block, prepared, count=1)
            else:
                prepared = (prepared.rstrip() + "\n\n" + block).strip() + "\n"
        else:
            # Keep up to 3 feedback entries.
            tag = f"feedback_{int(time.time())}"
            feedback_block = block.replace(f":{normalized_section}:start", f":{tag}:start").replace(
                f":{normalized_section}:end", f":{tag}:end"
            )
            prepared = (prepared.rstrip() + "\n\n" + feedback_block).strip() + "\n"
            entries = re.findall(r"(<!-- vaner:memory:feedback_\d+:start.*?-->(?:.|\n)*?<!-- vaner:memory:feedback_\d+:end -->)", prepared)
            if len(entries) > 3:
                to_remove = entries[: len(entries) - 3]
                for chunk in to_remove:
                    prepared = prepared.replace(chunk, "").strip() + "\n"
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE scenarios SET prepared_context = ? WHERE id = ?", (prepared, scenario_id))
            if mark_stale_older:
                await db.execute(
                    "UPDATE scenarios SET memory_state = CASE WHEN memory_state='trusted' THEN 'stale' ELSE memory_state END WHERE id = ?",
                    (scenario_id,),
                )
            await db.commit()

    async def scenario_memory_meta(self, scenario_id: str) -> MemoryMeta:
        scenario = await self.get(scenario_id)
        if scenario is None:
            return MemoryMeta(
                state="candidate",
                confidence=0.0,
                last_validated_at=0.0,
                evidence_count=0,
                prior_successes=0,
                contradiction_signal=0.0,
            )
        hashes = list(json.loads(scenario.memory_evidence_hashes_json or "[]"))
        return MemoryMeta(
            state=cast(MemoryState, scenario.memory_state),
            confidence=float(scenario.memory_confidence),
            last_validated_at=float(scenario.memory_last_validated_at or 0.0),
            evidence_count=len(hashes),
            prior_successes=int(scenario.prior_successes),
            contradiction_signal=float(scenario.contradiction_signal),
        )

    async def _load_evidence_for_scenarios(self, db: aiosqlite.Connection, scenario_ids: list[str]) -> dict[str, list[aiosqlite.Row]]:
        if not scenario_ids:
            return {}
        placeholders = ", ".join("?" for _ in scenario_ids)
        query = f"""
            SELECT scenario_id, evidence_key, source_path, excerpt, weight
            FROM scenario_evidence
            WHERE scenario_id IN ({placeholders})
            ORDER BY scenario_id, weight DESC, evidence_key
        """
        cur = await db.execute(query, scenario_ids)
        rows = await cur.fetchall()
        grouped: dict[str, list[aiosqlite.Row]] = defaultdict(list)
        for row in rows:
            grouped[str(row["scenario_id"])].append(row)
        return grouped

    def _row_to_scenario(self, row: aiosqlite.Row, evidence_rows: list[aiosqlite.Row]) -> Scenario:
        evidence = [
            EvidenceRef(
                key=str(item["evidence_key"]),
                source_path=str(item["source_path"] or ""),
                excerpt=str(item["excerpt"] or ""),
                weight=float(item["weight"] or 0.0),
            )
            for item in evidence_rows
        ]
        return Scenario(
            id=str(row["id"]),
            kind=cast(ScenarioKind, str(row["kind"])),
            score=float(row["score"]),
            confidence=float(row["confidence"]),
            entities=list(json.loads(row["entities_json"] or "[]")),
            evidence=evidence,
            prepared_context=str(row["prepared_context"] or ""),
            coverage_gaps=list(json.loads(row["coverage_gaps_json"] or "[]")),
            freshness=cast(ScenarioFreshness, str(row["freshness"])),
            cost_to_expand=cast(ScenarioCost, str(row["cost_to_expand"])),
            created_at=float(row["created_at"]),
            expanded_at=float(row["expanded_at"]) if row["expanded_at"] is not None else None,
            last_refreshed_at=float(row["last_refreshed_at"]),
            last_outcome=cast(ScenarioOutcome | None, str(row["last_outcome"]) if row["last_outcome"] else None),
            context_envelope_json=str(row["context_envelope_json"] if "context_envelope_json" in row.keys() else "{}"),
            memory_state=cast(MemoryState, str(row["memory_state"] if "memory_state" in row.keys() else "candidate")),
            memory_confidence=float(row["memory_confidence"] if "memory_confidence" in row.keys() else 0.0),
            memory_last_validated_at=(
                float(row["memory_last_validated_at"])
                if "memory_last_validated_at" in row.keys() and row["memory_last_validated_at"] is not None
                else None
            ),
            memory_evidence_hashes_json=str(row["memory_evidence_hashes_json"] if "memory_evidence_hashes_json" in row.keys() else "[]"),
            prior_successes=int(row["prior_successes"] if "prior_successes" in row.keys() else 0),
            contradiction_signal=float(row["contradiction_signal"] if "contradiction_signal" in row.keys() else 0.0),
            pinned=int(row["pinned"] if "pinned" in row.keys() else 0),
            relevance=float(row["relevance"] if "relevance" in row.keys() else row["score"]),
            visible_priority=float(row["visible_priority"] if "visible_priority" in row.keys() else row["score"]),
            readiness=cast(ScenarioReadiness, str(row["readiness"] if "readiness" in row.keys() else "unprepared")),
            visibility=cast(ScenarioVisibility, str(row["visibility"] if "visibility" in row.keys() else "warming")),
            lifecycle_motion=cast(
                ScenarioLifecycleMotion,
                str(row["lifecycle_motion"] if "lifecycle_motion" in row.keys() else "stable"),
            ),
            last_reinforced_at=(
                float(row["last_reinforced_at"]) if "last_reinforced_at" in row.keys() and row["last_reinforced_at"] is not None else None
            ),
            archived_at=(float(row["archived_at"]) if "archived_at" in row.keys() and row["archived_at"] is not None else None),
            visibility_reason=str(row["visibility_reason"] if "visibility_reason" in row.keys() else ""),
        )

    async def _refresh_lifecycle_for_ids(self, db: aiosqlite.Connection, scenario_ids: list[str], *, now: float | None = None) -> None:
        if not scenario_ids:
            return
        sample_ts = time.time() if now is None else now
        placeholders = ", ".join("?" for _ in scenario_ids)
        db.row_factory = aiosqlite.Row
        cur = await db.execute(f"SELECT * FROM scenarios WHERE id IN ({placeholders})", scenario_ids)
        rows = await cur.fetchall()
        evidence_map = await self._load_evidence_for_scenarios(db, [str(row["id"]) for row in rows])
        for row in rows:
            scenario = self._row_to_scenario(row, evidence_map.get(str(row["id"]), []))
            refreshed = apply_scenario_lifecycle(scenario, now=now)
            await db.execute(
                """
                UPDATE scenarios
                SET relevance = ?, visible_priority = ?, readiness = ?, visibility = ?,
                    lifecycle_motion = ?, last_reinforced_at = ?, archived_at = ?,
                    visibility_reason = ?
                WHERE id = ?
                """,
                (
                    refreshed.relevance,
                    refreshed.visible_priority,
                    refreshed.readiness,
                    refreshed.visibility,
                    refreshed.lifecycle_motion,
                    refreshed.last_reinforced_at,
                    refreshed.archived_at,
                    refreshed.visibility_reason,
                    refreshed.id,
                ),
            )
            await self._record_sample_if_changed(db, refreshed, ts=sample_ts)

    async def _record_sample_if_changed(self, db: aiosqlite.Connection, scenario: Scenario, *, ts: float) -> None:
        cur = await db.execute(
            """
            SELECT ts, relevance, readiness, confidence, freshness, visible_priority,
                   visibility, lifecycle_motion, status, pinned, active
            FROM scenario_samples
            WHERE scenario_id = ?
            ORDER BY ts DESC
            LIMIT 1
            """,
            (scenario.id,),
        )
        latest = await cur.fetchone()
        status = _scenario_status(scenario)
        active = 1 if status == "active" else 0
        pinned = 1 if int(scenario.pinned) else 0
        if latest is not None:
            age = ts - float(latest[0])
            numeric_delta = max(
                abs(float(latest[1]) - float(scenario.relevance)),
                abs(float(latest[3]) - float(scenario.confidence)),
                abs(float(latest[5]) - float(scenario.visible_priority)),
            )
            categorical_same = (
                str(latest[2]) == scenario.readiness
                and str(latest[4]) == scenario.freshness
                and str(latest[6]) == scenario.visibility
                and str(latest[7]) == scenario.lifecycle_motion
                and str(latest[8]) == status
                and int(latest[9]) == pinned
                and int(latest[10]) == active
            )
            if categorical_same and age < SCENARIO_SAMPLE_MIN_INTERVAL_SECONDS:
                return
            if categorical_same and numeric_delta < 0.002 and age < 300:
                return
        await db.execute(
            """
            INSERT INTO scenario_samples (
                ts, scenario_id, relevance, readiness, confidence, freshness,
                visible_priority, visibility, lifecycle_motion, status, pinned,
                active, cycle_id, job_id, source_event_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
            """,
            (
                float(ts),
                scenario.id,
                float(scenario.relevance),
                scenario.readiness,
                float(scenario.confidence),
                scenario.freshness,
                float(scenario.visible_priority),
                scenario.visibility,
                scenario.lifecycle_motion,
                status,
                pinned,
                active,
            ),
        )
        await db.execute(
            """
            DELETE FROM scenario_samples
            WHERE id IN (
                SELECT id FROM scenario_samples
                ORDER BY ts DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (SCENARIO_SAMPLE_KEEP_ROWS,),
        )

    def _sample_from_row(self, row: aiosqlite.Row) -> ScenarioSample:
        return ScenarioSample(
            ts=float(row["ts"]),
            scenario_id=str(row["scenario_id"]),
            relevance=float(row["relevance"]),
            readiness=str(row["readiness"]),
            confidence=float(row["confidence"]),
            freshness=str(row["freshness"]),
            visible_priority=float(row["visible_priority"]),
            visibility=str(row["visibility"]),
            lifecycle_motion=str(row["lifecycle_motion"]),
            status=str(row["status"]),
            pinned=bool(row["pinned"]),
            active=bool(row["active"]),
            cycle_id=str(row["cycle_id"]) if row["cycle_id"] is not None else None,
            job_id=str(row["job_id"]) if row["job_id"] is not None else None,
            source_event_id=str(row["source_event_id"]) if row["source_event_id"] is not None else None,
        )

    async def _add_column_if_missing(self, db: aiosqlite.Connection, ddl: str) -> None:
        try:
            await db.execute(ddl)
        except aiosqlite.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise


def _scenario_status(scenario: Scenario) -> str:
    if scenario.last_outcome == "useful":
        return "completed"
    if scenario.last_outcome in {"irrelevant", "wrong"}:
        return "rejected"
    if scenario.visibility == "archived" or scenario.freshness == "stale":
        return "stale"
    if scenario.readiness == "ready":
        return "ready"
    if scenario.readiness == "cooling":
        return "cooling"
    return "prep"
