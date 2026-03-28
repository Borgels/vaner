"""Durable job state store backed by SQLite.

Provides checkpointing, idempotency, status tracking, and dead-letter
quarantine for all background workflows in the vaner runtime.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from threading import Lock

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_STATUSES = frozenset(
    {"pending", "running", "completed", "failed", "cancelled", "dead_letter"}
)

# Legal transitions: from_status -> set of allowed to_status
_LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"running", "cancelled"}),
    "running": frozenset({"completed", "failed", "cancelled"}),
    "failed": frozenset({"pending", "dead_letter"}),
    "dead_letter": frozenset({"pending"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    workflow_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed','cancelled','dead_letter')),
    checkpoint_data TEXT,
    idempotency_key TEXT UNIQUE,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    last_error TEXT,
    context_key TEXT,
    priority INTEGER DEFAULT 50,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    quarantine_until REAL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_context_key ON jobs(context_key);
CREATE INDEX IF NOT EXISTS idx_jobs_idempotency ON jobs(idempotency_key);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if d.get("checkpoint_data") is not None:
        try:
            d["checkpoint_data"] = json.loads(d["checkpoint_data"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d


# ---------------------------------------------------------------------------
# JobStore
# ---------------------------------------------------------------------------


class JobStore:
    """Thread-safe, SQLite-backed durable job state store."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._lock = Lock()
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(_SCHEMA)
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_job(
        self,
        workflow_type: str,
        idempotency_key: str,
        context_key: str = "",
        priority: int = 50,
        max_retries: int = 3,
        checkpoint_data: dict | None = None,
    ) -> str:
        """Create a new job. Returns job_id.

        Raises ValueError if idempotency_key already exists and the job is
        not in a terminal state (completed or cancelled).
        """
        now = time.time()
        job_id = str(uuid.uuid4())
        checkpoint_json = json.dumps(checkpoint_data) if checkpoint_data is not None else None

        with self._lock:
            conn = self._connect()
            try:
                # Check for existing idempotency key
                existing = conn.execute(
                    "SELECT job_id, status FROM jobs WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing:
                    status = existing["status"]
                    if status not in ("completed", "cancelled"):
                        raise ValueError(
                            f"Job with idempotency_key {idempotency_key!r} already exists "
                            f"in non-terminal state {status!r} (job_id={existing['job_id']})"
                        )
                    # Terminal — allow re-creation by deleting the old entry
                    conn.execute("DELETE FROM jobs WHERE idempotency_key = ?", (idempotency_key,))

                conn.execute(
                    """
                    INSERT INTO jobs (
                        job_id, workflow_type, status, checkpoint_data,
                        idempotency_key, retry_count, max_retries, last_error,
                        context_key, priority, created_at, updated_at, quarantine_until
                    ) VALUES (?, ?, 'pending', ?, ?, 0, ?, NULL, ?, ?, ?, ?, NULL)
                    """,
                    (
                        job_id, workflow_type, checkpoint_json,
                        idempotency_key, max_retries,
                        context_key, priority, now, now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        return job_id

    def get_job(self, job_id: str) -> dict | None:
        """Return job as dict or None."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
                return _row_to_dict(row) if row else None
            finally:
                conn.close()

    def get_by_idempotency_key(self, key: str) -> dict | None:
        """Return job by idempotency key or None."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE idempotency_key = ?", (key,)
                ).fetchone()
                return _row_to_dict(row) if row else None
            finally:
                conn.close()

    def update_status(self, job_id: str, status: str, error: str | None = None) -> None:
        """Update job status. Validates that the transition is legal."""
        if status not in _VALID_STATUSES:
            raise ValueError(f"Invalid status: {status!r}")

        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT status FROM jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(f"Job not found: {job_id!r}")

                current = row["status"]
                allowed = _LEGAL_TRANSITIONS.get(current, frozenset())
                if status not in allowed:
                    raise ValueError(
                        f"Illegal status transition for job {job_id!r}: "
                        f"{current!r} → {status!r}"
                    )

                conn.execute(
                    "UPDATE jobs SET status = ?, last_error = ?, updated_at = ? WHERE job_id = ?",
                    (status, error, time.time(), job_id),
                )
                conn.commit()
            finally:
                conn.close()

    def checkpoint(self, job_id: str, data: dict) -> None:
        """Save checkpoint data. Transitions status to 'running' if currently pending."""
        checkpoint_json = json.dumps(data)
        now = time.time()

        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT status FROM jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(f"Job not found: {job_id!r}")

                current = row["status"]
                new_status = "running" if current == "pending" else current

                conn.execute(
                    "UPDATE jobs SET checkpoint_data = ?, status = ?, updated_at = ? WHERE job_id = ?",
                    (checkpoint_json, new_status, now, job_id),
                )
                conn.commit()
            finally:
                conn.close()

    def increment_retry(self, job_id: str) -> int:
        """Increment retry_count. Returns new count."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE jobs SET retry_count = retry_count + 1, updated_at = ? WHERE job_id = ?",
                    (time.time(), job_id),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT retry_count FROM jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(f"Job not found: {job_id!r}")
                return row["retry_count"]
            finally:
                conn.close()

    def quarantine(self, job_id: str, ttl_seconds: float = 3600.0) -> None:
        """Move job to dead_letter, set quarantine_until = now + ttl."""
        quarantine_until = time.time() + ttl_seconds
        now = time.time()

        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT status FROM jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(f"Job not found: {job_id!r}")

                current = row["status"]
                allowed = _LEGAL_TRANSITIONS.get(current, frozenset())
                if "dead_letter" not in allowed:
                    raise ValueError(
                        f"Cannot quarantine job {job_id!r} from status {current!r}"
                    )

                conn.execute(
                    "UPDATE jobs SET status = 'dead_letter', quarantine_until = ?, updated_at = ? WHERE job_id = ?",
                    (quarantine_until, now, job_id),
                )
                conn.commit()
            finally:
                conn.close()

    def release_quarantine(self, job_id: str) -> None:
        """Clear quarantine_until and reset status to pending."""
        now = time.time()

        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT status FROM jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(f"Job not found: {job_id!r}")
                if row["status"] != "dead_letter":
                    raise ValueError(
                        f"Job {job_id!r} is not in dead_letter state (status={row['status']!r})"
                    )

                conn.execute(
                    "UPDATE jobs SET status = 'pending', quarantine_until = NULL, updated_at = ? WHERE job_id = ?",
                    (now, job_id),
                )
                conn.commit()
            finally:
                conn.close()

    def cancel_by_context_key(self, context_key: str) -> int:
        """Cancel all running/pending jobs with this context_key. Returns count cancelled."""
        now = time.time()

        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'cancelled', updated_at = ?
                    WHERE context_key = ? AND status IN ('pending', 'running')
                    """,
                    (now, context_key),
                )
                conn.commit()
                return cursor.rowcount
            finally:
                conn.close()

    def list_jobs(
        self,
        status: str | None = None,
        context_key: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """List jobs with optional filters."""
        query = "SELECT * FROM jobs WHERE 1=1"
        params: list = []

        if status is not None:
            query += " AND status = ?"
            params.append(status)
        if context_key is not None:
            query += " AND context_key = ?"
            params.append(context_key)

        query += " ORDER BY priority ASC, created_at ASC LIMIT ?"
        params.append(limit)

        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(query, params).fetchall()
                return [_row_to_dict(r) for r in rows]
            finally:
                conn.close()

    def list_resumable(self) -> list[dict]:
        """Return all jobs in 'running' status (crashed and need recovery check)."""
        return self.list_jobs(status="running", limit=1000)

    def list_quarantined(self) -> list[dict]:
        """Return all dead_letter jobs where quarantine_until > now."""
        now = time.time()

        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status = 'dead_letter' AND quarantine_until > ? ORDER BY quarantine_until ASC",
                    (now,),
                ).fetchall()
                return [_row_to_dict(r) for r in rows]
            finally:
                conn.close()

    def purge_completed(self, older_than_seconds: float = 86400.0) -> int:
        """Delete completed/cancelled jobs older than threshold. Returns count purged."""
        cutoff = time.time() - older_than_seconds

        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "DELETE FROM jobs WHERE status IN ('completed', 'cancelled') AND updated_at < ?",
                    (cutoff,),
                )
                conn.commit()
                return cursor.rowcount
            finally:
                conn.close()
