# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import re as _re
import time
import uuid
from pathlib import Path

import aiosqlite

from vaner.models.artefact import Artefact, ArtefactKind
from vaner.models.session import WorkingSet
from vaner.models.signal import SignalEvent


class ArtefactStore:
    _PINNED_FACT_SCOPES = frozenset({"user", "project", "workflow"})
    _PINNED_FACTS_MAX = 50

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY)")
            await db.execute("INSERT OR IGNORE INTO schema_version(version) VALUES (1)")
            version_cursor = await db.execute("SELECT MAX(version) FROM schema_version")
            version_row = await version_cursor.fetchone()
            current_schema_version = int(version_row[0] or 1)
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
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS signal_events (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    corpus_id TEXT NOT NULL DEFAULT 'default'
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_signal_events_ts ON signal_events(timestamp DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_signal_events_corpus_id ON signal_events(corpus_id)")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS query_history (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    query_text TEXT NOT NULL,
                    selected_paths_json TEXT NOT NULL,
                    hit_precomputed INTEGER NOT NULL DEFAULT 0,
                    token_used INTEGER,
                    feedback_score REAL,
                    corpus_id TEXT NOT NULL DEFAULT 'default'
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_query_history_ts ON query_history(timestamp DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_query_history_corpus_id ON query_history(corpus_id)")
            await db.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS query_history_fts
                USING fts5(query_text, content='query_history', content_rowid='rowid')
                """
            )
            await db.execute(
                """
                CREATE TRIGGER IF NOT EXISTS query_history_ai
                AFTER INSERT ON query_history
                BEGIN
                    INSERT INTO query_history_fts(rowid, query_text) VALUES (new.rowid, new.query_text);
                END
                """
            )
            await db.execute(
                """
                CREATE TRIGGER IF NOT EXISTS query_history_ad
                AFTER DELETE ON query_history
                BEGIN
                    INSERT INTO query_history_fts(query_history_fts, rowid, query_text)
                    VALUES ('delete', old.rowid, old.query_text);
                END
                """
            )
            await db.execute(
                """
                CREATE TRIGGER IF NOT EXISTS query_history_au
                AFTER UPDATE ON query_history
                BEGIN
                    INSERT INTO query_history_fts(query_history_fts, rowid, query_text)
                    VALUES ('delete', old.rowid, old.query_text);
                    INSERT INTO query_history_fts(rowid, query_text) VALUES (new.rowid, new.query_text);
                END
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS hypotheses (
                    id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    question TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    evidence_json TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL DEFAULT '',
                    relevant_keys_json TEXT NOT NULL,
                    category TEXT NOT NULL,
                    response_format TEXT NOT NULL,
                    follow_ups_json TEXT NOT NULL
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_hypotheses_created_at ON hypotheses(created_at DESC)")
            async with db.execute("PRAGMA table_info(hypotheses)") as cursor:
                columns = [row[1] for row in await cursor.fetchall()]
            if "evidence_hash" not in columns:
                await db.execute("ALTER TABLE hypotheses ADD COLUMN evidence_hash TEXT NOT NULL DEFAULT ''")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS validated_patterns (
                    id TEXT PRIMARY KEY,
                    trigger_category TEXT NOT NULL,
                    trigger_keywords TEXT NOT NULL,
                    predicted_keys_json TEXT NOT NULL,
                    confirmation_count INTEGER NOT NULL DEFAULT 1,
                    last_confirmed_at REAL NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_validated_patterns_category ON validated_patterns(trigger_category)")
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_validated_patterns_confirmations ON validated_patterns(confirmation_count DESC)"
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS habit_transitions (
                    previous_category TEXT NOT NULL,
                    category TEXT NOT NULL,
                    previous_macro TEXT NOT NULL,
                    prompt_macro TEXT NOT NULL,
                    transition_count INTEGER NOT NULL DEFAULT 0,
                    last_seen REAL NOT NULL,
                    PRIMARY KEY (previous_category, category, previous_macro, prompt_macro)
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_habit_transitions_prev ON habit_transitions(previous_category, transition_count DESC)"
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS prompt_macros (
                    macro_key TEXT PRIMARY KEY,
                    example_query TEXT NOT NULL,
                    category TEXT NOT NULL,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    confidence REAL NOT NULL DEFAULT 0.0,
                    last_seen REAL NOT NULL
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_prompt_macros_count ON prompt_macros(use_count DESC)")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_phase_summaries (
                    session_id TEXT PRIMARY KEY,
                    phase TEXT NOT NULL,
                    dominant_category TEXT NOT NULL,
                    recent_categories_json TEXT NOT NULL,
                    recent_macro TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_workflow_phase_updated_at ON workflow_phase_summaries(updated_at DESC)")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS pinned_facts (
                    scope TEXT NOT NULL DEFAULT 'user',
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    scoring_hint_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (scope, key)
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_pinned_facts_scope ON pinned_facts(scope)")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS replay_buffer (
                    id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    priority REAL NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_replay_priority ON replay_buffer(priority DESC)")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS relationship_edges (
                    source_key TEXT NOT NULL,
                    target_key TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    corpus_id TEXT NOT NULL DEFAULT 'default',
                    PRIMARY KEY (source_key, target_key, kind, corpus_id)
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_relationship_source ON relationship_edges(source_key)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_relationship_target ON relationship_edges(target_key)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_relationship_corpus ON relationship_edges(corpus_id)")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS quality_issues (
                    id TEXT PRIMARY KEY,
                    key TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_quality_issues_key ON quality_issues(key)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_quality_issues_created_at ON quality_issues(created_at DESC)")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS prediction_cache (
                    cache_key TEXT PRIMARY KEY,
                    prompt_hint TEXT NOT NULL,
                    package_json TEXT,
                    enrichment_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_prediction_cache_expires_at ON prediction_cache(expires_at)")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback_events (
                    id TEXT PRIMARY KEY,
                    query_id TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    cache_tier TEXT NOT NULL,
                    similarity REAL NOT NULL,
                    quality_lift REAL,
                    latency_ms REAL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_feedback_query_id ON feedback_events(query_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_feedback_ts ON feedback_events(timestamp DESC)")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS prior_divergence (
                    category TEXT PRIMARY KEY,
                    kl_divergence REAL NOT NULL,
                    sample_count INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            async with db.execute("PRAGMA table_info(signal_events)") as cursor:
                signal_columns = [row[1] for row in await cursor.fetchall()]
            if "corpus_id" not in signal_columns:
                await db.execute("ALTER TABLE signal_events ADD COLUMN corpus_id TEXT NOT NULL DEFAULT 'default'")
            async with db.execute("PRAGMA table_info(query_history)") as cursor:
                query_columns = [row[1] for row in await cursor.fetchall()]
            if "corpus_id" not in query_columns:
                await db.execute("ALTER TABLE query_history ADD COLUMN corpus_id TEXT NOT NULL DEFAULT 'default'")
            async with db.execute("PRAGMA table_info(relationship_edges)") as cursor:
                relationship_columns = [row[1] for row in await cursor.fetchall()]
            if "corpus_id" not in relationship_columns:
                await db.execute("ALTER TABLE relationship_edges ADD COLUMN corpus_id TEXT NOT NULL DEFAULT 'default'")
                relationship_columns.append("corpus_id")

            # v2: one-time FTS rebuild after schema migrations
            if current_schema_version < 2:
                await db.execute("INSERT INTO query_history_fts(query_history_fts) VALUES ('rebuild')")
                await db.execute("INSERT OR IGNORE INTO schema_version(version) VALUES (2)")
                current_schema_version = 2

            # v3: rebuild relationship_edges so corpus_id participates in PK
            # on upgraded databases (fresh DBs already have the correct PK).
            if current_schema_version < 3:
                relationship_info_cursor = await db.execute("PRAGMA table_info(relationship_edges)")
                relationship_info = await relationship_info_cursor.fetchall()
                corpus_is_pk = any(row[1] == "corpus_id" and int(row[5]) > 0 for row in relationship_info)
                if not corpus_is_pk:
                    await db.execute(
                        """
                        CREATE TABLE relationship_edges_new (
                            source_key TEXT NOT NULL,
                            target_key TEXT NOT NULL,
                            kind TEXT NOT NULL,
                            updated_at REAL NOT NULL,
                            corpus_id TEXT NOT NULL DEFAULT 'default',
                            PRIMARY KEY (source_key, target_key, kind, corpus_id)
                        )
                        """
                    )
                    await db.execute(
                        """
                        INSERT INTO relationship_edges_new(source_key, target_key, kind, updated_at, corpus_id)
                        SELECT source_key, target_key, kind, updated_at, COALESCE(corpus_id, 'default')
                        FROM relationship_edges
                        """
                    )
                    await db.execute("DROP TABLE relationship_edges")
                    await db.execute("ALTER TABLE relationship_edges_new RENAME TO relationship_edges")
                    await db.execute("CREATE INDEX IF NOT EXISTS idx_relationship_source ON relationship_edges(source_key)")
                    await db.execute("CREATE INDEX IF NOT EXISTS idx_relationship_target ON relationship_edges(target_key)")
                    await db.execute("CREATE INDEX IF NOT EXISTS idx_relationship_corpus ON relationship_edges(corpus_id)")
                await db.execute("INSERT OR IGNORE INTO schema_version(version) VALUES (3)")
                current_schema_version = 3

            # v4: add pinned_facts as a bounded user-authored profile table.
            if current_schema_version < 4:
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pinned_facts (
                        scope TEXT NOT NULL DEFAULT 'user',
                        key TEXT NOT NULL,
                        value TEXT NOT NULL,
                        scoring_hint_json TEXT,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        PRIMARY KEY (scope, key)
                    )
                    """
                )
                await db.execute("CREATE INDEX IF NOT EXISTS idx_pinned_facts_scope ON pinned_facts(scope)")
                await db.execute("INSERT OR IGNORE INTO schema_version(version) VALUES (4)")
                current_schema_version = 4

            # v5: allow multiple facts with same key across scopes by making
            # (scope, key) the primary key.
            if current_schema_version < 5:
                pinned_info_cursor = await db.execute("PRAGMA table_info(pinned_facts)")
                pinned_info = await pinned_info_cursor.fetchall()
                scope_is_pk = any(row[1] == "scope" and int(row[5]) > 0 for row in pinned_info)
                key_is_pk = any(row[1] == "key" and int(row[5]) > 0 for row in pinned_info)
                if not (scope_is_pk and key_is_pk):
                    await db.execute(
                        """
                        CREATE TABLE pinned_facts_new (
                            scope TEXT NOT NULL DEFAULT 'user',
                            key TEXT NOT NULL,
                            value TEXT NOT NULL,
                            scoring_hint_json TEXT,
                            created_at REAL NOT NULL,
                            updated_at REAL NOT NULL,
                            PRIMARY KEY (scope, key)
                        )
                        """
                    )
                    await db.execute(
                        """
                        INSERT INTO pinned_facts_new(scope, key, value, scoring_hint_json, created_at, updated_at)
                        SELECT COALESCE(scope, 'user'), key, value, scoring_hint_json, created_at, updated_at
                        FROM pinned_facts
                        """
                    )
                    await db.execute("DROP TABLE pinned_facts")
                    await db.execute("ALTER TABLE pinned_facts_new RENAME TO pinned_facts")
                    await db.execute("CREATE INDEX IF NOT EXISTS idx_pinned_facts_scope ON pinned_facts(scope)")
                await db.execute("INSERT OR IGNORE INTO schema_version(version) VALUES (5)")
                current_schema_version = 5

            # v6: add persistent learning_state for policy/scorer metadata.
            if current_schema_version < 6:
                await db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS learning_state (
                        key TEXT PRIMARY KEY,
                        value_json TEXT NOT NULL,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                await db.execute("INSERT OR IGNORE INTO schema_version(version) VALUES (6)")
                current_schema_version = 6

            # FTS5 index on artefact source_path + content for sub-millisecond
            # candidate retrieval; the full scorer then re-ranks the top-N hits.
            await db.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS artefacts_fts
                USING fts5(key UNINDEXED, source_path, content, tokenize='unicode61')
                """
            )
            await db.commit()

    async def upsert(self, artefact: Artefact) -> None:
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
            # Keep the FTS index in sync.  Delete-then-insert handles both
            # fresh inserts and updates correctly without needing triggers.
            await db.execute("DELETE FROM artefacts_fts WHERE key = ?", (artefact.key,))
            await db.execute(
                "INSERT INTO artefacts_fts(key, source_path, content) VALUES (?, ?, ?)",
                (artefact.key, artefact.source_path, artefact.content[:8192]),
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

    async def insert_signal_event(self, event: SignalEvent) -> None:
        corpus_id = str(event.payload.get("corpus_id", "default"))
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO signal_events(id, source, kind, timestamp, payload_json, corpus_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (event.id, event.source, event.kind, event.timestamp, json.dumps(event.payload), corpus_id),
            )
            await db.commit()

    async def list_signal_events(self, *, corpus_id: str | None = None, limit: int = 200) -> list[SignalEvent]:
        query = "SELECT id, source, kind, timestamp, payload_json FROM signal_events"
        params: list[object] = []
        if corpus_id is not None:
            query += " WHERE corpus_id = ?"
            params.append(corpus_id)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, tuple(params))
            rows = await cursor.fetchall()
        return [SignalEvent(id=row[0], source=row[1], kind=row[2], timestamp=row[3], payload=json.loads(row[4])) for row in rows]

    async def purge_old_signal_events(self, *, max_age_seconds: int) -> int:
        cutoff = time.time() - max_age_seconds
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM signal_events WHERE timestamp < ?", (cutoff,))
            await db.commit()
            return cursor.rowcount

    async def insert_query_history(
        self,
        *,
        session_id: str,
        query_text: str,
        selected_paths: list[str],
        hit_precomputed: bool,
        token_used: int | None,
        feedback_score: float | None = None,
        corpus_id: str = "default",
    ) -> str:
        query_id = str(uuid.uuid4())
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO query_history(
                    id, session_id, timestamp, query_text, selected_paths_json, hit_precomputed, token_used, feedback_score, corpus_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    query_id,
                    session_id,
                    time.time(),
                    query_text,
                    json.dumps(selected_paths),
                    int(hit_precomputed),
                    token_used,
                    feedback_score,
                    corpus_id,
                ),
            )
            await db.commit()
        return query_id

    async def update_query_feedback(self, query_id: str, feedback_score: float) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE query_history SET feedback_score = ? WHERE id = ?",
                (feedback_score, query_id),
            )
            await db.commit()

    async def list_query_history(self, *, corpus_id: str | None = None, limit: int = 200) -> list[dict[str, object]]:
        query = (
            "SELECT id, session_id, timestamp, query_text, selected_paths_json, hit_precomputed, token_used, feedback_score, corpus_id "
            "FROM query_history"
        )
        params: list[object] = []
        if corpus_id is not None:
            query += " WHERE corpus_id = ?"
            params.append(corpus_id)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, tuple(params))
            rows = await cursor.fetchall()
        output: list[dict[str, object]] = []
        for row in rows:
            output.append(
                {
                    "id": row[0],
                    "session_id": row[1],
                    "timestamp": row[2],
                    "query_text": row[3],
                    "selected_paths": json.loads(row[4]),
                    "hit_precomputed": bool(row[5]),
                    "token_used": row[6],
                    "feedback_score": row[7],
                    "corpus_id": row[8],
                }
            )
        return output

    async def search_query_history(self, query: str, *, corpus_id: str | None = None, limit: int = 10) -> list[dict[str, object]]:
        if not query.strip():
            return []
        async with aiosqlite.connect(self.db_path) as db:
            sql = (
                "SELECT\n"
                "    query_history.id,\n"
                "    query_history.session_id,\n"
                "    query_history.timestamp,\n"
                "    query_history.query_text,\n"
                "    query_history.selected_paths_json,\n"
                "    query_history.hit_precomputed,\n"
                "    query_history.token_used,\n"
                "    query_history.feedback_score,\n"
                "    query_history.corpus_id\n"
                "FROM query_history\n"
                "JOIN query_history_fts ON query_history.rowid = query_history_fts.rowid\n"
                "WHERE query_history_fts MATCH ?"
            )
            params: list[object] = [query]
            if corpus_id is not None:
                sql += " AND query_history.corpus_id = ?"
                params.append(corpus_id)
            sql += " ORDER BY bm25(query_history_fts) LIMIT ?"
            params.append(limit)
            cursor = await db.execute(sql, tuple(params))
            rows = await cursor.fetchall()
        output: list[dict[str, object]] = []
        for row in rows:
            output.append(
                {
                    "id": row[0],
                    "session_id": row[1],
                    "timestamp": row[2],
                    "query_text": row[3],
                    "selected_paths": json.loads(row[4]),
                    "hit_precomputed": bool(row[5]),
                    "token_used": row[6],
                    "feedback_score": row[7],
                    "corpus_id": row[8],
                }
            )
        return output

    async def count_query_history(self, *, corpus_id: str | None = None) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            if corpus_id is None:
                cursor = await db.execute("SELECT COUNT(*) FROM query_history")
            else:
                cursor = await db.execute("SELECT COUNT(*) FROM query_history WHERE corpus_id = ?", (corpus_id,))
            row = await cursor.fetchone()
        return int(row[0]) if row is not None else 0

    async def insert_hypothesis(
        self,
        *,
        question: str,
        confidence: float,
        evidence: list[str],
        relevant_keys: list[str],
        category: str,
        response_format: str,
        follow_ups: list[str],
    ) -> str:
        hypothesis_id = str(uuid.uuid4())
        evidence_hash = hashlib.sha1(
            "\n".join(sorted(str(item) for item in evidence)).encode("utf-8")  # noqa: S324
        ).hexdigest()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO hypotheses(
                    id, created_at, question, confidence, evidence_json, evidence_hash,
                    relevant_keys_json, category, response_format, follow_ups_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hypothesis_id,
                    time.time(),
                    question,
                    confidence,
                    json.dumps(evidence),
                    evidence_hash,
                    json.dumps(relevant_keys),
                    category,
                    response_format,
                    json.dumps(follow_ups),
                ),
            )
            await db.commit()
        return hypothesis_id

    async def list_hypotheses(self, *, limit: int = 50) -> list[dict[str, object]]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT
                    id, created_at, question, confidence, evidence_json, evidence_hash,
                    relevant_keys_json, category, response_format, follow_ups_json
                FROM hypotheses
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
        return [
            {
                "id": row[0],
                "created_at": row[1],
                "question": row[2],
                "confidence": row[3],
                "evidence": json.loads(row[4]),
                "evidence_hash": row[5],
                "relevant_keys": json.loads(row[6]),
                "category": row[7],
                "response_format": row[8],
                "follow_ups": json.loads(row[9]),
            }
            for row in rows
        ]

    async def invalidate_stale_hypotheses(self, valid_keys: set[str]) -> int:
        hypotheses = await self.list_hypotheses(limit=500)
        stale_ids = []
        for hypothesis in hypotheses:
            relevant_keys = [str(item) for item in hypothesis.get("relevant_keys", [])]
            if not relevant_keys:
                continue
            if all(key not in valid_keys for key in relevant_keys):
                stale_ids.append(str(hypothesis["id"]))
        if not stale_ids:
            return 0
        placeholders = ",".join("?" for _ in stale_ids)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(f"DELETE FROM hypotheses WHERE id IN ({placeholders})", tuple(stale_ids))
            await db.commit()
            return cursor.rowcount

    async def insert_replay_entry(self, *, payload: dict[str, object], priority: float) -> str:
        replay_id = str(uuid.uuid4())
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO replay_buffer(id, created_at, priority, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (replay_id, time.time(), priority, json.dumps(payload)),
            )
            await db.commit()
        return replay_id

    async def insert_replay_entries_bulk(self, *, entries: list[tuple[dict[str, object], float]]) -> int:
        if not entries:
            return 0
        now = time.time()
        rows = [(str(uuid.uuid4()), now, float(priority), json.dumps(payload)) for payload, priority in entries]
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany(
                """
                INSERT INTO replay_buffer(id, created_at, priority, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                rows,
            )
            await db.commit()
        return len(rows)

    async def sample_replay_entries(self, *, limit: int = 128) -> list[dict[str, object]]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT id, created_at, priority, payload_json
                FROM replay_buffer
                ORDER BY priority DESC, created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
        return [
            {
                "id": row[0],
                "created_at": row[1],
                "priority": row[2],
                "payload": json.loads(row[3]),
            }
            for row in rows
        ]

    async def purge_old_replay_entries(self, *, max_age_seconds: int) -> int:
        cutoff = time.time() - max_age_seconds
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM replay_buffer WHERE created_at < ?", (cutoff,))
            await db.commit()
            return cursor.rowcount

    async def replace_relationship_edges(self, edges: list[tuple[str, str, str] | tuple[str, str, str, str]]) -> None:
        # Deduplicate before insert to avoid UNIQUE constraint errors on larger repos.
        normalized_edges: list[tuple[str, str, str, str]] = []
        for edge in edges:
            if len(edge) == 3:
                source_key, target_key, kind = edge
                corpus_id = "default"
            else:
                source_key, target_key, kind, corpus_id = edge
            normalized_edges.append((source_key, target_key, kind, corpus_id))
        unique_edges = list(dict.fromkeys(normalized_edges))
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM relationship_edges")
            if unique_edges:
                await db.executemany(
                    """
                    INSERT INTO relationship_edges(source_key, target_key, kind, updated_at, corpus_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [(source_key, target_key, kind, time.time(), corpus_id) for source_key, target_key, kind, corpus_id in unique_edges],
                )
            await db.commit()

    async def list_relationship_edges(
        self,
        *,
        source_key: str | None = None,
        corpus_id: str | None = None,
        limit: int = 2000,
    ) -> list[tuple[str, str, str]]:
        async with aiosqlite.connect(self.db_path) as db:
            if source_key is None and corpus_id is None:
                cursor = await db.execute(
                    """
                    SELECT source_key, target_key, kind
                    FROM relationship_edges
                    ORDER BY source_key, target_key
                    LIMIT ?
                    """,
                    (limit,),
                )
            elif source_key is None:
                cursor = await db.execute(
                    """
                    SELECT source_key, target_key, kind
                    FROM relationship_edges
                    WHERE corpus_id = ?
                    ORDER BY source_key, target_key
                    LIMIT ?
                    """,
                    (corpus_id, limit),
                )
            elif corpus_id is None:
                cursor = await db.execute(
                    """
                    SELECT source_key, target_key, kind
                    FROM relationship_edges
                    WHERE source_key = ?
                    ORDER BY target_key
                    LIMIT ?
                    """,
                    (source_key, limit),
                )
            else:
                cursor = await db.execute(
                    """
                    SELECT source_key, target_key, kind
                    FROM relationship_edges
                    WHERE source_key = ? AND corpus_id = ?
                    ORDER BY target_key
                    LIMIT ?
                    """,
                    (source_key, corpus_id, limit),
                )
            rows = await cursor.fetchall()
        return [(row[0], row[1], row[2]) for row in rows]

    async def select_artefacts_fts(self, query: str, limit: int = 50) -> list[str]:
        """Return artefact keys whose source_path or content matches *query*.

        Uses the FTS5 full-text index for sub-millisecond candidate retrieval.
        The caller should re-rank results with the full scorer.  Returns an
        empty list if the FTS table is unavailable or the query is empty.
        """
        if not query.strip():
            return []
        # Sanitise the query for FTS5: strip special chars that cause parse errors.
        safe_query = " ".join(_re.findall(r"[a-zA-Z0-9_]{2,}", query))
        if not safe_query:
            return []
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "SELECT key FROM artefacts_fts WHERE artefacts_fts MATCH ? ORDER BY rank LIMIT ?",
                    (safe_query, limit),
                )
                rows = await cursor.fetchall()
            return [row[0] for row in rows]
        except Exception:
            return []

    async def replace_quality_issues(self, issues: list[dict[str, object]]) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM quality_issues")
            if issues:
                await db.executemany(
                    """
                    INSERT INTO quality_issues(id, key, severity, message, metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            str(uuid.uuid4()),
                            str(issue["key"]),
                            str(issue["severity"]),
                            str(issue["message"]),
                            json.dumps(issue.get("metadata", {})),
                            time.time(),
                        )
                        for issue in issues
                    ],
                )
            await db.commit()

    async def list_quality_issues(self, *, limit: int = 200) -> list[dict[str, object]]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT key, severity, message, metadata_json, created_at
                FROM quality_issues
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
        return [
            {
                "key": row[0],
                "severity": row[1],
                "message": row[2],
                "metadata": json.loads(row[3]),
                "created_at": row[4],
            }
            for row in rows
        ]

    async def upsert_prediction_cache(
        self,
        *,
        cache_key: str,
        prompt_hint: str,
        package_json: str | None,
        enrichment: dict[str, object],
        ttl_seconds: int,
    ) -> None:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO prediction_cache(cache_key, prompt_hint, package_json, enrichment_json, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    prompt_hint=excluded.prompt_hint,
                    package_json=excluded.package_json,
                    enrichment_json=excluded.enrichment_json,
                    created_at=excluded.created_at,
                    expires_at=excluded.expires_at
                """,
                (
                    cache_key,
                    prompt_hint,
                    package_json,
                    json.dumps(enrichment),
                    now,
                    now + ttl_seconds,
                ),
            )
            await db.commit()

    async def list_prediction_cache(self, *, include_expired: bool = False, limit: int = 200) -> list[dict[str, object]]:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            if include_expired:
                cursor = await db.execute(
                    """
                    SELECT cache_key, prompt_hint, package_json, enrichment_json, created_at, expires_at
                    FROM prediction_cache
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            else:
                cursor = await db.execute(
                    """
                    SELECT cache_key, prompt_hint, package_json, enrichment_json, created_at, expires_at
                    FROM prediction_cache
                    WHERE expires_at >= ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (now, limit),
                )
            rows = await cursor.fetchall()
        return [
            {
                "cache_key": row[0],
                "prompt_hint": row[1],
                "package_json": row[2],
                "enrichment": json.loads(row[3]),
                "created_at": row[4],
                "expires_at": row[5],
            }
            for row in rows
        ]

    async def purge_expired_prediction_cache(self) -> int:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM prediction_cache WHERE expires_at < ?", (now,))
            await db.commit()
            return cursor.rowcount

    async def insert_feedback_event(
        self,
        *,
        query_id: str,
        cache_tier: str,
        similarity: float,
        quality_lift: float | None = None,
        latency_ms: float | None = None,
        metadata: dict[str, object] | None = None,
    ) -> str:
        feedback_id = str(uuid.uuid4())
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO feedback_events(id, query_id, timestamp, cache_tier, similarity, quality_lift, latency_ms, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback_id,
                    query_id,
                    time.time(),
                    cache_tier,
                    similarity,
                    quality_lift,
                    latency_ms,
                    json.dumps(metadata or {}),
                ),
            )
            await db.commit()
        return feedback_id

    async def list_feedback_events(self, *, limit: int = 200) -> list[dict[str, object]]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT id, query_id, timestamp, cache_tier, similarity, quality_lift, latency_ms, metadata_json
                FROM feedback_events
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
        return [
            {
                "id": row[0],
                "query_id": row[1],
                "timestamp": row[2],
                "cache_tier": row[3],
                "similarity": row[4],
                "quality_lift": row[5],
                "latency_ms": row[6],
                "metadata": json.loads(row[7]),
            }
            for row in rows
        ]

    async def update_feedback_event_metadata(self, feedback_id: str, metadata: dict[str, object]) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                UPDATE feedback_events
                SET metadata_json = ?
                WHERE id = ?
                """,
                (json.dumps(metadata), feedback_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def upsert_learning_state(self, *, key: str, value: dict[str, object]) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO learning_state(key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, json.dumps(value), time.time()),
            )
            await db.commit()

    async def get_learning_state(self, key: str) -> dict[str, object] | None:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT value_json FROM learning_state WHERE key = ?",
                (key,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row[0])
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    async def insert_validated_pattern(
        self,
        *,
        trigger_category: str,
        trigger_keywords: str,
        predicted_keys: list[str],
        confirmation_count: int = 1,
    ) -> str:
        pattern_id = str(uuid.uuid4())
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO validated_patterns(
                    id, trigger_category, trigger_keywords, predicted_keys_json, confirmation_count, last_confirmed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pattern_id,
                    trigger_category,
                    trigger_keywords,
                    json.dumps(predicted_keys),
                    confirmation_count,
                    now,
                    now,
                ),
            )
            await db.commit()
        return pattern_id

    async def list_validated_patterns(
        self,
        *,
        trigger_category: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        query = (
            "SELECT id, trigger_category, trigger_keywords, predicted_keys_json, confirmation_count, "
            "last_confirmed_at, created_at FROM validated_patterns WHERE 1=1"
        )
        params: list[object] = []
        if trigger_category is not None:
            query += " AND trigger_category = ?"
            params.append(trigger_category)
        query += " ORDER BY confirmation_count DESC, last_confirmed_at DESC LIMIT ?"
        params.append(limit)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, tuple(params))
            rows = await cursor.fetchall()
        return [
            {
                "id": row[0],
                "trigger_category": row[1],
                "trigger_keywords": row[2],
                "predicted_keys": json.loads(row[3]),
                "confirmation_count": int(row[4]),
                "last_confirmed_at": row[5],
                "created_at": row[6],
            }
            for row in rows
        ]

    async def increment_pattern_confirmation(self, pattern_id: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                UPDATE validated_patterns
                SET confirmation_count = confirmation_count + 1, last_confirmed_at = ?
                WHERE id = ?
                """,
                (time.time(), pattern_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def replace_habit_transitions(self, rows: list[dict[str, object]]) -> None:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM habit_transitions")
            for row in rows:
                await db.execute(
                    """
                    INSERT INTO habit_transitions(
                        previous_category, category, previous_macro, prompt_macro, transition_count, last_seen
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(row.get("previous_category", "understanding")),
                        str(row.get("category", "understanding")),
                        str(row.get("previous_macro", "")),
                        str(row.get("prompt_macro", "")),
                        int(row.get("transition_count", 0)),
                        now,
                    ),
                )
            await db.commit()

    async def record_habit_transition(
        self,
        *,
        previous_category: str,
        category: str,
        previous_macro: str,
        prompt_macro: str,
    ) -> None:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO habit_transitions(
                    previous_category, category, previous_macro, prompt_macro, transition_count, last_seen
                ) VALUES (?, ?, ?, ?, 1, ?)
                ON CONFLICT(previous_category, category, previous_macro, prompt_macro)
                DO UPDATE SET
                    transition_count = transition_count + 1,
                    last_seen = excluded.last_seen
                """,
                (previous_category, category, previous_macro, prompt_macro, now),
            )
            await db.commit()

    async def list_habit_transitions(
        self,
        *,
        previous_category: str | None = None,
        previous_macro: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        query = (
            "SELECT previous_category, category, previous_macro, prompt_macro, transition_count, last_seen FROM habit_transitions WHERE 1=1"
        )
        params: list[object] = []
        if previous_category is not None:
            query += " AND previous_category = ?"
            params.append(previous_category)
        if previous_macro is not None:
            query += " AND previous_macro = ?"
            params.append(previous_macro)
        query += " ORDER BY transition_count DESC, last_seen DESC LIMIT ?"
        params.append(limit)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, tuple(params))
            rows = await cursor.fetchall()
        return [
            {
                "previous_category": row[0],
                "category": row[1],
                "previous_macro": row[2],
                "prompt_macro": row[3],
                "transition_count": int(row[4]),
                "last_seen": row[5],
            }
            for row in rows
        ]

    async def replace_prompt_macros(self, rows: list[dict[str, object]]) -> None:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM prompt_macros")
            for row in rows:
                await db.execute(
                    """
                    INSERT INTO prompt_macros(macro_key, example_query, category, use_count, confidence, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(row.get("macro_key", "general")),
                        str(row.get("example_query", "")),
                        str(row.get("category", "understanding")),
                        int(row.get("use_count", 0)),
                        float(row.get("confidence", 0.0)),
                        now,
                    ),
                )
            await db.commit()

    async def bump_prompt_macro(
        self,
        *,
        macro_key: str,
        example_query: str,
        category: str,
        confidence: float = 1.0,
    ) -> None:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO prompt_macros(macro_key, example_query, category, use_count, confidence, last_seen)
                VALUES (?, ?, ?, 1, ?, ?)
                ON CONFLICT(macro_key)
                DO UPDATE SET
                    example_query = excluded.example_query,
                    category = excluded.category,
                    use_count = prompt_macros.use_count + 1,
                    confidence = MAX(prompt_macros.confidence, excluded.confidence),
                    last_seen = excluded.last_seen
                """,
                (macro_key, example_query, category, confidence, now),
            )
            await db.commit()

    async def upsert_pinned_fact(
        self,
        *,
        key: str,
        value: str,
        scope: str = "user",
        scoring_hint: dict[str, object] | None = None,
    ) -> None:
        scope_normalized = scope.strip().lower()
        if scope_normalized not in self._PINNED_FACT_SCOPES:
            allowed = ", ".join(sorted(self._PINNED_FACT_SCOPES))
            raise ValueError(f"Unsupported pinned fact scope '{scope}'. Allowed: {allowed}")
        key_normalized = key.strip()
        if not key_normalized:
            raise ValueError("Pinned fact key must not be empty")
        now = time.time()
        scoring_hint_json = json.dumps(scoring_hint) if scoring_hint is not None else None
        async with aiosqlite.connect(self.db_path) as db:
            exists_cursor = await db.execute(
                "SELECT 1 FROM pinned_facts WHERE scope = ? AND key = ?",
                (scope_normalized, key_normalized),
            )
            exists = await exists_cursor.fetchone() is not None
            if not exists:
                count_cursor = await db.execute("SELECT COUNT(*) FROM pinned_facts")
                count_row = await count_cursor.fetchone()
                count = int(count_row[0] or 0) if count_row is not None else 0
                if count >= self._PINNED_FACTS_MAX:
                    raise ValueError(f"Pinned facts overflow: maximum {self._PINNED_FACTS_MAX} entries allowed. Remove one before adding.")
            await db.execute(
                """
                INSERT INTO pinned_facts(scope, key, value, scoring_hint_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope, key) DO UPDATE SET
                    value = excluded.value,
                    scoring_hint_json = excluded.scoring_hint_json,
                    updated_at = excluded.updated_at
                """,
                (scope_normalized, key_normalized, value, scoring_hint_json, now, now),
            )
            await db.commit()

    async def remove_pinned_fact(self, key: str, *, scope: str | None = None) -> bool:
        key_normalized = key.strip()
        if not key_normalized:
            return False
        async with aiosqlite.connect(self.db_path) as db:
            if scope is None:
                cursor = await db.execute("DELETE FROM pinned_facts WHERE key = ?", (key_normalized,))
            else:
                scope_normalized = scope.strip().lower()
                if scope_normalized not in self._PINNED_FACT_SCOPES:
                    allowed = ", ".join(sorted(self._PINNED_FACT_SCOPES))
                    raise ValueError(f"Unsupported pinned fact scope '{scope}'. Allowed: {allowed}")
                cursor = await db.execute(
                    "DELETE FROM pinned_facts WHERE scope = ? AND key = ?",
                    (scope_normalized, key_normalized),
                )
            await db.commit()
            return cursor.rowcount > 0

    async def list_pinned_facts(self, *, scope: str | None = None) -> list[dict[str, object]]:
        query = "SELECT scope, key, value, scoring_hint_json, created_at, updated_at FROM pinned_facts"
        params: list[object] = []
        if scope is not None:
            scope_normalized = scope.strip().lower()
            if scope_normalized not in self._PINNED_FACT_SCOPES:
                allowed = ", ".join(sorted(self._PINNED_FACT_SCOPES))
                raise ValueError(f"Unsupported pinned fact scope '{scope}'. Allowed: {allowed}")
            query += " WHERE scope = ?"
            params.append(scope_normalized)
        query += " ORDER BY updated_at DESC, scope ASC, key ASC"
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, tuple(params))
            rows = await cursor.fetchall()
        output: list[dict[str, object]] = []
        for row in rows:
            scoring_hint_raw = row[3]
            scoring_hint: dict[str, object] | None = None
            if scoring_hint_raw:
                try:
                    parsed = json.loads(scoring_hint_raw)
                except (json.JSONDecodeError, TypeError, ValueError):
                    parsed = None
                if isinstance(parsed, dict):
                    scoring_hint = parsed
            output.append(
                {
                    "scope": row[0],
                    "key": row[1],
                    "value": row[2],
                    "scoring_hint": scoring_hint,
                    "created_at": row[4],
                    "updated_at": row[5],
                }
            )
        return output

    async def replace_pinned_facts(self, rows: list[dict[str, object]]) -> None:
        deduped: dict[tuple[str, str], dict[str, object]] = {}
        for row in rows:
            key = str(row.get("key", "")).strip()
            scope = str(row.get("scope", "user")).strip().lower()
            if not key:
                continue
            deduped[(scope, key)] = row
        if len(deduped) > self._PINNED_FACTS_MAX:
            raise ValueError(f"Pinned facts overflow: maximum {self._PINNED_FACTS_MAX} entries allowed, got {len(deduped)}.")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN")
            try:
                await db.execute("DELETE FROM pinned_facts")
                now = time.time()
                for _scope_key, row in deduped.items():
                    key = str(row.get("key", "")).strip()
                    value = str(row.get("value", ""))
                    scope = str(row.get("scope", "user")).strip().lower()
                    if not key:
                        continue
                    if scope not in self._PINNED_FACT_SCOPES:
                        allowed = ", ".join(sorted(self._PINNED_FACT_SCOPES))
                        raise ValueError(f"Unsupported pinned fact scope '{scope}'. Allowed: {allowed}")
                    scoring_hint = row.get("scoring_hint")
                    scoring_hint_json = json.dumps(scoring_hint) if isinstance(scoring_hint, dict) else None
                    await db.execute(
                        """
                        INSERT INTO pinned_facts(scope, key, value, scoring_hint_json, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (scope, key, value, scoring_hint_json, now, now),
                    )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def list_prompt_macros(
        self,
        *,
        category: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        query = "SELECT macro_key, example_query, category, use_count, confidence, last_seen FROM prompt_macros WHERE 1=1"
        params: list[object] = []
        if category is not None:
            query += " AND category = ?"
            params.append(category)
        query += " ORDER BY use_count DESC, last_seen DESC LIMIT ?"
        params.append(limit)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, tuple(params))
            rows = await cursor.fetchall()
        return [
            {
                "macro_key": row[0],
                "example_query": row[1],
                "category": row[2],
                "use_count": int(row[3]),
                "confidence": float(row[4]),
                "last_seen": row[5],
            }
            for row in rows
        ]

    async def upsert_workflow_phase_summary(
        self,
        *,
        session_id: str,
        phase: str,
        dominant_category: str,
        recent_categories: list[str],
        recent_macro: str,
    ) -> None:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO workflow_phase_summaries(
                    session_id, phase, dominant_category, recent_categories_json, recent_macro, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id)
                DO UPDATE SET
                    phase = excluded.phase,
                    dominant_category = excluded.dominant_category,
                    recent_categories_json = excluded.recent_categories_json,
                    recent_macro = excluded.recent_macro,
                    updated_at = excluded.updated_at
                """,
                (session_id, phase, dominant_category, json.dumps(recent_categories), recent_macro, now),
            )
            await db.commit()

    async def get_workflow_phase_summary(self, *, session_id: str | None = None) -> dict[str, object] | None:
        query = (
            "SELECT session_id, phase, dominant_category, recent_categories_json, recent_macro, updated_at FROM workflow_phase_summaries"
        )
        params: tuple[object, ...]
        if session_id is not None:
            query += " WHERE session_id = ? ORDER BY updated_at DESC LIMIT 1"
            params = (session_id,)
        else:
            query += " ORDER BY updated_at DESC LIMIT 1"
            params = ()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, params)
            row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "session_id": row[0],
            "phase": row[1],
            "dominant_category": row[2],
            "recent_categories": json.loads(row[3]),
            "recent_macro": row[4],
            "updated_at": row[5],
        }

    async def purge_stale_patterns(self, *, max_age_seconds: int) -> int:
        cutoff = time.time() - max_age_seconds
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM validated_patterns WHERE last_confirmed_at < ?", (cutoff,))
            await db.commit()
            return cursor.rowcount

    async def purge_old_query_history(self, *, max_age_seconds: int) -> int:
        cutoff = time.time() - max_age_seconds
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM query_history WHERE timestamp < ?", (cutoff,))
            await db.commit()
            return cursor.rowcount

    async def upsert_prior_divergence(self, category: str, kl_divergence: float, sample_count: int) -> None:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO prior_divergence(category, kl_divergence, sample_count, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(category) DO UPDATE SET
                    kl_divergence = excluded.kl_divergence,
                    sample_count = excluded.sample_count,
                    updated_at = excluded.updated_at
                """,
                (category, float(kl_divergence), int(sample_count), now),
            )
            await db.commit()

    async def list_prior_divergence(self) -> list[dict[str, object]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT category, kl_divergence, sample_count, updated_at
                FROM prior_divergence
                ORDER BY kl_divergence DESC
                """
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]
