# SPDX-License-Identifier: Apache-2.0
"""WS1 — SQLite persistence for Deep-Run sessions and pass log (0.8.3).

Two tables:

- ``deep_run_sessions`` — one row per declared away window.
  Single-active-session is enforced by a UNIQUE partial index on
  ``status='active'`` plus a defensive check in :func:`create_session`.
- ``deep_run_pass_log`` — one row per per-prediction action during a
  session (maturation kept / discarded / rolled-back / failed, plus
  ``promoted`` / ``explored``). Append-only audit trail; never updated
  in place.

DDL is owned by :func:`create_deep_run_tables`, which is called from
``ArtefactStore.initialize()`` so all tables in the artefact DB are
declared together. DAO functions are module-level and take ``db_path``
to match the ``ArtefactStore`` per-method-connection pattern; the engine
imports them directly.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import cast

import aiosqlite

from vaner.intent.deep_run import (
    DeepRunFocus,
    DeepRunHorizonBias,
    DeepRunLocality,
    DeepRunPassAction,
    DeepRunPassLogEntry,
    DeepRunPauseReason,
    DeepRunPreset,
    DeepRunSession,
    DeepRunStatus,
)


async def create_deep_run_tables(db: aiosqlite.Connection) -> None:
    """Create the Deep-Run tables and indices on an open connection.

    Called from :meth:`ArtefactStore.initialize` so DDL happens inside
    the same connection / transaction the rest of the artefact DB uses.
    Safe to call repeatedly — every statement is ``IF NOT EXISTS``.
    """

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS deep_run_sessions (
            id TEXT PRIMARY KEY,
            started_at REAL NOT NULL,
            ends_at REAL NOT NULL,
            preset TEXT NOT NULL,
            focus TEXT NOT NULL,
            horizon_bias TEXT NOT NULL,
            locality TEXT NOT NULL,
            cost_cap_usd REAL NOT NULL DEFAULT 0.0,
            workspace_root TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            pause_reasons_json TEXT NOT NULL DEFAULT '[]',
            spend_usd REAL NOT NULL DEFAULT 0.0,
            cycles_run INTEGER NOT NULL DEFAULT 0,
            matured_kept INTEGER NOT NULL DEFAULT 0,
            matured_discarded INTEGER NOT NULL DEFAULT 0,
            matured_rolled_back INTEGER NOT NULL DEFAULT 0,
            matured_failed INTEGER NOT NULL DEFAULT 0,
            promoted_count INTEGER NOT NULL DEFAULT 0,
            ended_at REAL,
            cancelled_reason TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    # The UNIQUE partial index gives us belt-and-braces single-active-
    # session enforcement at the DB layer; create_session() also checks
    # explicitly to give callers a clean error rather than an
    # IntegrityError surface.
    await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_deep_run_sessions_active ON deep_run_sessions(status) WHERE status = 'active'")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_deep_run_sessions_started_at ON deep_run_sessions(started_at DESC)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_deep_run_sessions_status ON deep_run_sessions(status)")
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS deep_run_pass_log (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            prediction_id TEXT NOT NULL,
            pass_at REAL NOT NULL,
            action TEXT NOT NULL,
            cycle_index INTEGER NOT NULL,
            before_evidence_score REAL,
            after_evidence_score REAL,
            before_draft_hash TEXT,
            after_draft_hash TEXT,
            contract_json TEXT,
            judge_verdict_json TEXT
        )
        """
    )
    await db.execute("CREATE INDEX IF NOT EXISTS idx_deep_run_pass_log_session_id ON deep_run_pass_log(session_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_deep_run_pass_log_prediction_id ON deep_run_pass_log(prediction_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_deep_run_pass_log_pass_at ON deep_run_pass_log(pass_at DESC)")


class DeepRunActiveSessionExistsError(RuntimeError):
    """Raised when a caller tries to start a second Deep-Run session
    while one is already active. The engine surfaces this to the CLI /
    MCP / cockpit / desktop with a stable error message so all surfaces
    can render a consistent "another session is active" notice."""


def _row_to_session(row: aiosqlite.Row) -> DeepRunSession:
    pause_reasons_raw = json.loads(row["pause_reasons_json"] or "[]")
    metadata_raw = json.loads(row["metadata_json"] or "{}")
    return DeepRunSession(
        id=str(row["id"]),
        started_at=float(row["started_at"]),
        ends_at=float(row["ends_at"]),
        preset=cast(DeepRunPreset, str(row["preset"])),
        focus=cast(DeepRunFocus, str(row["focus"])),
        horizon_bias=cast(DeepRunHorizonBias, str(row["horizon_bias"])),
        locality=cast(DeepRunLocality, str(row["locality"])),
        cost_cap_usd=float(row["cost_cap_usd"]),
        workspace_root=str(row["workspace_root"]),
        status=cast(DeepRunStatus, str(row["status"])),
        pause_reasons=[cast(DeepRunPauseReason, str(r)) for r in pause_reasons_raw],
        spend_usd=float(row["spend_usd"]),
        cycles_run=int(row["cycles_run"]),
        matured_kept=int(row["matured_kept"]),
        matured_discarded=int(row["matured_discarded"]),
        matured_rolled_back=int(row["matured_rolled_back"]),
        matured_failed=int(row["matured_failed"]),
        promoted_count=int(row["promoted_count"]),
        ended_at=None if row["ended_at"] is None else float(row["ended_at"]),
        cancelled_reason=None if row["cancelled_reason"] is None else str(row["cancelled_reason"]),
        metadata={str(k): str(v) for k, v in metadata_raw.items()},
    )


_SESSION_COLUMNS = (
    "id, started_at, ends_at, preset, focus, horizon_bias, locality, "
    "cost_cap_usd, workspace_root, status, pause_reasons_json, spend_usd, "
    "cycles_run, matured_kept, matured_discarded, matured_rolled_back, "
    "matured_failed, promoted_count, ended_at, metadata_json, cancelled_reason"
)


async def create_session(db_path: Path, session: DeepRunSession) -> DeepRunSession:
    """Insert a new active session.

    Raises :class:`DeepRunActiveSessionExistsError` if another session is
    already active. The defensive SELECT runs inside a
    ``BEGIN IMMEDIATE`` write transaction so a concurrent caller's
    SELECT sees our in-progress insert and serialises on the write
    lock. The UNIQUE partial index on ``status='active'`` is the
    second line of defense if a caller bypasses this function — we
    also catch ``IntegrityError`` from the insert and translate it to
    the same domain exception so the caller never sees the low-level
    SQLite surface. (0.8.4 hardening — see HIGH-4 in
    docs/reviews/0.8.4-hardening.md.)
    """

    if session.status != "active":
        raise ValueError(f"create_session requires status='active', got {session.status!r}")
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout=5000")
        # BEGIN IMMEDIATE acquires a write lock up-front so concurrent
        # callers serialise rather than racing the SELECT-then-INSERT.
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute("SELECT id FROM deep_run_sessions WHERE status = 'active' LIMIT 1") as cursor:
                existing = await cursor.fetchone()
            if existing is not None:
                await db.execute("ROLLBACK")
                raise DeepRunActiveSessionExistsError(f"another Deep-Run session is already active: {existing[0]}")
            try:
                await db.execute(
                    f"""
                    INSERT INTO deep_run_sessions ({_SESSION_COLUMNS})
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session.id,
                        float(session.started_at),
                        float(session.ends_at),
                        session.preset,
                        session.focus,
                        session.horizon_bias,
                        session.locality,
                        float(session.cost_cap_usd),
                        session.workspace_root,
                        session.status,
                        json.dumps(list(session.pause_reasons)),
                        float(session.spend_usd),
                        int(session.cycles_run),
                        int(session.matured_kept),
                        int(session.matured_discarded),
                        int(session.matured_rolled_back),
                        int(session.matured_failed),
                        int(session.promoted_count),
                        None if session.ended_at is None else float(session.ended_at),
                        json.dumps(dict(session.metadata)),
                        session.cancelled_reason,
                    ),
                )
            except aiosqlite.IntegrityError as exc:
                # Belt-and-braces: the UNIQUE partial index on
                # status='active' should never fire now that we hold
                # the write lock, but translate if it ever does so the
                # caller always sees the domain exception.
                await db.execute("ROLLBACK")
                raise DeepRunActiveSessionExistsError("another Deep-Run session became active while we were inserting") from exc
            await db.commit()
        except Exception:
            # Any other exception also rolls back. The ``async with``
            # will close the connection; BEGIN IMMEDIATE's write lock
            # is released on connection close.
            raise
    return session


async def get_active_session(db_path: Path) -> DeepRunSession | None:
    """Return the single active session, or ``None`` if none.

    Treats both ``status='active'`` and ``status='paused'`` as
    "currently in flight" — the engine resumes a paused session when
    its pause reasons clear, so it is still the canonical record the
    surfaces should render.
    """

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT {_SESSION_COLUMNS} FROM deep_run_sessions WHERE status IN ('active', 'paused') ORDER BY started_at DESC LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
    return None if row is None else _row_to_session(row)


async def get_session(db_path: Path, session_id: str) -> DeepRunSession | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT {_SESSION_COLUMNS} FROM deep_run_sessions WHERE id = ?",
            (session_id,),
        ) as cursor:
            row = await cursor.fetchone()
    return None if row is None else _row_to_session(row)


async def list_sessions(
    db_path: Path,
    *,
    limit: int = 20,
    status: DeepRunStatus | None = None,
) -> list[DeepRunSession]:
    query = f"SELECT {_SESSION_COLUMNS} FROM deep_run_sessions"
    params: list[object] = []
    if status is not None:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY started_at DESC LIMIT ?"
    params.append(int(limit))
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
    return [_row_to_session(r) for r in rows]


async def update_session_status(
    db_path: Path,
    session_id: str,
    *,
    status: DeepRunStatus,
    ended_at: float | None = None,
    cancelled_reason: str | None = None,
    pause_reasons: Iterable[DeepRunPauseReason] | None = None,
) -> bool:
    """Update the status of a session. Returns ``True`` if a row matched."""

    fields: list[str] = ["status = ?"]
    params: list[object] = [status]
    if ended_at is not None:
        fields.append("ended_at = ?")
        params.append(float(ended_at))
    if cancelled_reason is not None:
        fields.append("cancelled_reason = ?")
        params.append(cancelled_reason)
    if pause_reasons is not None:
        fields.append("pause_reasons_json = ?")
        params.append(json.dumps([str(r) for r in pause_reasons]))
    params.append(session_id)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout=5000")
        cursor = await db.execute(
            f"UPDATE deep_run_sessions SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        await db.commit()
        return cursor.rowcount > 0


async def increment_session_counters(
    db_path: Path,
    session_id: str,
    *,
    cycles_run: int = 0,
    matured_kept: int = 0,
    matured_discarded: int = 0,
    matured_rolled_back: int = 0,
    matured_failed: int = 0,
    promoted_count: int = 0,
    spend_usd: float = 0.0,
) -> bool:
    """Atomically increment cumulative counters on the session row."""

    if not any(
        v != 0
        for v in (
            cycles_run,
            matured_kept,
            matured_discarded,
            matured_rolled_back,
            matured_failed,
            promoted_count,
            spend_usd,
        )
    ):
        return False
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout=5000")
        cursor = await db.execute(
            """
            UPDATE deep_run_sessions
            SET cycles_run = cycles_run + ?,
                matured_kept = matured_kept + ?,
                matured_discarded = matured_discarded + ?,
                matured_rolled_back = matured_rolled_back + ?,
                matured_failed = matured_failed + ?,
                promoted_count = promoted_count + ?,
                spend_usd = spend_usd + ?
            WHERE id = ?
            """,
            (
                int(cycles_run),
                int(matured_kept),
                int(matured_discarded),
                int(matured_rolled_back),
                int(matured_failed),
                int(promoted_count),
                float(spend_usd),
                session_id,
            ),
        )
        await db.commit()
        return cursor.rowcount > 0


async def insert_pass_log_entry(db_path: Path, entry: DeepRunPassLogEntry) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute(
            """
            INSERT INTO deep_run_pass_log (
                id, session_id, prediction_id, pass_at, action, cycle_index,
                before_evidence_score, after_evidence_score,
                before_draft_hash, after_draft_hash,
                contract_json, judge_verdict_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.id,
                entry.session_id,
                entry.prediction_id,
                float(entry.pass_at),
                entry.action,
                int(entry.cycle_index),
                entry.before_evidence_score,
                entry.after_evidence_score,
                entry.before_draft_hash,
                entry.after_draft_hash,
                entry.contract_json,
                entry.judge_verdict_json,
            ),
        )
        await db.commit()


async def list_pass_log_entries(
    db_path: Path,
    *,
    session_id: str | None = None,
    prediction_id: str | None = None,
    action: DeepRunPassAction | None = None,
    limit: int = 100,
) -> list[DeepRunPassLogEntry]:
    query = (
        "SELECT id, session_id, prediction_id, pass_at, action, cycle_index, "
        "before_evidence_score, after_evidence_score, before_draft_hash, "
        "after_draft_hash, contract_json, judge_verdict_json "
        "FROM deep_run_pass_log WHERE 1=1"
    )
    params: list[object] = []
    if session_id is not None:
        query += " AND session_id = ?"
        params.append(session_id)
    if prediction_id is not None:
        query += " AND prediction_id = ?"
        params.append(prediction_id)
    if action is not None:
        query += " AND action = ?"
        params.append(action)
    query += " ORDER BY pass_at DESC LIMIT ?"
    params.append(int(limit))
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
    return [
        DeepRunPassLogEntry(
            id=str(r["id"]),
            session_id=str(r["session_id"]),
            prediction_id=str(r["prediction_id"]),
            pass_at=float(r["pass_at"]),
            action=cast(DeepRunPassAction, str(r["action"])),
            cycle_index=int(r["cycle_index"]),
            before_evidence_score=None if r["before_evidence_score"] is None else float(r["before_evidence_score"]),
            after_evidence_score=None if r["after_evidence_score"] is None else float(r["after_evidence_score"]),
            before_draft_hash=None if r["before_draft_hash"] is None else str(r["before_draft_hash"]),
            after_draft_hash=None if r["after_draft_hash"] is None else str(r["after_draft_hash"]),
            contract_json=None if r["contract_json"] is None else str(r["contract_json"]),
            judge_verdict_json=None if r["judge_verdict_json"] is None else str(r["judge_verdict_json"]),
        )
        for r in rows
    ]


async def close_expired_sessions(db_path: Path, *, now: float) -> int:
    """Close any active/paused sessions whose ``ends_at`` is in the past.

    Called by the engine on startup so a daemon that was killed during a
    Deep-Run window does not leave a dangling "active" record long after
    the user expected it to end. Returns the number of sessions closed.

    Sessions whose ``ends_at`` is still in the future are left alone —
    the engine's resume-on-restart logic picks them up via
    :func:`get_active_session`.
    """

    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout=5000")
        cursor = await db.execute(
            """
            UPDATE deep_run_sessions
            SET status = 'ended', ended_at = ?,
                cancelled_reason = COALESCE(cancelled_reason, 'expired_on_restart')
            WHERE status IN ('active', 'paused') AND ends_at <= ?
            """,
            (float(now), float(now)),
        )
        await db.commit()
        return cursor.rowcount


__all__ = [
    "DeepRunActiveSessionExistsError",
    "close_expired_sessions",
    "create_deep_run_tables",
    "create_session",
    "get_active_session",
    "get_session",
    "increment_session_counters",
    "insert_pass_log_entry",
    "list_pass_log_entries",
    "list_sessions",
    "update_session_status",
]
