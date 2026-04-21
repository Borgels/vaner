# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, cast

import aiosqlite

from vaner.events import publish as publish_event
from vaner.intent.scenario_scorer import scenario_score
from vaner.models.scenario import (
    EvidenceRef,
    Scenario,
    ScenarioCost,
    ScenarioFreshness,
    ScenarioKind,
    ScenarioOutcome,
)

_EVENT_COLORS = {
    "score": "var(--fg-3)",
    "expand": "var(--accent)",
    "user": "var(--amber)",
    "pin": "var(--amber)",
    "stale": "var(--fg-4)",
}


def _event_timestamp() -> str:
    now = time.time()
    minutes = int(now // 60) % 60
    seconds = int(now) % 60
    centiseconds = int((now - int(now)) * 100)
    return f"{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


class ScenarioStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._subscribers: set[asyncio.Queue[dict[str, Any] | None]] = set()

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
                CREATE TABLE IF NOT EXISTS pinned_facts (
                    id TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_scenarios_kind ON scenarios(kind)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_scenarios_score ON scenarios(score DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_scenarios_freshness ON scenarios(freshness)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_scenario_evidence_sid ON scenario_evidence(scenario_id)")
            await self._add_column_if_missing(db, "ALTER TABLE scenarios ADD COLUMN memory_state TEXT NOT NULL DEFAULT 'candidate'")
            await self._add_column_if_missing(db, "ALTER TABLE scenarios ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
            await self._add_column_if_missing(db, "ALTER TABLE scenario_evidence ADD COLUMN start_line INTEGER")
            await self._add_column_if_missing(db, "ALTER TABLE scenario_evidence ADD COLUMN end_line INTEGER")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS skill_weights (
                    name TEXT PRIMARY KEY,
                    weight REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            await db.commit()

    def subscribe(self) -> asyncio.Queue[dict[str, Any] | None]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=200)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        self._subscribers.discard(queue)

    def _publish(self, tag: str, scn_id: str | None, msg: str) -> None:
        # Fan out to the unified process-global event bus first so every
        # /events/stream subscriber sees scenario mutations alongside daemon,
        # LLM, artefact, and proxy-decision activity.
        publish_event("scenarios", tag, {"msg": msg}, scn=scn_id)
        if not self._subscribers:
            return
        payload = {
            "id": f"ev_{uuid.uuid4().hex[:8]}",
            "t": _event_timestamp(),
            "tag": tag,
            "color": _EVENT_COLORS.get(tag, "var(--fg-3)"),
            "msg": msg,
            "scn": scn_id,
        }
        stale_queues: list[asyncio.Queue[dict[str, Any] | None]] = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                stale_queues.append(queue)
        for queue in stale_queues:
            self._subscribers.discard(queue)

    async def upsert(self, scenario: Scenario) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            existing_cur = await db.execute("SELECT 1 FROM scenarios WHERE id = ?", (scenario.id,))
            existed = await existing_cur.fetchone() is not None
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
                    INSERT INTO scenario_evidence (
                        scenario_id, evidence_key, source_path, excerpt, weight,
                        start_line, end_line
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scenario.id,
                        evidence.key,
                        evidence.source_path,
                        evidence.excerpt,
                        evidence.weight,
                        evidence.start_line,
                        evidence.end_line,
                    ),
                )
            await db.commit()

        if existed:
            self._publish("score", scenario.id, f"{scenario.id} re-scored to {scenario.score:.3f}")
        else:
            self._publish("expand", scenario.id, f"{scenario.id} discovered in frontier")

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

    async def set_pinned(self, scenario_id: str, pinned: bool) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE scenarios SET pinned = ? WHERE id = ?", (1 if pinned else 0, scenario_id))
            await db.commit()
        self._publish("pin", scenario_id, f"{scenario_id} {'pinned' if pinned else 'unpinned'}")

    async def list_pinned_facts(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT id, text, created_at FROM pinned_facts ORDER BY created_at DESC, id DESC")
            rows = await cur.fetchall()
        return [{"id": str(row["id"]), "text": str(row["text"]), "created_at": float(row["created_at"])} for row in rows]

    async def add_pinned_fact(self, text: str) -> dict[str, Any]:
        fact = {"id": f"pf_{uuid.uuid4().hex[:8]}", "text": text, "created_at": time.time()}
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO pinned_facts (id, text, created_at) VALUES (?, ?, ?)",
                (fact["id"], fact["text"], fact["created_at"]),
            )
            await db.commit()
        return fact

    async def delete_pinned_fact(self, fact_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM pinned_facts WHERE id = ?", (fact_id,))
            await db.commit()

    async def record_expansion(self, scenario_id: str) -> None:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE scenarios SET freshness = 'fresh', expanded_at = ?, last_refreshed_at = ? WHERE id = ?",
                (now, now, scenario_id),
            )
            await db.commit()
        self._publish("expand", scenario_id, f"{scenario_id} expanded")

    async def record_outcome(self, scenario_id: str, outcome: str) -> None:
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
            await db.commit()
        self._publish("user", scenario_id, f"{scenario_id} marked {outcome}")

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
            changed = db.total_changes
            await db.commit()
        if changed:
            self._publish("stale", None, "scenario freshness updated")

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

    async def list_skill_weights(self) -> dict[str, float]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT name, weight FROM skill_weights")
            rows = await cur.fetchall()
        return {str(row["name"]): float(row["weight"]) for row in rows}

    async def set_skill_weight(self, name: str, weight: float) -> float:
        clamped = max(0.0, min(1.0, float(weight)))
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO skill_weights (name, weight, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET weight=excluded.weight, updated_at=excluded.updated_at
                """,
                (name, clamped, time.time()),
            )
            await db.commit()
        return clamped

    async def _add_column_if_missing(self, db: aiosqlite.Connection, statement: str) -> None:
        try:
            await db.execute(statement)
        except aiosqlite.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise

    async def _load_evidence_for_scenarios(self, db: aiosqlite.Connection, scenario_ids: list[str]) -> dict[str, list[aiosqlite.Row]]:
        if not scenario_ids:
            return {}
        placeholders = ", ".join("?" for _ in scenario_ids)
        query = f"""
            SELECT scenario_id, evidence_key, source_path, excerpt, weight, start_line, end_line
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
        def _optional_int(row: aiosqlite.Row, key: str) -> int | None:
            try:
                value = row[key]
            except (IndexError, KeyError):
                return None
            return int(value) if value is not None else None

        evidence = [
            EvidenceRef(
                key=str(item["evidence_key"]),
                source_path=str(item["source_path"] or ""),
                excerpt=str(item["excerpt"] or ""),
                weight=float(item["weight"] or 0.0),
                start_line=_optional_int(item, "start_line"),
                end_line=_optional_int(item, "end_line"),
            )
            for item in evidence_rows
        ]
        scenario = Scenario(
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
            memory_state=str(row["memory_state"] or "candidate"),
            pinned=bool(row["pinned"]),
        )
        return scenario
