# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import time

import pytest

from vaner.intent.profile import UserProfile
from vaner.store.profile_store import UserProfileStore


@pytest.mark.asyncio
async def test_load_on_empty_db_returns_fresh_profile(tmp_path):
    store = UserProfileStore(tmp_path / "user_profile.db")
    profile = await store.load()
    assert profile.pace_ema_seconds == 0.0
    assert profile.mode_mix == {}
    assert profile._query_count == 0


@pytest.mark.asyncio
async def test_save_load_roundtrip(tmp_path):
    store = UserProfileStore(tmp_path / "user_profile.db")
    profile = UserProfile()
    t = time.time()
    profile.observe(mode="implement", depth=5, ts=t)
    profile.observe(mode="debug", depth=3, ts=t + 30)
    profile.observe(mode="debug", depth=4, ts=t + 60)
    await store.save(profile)

    loaded = await store.load()
    assert loaded.depth_preference == pytest.approx(profile.depth_preference, abs=1e-6)
    assert loaded.pivot_rate == pytest.approx(profile.pivot_rate, abs=1e-6)
    assert loaded._query_count == profile._query_count
    assert loaded._pivots == profile._pivots
    assert loaded._last_mode == profile._last_mode
    assert loaded.mode_mix == pytest.approx(profile.mode_mix, abs=1e-6)


@pytest.mark.asyncio
async def test_save_overwrites_existing_row(tmp_path):
    store = UserProfileStore(tmp_path / "user_profile.db")
    first = UserProfile()
    first.observe(mode="implement", depth=5, ts=1000.0)
    await store.save(first)

    second = UserProfile()
    second.observe(mode="debug", depth=2, ts=2000.0)
    second.observe(mode="debug", depth=2, ts=2500.0)
    await store.save(second)

    loaded = await store.load()
    assert loaded._query_count == 2
    assert loaded._last_mode == "debug"


@pytest.mark.asyncio
async def test_initialize_creates_parent_dirs(tmp_path):
    db_path = tmp_path / "nested" / "dir" / "user_profile.db"
    store = UserProfileStore(db_path)
    await store.initialize()
    assert db_path.exists()


@pytest.mark.asyncio
async def test_separate_profile_keys_are_isolated(tmp_path):
    db_path = tmp_path / "user_profile.db"
    store_a = UserProfileStore(db_path, profile_key="user_a")
    store_b = UserProfileStore(db_path, profile_key="user_b")
    pa = UserProfile()
    pa.observe(mode="implement", depth=1, ts=1000.0)
    await store_a.save(pa)

    pb = UserProfile()
    pb.observe(mode="debug", depth=5, ts=1000.0)
    pb.observe(mode="debug", depth=5, ts=1100.0)
    await store_b.save(pb)

    loaded_a = await store_a.load()
    loaded_b = await store_b.load()
    assert loaded_a._query_count == 1
    assert loaded_b._query_count == 2
    assert loaded_a._last_mode == "implement"
    assert loaded_b._last_mode == "debug"


# ---------------------------------------------------------------------------
# migrate_from_json
# ---------------------------------------------------------------------------


def _write_legacy_json(path, query_count=5, last_mode="implement"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pace_ema_seconds": 42.0,
                "pivot_rate": 0.2,
                "depth_preference": 3.5,
                "mode_mix": {last_mode: 1.0},
                "updated_at": 1000.0,
                "_last_query_ts": 1000.0,
                "_query_count": query_count,
                "_pivots": 1,
                "_last_mode": last_mode,
            }
        )
    )


@pytest.mark.asyncio
async def test_migrate_from_json_succeeds_and_removes_file(tmp_path):
    json_path = tmp_path / "user_profile.json"
    _write_legacy_json(json_path, query_count=7, last_mode="plan")
    store = UserProfileStore(tmp_path / "user_profile.db")

    migrated = await store.migrate_from_json(json_path)
    assert migrated is True
    assert not json_path.exists()

    loaded = await store.load()
    assert loaded._query_count == 7
    assert loaded._last_mode == "plan"
    assert loaded.pace_ema_seconds == pytest.approx(42.0)


@pytest.mark.asyncio
async def test_migrate_from_json_missing_file_returns_false(tmp_path):
    store = UserProfileStore(tmp_path / "user_profile.db")
    assert await store.migrate_from_json(tmp_path / "no_such.json") is False


@pytest.mark.asyncio
async def test_migrate_from_json_skips_when_sqlite_has_data(tmp_path):
    json_path = tmp_path / "user_profile.json"
    _write_legacy_json(json_path, query_count=2, last_mode="implement")
    store = UserProfileStore(tmp_path / "user_profile.db")

    # Seed SQLite first
    existing = UserProfile()
    existing.observe(mode="debug", depth=1, ts=5000.0)
    await store.save(existing)

    migrated = await store.migrate_from_json(json_path)
    assert migrated is False
    assert json_path.exists()  # JSON preserved — don't stomp unknown state

    loaded = await store.load()
    assert loaded._last_mode == "debug"  # SQLite data intact


@pytest.mark.asyncio
async def test_migrate_from_json_corrupt_returns_false(tmp_path):
    json_path = tmp_path / "user_profile.json"
    json_path.write_text("{not valid json")
    store = UserProfileStore(tmp_path / "user_profile.db")

    migrated = await store.migrate_from_json(json_path)
    assert migrated is False
    assert json_path.exists()  # corrupt file preserved for inspection


@pytest.mark.asyncio
async def test_migrate_from_json_empty_profile_still_removes_json(tmp_path):
    json_path = tmp_path / "user_profile.json"
    _write_legacy_json(json_path, query_count=0, last_mode="")
    store = UserProfileStore(tmp_path / "user_profile.db")

    migrated = await store.migrate_from_json(json_path)
    # Nothing worth migrating, but the stub JSON is still cleaned up
    assert migrated is False
    assert not json_path.exists()
