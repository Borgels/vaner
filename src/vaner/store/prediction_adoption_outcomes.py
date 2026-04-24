# SPDX-License-Identifier: Apache-2.0
"""WS4 — Prediction adoption-outcome SQLite DAO (0.8.4).

One table: ``prediction_adoption_outcomes``. DDL is owned by
:func:`create_prediction_adoption_outcomes_table`, called from
``ArtefactStore.initialize()`` so the table lives alongside the 0.8.2
intent / 0.8.3 deep-run tables in the artefact DB.

DAO functions are module-level and take ``db_path`` to match the
``ArtefactStore`` per-method-connection pattern.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import cast

import aiosqlite

from vaner.models.prediction_adoption_outcome import (
    AdoptionOutcomeState,
    PredictionAdoptionOutcome,
)


async def create_prediction_adoption_outcomes_table(db: aiosqlite.Connection) -> None:
    """Create the adoption-outcome table + indices on an open connection.

    Called from :meth:`ArtefactStore.initialize` so DDL happens inside
    the same connection / transaction the rest of the artefact DB uses.
    Safe to call repeatedly — every statement is ``IF NOT EXISTS``.
    """

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS prediction_adoption_outcomes (
            id TEXT PRIMARY KEY,
            prediction_id TEXT NOT NULL,
            prediction_label_hash TEXT NOT NULL,
            adopted_at REAL NOT NULL,
            revision_at_adoption INTEGER NOT NULL,
            had_kept_maturation INTEGER NOT NULL,
            workspace_root TEXT NOT NULL,
            source TEXT NOT NULL,
            outcome TEXT NOT NULL DEFAULT 'pending',
            resolved_at REAL,
            rollback_reason TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_adoption_outcomes_label_hash ON prediction_adoption_outcomes(prediction_label_hash)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_adoption_outcomes_outcome ON prediction_adoption_outcomes(outcome)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_adoption_outcomes_resolved ON prediction_adoption_outcomes(resolved_at DESC)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_adoption_outcomes_prediction_id ON prediction_adoption_outcomes(prediction_id)")


_COLUMNS = (
    "id, prediction_id, prediction_label_hash, adopted_at, revision_at_adoption, "
    "had_kept_maturation, workspace_root, source, outcome, resolved_at, "
    "rollback_reason, metadata_json"
)


def _row_to_outcome(row: aiosqlite.Row) -> PredictionAdoptionOutcome:
    metadata_raw = json.loads(row["metadata_json"] or "{}")
    return PredictionAdoptionOutcome(
        id=str(row["id"]),
        prediction_id=str(row["prediction_id"]),
        prediction_label_hash=str(row["prediction_label_hash"]),
        adopted_at=float(row["adopted_at"]),
        revision_at_adoption=int(row["revision_at_adoption"]),
        had_kept_maturation=bool(row["had_kept_maturation"]),
        workspace_root=str(row["workspace_root"]),
        source=str(row["source"]),
        outcome=cast(AdoptionOutcomeState, str(row["outcome"])),
        resolved_at=None if row["resolved_at"] is None else float(row["resolved_at"]),
        rollback_reason=None if row["rollback_reason"] is None else str(row["rollback_reason"]),
        metadata={str(k): str(v) for k, v in metadata_raw.items()},
    )


async def create_outcome(db_path: Path, outcome: PredictionAdoptionOutcome) -> PredictionAdoptionOutcome:
    """Insert a new outcome row. Typically called at adoption time with
    ``outcome.outcome == 'pending'``."""

    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute(
            f"""
            INSERT INTO prediction_adoption_outcomes ({_COLUMNS})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                outcome.id,
                outcome.prediction_id,
                outcome.prediction_label_hash,
                float(outcome.adopted_at),
                int(outcome.revision_at_adoption),
                int(bool(outcome.had_kept_maturation)),
                outcome.workspace_root,
                outcome.source,
                outcome.outcome,
                None if outcome.resolved_at is None else float(outcome.resolved_at),
                outcome.rollback_reason,
                json.dumps(dict(outcome.metadata)),
            ),
        )
        await db.commit()
    return outcome


async def get_outcome(db_path: Path, outcome_id: str) -> PredictionAdoptionOutcome | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT {_COLUMNS} FROM prediction_adoption_outcomes WHERE id = ?",
            (outcome_id,),
        ) as cursor:
            row = await cursor.fetchone()
    return None if row is None else _row_to_outcome(row)


async def list_pending_outcomes(db_path: Path, *, limit: int = 500) -> list[PredictionAdoptionOutcome]:
    """List pending outcomes, oldest first (so the sweep resolves them
    in adoption order). The sweep bounds the query by ``limit`` to cap
    per-cycle work."""

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT {_COLUMNS} FROM prediction_adoption_outcomes WHERE outcome = 'pending' ORDER BY adopted_at ASC LIMIT ?",
            (int(limit),),
        ) as cursor:
            rows = await cursor.fetchall()
    return [_row_to_outcome(r) for r in rows]


async def list_by_label_hash(
    db_path: Path,
    label_hash: str,
    *,
    limit: int = 100,
) -> list[PredictionAdoptionOutcome]:
    """Fetch the history of outcomes for a given prediction label_hash.

    Used by the adoption_success_factor lookup in
    ``score_maturation_value()`` — aggregates confirmed / rejected
    counts to bias refinement scoring.
    """

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT {_COLUMNS} FROM prediction_adoption_outcomes WHERE prediction_label_hash = ? ORDER BY adopted_at DESC LIMIT ?",
            (label_hash, int(limit)),
        ) as cursor:
            rows = await cursor.fetchall()
    return [_row_to_outcome(r) for r in rows]


async def update_outcome_state(
    db_path: Path,
    outcome_id: str,
    *,
    outcome: AdoptionOutcomeState,
    resolved_at: float,
    rollback_reason: str | None = None,
) -> bool:
    """Transition a pending outcome to a terminal state. Returns ``True``
    if a row matched and was updated."""

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            """
            UPDATE prediction_adoption_outcomes
            SET outcome = ?, resolved_at = ?, rollback_reason = ?
            WHERE id = ? AND outcome = 'pending'
            """,
            (outcome, float(resolved_at), rollback_reason, outcome_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def update_pending_by_prediction_id(
    db_path: Path,
    prediction_ids: Iterable[str],
    *,
    outcome: AdoptionOutcomeState,
    resolved_at: float,
    rollback_reason: str | None = None,
) -> int:
    """Batch-resolve all pending outcomes for a set of prediction ids.

    Used by the engine when a ``rollback_kept_maturation()`` fires or
    when an invalidation signal stales a prediction whose adoption is
    still pending. Returns the number of rows updated.
    """

    ids = list(prediction_ids)
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            f"""
            UPDATE prediction_adoption_outcomes
            SET outcome = ?, resolved_at = ?, rollback_reason = ?
            WHERE outcome = 'pending' AND prediction_id IN ({placeholders})
            """,
            (outcome, float(resolved_at), rollback_reason, *ids),
        )
        await db.commit()
        return cursor.rowcount


async def count_by_outcome_for_label(db_path: Path, label_hash: str) -> dict[AdoptionOutcomeState, int]:
    """Return a dict mapping each terminal outcome state to its count for
    the given label_hash. Used by ``score_maturation_value()``."""

    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            """
            SELECT outcome, COUNT(*) FROM prediction_adoption_outcomes
            WHERE prediction_label_hash = ?
            GROUP BY outcome
            """,
            (label_hash,),
        ) as cursor:
            rows = await cursor.fetchall()
    counts: dict[AdoptionOutcomeState, int] = {
        "pending": 0,
        "confirmed": 0,
        "rejected": 0,
        "stale": 0,
    }
    for row in rows:
        key = cast(AdoptionOutcomeState, str(row[0]))
        counts[key] = int(row[1])
    return counts


__all__ = [
    "count_by_outcome_for_label",
    "create_outcome",
    "create_prediction_adoption_outcomes_table",
    "get_outcome",
    "list_by_label_hash",
    "list_pending_outcomes",
    "update_outcome_state",
    "update_pending_by_prediction_id",
]
