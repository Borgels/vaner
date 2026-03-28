"""Tests for EventCollector and _VanerFileHandler."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vaner_daemon.config import DaemonConfig
from vaner_daemon.event_collector import (
    EventCollector,
    EventKind,
    _VanerFileHandler,
    _should_ignore,
)


def make_config(repo_path: Path) -> DaemonConfig:
    cfg = DaemonConfig.load(repo_path)
    return cfg


# ---------------------------------------------------------------------------
# _should_ignore unit tests (synchronous)
# ---------------------------------------------------------------------------

def test_watched_extension_not_ignored(tmp_path):
    """File with watched extension should not be ignored."""
    cfg = make_config(tmp_path)
    target = tmp_path / "src" / "main.py"
    target.parent.mkdir(parents=True)
    target.touch()
    assert not _should_ignore(str(target), cfg)


def test_non_watched_extension_ignored(tmp_path):
    """File with non-watched extension (.pyc, .log) should be ignored."""
    cfg = make_config(tmp_path)
    for ext in [".pyc", ".log", ".tmp", ".swp"]:
        f = tmp_path / f"somefile{ext}"
        f.touch()
        assert _should_ignore(str(f), cfg), f"Expected {ext} to be ignored"


def test_ignored_dir_git(tmp_path):
    """File inside .git/ should be ignored."""
    cfg = make_config(tmp_path)
    git_file = tmp_path / ".git" / "COMMIT_EDITMSG"
    git_file.parent.mkdir(parents=True)
    git_file.touch()
    assert _should_ignore(str(git_file), cfg)


def test_ignored_dir_pycache(tmp_path):
    """File inside __pycache__/ should be ignored."""
    cfg = make_config(tmp_path)
    cache_file = tmp_path / "src" / "__pycache__" / "module.cpython-312.pyc"
    cache_file.parent.mkdir(parents=True)
    cache_file.touch()
    assert _should_ignore(str(cache_file), cfg)


def test_ignored_dir_node_modules(tmp_path):
    """File inside node_modules/ should be ignored."""
    cfg = make_config(tmp_path)
    nm_file = tmp_path / "node_modules" / "lib" / "index.js"
    nm_file.parent.mkdir(parents=True)
    nm_file.touch()
    assert _should_ignore(str(nm_file), cfg)


def test_egg_info_ignored(tmp_path):
    """File inside a .egg-info directory should be ignored."""
    cfg = make_config(tmp_path)
    egg_file = tmp_path / "mypackage.egg-info" / "SOURCES.txt"
    egg_file.parent.mkdir(parents=True)
    egg_file.touch()
    assert _should_ignore(str(egg_file), cfg)


def test_vaner_dir_ignored(tmp_path):
    """File inside .vaner/ should be ignored."""
    cfg = make_config(tmp_path)
    vaner_file = tmp_path / ".vaner" / "state.db"
    vaner_file.parent.mkdir(parents=True)
    vaner_file.touch()
    assert _should_ignore(str(vaner_file), cfg)


# ---------------------------------------------------------------------------
# _VanerFileHandler integration tests
# ---------------------------------------------------------------------------

def test_handler_emits_file_changed_event(tmp_path):
    """Handler emits FILE_CHANGED event for watched extensions."""
    cfg = make_config(tmp_path)
    emitted = []

    handler = _VanerFileHandler(cfg, emitted.append)

    from watchdog.events import FileModifiedEvent
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir(parents=True)
    target.touch()

    event = FileModifiedEvent(str(target))
    event.is_directory = False
    handler.on_modified(event)

    assert len(emitted) == 1
    assert emitted[0].kind == EventKind.FILE_CHANGED
    assert emitted[0].path == "src/app.py"


def test_handler_emits_file_created_event(tmp_path):
    """Handler emits FILE_CREATED event."""
    cfg = make_config(tmp_path)
    emitted = []
    handler = _VanerFileHandler(cfg, emitted.append)

    from watchdog.events import FileCreatedEvent
    target = tmp_path / "new_file.ts"
    target.touch()

    event = FileCreatedEvent(str(target))
    event.is_directory = False
    handler.on_created(event)

    assert len(emitted) == 1
    assert emitted[0].kind == EventKind.FILE_CREATED


def test_handler_emits_file_deleted_event(tmp_path):
    """Handler emits FILE_DELETED event."""
    cfg = make_config(tmp_path)
    emitted = []
    handler = _VanerFileHandler(cfg, emitted.append)

    from watchdog.events import FileDeletedEvent
    # deleted file doesn't need to exist
    event = FileDeletedEvent(str(tmp_path / "gone.py"))
    event.is_directory = False
    handler.on_deleted(event)

    assert len(emitted) == 1
    assert emitted[0].kind == EventKind.FILE_DELETED


def test_handler_does_not_emit_for_git_dir(tmp_path):
    """Handler does not emit events for files inside .git/."""
    cfg = make_config(tmp_path)
    emitted = []
    handler = _VanerFileHandler(cfg, emitted.append)

    from watchdog.events import FileModifiedEvent
    git_file = tmp_path / ".git" / "COMMIT_EDITMSG"
    git_file.parent.mkdir(parents=True)
    git_file.touch()

    event = FileModifiedEvent(str(git_file))
    event.is_directory = False
    handler.on_modified(event)

    assert emitted == []


def test_handler_does_not_emit_for_pycache(tmp_path):
    """Handler does not emit events for __pycache__ files."""
    cfg = make_config(tmp_path)
    emitted = []
    handler = _VanerFileHandler(cfg, emitted.append)

    from watchdog.events import FileModifiedEvent
    cache_file = tmp_path / "src" / "__pycache__" / "module.pyc"
    cache_file.parent.mkdir(parents=True)
    cache_file.touch()

    event = FileModifiedEvent(str(cache_file))
    event.is_directory = False
    handler.on_modified(event)

    assert emitted == []


def test_handler_does_not_emit_for_non_watched_extension(tmp_path):
    """Handler does not emit events for .pyc, .log files."""
    cfg = make_config(tmp_path)
    emitted = []
    handler = _VanerFileHandler(cfg, emitted.append)

    from watchdog.events import FileModifiedEvent
    for ext in [".pyc", ".log"]:
        f = tmp_path / f"test{ext}"
        f.touch()
        event = FileModifiedEvent(str(f))
        event.is_directory = False
        handler.on_modified(event)

    assert emitted == []


# ---------------------------------------------------------------------------
# EventCollector integration test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_event_collector_emits_on_file_write(tmp_path):
    """EventCollector emits an event when a watched file is written."""
    cfg = make_config(tmp_path)
    queue: asyncio.Queue = asyncio.Queue()
    collector = EventCollector(cfg, queue)

    loop = asyncio.get_running_loop()
    collector.start(loop)

    try:
        # Write a watched file
        target = tmp_path / "hello.py"
        target.write_text("print('hello')")

        # Wait for the event to arrive
        try:
            event = await asyncio.wait_for(queue.get(), timeout=3.0)
            assert event.kind in (EventKind.FILE_CHANGED, EventKind.FILE_CREATED)
            assert "hello.py" in event.path
        except asyncio.TimeoutError:
            pytest.fail("No event received within timeout — watchdog may not be working")
    finally:
        collector.stop()


@pytest.mark.asyncio
async def test_event_collector_no_event_for_ignored_dir(tmp_path):
    """EventCollector does not emit events for files in ignored dirs."""
    cfg = make_config(tmp_path)
    queue: asyncio.Queue = asyncio.Queue()
    collector = EventCollector(cfg, queue)

    loop = asyncio.get_running_loop()
    collector.start(loop)

    try:
        # Write to a vaner state file (should be ignored)
        vaner_dir = tmp_path / ".vaner"
        vaner_dir.mkdir()
        (vaner_dir / "state.db").write_bytes(b"ignored")

        # Wait briefly — no event should arrive
        try:
            event = await asyncio.wait_for(queue.get(), timeout=0.5)
            pytest.fail(f"Unexpected event received: {event}")
        except asyncio.TimeoutError:
            pass  # Expected — no event
    finally:
        collector.stop()
