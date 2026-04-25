# SPDX-License-Identifier: Apache-2.0
"""Tests for the 0.8.7 WS6 ``draft_events.event_type`` migration.

Validates:

- A pre-0.8.7 schema (``draft_events`` without ``event_type``) is
  migrated idempotently on first ``initialize()``.
- Migration is repeatable (running ``initialize()`` twice is a no-op).
- Pre-existing rows default to ``event_type='legacy_draft'``.
- ``record_draft_event`` writes ``event_type='legacy_draft'`` and bumps
  only the ``draft_*`` counter prefix (not ``composer_*``).
- ``record_composer_lifecycle_event`` writes
  ``event_type='composer_lifecycle'`` and bumps only the ``composer_*``
  counter prefix (not ``draft_*``).
"""

from __future__ import annotations

import asyncio

import aiosqlite

from vaner.telemetry.metrics import MetricsStore


def _new_store(temp_repo):
    return MetricsStore(temp_repo / ".vaner" / "metrics.db")


async def _table_columns(db_path) -> set[str]:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("PRAGMA table_info(draft_events)")
        rows = await cursor.fetchall()
    return {str(row[1]) for row in rows}


async def _seed_pre_0_8_7_db(db_path) -> None:
    """Create a draft_events table missing the event_type column."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE draft_events (
                id TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                status TEXT NOT NULL,
                predicted_prompt_similarity REAL NOT NULL DEFAULT 0.0,
                evidence_overlap REAL NOT NULL DEFAULT 0.0,
                answer_reuse_ratio REAL NOT NULL DEFAULT 0.0,
                directional_correct INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        # One pre-existing legacy row.
        await db.execute(
            """
            INSERT INTO draft_events
                (id, timestamp, status, predicted_prompt_similarity,
                 evidence_overlap, answer_reuse_ratio, directional_correct,
                 metadata_json)
            VALUES ('pre-0.8.7-row', 1.0, 'served', 0.5, 0.5, 0.5, 0, '{}')
            """
        )
        await db.commit()


def test_migration_adds_event_type_column_to_pre_0_8_7_db(temp_repo):
    store = _new_store(temp_repo)

    async def _run() -> set[str]:
        await _seed_pre_0_8_7_db(store.db_path)
        # Sanity: the seeded DB does not have event_type yet.
        assert "event_type" not in await _table_columns(store.db_path)
        await store.initialize()
        return await _table_columns(store.db_path)

    columns = asyncio.run(_run())
    assert "event_type" in columns


def test_migration_is_idempotent(temp_repo):
    store = _new_store(temp_repo)

    async def _run() -> set[str]:
        await _seed_pre_0_8_7_db(store.db_path)
        await store.initialize()
        # Run again — must not raise (the helper is idempotent).
        await store.initialize()
        return await _table_columns(store.db_path)

    columns = asyncio.run(_run())
    assert "event_type" in columns


def test_pre_existing_rows_default_to_legacy_draft(temp_repo):
    store = _new_store(temp_repo)

    async def _run() -> str:
        await _seed_pre_0_8_7_db(store.db_path)
        await store.initialize()
        async with aiosqlite.connect(store.db_path) as db:
            cursor = await db.execute(
                "SELECT event_type FROM draft_events WHERE id = ?",
                ("pre-0.8.7-row",),
            )
            row = await cursor.fetchone()
        assert row is not None
        return str(row[0])

    event_type = asyncio.run(_run())
    assert event_type == "legacy_draft"


def test_record_draft_event_writes_legacy_draft_event_type(temp_repo):
    store = _new_store(temp_repo)

    async def _run() -> str:
        await store.initialize()
        await store.record_draft_event(status="served")
        async with aiosqlite.connect(store.db_path) as db:
            cursor = await db.execute("SELECT event_type FROM draft_events")
            row = await cursor.fetchone()
        assert row is not None
        return str(row[0])

    event_type = asyncio.run(_run())
    assert event_type == "legacy_draft"


def test_record_composer_lifecycle_event_writes_composer_event_type(temp_repo):
    store = _new_store(temp_repo)

    async def _run() -> tuple[str, str]:
        await store.initialize()
        await store.record_composer_lifecycle_event(
            session_id="s1",
            snapshot_id="snap-1",
            lifecycle_state="submitted",
            text_hash="0" * 64,
            length_chars=10,
            composer_event_id="evt-1",
        )
        async with aiosqlite.connect(store.db_path) as db:
            cursor = await db.execute("SELECT event_type, status FROM draft_events")
            row = await cursor.fetchone()
        assert row is not None
        return str(row[0]), str(row[1])

    event_type, status = asyncio.run(_run())
    assert event_type == "composer_lifecycle"
    assert status == "submitted"


def test_counter_namespace_is_isolated(temp_repo):
    """Composer rows must NOT bump existing draft_*_total counters.

    0.8.6 dashboards index by ``draft_{served|useful|wrong|unused}_total``.
    A composer-lifecycle write that mistakenly fed those counters would
    silently corrupt the dashboards. WS6's invariant: composer events
    write only to ``composer_*_total``.
    """
    store = _new_store(temp_repo)

    async def _run() -> dict[str, float]:
        await store.initialize()
        # One composer event with status='submitted'.
        await store.record_composer_lifecycle_event(
            session_id="s1",
            snapshot_id="snap-1",
            lifecycle_state="submitted",
            text_hash="0" * 64,
            length_chars=10,
            composer_event_id="evt-1",
        )
        async with aiosqlite.connect(store.db_path) as db:
            cursor = await db.execute("SELECT name, value FROM memory_quality_counters")
            rows = await cursor.fetchall()
        return {str(row[0]): float(row[1]) for row in rows}

    counters = asyncio.run(_run())
    assert counters.get("composer_submitted_total") == 1.0
    # The 0.8.6 draft counters MUST be untouched.
    for legacy in (
        "draft_served_total",
        "draft_useful_total",
        "draft_wrong_total",
        "draft_unused_total",
    ):
        assert legacy not in counters, f"composer event corrupted legacy counter {legacy!r}"


def test_metadata_carries_audit_fields_not_raw_text(temp_repo):
    """The composer-lifecycle metadata must NEVER carry raw text."""
    store = _new_store(temp_repo)

    async def _run() -> dict:
        await store.initialize()
        await store.record_composer_lifecycle_event(
            session_id="s1",
            snapshot_id="snap-1",
            lifecycle_state="submitted",
            text_hash="0" * 64,
            length_chars=42,
            composer_event_id="evt-1",
        )
        async with aiosqlite.connect(store.db_path) as db:
            cursor = await db.execute("SELECT metadata_json FROM draft_events")
            row = await cursor.fetchone()
        assert row is not None
        import json

        return json.loads(row[0])

    meta = asyncio.run(_run())
    assert meta["session_id"] == "s1"
    assert meta["snapshot_id"] == "snap-1"
    assert meta["text_hash"] == "0" * 64
    assert meta["length_chars"] == 42
    assert meta["composer_event_id"] == "evt-1"
    # Pin the privacy invariant: no raw-text key under any reasonable name.
    for forbidden in ("text", "draft_text", "raw_text", "preview", "prompt"):
        assert forbidden not in meta
