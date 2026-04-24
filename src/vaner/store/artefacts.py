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
            # WS7 — workspace_goals: long-horizon intent spanning many
            # prompts / cycles. Keyed by the goal's deterministic id
            # (sha1 over source|title). ``evidence_json`` carries the
            # supporting observations (commits, queries, paths) as an
            # opaque JSON blob so the goal row stays compact.
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS workspace_goals (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at REAL NOT NULL,
                    last_observed_at REAL NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    related_files_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_workspace_goals_status ON workspace_goals(status)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_workspace_goals_created_at ON workspace_goals(created_at DESC)")
            # 0.8.2 WS1 — intent-bearing artefacts (plans, outlines, task lists,
            # briefs, roadmaps, runbooks). See src/vaner/intent/artefacts.py for
            # the companion dataclasses; per the release spec §6.5 the store
            # layer owns identity + versioned snapshots + flattened items +
            # persisted reconciliation outcomes. All tables are declared here
            # unconditionally so fresh databases get the full schema; legacy
            # databases pick up the workspace_goals column additions from the
            # v8 migration block below.
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS intent_artefacts (
                    id TEXT PRIMARY KEY,
                    source_uri TEXT NOT NULL,
                    source_tier TEXT NOT NULL,
                    connector TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    confidence REAL NOT NULL,
                    created_at REAL NOT NULL,
                    last_observed_at REAL NOT NULL,
                    last_reconciled_at REAL,
                    latest_snapshot TEXT NOT NULL DEFAULT '',
                    linked_goals_json TEXT NOT NULL DEFAULT '[]',
                    linked_files_json TEXT NOT NULL DEFAULT '[]',
                    supersedes TEXT
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_intent_artefacts_status ON intent_artefacts(status)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_intent_artefacts_connector ON intent_artefacts(connector)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_intent_artefacts_source_uri ON intent_artefacts(source_uri)")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS intent_artefact_snapshots (
                    id TEXT PRIMARY KEY,
                    artefact_id TEXT NOT NULL,
                    captured_at REAL NOT NULL,
                    content_hash TEXT NOT NULL,
                    text TEXT NOT NULL
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_intent_artefact_snapshots_artefact_id ON intent_artefact_snapshots(artefact_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_intent_artefact_snapshots_captured_at ON intent_artefact_snapshots(captured_at DESC)"
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS intent_artefact_items (
                    id TEXT NOT NULL,
                    artefact_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    section_path TEXT NOT NULL DEFAULT '',
                    parent_item TEXT,
                    related_files_json TEXT NOT NULL DEFAULT '[]',
                    related_entities_json TEXT NOT NULL DEFAULT '[]',
                    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                    PRIMARY KEY (id, snapshot_id)
                )
                """
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_intent_artefact_items_artefact_id ON intent_artefact_items(artefact_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_intent_artefact_items_state ON intent_artefact_items(state)")
            # Reconciliation outcomes are first-class persisted state (spec
            # §10.3). The ``progress_reconciled`` invalidation signal carries
            # only a pointer (outcome_id + artefact_id); downstream scoring /
            # explanation paths fetch full detail from here.
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS intent_reconciliation_outcomes (
                    id TEXT PRIMARY KEY,
                    artefact_id TEXT NOT NULL,
                    pass_at REAL NOT NULL,
                    triggering_signal_id TEXT,
                    item_state_deltas_json TEXT NOT NULL DEFAULT '[]',
                    goal_status_deltas_json TEXT NOT NULL DEFAULT '[]',
                    supersedes_json TEXT NOT NULL DEFAULT '[]',
                    evidence_refs_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_intent_reconciliation_outcomes_artefact_id ON intent_reconciliation_outcomes(artefact_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_intent_reconciliation_outcomes_pass_at ON intent_reconciliation_outcomes(pass_at DESC)"
            )
            # 0.8.3 WS1 — Deep-Run sessions + pass log. Owned by
            # vaner.store.deep_run; declared here so all artefact-DB tables
            # share one initialize() call and one connection lifecycle.
            from vaner.store.deep_run import create_deep_run_tables

            await create_deep_run_tables(db)
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

            # v7: add access tracking to prediction_cache so unused entries
            # can be decayed out of the store before they expire naturally.
            # ``access_count`` counts successful cache matches (any tier); a
            # value of 0 at prune time means Vaner spent compute precomputing
            # this entry and the developer never benefitted from it, so it
            # is a prime candidate for removal.
            if current_schema_version < 7:
                async with db.execute("PRAGMA table_info(prediction_cache)") as cursor:
                    prediction_columns = [row[1] for row in await cursor.fetchall()]
                if "access_count" not in prediction_columns:
                    await db.execute("ALTER TABLE prediction_cache ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0")
                if "last_accessed_at" not in prediction_columns:
                    await db.execute("ALTER TABLE prediction_cache ADD COLUMN last_accessed_at REAL NOT NULL DEFAULT 0")
                await db.execute("INSERT OR IGNORE INTO schema_version(version) VALUES (7)")

            # v8: extend workspace_goals with artefact back-refs + the §6.6
            # policy-consumer metadata block so downstream scheduling /
            # allocation / abstention / explanation policies read one
            # canonical representation of goal state (0.8.2 WS1/WS2).
            # ``artefact_refs_json`` lists artefact ids backing this goal;
            # ``subgoal_of`` is the parent goal id when the goal was
            # decomposed from an outline item. ``pc_freshness``,
            # ``pc_reconciliation_state``, ``pc_unfinished_item_state``
            # complete the §6.6 block (``status`` and ``confidence`` already
            # exist as native columns and serve the block directly).
            if current_schema_version < 8:
                async with db.execute("PRAGMA table_info(workspace_goals)") as cursor:
                    goal_columns = [row[1] for row in await cursor.fetchall()]
                if "artefact_refs_json" not in goal_columns:
                    await db.execute("ALTER TABLE workspace_goals ADD COLUMN artefact_refs_json TEXT NOT NULL DEFAULT '[]'")
                if "subgoal_of" not in goal_columns:
                    await db.execute("ALTER TABLE workspace_goals ADD COLUMN subgoal_of TEXT")
                if "pc_freshness" not in goal_columns:
                    await db.execute("ALTER TABLE workspace_goals ADD COLUMN pc_freshness REAL NOT NULL DEFAULT 1.0")
                if "pc_reconciliation_state" not in goal_columns:
                    await db.execute("ALTER TABLE workspace_goals ADD COLUMN pc_reconciliation_state TEXT NOT NULL DEFAULT 'unreconciled'")
                if "pc_unfinished_item_state" not in goal_columns:
                    await db.execute("ALTER TABLE workspace_goals ADD COLUMN pc_unfinished_item_state TEXT NOT NULL DEFAULT 'none'")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_workspace_goals_subgoal_of ON workspace_goals(subgoal_of)")
                await db.execute("INSERT OR IGNORE INTO schema_version(version) VALUES (8)")

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
        timestamp: float | None = None,
    ) -> str:
        query_id = str(uuid.uuid4())
        ts = float(timestamp) if timestamp is not None else time.time()
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
                    ts,
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
                INSERT INTO prediction_cache(
                    cache_key, prompt_hint, package_json, enrichment_json,
                    created_at, expires_at, access_count, last_accessed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 0, 0)
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

    async def touch_prediction_cache(self, cache_key: str) -> None:
        """Bump access_count + last_accessed_at for a cache entry.

        Called from ``TieredPredictionCache.match`` whenever an entry is
        selected as the best match. Access tracking is what lets the unused-
        decay prune distinguish "never consulted" entries (candidates for
        removal) from "actively useful" entries (worth extending).
        """
        if not cache_key:
            return
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE prediction_cache
                   SET access_count = access_count + 1,
                       last_accessed_at = ?
                 WHERE cache_key = ?
                """,
                (now, cache_key),
            )
            await db.commit()

    async def purge_unused_prediction_cache(
        self,
        *,
        max_age_seconds_without_access: float,
        min_access_count_to_protect: int = 1,
    ) -> int:
        """Delete cache entries that look unlikely to be needed.

        An entry is considered unused when:

        - its ``access_count`` is strictly below ``min_access_count_to_protect``
          (so Vaner precomputed it and the developer never consumed it), **and**
        - it has been sitting idle — defined as the larger of ``created_at`` and
          ``last_accessed_at`` — for longer than ``max_age_seconds_without_access``.

        This is a *decay* pass complementary to
        ``purge_expired_prediction_cache``: expired entries are dropped because
        their TTL elapsed, these are dropped because nothing asked for them,
        which usually means the prediction got unlikely over time and Vaner
        should reclaim the slot for something more promising.
        """
        if max_age_seconds_without_access <= 0.0:
            return 0
        now = time.time()
        cutoff = now - float(max_age_seconds_without_access)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                DELETE FROM prediction_cache
                 WHERE access_count < ?
                   AND MAX(COALESCE(last_accessed_at, 0), created_at) < ?
                """,
                (int(min_access_count_to_protect), cutoff),
            )
            await db.commit()
            return cursor.rowcount

    async def list_prediction_cache(self, *, include_expired: bool = False, limit: int = 200) -> list[dict[str, object]]:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            if include_expired:
                cursor = await db.execute(
                    """
                    SELECT cache_key, prompt_hint, package_json, enrichment_json,
                           created_at, expires_at, access_count, last_accessed_at
                    FROM prediction_cache
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            else:
                cursor = await db.execute(
                    """
                    SELECT cache_key, prompt_hint, package_json, enrichment_json,
                           created_at, expires_at, access_count, last_accessed_at
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
                "access_count": int(row[6] or 0),
                "last_accessed_at": float(row[7] or 0.0),
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

    async def bootstrap_habit_transitions(self, rows: list[dict[str, object]]) -> bool:
        existing = await self.list_habit_transitions(limit=1)
        if existing:
            return False
        await self.replace_habit_transitions(rows)
        return True

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

    async def bootstrap_prompt_macros(self, rows: list[dict[str, object]]) -> bool:
        existing = await self.list_prompt_macros(limit=1)
        if existing:
            return False
        await self.replace_prompt_macros(rows)
        return True

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

    # -----------------------------------------------------------------------
    # WS7 — workspace_goals
    # -----------------------------------------------------------------------

    async def upsert_workspace_goal(
        self,
        *,
        id: str,
        title: str,
        description: str,
        source: str,
        confidence: float,
        status: str,
        evidence_json: str,
        related_files_json: str,
        artefact_refs_json: str | None = None,
        subgoal_of: str | None = None,
        pc_freshness: float | None = None,
        pc_reconciliation_state: str | None = None,
        pc_unfinished_item_state: str | None = None,
    ) -> None:
        """Insert or update a goal by id.

        On conflict, preserves the original ``created_at`` (goals don't
        change their creation timestamp when re-observed) and refreshes
        everything else including ``last_observed_at``.

        The five WS2 keyword arguments default to ``None``; when *any*
        of them is set the extended SQL path writes all 0.8.2
        ``workspace_goals`` columns added in the v8 migration. When all
        five are unset, the legacy SQL path runs — it touches only the
        pre-0.8.2 columns, so existing callers (``vaner.goals.declare``,
        branch-name inference) don't accidentally overwrite
        artefact-set metadata a prior WS2 cycle may have written.
        """

        now = time.time()
        extended = any(
            value is not None
            for value in (
                artefact_refs_json,
                subgoal_of,
                pc_freshness,
                pc_reconciliation_state,
                pc_unfinished_item_state,
            )
        )
        async with aiosqlite.connect(self.db_path) as db:
            if not extended:
                # Legacy path — preserves pre-0.8.2 semantics exactly.
                await db.execute(
                    """
                    INSERT INTO workspace_goals(
                        id, title, description, source, confidence, status,
                        created_at, last_observed_at, evidence_json, related_files_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        description = excluded.description,
                        source = excluded.source,
                        confidence = excluded.confidence,
                        status = excluded.status,
                        last_observed_at = excluded.last_observed_at,
                        evidence_json = excluded.evidence_json,
                        related_files_json = excluded.related_files_json
                    """,
                    (
                        id,
                        title,
                        description,
                        source,
                        float(confidence),
                        status,
                        now,
                        now,
                        evidence_json,
                        related_files_json,
                    ),
                )
            else:
                # WS2 path — writes the extended column set. Columns that
                # the caller left as ``None`` fall back to the schema
                # defaults on INSERT; on UPDATE the CASE blocks preserve
                # any prior value so a follow-up inference cycle that
                # only updates a subset doesn't stomp the others.
                await db.execute(
                    """
                    INSERT INTO workspace_goals(
                        id, title, description, source, confidence, status,
                        created_at, last_observed_at, evidence_json, related_files_json,
                        artefact_refs_json, subgoal_of, pc_freshness,
                        pc_reconciliation_state, pc_unfinished_item_state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        description = excluded.description,
                        source = excluded.source,
                        confidence = excluded.confidence,
                        status = excluded.status,
                        last_observed_at = excluded.last_observed_at,
                        evidence_json = excluded.evidence_json,
                        related_files_json = excluded.related_files_json,
                        artefact_refs_json = excluded.artefact_refs_json,
                        subgoal_of = CASE
                            WHEN excluded.subgoal_of IS NULL
                            THEN workspace_goals.subgoal_of
                            ELSE excluded.subgoal_of END,
                        pc_freshness = excluded.pc_freshness,
                        pc_reconciliation_state = excluded.pc_reconciliation_state,
                        pc_unfinished_item_state = excluded.pc_unfinished_item_state
                    """,
                    (
                        id,
                        title,
                        description,
                        source,
                        float(confidence),
                        status,
                        now,
                        now,
                        evidence_json,
                        related_files_json,
                        artefact_refs_json if artefact_refs_json is not None else "[]",
                        subgoal_of,
                        1.0 if pc_freshness is None else float(pc_freshness),
                        pc_reconciliation_state or "unreconciled",
                        pc_unfinished_item_state or "none",
                    ),
                )
            await db.commit()

    async def list_workspace_goals(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        query = (
            "SELECT id, title, description, source, confidence, status, "
            "created_at, last_observed_at, evidence_json, related_files_json, "
            "artefact_refs_json, subgoal_of, pc_freshness, "
            "pc_reconciliation_state, pc_unfinished_item_state "
            "FROM workspace_goals WHERE 1=1"
        )
        params: list[object] = []
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY confidence DESC, created_at DESC LIMIT ?"
        params.append(int(limit))
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, tuple(params))
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_workspace_goal(self, goal_id: str) -> dict[str, object] | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, title, description, source, confidence, status, "
                "created_at, last_observed_at, evidence_json, related_files_json, "
                "artefact_refs_json, subgoal_of, pc_freshness, "
                "pc_reconciliation_state, pc_unfinished_item_state "
                "FROM workspace_goals WHERE id = ?",
                (goal_id,),
            )
            row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def update_workspace_goal_status(self, goal_id: str, status: str) -> bool:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE workspace_goals SET status = ?, last_observed_at = ? WHERE id = ?",
                (status, now, goal_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def delete_workspace_goal(self, goal_id: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM workspace_goals WHERE id = ?",
                (goal_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def update_workspace_goal_artefact_metadata(
        self,
        goal_id: str,
        *,
        artefact_refs_json: str | None = None,
        subgoal_of: str | None = None,
        pc_freshness: float | None = None,
        pc_reconciliation_state: str | None = None,
        pc_unfinished_item_state: str | None = None,
    ) -> bool:
        """Update the 0.8.2 goal columns: artefact refs + §6.6 metadata.

        Kept separate from :meth:`upsert_workspace_goal` so the existing
        WS7 callers (`vaner.goals.declare`, branch-name inference) keep
        their simple signature. Artefact-driven and reconciliation-driven
        updaters use this method instead.
        """

        updates: list[str] = []
        params: list[object] = []
        if artefact_refs_json is not None:
            updates.append("artefact_refs_json = ?")
            params.append(artefact_refs_json)
        if subgoal_of is not None:
            updates.append("subgoal_of = ?")
            params.append(subgoal_of)
        if pc_freshness is not None:
            updates.append("pc_freshness = ?")
            params.append(float(pc_freshness))
        if pc_reconciliation_state is not None:
            updates.append("pc_reconciliation_state = ?")
            params.append(pc_reconciliation_state)
        if pc_unfinished_item_state is not None:
            updates.append("pc_unfinished_item_state = ?")
            params.append(pc_unfinished_item_state)
        if not updates:
            return False
        params.append(goal_id)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                f"UPDATE workspace_goals SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )
            await db.commit()
            return cursor.rowcount > 0

    # -----------------------------------------------------------------------
    # 0.8.2 WS1 — intent-bearing artefacts
    # -----------------------------------------------------------------------

    async def upsert_intent_artefact(
        self,
        *,
        id: str,
        source_uri: str,
        source_tier: str,
        connector: str,
        kind: str,
        title: str,
        status: str,
        confidence: float,
        created_at: float,
        last_observed_at: float,
        last_reconciled_at: float | None,
        latest_snapshot: str,
        linked_goals_json: str,
        linked_files_json: str,
        supersedes: str | None,
    ) -> None:
        """Insert or update an intent artefact by id.

        On conflict, preserves the original ``created_at`` and refreshes
        everything else. ``latest_snapshot`` points at the current
        snapshot id; callers are expected to write the snapshot row
        first.
        """

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO intent_artefacts(
                    id, source_uri, source_tier, connector, kind, title, status,
                    confidence, created_at, last_observed_at, last_reconciled_at,
                    latest_snapshot, linked_goals_json, linked_files_json, supersedes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_uri = excluded.source_uri,
                    source_tier = excluded.source_tier,
                    connector = excluded.connector,
                    kind = excluded.kind,
                    title = excluded.title,
                    status = excluded.status,
                    confidence = excluded.confidence,
                    last_observed_at = excluded.last_observed_at,
                    last_reconciled_at = excluded.last_reconciled_at,
                    latest_snapshot = excluded.latest_snapshot,
                    linked_goals_json = excluded.linked_goals_json,
                    linked_files_json = excluded.linked_files_json,
                    supersedes = excluded.supersedes
                """,
                (
                    id,
                    source_uri,
                    source_tier,
                    connector,
                    kind,
                    title,
                    status,
                    float(confidence),
                    float(created_at),
                    float(last_observed_at),
                    None if last_reconciled_at is None else float(last_reconciled_at),
                    latest_snapshot,
                    linked_goals_json,
                    linked_files_json,
                    supersedes,
                ),
            )
            await db.commit()

    async def list_intent_artefacts(
        self,
        *,
        status: str | None = None,
        connector: str | None = None,
        source_tier: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        query = (
            "SELECT id, source_uri, source_tier, connector, kind, title, status, "
            "confidence, created_at, last_observed_at, last_reconciled_at, "
            "latest_snapshot, linked_goals_json, linked_files_json, supersedes "
            "FROM intent_artefacts WHERE 1=1"
        )
        params: list[object] = []
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        if connector is not None:
            query += " AND connector = ?"
            params.append(connector)
        if source_tier is not None:
            query += " AND source_tier = ?"
            params.append(source_tier)
        query += " ORDER BY last_observed_at DESC LIMIT ?"
        params.append(int(limit))
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, tuple(params))
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_intent_artefact(self, artefact_id: str) -> dict[str, object] | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, source_uri, source_tier, connector, kind, title, status, "
                "confidence, created_at, last_observed_at, last_reconciled_at, "
                "latest_snapshot, linked_goals_json, linked_files_json, supersedes "
                "FROM intent_artefacts WHERE id = ?",
                (artefact_id,),
            )
            row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def update_intent_artefact_status(self, artefact_id: str, status: str) -> bool:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE intent_artefacts SET status = ?, last_observed_at = ? WHERE id = ?",
                (status, now, artefact_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def upsert_intent_artefact_snapshot(
        self,
        *,
        id: str,
        artefact_id: str,
        captured_at: float,
        content_hash: str,
        text: str,
    ) -> None:
        """Insert or update a snapshot by id.

        Snapshots are content-hash-addressed (``id == content_hash``), so
        the on-conflict path is only reached for retries; it leaves
        ``text`` and ``captured_at`` as-is to preserve first-seen-time
        ordering.
        """

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO intent_artefact_snapshots(id, artefact_id, captured_at, content_hash, text)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (id, artefact_id, float(captured_at), content_hash, text),
            )
            await db.commit()

    async def list_intent_artefact_snapshots(
        self,
        artefact_id: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, artefact_id, captured_at, content_hash, text "
                "FROM intent_artefact_snapshots WHERE artefact_id = ? "
                "ORDER BY captured_at DESC LIMIT ?",
                (artefact_id, int(limit)),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_intent_artefact_snapshot(self, snapshot_id: str) -> dict[str, object] | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, artefact_id, captured_at, content_hash, text FROM intent_artefact_snapshots WHERE id = ?",
                (snapshot_id,),
            )
            row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def replace_intent_artefact_items(
        self,
        *,
        snapshot_id: str,
        artefact_id: str,
        items: list[dict[str, object]],
    ) -> None:
        """Replace the full item set for a snapshot.

        Called from the extraction pipeline once per snapshot. Each
        ``items`` entry must carry: ``id``, ``text``, ``kind``, ``state``,
        ``section_path``, ``parent_item`` (optional), plus the three JSON
        fields ``related_files_json``, ``related_entities_json``,
        ``evidence_refs_json``. Reconciliation (WS3) updates item state
        via :meth:`update_intent_artefact_item_state` without going
        through this method.
        """

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM intent_artefact_items WHERE snapshot_id = ?",
                (snapshot_id,),
            )
            await db.executemany(
                """
                INSERT INTO intent_artefact_items(
                    id, artefact_id, snapshot_id, text, kind, state, section_path,
                    parent_item, related_files_json, related_entities_json, evidence_refs_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(item["id"]),
                        artefact_id,
                        snapshot_id,
                        str(item["text"]),
                        str(item["kind"]),
                        str(item.get("state", "pending")),
                        str(item.get("section_path", "")),
                        item.get("parent_item"),
                        str(item.get("related_files_json", "[]")),
                        str(item.get("related_entities_json", "[]")),
                        str(item.get("evidence_refs_json", "[]")),
                    )
                    for item in items
                ],
            )
            await db.commit()

    async def list_intent_artefact_items(
        self,
        *,
        artefact_id: str | None = None,
        snapshot_id: str | None = None,
        state: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, object]]:
        """List items filtered by artefact, snapshot, and/or state.

        When ``snapshot_id`` is omitted but ``artefact_id`` is given, the
        query returns items from **all** snapshots of that artefact.
        Callers that want only the current items should pass
        ``snapshot_id`` = the artefact's ``latest_snapshot``.
        """

        query = (
            "SELECT id, artefact_id, snapshot_id, text, kind, state, section_path, "
            "parent_item, related_files_json, related_entities_json, evidence_refs_json "
            "FROM intent_artefact_items WHERE 1=1"
        )
        params: list[object] = []
        if artefact_id is not None:
            query += " AND artefact_id = ?"
            params.append(artefact_id)
        if snapshot_id is not None:
            query += " AND snapshot_id = ?"
            params.append(snapshot_id)
        if state is not None:
            query += " AND state = ?"
            params.append(state)
        query += " LIMIT ?"
        params.append(int(limit))
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, tuple(params))
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def update_intent_artefact_item_state(
        self,
        *,
        item_id: str,
        snapshot_id: str,
        state: str,
        evidence_refs_json: str | None = None,
    ) -> bool:
        """Update a single item's state (WS3 reconciliation path).

        ``item_id`` and ``snapshot_id`` form the composite primary key;
        items are pinned to the snapshot they were extracted from so
        state history is preserved when a new snapshot supersedes.
        """

        async with aiosqlite.connect(self.db_path) as db:
            if evidence_refs_json is not None:
                cursor = await db.execute(
                    "UPDATE intent_artefact_items SET state = ?, evidence_refs_json = ? WHERE id = ? AND snapshot_id = ?",
                    (state, evidence_refs_json, item_id, snapshot_id),
                )
            else:
                cursor = await db.execute(
                    "UPDATE intent_artefact_items SET state = ? WHERE id = ? AND snapshot_id = ?",
                    (state, item_id, snapshot_id),
                )
            await db.commit()
            return cursor.rowcount > 0

    async def insert_reconciliation_outcome(
        self,
        *,
        id: str,
        artefact_id: str,
        pass_at: float,
        triggering_signal_id: str | None,
        item_state_deltas_json: str,
        goal_status_deltas_json: str,
        supersedes_json: str,
        evidence_refs_json: str,
    ) -> None:
        """Write one reconciliation outcome record (WS3, spec §10.3).

        Authoritative persisted state for what a reconciliation pass
        decided. The ``progress_reconciled`` invalidation signal carries
        only ``{outcome_id, artefact_id}``; every consumer resolves full
        detail via :meth:`get_reconciliation_outcome`.
        """

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO intent_reconciliation_outcomes(
                    id, artefact_id, pass_at, triggering_signal_id,
                    item_state_deltas_json, goal_status_deltas_json,
                    supersedes_json, evidence_refs_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    id,
                    artefact_id,
                    float(pass_at),
                    triggering_signal_id,
                    item_state_deltas_json,
                    goal_status_deltas_json,
                    supersedes_json,
                    evidence_refs_json,
                ),
            )
            await db.commit()

    async def list_reconciliation_outcomes(
        self,
        *,
        artefact_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        query = (
            "SELECT id, artefact_id, pass_at, triggering_signal_id, "
            "item_state_deltas_json, goal_status_deltas_json, supersedes_json, "
            "evidence_refs_json FROM intent_reconciliation_outcomes WHERE 1=1"
        )
        params: list[object] = []
        if artefact_id is not None:
            query += " AND artefact_id = ?"
            params.append(artefact_id)
        query += " ORDER BY pass_at DESC LIMIT ?"
        params.append(int(limit))
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, tuple(params))
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_reconciliation_outcome(self, outcome_id: str) -> dict[str, object] | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, artefact_id, pass_at, triggering_signal_id, "
                "item_state_deltas_json, goal_status_deltas_json, supersedes_json, "
                "evidence_refs_json FROM intent_reconciliation_outcomes WHERE id = ?",
                (outcome_id,),
            )
            row = await cursor.fetchone()
        return dict(row) if row is not None else None
