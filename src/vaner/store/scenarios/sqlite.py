from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import cast

import aiosqlite

from vaner.intent.scenario_scorer import scenario_score
from vaner.models.scenario import (
    EvidenceRef,
    Scenario,
    ScenarioCost,
    ScenarioFreshness,
    ScenarioKind,
    ScenarioOutcome,
)


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
                CREATE TABLE IF NOT EXISTS scenario_feedback (
                    id TEXT PRIMARY KEY,
                    scenario_id TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'graph',
                    skill TEXT,
                    result TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    processed INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_scenarios_kind ON scenarios(kind)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_scenarios_score ON scenarios(score DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_scenarios_freshness ON scenarios(freshness)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_scenario_evidence_sid ON scenario_evidence(scenario_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_scenario_feedback_processed ON scenario_feedback(processed, timestamp)")
            await db.commit()

    async def upsert(self, scenario: Scenario) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO scenarios (
                    id, kind, score, confidence, entities_json, prepared_context,
                    coverage_gaps_json, freshness, cost_to_expand, created_at,
                    expanded_at, last_refreshed_at, last_outcome
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    last_outcome=excluded.last_outcome
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
            await db.commit()

    async def list_top(self, *, kind: str | None = None, limit: int = 10) -> list[Scenario]:
        query = "SELECT * FROM scenarios"
        params: list[object] = []
        if kind:
            query += " WHERE kind = ?"
            params.append(kind)
        query += " ORDER BY score DESC, last_refreshed_at DESC LIMIT ?"
        params.append(max(1, limit))
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(query, params)
            rows = await cur.fetchall()
            evidence_map = await self._load_evidence_for_scenarios(db, [str(row["id"]) for row in rows])
        return [self._row_to_scenario(row, evidence_map.get(str(row["id"]), [])) for row in rows]

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
                "UPDATE scenarios SET freshness = 'fresh', expanded_at = ?, last_refreshed_at = ? WHERE id = ?",
                (now, now, scenario_id),
            )
            await db.commit()

    async def record_outcome(self, scenario_id: str, outcome: str, *, skill: str | None = None, source: str | None = None) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("UPDATE scenarios SET last_outcome = ? WHERE id = ?", (outcome, scenario_id))
            row_cur = await db.execute("SELECT * FROM scenarios WHERE id = ?", (scenario_id,))
            row = await row_cur.fetchone()
            if row is not None:
                evidence_map = await self._load_evidence_for_scenarios(db, [scenario_id])
                scenario = self._row_to_scenario(row, evidence_map.get(scenario_id, []))
                score = scenario_score(scenario)
                await db.execute("UPDATE scenarios SET score = ? WHERE id = ?", (score, scenario_id))
                now = time.time()
                resolved_source = source or ("skill" if skill else "graph")
                await db.execute(
                    """
                    INSERT INTO scenario_feedback(id, scenario_id, source, skill, result, timestamp, processed)
                    VALUES (?, ?, ?, ?, ?, ?, 0)
                    """,
                    (f"{scenario_id}:{now}", scenario_id, resolved_source, skill, outcome, now),
                )
            await db.commit()

    async def consume_feedback(self, *, limit: int = 200) -> list[tuple[str, bool]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT id, source, result
                FROM scenario_feedback
                WHERE processed = 0
                ORDER BY timestamp ASC
                LIMIT ?
                """,
                (max(1, limit),),
            )
            rows = await cur.fetchall()
            if not rows:
                return []
            ids = [str(row["id"]) for row in rows]
            placeholders = ", ".join("?" for _ in ids)
            await db.execute(f"UPDATE scenario_feedback SET processed = 1 WHERE id IN ({placeholders})", ids)
            await db.commit()
        return [(str(row["source"] or "graph"), str(row["result"] or "") in {"useful", "partial"}) for row in rows]

    async def mark_stale(self) -> None:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE scenarios
                SET freshness = CASE
                    WHEN ? - last_refreshed_at > 1800 THEN 'stale'
                    WHEN ? - last_refreshed_at > 300 THEN 'recent'
                    ELSE freshness
                END
                """,
                (now, now),
            )
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
        )
