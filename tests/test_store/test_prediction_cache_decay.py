# SPDX-License-Identifier: Apache-2.0
"""Tests for access tracking + unused-decay purge on ``prediction_cache``."""

from __future__ import annotations

import asyncio
import time

import aiosqlite
import pytest

from vaner.store.artefacts import ArtefactStore


async def _count_rows(store: ArtefactStore) -> int:
    async with aiosqlite.connect(store.db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM prediction_cache")
        row = await cursor.fetchone()
    return int(row[0]) if row else 0


@pytest.mark.asyncio
async def test_new_cache_entry_starts_with_zero_accesses(tmp_path):
    store = ArtefactStore(tmp_path / "store.db")
    await store.initialize()
    await store.upsert_prediction_cache(
        cache_key="k1",
        prompt_hint="hint",
        package_json=None,
        enrichment={"x": 1},
        ttl_seconds=3600,
    )
    rows = await store.list_prediction_cache()
    assert len(rows) == 1
    assert rows[0]["access_count"] == 0
    assert rows[0]["last_accessed_at"] == 0.0


@pytest.mark.asyncio
async def test_touch_prediction_cache_bumps_access(tmp_path):
    store = ArtefactStore(tmp_path / "store.db")
    await store.initialize()
    await store.upsert_prediction_cache(
        cache_key="k1",
        prompt_hint="hint",
        package_json=None,
        enrichment={},
        ttl_seconds=3600,
    )
    await store.touch_prediction_cache("k1")
    await store.touch_prediction_cache("k1")
    rows = await store.list_prediction_cache()
    assert rows[0]["access_count"] == 2
    assert rows[0]["last_accessed_at"] > 0.0


@pytest.mark.asyncio
async def test_purge_unused_removes_stale_zero_access_entries(tmp_path):
    store = ArtefactStore(tmp_path / "store.db")
    await store.initialize()
    # Create an entry, then backdate its created_at so it looks stale.
    await store.upsert_prediction_cache(
        cache_key="stale",
        prompt_hint="old",
        package_json=None,
        enrichment={},
        ttl_seconds=3600,
    )
    async with aiosqlite.connect(store.db_path) as db:
        await db.execute(
            "UPDATE prediction_cache SET created_at = ?, last_accessed_at = 0 WHERE cache_key = 'stale'",
            (time.time() - 7200,),
        )
        await db.commit()

    # Fresh entry (should be protected by age guard).
    await store.upsert_prediction_cache(
        cache_key="fresh",
        prompt_hint="new",
        package_json=None,
        enrichment={},
        ttl_seconds=3600,
    )
    # Touched entry (should be protected by access-count guard).
    await store.upsert_prediction_cache(
        cache_key="touched",
        prompt_hint="useful",
        package_json=None,
        enrichment={},
        ttl_seconds=3600,
    )
    await store.touch_prediction_cache("touched")
    async with aiosqlite.connect(store.db_path) as db:
        await db.execute(
            "UPDATE prediction_cache SET created_at = ? WHERE cache_key = 'touched'",
            (time.time() - 7200,),
        )
        await db.commit()

    assert await _count_rows(store) == 3
    removed = await store.purge_unused_prediction_cache(
        max_age_seconds_without_access=3600.0,
        min_access_count_to_protect=1,
    )
    assert removed == 1
    remaining_keys = {row["cache_key"] for row in await store.list_prediction_cache(include_expired=True)}
    assert "stale" not in remaining_keys
    assert "fresh" in remaining_keys
    assert "touched" in remaining_keys


@pytest.mark.asyncio
async def test_purge_unused_is_noop_when_disabled(tmp_path):
    store = ArtefactStore(tmp_path / "store.db")
    await store.initialize()
    await store.upsert_prediction_cache(
        cache_key="k",
        prompt_hint="h",
        package_json=None,
        enrichment={},
        ttl_seconds=3600,
    )
    removed = await store.purge_unused_prediction_cache(max_age_seconds_without_access=0.0)
    assert removed == 0
    assert await _count_rows(store) == 1


@pytest.mark.asyncio
async def test_touch_is_safe_for_missing_key(tmp_path):
    store = ArtefactStore(tmp_path / "store.db")
    await store.initialize()
    # Should not raise even though no cache entry exists yet.
    await store.touch_prediction_cache("does-not-exist")
    await store.touch_prediction_cache("")
    await asyncio.sleep(0)
