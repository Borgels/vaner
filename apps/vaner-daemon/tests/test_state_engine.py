"""Tests for StateEngine."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from vaner_daemon.config import DaemonConfig
from vaner_daemon.event_collector import EventKind, VanerEvent
from vaner_daemon.state_engine import StateEngine


def make_config(tmp_path: Path, max_active_files: int = 5) -> DaemonConfig:
    cfg = DaemonConfig.load(tmp_path)
    cfg.max_active_files = max_active_files
    return cfg


async def _start_engine(engine: StateEngine, branch: str = "main") -> None:
    """Start engine with a mocked git branch call."""
    with patch.object(engine, "_get_current_branch", return_value=branch):
        await engine.start()
    # Give the consumer task a tick to start
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_initial_snapshot(tmp_path):
    """get_snapshot() returns correct branch and empty active_files on init."""
    queue = asyncio.Queue()
    cfg = make_config(tmp_path)
    engine = StateEngine(cfg, queue)
    await _start_engine(engine, branch="main")

    snap = engine.get_snapshot()
    assert snap.branch == "main"
    assert snap.active_files == []
    assert snap.repo_path == str(tmp_path.resolve())

    await engine.stop()


@pytest.mark.asyncio
async def test_file_changed_appears_in_active_files(tmp_path):
    """FILE_CHANGED event → file appears in active_files."""
    queue = asyncio.Queue()
    cfg = make_config(tmp_path)
    engine = StateEngine(cfg, queue)
    await _start_engine(engine, branch="main")

    event = VanerEvent(kind=EventKind.FILE_CHANGED, path="src/foo.py")
    await queue.put(event)
    await asyncio.sleep(0.05)

    snap = engine.get_snapshot()
    assert "src/foo.py" in snap.active_files

    await engine.stop()


@pytest.mark.asyncio
async def test_lru_trims_oldest_files(tmp_path):
    """More than max_active_files events → oldest files dropped."""
    queue = asyncio.Queue()
    cfg = make_config(tmp_path, max_active_files=3)
    engine = StateEngine(cfg, queue)
    await _start_engine(engine, branch="main")

    files = ["a.py", "b.py", "c.py", "d.py", "e.py"]
    for f in files:
        await queue.put(VanerEvent(kind=EventKind.FILE_CHANGED, path=f))
        await asyncio.sleep(0.01)

    await asyncio.sleep(0.1)
    snap = engine.get_snapshot()

    assert len(snap.active_files) <= 3
    # Most recently touched files should be present
    assert "e.py" in snap.active_files
    # Oldest should be gone
    assert "a.py" not in snap.active_files

    await engine.stop()


@pytest.mark.asyncio
async def test_branch_switch_clears_active_files(tmp_path):
    """GIT_BRANCH_SWITCH → branch updated, active_files cleared, callback called."""
    queue = asyncio.Queue()
    cfg = make_config(tmp_path)
    engine = StateEngine(cfg, queue)
    await _start_engine(engine, branch="main")

    # Add some files
    for f in ["a.py", "b.py"]:
        await queue.put(VanerEvent(kind=EventKind.FILE_CHANGED, path=f))
    await asyncio.sleep(0.05)

    invalidated_calls = []
    engine.on_context_invalidated(lambda old, new: invalidated_calls.append((old, new)))

    branch_event = VanerEvent(kind=EventKind.GIT_BRANCH_SWITCH, path="", branch="feature/x", from_branch="main")
    await queue.put(branch_event)
    await asyncio.sleep(0.05)

    snap = engine.get_snapshot()
    assert snap.branch == "feature/x"
    assert snap.active_files == []
    assert len(invalidated_calls) == 1
    assert invalidated_calls[0] == ("main", "feature/x")

    await engine.stop()


@pytest.mark.asyncio
async def test_context_key_changes_on_branch_switch(tmp_path):
    """context_key changes when branch changes."""
    queue = asyncio.Queue()
    cfg = make_config(tmp_path)
    engine = StateEngine(cfg, queue)
    await _start_engine(engine, branch="main")

    key1 = engine.get_snapshot().context_key

    branch_event = VanerEvent(kind=EventKind.GIT_BRANCH_SWITCH, path="", branch="other", from_branch="main")
    await queue.put(branch_event)
    await asyncio.sleep(0.05)

    key2 = engine.get_snapshot().context_key
    assert key1 != key2

    await engine.stop()


def test_get_snapshot_is_fast(tmp_path):
    """get_snapshot() completes in <1ms (time with 1000 iterations)."""
    queue = asyncio.Queue()
    cfg = make_config(tmp_path)
    engine = StateEngine(cfg, queue)
    # Manually initialize without starting the async loop
    engine._branch = "main"

    iterations = 1000
    start = time.perf_counter()
    for _ in range(iterations):
        engine.get_snapshot()
    elapsed = time.perf_counter() - start

    avg_ms = (elapsed / iterations) * 1000
    assert avg_ms < 1.0, f"get_snapshot() avg {avg_ms:.3f}ms — must be <1ms"


@pytest.mark.asyncio
async def test_state_persists_across_restart(tmp_path):
    """State persists across restart: write files to DB, reinit StateEngine, verify files present."""
    queue = asyncio.Queue()
    cfg = make_config(tmp_path, max_active_files=5)
    engine = StateEngine(cfg, queue)
    await _start_engine(engine, branch="persist-branch")

    # Add some files
    for f in ["x.py", "y.py", "z.py"]:
        await queue.put(VanerEvent(kind=EventKind.FILE_CHANGED, path=f))
    await asyncio.sleep(0.1)

    await engine.stop()

    # Re-create engine — should load from DB
    queue2 = asyncio.Queue()
    engine2 = StateEngine(cfg, queue2)
    await _start_engine(engine2, branch="persist-branch")

    snap = engine2.get_snapshot()
    assert "x.py" in snap.active_files
    assert "y.py" in snap.active_files
    assert "z.py" in snap.active_files

    await engine2.stop()


@pytest.mark.asyncio
async def test_file_deleted_removed_from_active_files(tmp_path):
    """FILE_DELETED event removes file from active_files."""
    queue = asyncio.Queue()
    cfg = make_config(tmp_path)
    engine = StateEngine(cfg, queue)
    await _start_engine(engine, branch="main")

    await queue.put(VanerEvent(kind=EventKind.FILE_CHANGED, path="delete_me.py"))
    await asyncio.sleep(0.05)
    assert "delete_me.py" in engine.get_snapshot().active_files

    await queue.put(VanerEvent(kind=EventKind.FILE_DELETED, path="delete_me.py"))
    await asyncio.sleep(0.05)
    assert "delete_me.py" not in engine.get_snapshot().active_files

    await engine.stop()


@pytest.mark.asyncio
async def test_on_context_changed_callback(tmp_path):
    """on_context_changed callback is called when file changes."""
    queue = asyncio.Queue()
    cfg = make_config(tmp_path)
    engine = StateEngine(cfg, queue)
    await _start_engine(engine, branch="main")

    changed_snapshots = []
    engine.on_context_changed(lambda snap: changed_snapshots.append(snap))

    await queue.put(VanerEvent(kind=EventKind.FILE_CHANGED, path="test.py"))
    await asyncio.sleep(0.05)

    assert len(changed_snapshots) >= 1
    assert any("test.py" in s.active_files for s in changed_snapshots)

    await engine.stop()
