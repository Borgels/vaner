"""Local telemetry store for Vaner preparation engine runs."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TelemetryEvent:
    id: int
    ts: float
    event_type: str
    context_key: str
    duration_ms: float
    artifact_count: int
    error: str


class TelemetryStore:
    """SQLite-backed telemetry for preparation engine events."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                event_type TEXT NOT NULL,
                context_key TEXT NOT NULL DEFAULT '',
                duration_ms REAL NOT NULL DEFAULT 0,
                artifact_count INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT ''
            )
        """)
        self._conn.commit()

    def record_prep_run(
        self,
        context_key: str,
        duration_ms: float,
        artifact_count: int,
        error: str = "",
    ) -> None:
        self._conn.execute(
            "INSERT INTO events (ts, event_type, context_key, duration_ms, artifact_count, error) "
            "VALUES (?, 'prep_run', ?, ?, ?, ?)",
            (time.time(), context_key, duration_ms, artifact_count, error),
        )
        self._conn.commit()

    def record_artifact_generated(
        self,
        kind: str,
        source_path: str,
        model: str,
        duration_ms: float,
    ) -> None:
        self._conn.execute(
            "INSERT INTO events (ts, event_type, context_key, duration_ms, artifact_count, error) "
            "VALUES (?, 'artifact_generated', ?, ?, 1, '')",
            (time.time(), f"{kind}:{source_path}", duration_ms),
        )
        self._conn.commit()

    def get_recent(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        rows = self._conn.execute(
            "SELECT event_type, error, duration_ms, artifact_count FROM events"
        ).fetchall()
        total = len([r for r in rows if r["event_type"] == "prep_run"])
        failed = len([r for r in rows if r["event_type"] == "prep_run" and r["error"]])
        durations = [r["duration_ms"] for r in rows if r["event_type"] == "prep_run"]
        artifacts = sum(r["artifact_count"] for r in rows if r["event_type"] == "artifact_generated")
        return {
            "total_runs": total,
            "successful_runs": total - failed,
            "failed_runs": failed,
            "avg_duration_ms": sum(durations) / len(durations) if durations else 0.0,
            "total_artifacts_generated": artifacts,
        }
