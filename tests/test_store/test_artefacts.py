# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time

import aiosqlite
import pytest

from vaner.models.artefact import Artefact, ArtefactKind
from vaner.store.artefacts import ArtefactStore


@pytest.mark.asyncio
async def test_store_upsert_and_list(tmp_path):
    store = ArtefactStore(tmp_path / "store.db")
    await store.initialize()
    artefact = Artefact(
        key="file_summary:sample.py",
        kind=ArtefactKind.FILE_SUMMARY,
        source_path="sample.py",
        source_mtime=time.time(),
        generated_at=time.time(),
        model="test",
        content="summary",
    )
    await store.upsert(artefact)
    rows = await store.list()
    assert len(rows) == 1
    assert rows[0].key == artefact.key


@pytest.mark.asyncio
async def test_store_lists_artefacts_by_keys_and_source_paths(tmp_path):
    store = ArtefactStore(tmp_path / "store.db")
    await store.initialize()
    now = time.time()
    artefacts = [
        Artefact(
            key="file_summary:a.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="a.py",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="alpha",
        ),
        Artefact(
            key="file_summary:b.py",
            kind=ArtefactKind.FILE_SUMMARY,
            source_path="b.py",
            source_mtime=now,
            generated_at=now,
            model="test",
            content="beta",
        ),
    ]
    for artefact in artefacts:
        await store.upsert(artefact)

    by_key = await store.list_by_keys(["file_summary:b.py", "missing", "file_summary:a.py"])
    by_path = await store.list_by_source_paths(["b.py", "a.py"])
    paths = await store.list_source_paths()

    assert [row.key for row in by_key] == ["file_summary:b.py", "file_summary:a.py"]
    assert [row.source_path for row in by_path] == ["b.py", "a.py"]
    assert paths == ["a.py", "b.py"]


@pytest.mark.asyncio
async def test_store_initialize_migrates_synthetic_v6_database(tmp_path):
    db_path = tmp_path / "store.db"
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE schema_version(version INTEGER PRIMARY KEY)")
        await db.execute("INSERT INTO schema_version(version) VALUES (6)")
        await db.execute(
            """
            CREATE TABLE prediction_cache (
                cache_key TEXT PRIMARY KEY,
                prompt_hint TEXT NOT NULL,
                package_json TEXT,
                enrichment_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE workspace_goals (
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
        await db.commit()

    store = ArtefactStore(db_path)
    await store.initialize()

    async with aiosqlite.connect(db_path) as db:
        version_row = await (await db.execute("SELECT MAX(version) FROM schema_version")).fetchone()
        prediction_columns = [row[1] for row in await (await db.execute("PRAGMA table_info(prediction_cache)")).fetchall()]
        goal_columns = [row[1] for row in await (await db.execute("PRAGMA table_info(workspace_goals)")).fetchall()]

    assert version_row[0] == 8
    assert "last_accessed_at" in prediction_columns
    assert "subgoal_of" in goal_columns
    assert "pc_reconciliation_state" in goal_columns
    assert "pc_unfinished_item_state" in goal_columns
