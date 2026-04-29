# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time

import aiosqlite
import pytest

from vaner.models.artefact import Artefact, ArtefactKind
from vaner.store.artefacts import ArtefactStore
from vaner.store.scenarios.sqlite import ScenarioStore


@pytest.mark.asyncio
async def test_store_staleness_and_access_tracking(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    source = repo_root / "file.py"
    source.write_text("print('hello')\n", encoding="utf-8")

    store = ArtefactStore(tmp_path / "store.db")
    await store.initialize()
    artefact = Artefact(
        key="file_summary:file.py",
        kind=ArtefactKind.FILE_SUMMARY,
        source_path="file.py",
        source_mtime=source.stat().st_mtime,
        generated_at=time.time(),
        model="test",
        content="summary",
    )
    await store.upsert(artefact)
    await store.mark_accessed(artefact.key)

    stored = await store.get(artefact.key)
    assert stored is not None
    assert stored.access_count == 1
    assert stored.last_accessed is not None
    assert await store.is_stale(stored, repo_root, max_age_seconds=10_000) is False

    source.write_text("print('changed')\n", encoding="utf-8")
    assert await store.is_stale(stored, repo_root, max_age_seconds=10_000) is True


@pytest.mark.asyncio
async def test_purge_expired(tmp_path):
    store = ArtefactStore(tmp_path / "store.db")
    await store.initialize()
    old = Artefact(
        key="file_summary:old.py",
        kind=ArtefactKind.FILE_SUMMARY,
        source_path="old.py",
        source_mtime=0.0,
        generated_at=time.time() - 10_000,
        model="test",
        content="old",
    )
    fresh = Artefact(
        key="file_summary:fresh.py",
        kind=ArtefactKind.FILE_SUMMARY,
        source_path="fresh.py",
        source_mtime=0.0,
        generated_at=time.time(),
        model="test",
        content="fresh",
    )
    await store.upsert(old)
    await store.upsert(fresh)

    removed = await store.purge_expired(max_age_seconds=60)
    keys = [item.key for item in await store.list(limit=10)]

    assert removed == 1
    assert keys == [fresh.key]


@pytest.mark.asyncio
async def test_scenario_store_initialize_migrates_legacy_scenarios_table(tmp_path):
    db_path = tmp_path / "scenarios.db"
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE scenarios (
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
        await db.commit()

    store = ScenarioStore(db_path)
    await store.initialize()

    async with aiosqlite.connect(db_path) as db:
        columns = [row[1] for row in await (await db.execute("PRAGMA table_info(scenarios)")).fetchall()]

    assert "memory_state" in columns
    assert "memory_confidence" in columns
    assert "memory_evidence_hashes_json" in columns
    assert "prior_successes" in columns
