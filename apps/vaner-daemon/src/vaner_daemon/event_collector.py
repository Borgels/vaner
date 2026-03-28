"""Event collector: watches repo for file changes and git events.

Uses watchdog for inotify-based file watching.
Git events are injected via hook scripts written by `vaner init`.
Events are placed on an asyncio.Queue for the state engine to consume.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from vaner_daemon.config import DaemonConfig

logger = logging.getLogger("vaner.event_collector")


class EventKind(Enum):
    FILE_CHANGED = "file_changed"
    FILE_CREATED = "file_created"
    FILE_DELETED = "file_deleted"
    GIT_COMMIT = "git_commit"
    GIT_CHECKOUT = "git_checkout"       # branch switch or file checkout
    GIT_BRANCH_SWITCH = "git_branch_switch"


@dataclass
class VanerEvent:
    kind: EventKind
    path: str           # repo-relative path, empty for git events
    branch: str = ""    # current branch after event
    from_branch: str = ""  # previous branch (for GIT_BRANCH_SWITCH)
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if self.timestamp == 0.0:
            self.timestamp = time.time()


def _should_ignore(path: str, config: DaemonConfig) -> bool:
    """Return True if this path should be filtered out."""
    p = Path(path)

    # Skip non-files (directories etc. produce events too)
    if p.is_dir():
        return True

    # Check extension
    if p.suffix not in config.watch_extensions:
        return True

    # Check all path components against ignored dirs
    parts = p.parts
    for part in parts:
        if part in config.watch_ignore_dirs:
            return True
        if part.endswith(".egg-info"):
            return True

    return False


class _VanerFileHandler(FileSystemEventHandler):
    """Watchdog event handler that filters and enqueues VanerEvents."""

    def __init__(self, config: DaemonConfig, enqueue_fn) -> None:
        super().__init__()
        self._config = config
        self._enqueue = enqueue_fn

    def _make_relative(self, abs_path: str) -> str:
        try:
            return str(Path(abs_path).relative_to(self._config.repo_path))
        except ValueError:
            return abs_path

    def _check_and_emit(self, abs_path: str, kind: EventKind) -> None:
        if _should_ignore(abs_path, self._config):
            return
        rel = self._make_relative(abs_path)
        event = VanerEvent(kind=kind, path=rel)
        self._enqueue(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._check_and_emit(event.src_path, EventKind.FILE_CHANGED)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._check_and_emit(event.src_path, EventKind.FILE_CREATED)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            # For deleted files, we can't check suffix/dir easily via Path.is_dir
            # so we do a lighter check: only filter by ignored dirs and extension
            p = Path(event.src_path)
            parts = p.parts
            for part in parts:
                if part in self._config.watch_ignore_dirs:
                    return
                if part.endswith(".egg-info"):
                    return
            if p.suffix not in self._config.watch_extensions:
                return
            rel = self._make_relative(event.src_path)
            self._enqueue(VanerEvent(kind=EventKind.FILE_DELETED, path=rel))


class EventCollector:
    """Collects file system and git events and places them on an asyncio.Queue."""

    def __init__(self, config: DaemonConfig, queue: asyncio.Queue) -> None:
        self._config = config
        self._queue = queue
        self._observer: Observer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start the file watcher in a background thread. Call from async context."""
        self._loop = loop
        handler = _VanerFileHandler(self._config, self._enqueue_threadsafe)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._config.repo_path), recursive=True)
        self._observer.start()
        logger.info("EventCollector started watching %s", self._config.repo_path)

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5.0)
            logger.info("EventCollector stopped")

    def _enqueue_threadsafe(self, event: VanerEvent) -> None:
        """Thread-safe: called from watchdog's background thread."""
        if self._loop and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, event)
            except Exception:
                pass  # queue full or loop closing — drop event

    def inject_git_event(self, event: VanerEvent) -> None:
        """Called by git hook scripts via IPC socket or direct queue injection."""
        self._enqueue_threadsafe(event)
