# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import time

import pytest

from vaner.models.artefact import Artefact, ArtefactKind
from vaner.store.artefacts import ArtefactStore


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
