"""Tests for crash recovery and cancellation persistence."""
from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_engine(tmp_path: Path):
    from vaner_daemon.preparation_engine.engine import PreparationEngine
    state_engine = MagicMock()
    state_engine.on_context_changed = MagicMock()
    state_engine.on_context_invalidated = MagicMock()
    loop = asyncio.new_event_loop()
    engine = PreparationEngine(
        repo_root=tmp_path,
        state_engine=state_engine,
        loop=loop,
        model_name="test-model",
    )
    return engine, loop


def _seed_checkpoint(db_path: Path, thread_id: str, next_val: str) -> None:
    """Insert a fake LangGraph checkpoint row."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS checkpoints "
        "(thread_id TEXT, checkpoint_id TEXT, next TEXT)"
    )
    conn.execute(
        "INSERT INTO checkpoints VALUES (?, ?, ?)",
        (thread_id, "cp-001", next_val),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Crash recovery tests
# ---------------------------------------------------------------------------

def test_recovery_resumes_in_progress(tmp_path):
    engine, loop = _make_engine(tmp_path)
    db_path = tmp_path / ".vaner" / "preparation.db"
    _seed_checkpoint(db_path, "ctx-abc", "run_jobs")  # in-progress

    invocations = []

    async def fake_ainvoke(state, config):
        invocations.append(config["configurable"]["thread_id"])

    engine._graph = MagicMock()
    engine._graph.ainvoke = fake_ainvoke

    engine._ensure_cancelled_table()
    engine.recover_in_progress_runs()

    # Give the coroutine a tick to run
    loop.run_until_complete(asyncio.sleep(0.05))
    loop.close()

    assert "ctx-abc" in invocations


def test_recovery_skips_completed(tmp_path):
    engine, loop = _make_engine(tmp_path)
    db_path = tmp_path / ".vaner" / "preparation.db"
    _seed_checkpoint(db_path, "ctx-done", "")  # empty next = completed

    invocations = []

    async def fake_ainvoke(state, config):
        invocations.append(config["configurable"]["thread_id"])

    engine._graph = MagicMock()
    engine._graph.ainvoke = fake_ainvoke

    engine._ensure_cancelled_table()
    engine.recover_in_progress_runs()

    loop.run_until_complete(asyncio.sleep(0.05))
    loop.close()

    assert invocations == []


def test_recovery_skips_cancelled_context(tmp_path):
    engine, loop = _make_engine(tmp_path)
    db_path = tmp_path / ".vaner" / "preparation.db"
    _seed_checkpoint(db_path, "ctx-cancelled", "run_jobs")

    engine._ensure_cancelled_table()
    engine._mark_context_cancelled("ctx-cancelled")

    invocations = []

    async def fake_ainvoke(state, config):
        invocations.append(config["configurable"]["thread_id"])

    engine._graph = MagicMock()
    engine._graph.ainvoke = fake_ainvoke

    engine.recover_in_progress_runs()
    loop.run_until_complete(asyncio.sleep(0.05))
    loop.close()

    assert invocations == []


def test_recovery_handles_missing_db(tmp_path):
    """No DB → no crash, no recoveries."""
    engine, loop = _make_engine(tmp_path)
    engine._ensure_cancelled_table()
    # Don't seed any checkpoint — db only has cancelled_contexts table
    engine.recover_in_progress_runs()  # must not raise
    loop.close()


# ---------------------------------------------------------------------------
# Cancellation persistence tests
# ---------------------------------------------------------------------------

def test_cancellation_persisted(tmp_path):
    engine, _ = _make_engine(tmp_path)
    engine._ensure_cancelled_table()
    engine._mark_context_cancelled("ctx-x")
    assert engine._is_context_cancelled("ctx-x") is True
    assert engine._is_context_cancelled("ctx-y") is False


def test_old_cancellations_cleaned_up(tmp_path):
    engine, _ = _make_engine(tmp_path)
    engine._ensure_cancelled_table()

    # Insert an old entry directly
    db_path = tmp_path / ".vaner" / "preparation.db"
    conn = sqlite3.connect(str(db_path))
    old_ts = time.time() - 90000  # 25 hours ago
    conn.execute(
        "INSERT OR REPLACE INTO cancelled_contexts VALUES (?, ?)",
        ("ctx-old", old_ts),
    )
    conn.commit()
    conn.close()

    assert engine._is_context_cancelled("ctx-old") is True
    engine._cleanup_old_cancellations()
    assert engine._is_context_cancelled("ctx-old") is False


def test_on_context_invalidated_persists_cancellation(tmp_path):
    engine, loop = _make_engine(tmp_path)
    engine._ensure_cancelled_table()

    # Pre-seed an active task mock
    mock_task = MagicMock()
    engine._active_tasks["ctx-running"] = mock_task

    engine._on_context_invalidated("main", "feature")

    mock_task.cancel.assert_called_once()
    assert engine._is_context_cancelled("ctx-running") is True
    assert engine._active_tasks == {}
    loop.close()
