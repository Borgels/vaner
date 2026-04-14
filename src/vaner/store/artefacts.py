# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time
from pathlib import Path

import aiosqlite

from vaner.models.artefact import Artefact, ArtefactKind
from vaner.models.session import WorkingSet


class ArtefactStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY)")
            await db.execute("INSERT OR IGNORE INTO schema_version(version) VALUES (1)")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS artefacts (
                    key TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    source_mtime REAL NOT NULL,
                    generated_at REAL NOT NULL,
                    model TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    relevance_score REAL NOT NULL DEFAULT 0.0,
                    access_count INTEGER NOT NULL DEFAULT 0,
                    last_accessed REAL,
                    signal_id TEXT
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_artefacts_kind ON artefacts(kind)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_artefacts_source_path ON artefacts(source_path)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_artefacts_generated_at ON artefacts(generated_at)")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS working_sets (
                    session_id TEXT PRIMARY KEY,
                    artefact_keys_json TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    reason TEXT NOT NULL
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_working_sets_updated_at ON working_sets(updated_at DESC)")
            await db.commit()

    async def upsert(self, artefact: Artefact) -> None:
        import json

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO artefacts(
                    key, kind, source_path, source_mtime, generated_at, model, content,
                    metadata_json, relevance_score, access_count, last_accessed, signal_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    kind=excluded.kind,
                    source_path=excluded.source_path,
                    source_mtime=excluded.source_mtime,
                    generated_at=excluded.generated_at,
                    model=excluded.model,
                    content=excluded.content,
                    metadata_json=excluded.metadata_json,
                    relevance_score=excluded.relevance_score,
                    signal_id=excluded.signal_id
                """,
                (
                    artefact.key,
                    artefact.kind.value,
                    artefact.source_path,
                    artefact.source_mtime,
                    artefact.generated_at,
                    artefact.model,
                    artefact.content,
                    json.dumps(artefact.metadata),
                    artefact.relevance_score,
                    artefact.access_count,
                    artefact.last_accessed,
                    artefact.signal_id,
                ),
            )
            await db.commit()

    async def get(self, key: str) -> Artefact | None:
        rows = await self.list(limit=1, key=key)
        return rows[0] if rows else None

    async def list(
        self,
        *,
        kind: ArtefactKind | None = None,
        limit: int = 200,
        key: str | None = None,
    ) -> list[Artefact]:
        import json

        query = (
            "SELECT key, kind, source_path, source_mtime, generated_at, model, content, "
            "metadata_json, relevance_score, access_count, last_accessed, signal_id "
            "FROM artefacts WHERE 1=1"
        )
        params: list[object] = []
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind.value)
        if key is not None:
            query += " AND key = ?"
            params.append(key)
        query += " ORDER BY generated_at DESC LIMIT ?"
        params.append(limit)

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, tuple(params))
            rows = await cursor.fetchall()

        artefacts: list[Artefact] = []
        for row in rows:
            artefacts.append(
                Artefact(
                    key=row[0],
                    kind=ArtefactKind(row[1]),
                    source_path=row[2],
                    source_mtime=row[3],
                    generated_at=row[4],
                    model=row[5],
                    content=row[6],
                    metadata=json.loads(row[7]),
                    relevance_score=row[8],
                    access_count=row[9],
                    last_accessed=row[10],
                    signal_id=row[11],
                )
            )
        return artefacts

    async def mark_accessed(self, key: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE artefacts
                SET access_count = access_count + 1, last_accessed = ?
                WHERE key = ?
                """,
                (time.time(), key),
            )
            await db.commit()

    async def purge_expired(self, max_age_seconds: int) -> int:
        cutoff = time.time() - max_age_seconds
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM artefacts WHERE generated_at < ?", (cutoff,))
            await db.commit()
            return cursor.rowcount

    async def is_stale(self, artefact: Artefact, repo_root: Path, max_age_seconds: int) -> bool:
        age = time.time() - artefact.generated_at
        if age > max_age_seconds:
            return True
        source_abs = repo_root / artefact.source_path
        if source_abs.exists() and source_abs.stat().st_mtime > artefact.source_mtime:
            return True
        return False

    async def upsert_working_set(self, working_set: WorkingSet) -> None:
        import json

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO working_sets(session_id, artefact_keys_json, updated_at, reason)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    artefact_keys_json=excluded.artefact_keys_json,
                    updated_at=excluded.updated_at,
                    reason=excluded.reason
                """,
                (
                    working_set.session_id,
                    json.dumps(working_set.artefact_keys),
                    working_set.updated_at,
                    working_set.reason,
                ),
            )
            await db.commit()

    async def get_latest_working_set(self) -> WorkingSet | None:
        import json

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT session_id, artefact_keys_json, updated_at, reason
                FROM working_sets
                ORDER BY updated_at DESC
                LIMIT 1
                """
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return WorkingSet(
            session_id=row[0],
            artefact_keys=json.loads(row[1]),
            updated_at=row[2],
            reason=row[3],
        )
