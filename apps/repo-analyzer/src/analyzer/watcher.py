"""File watcher for auto-refresh of stale artefacts.

Watches the Vaner repo root for file changes and marks affected artefacts
stale by deleting them from the SQLite cache.  A 10-second debounce prevents
thrashing during rapid saves.

Usage (via vaner.py --watch):
    python vaner.py --watch
    python vaner.py --watch "your question"   # watch + answer
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import urllib.parse
from pathlib import Path

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    _WATCHDOG_AVAILABLE = True
except ImportError:  # pragma: no cover
    _WATCHDOG_AVAILABLE = False

from vaner_tools.paths import CACHE_DIR, REPO_ROOT

logger = logging.getLogger(__name__)

# Directories to ignore during watching
IGNORE_DIRS: frozenset[str] = frozenset({
    ".git",
    ".vaner",
    "__pycache__",
    ".venv",
    "node_modules",
    ".ruff_cache",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
})

# File suffixes to ignore
IGNORE_SUFFIXES: frozenset[str] = frozenset({".pyc", ".pyo", ".pyd", ".so", ".db", ".db-wal", ".db-shm"})

# Debounce window in seconds
DEBOUNCE_SECONDS = 10.0


def _make_key(kind: str, source_path: str) -> str:
    return f"{kind}:{urllib.parse.quote(source_path, safe='')}"


def _delete_artefacts_for_path(rel_path: str) -> None:
    """Remove all artefacts whose source_path matches *rel_path* from SQLite."""
    db_path = CACHE_DIR / "artefacts.db"
    if not db_path.exists():
        return
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        deleted = conn.execute(
            "DELETE FROM artefacts WHERE source_path = ? OR source_path LIKE ?",
            (rel_path, rel_path + "/%"),
        ).rowcount
        conn.commit()
        conn.close()
        if deleted:
            logger.info("Watcher: invalidated %d artefact(s) for %s", deleted, rel_path)
        else:
            logger.debug("Watcher: no cached artefacts found for %s", rel_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Watcher: failed to delete artefacts for %s: %s", rel_path, exc)


def _is_ignored(path: Path) -> bool:
    """Return True if *path* should be ignored by the watcher."""
    parts = path.parts
    if any(part in IGNORE_DIRS for part in parts):
        return True
    if path.suffix in IGNORE_SUFFIXES:
        return True
    return False


class _DebounceHandler(FileSystemEventHandler if _WATCHDOG_AVAILABLE else object):  # type: ignore[misc]
    """Watchdog handler with per-file debounce."""

    def __init__(self, repo_root: Path, debounce: float = DEBOUNCE_SECONDS) -> None:
        if _WATCHDOG_AVAILABLE:
            super().__init__()
        self._repo_root = repo_root
        self._debounce = debounce
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # watchdog callbacks
    # ------------------------------------------------------------------

    def on_modified(self, event: FileSystemEvent) -> None:  # type: ignore[override]
        if not event.is_directory:
            self._schedule(event.src_path)

    def on_created(self, event: FileSystemEvent) -> None:  # type: ignore[override]
        if not event.is_directory:
            self._schedule(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:  # type: ignore[override]
        # Both old and new locations may have cached artefacts
        self._schedule(event.src_path)
        if hasattr(event, "dest_path") and event.dest_path:
            self._schedule(event.dest_path)

    def on_deleted(self, event: FileSystemEvent) -> None:  # type: ignore[override]
        if not event.is_directory:
            self._schedule(event.src_path)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _schedule(self, abs_path_str: str) -> None:
        abs_path = Path(abs_path_str)
        if _is_ignored(abs_path):
            return
        try:
            rel = str(abs_path.relative_to(self._repo_root))
        except ValueError:
            return  # outside repo root

        with self._lock:
            # Cancel any existing timer for this path and restart it
            existing = self._timers.pop(rel, None)
            if existing is not None:
                existing.cancel()
            timer = threading.Timer(self._debounce, self._flush, args=(rel,))
            timer.daemon = True
            timer.start()
            self._timers[rel] = timer

    def _flush(self, rel_path: str) -> None:
        with self._lock:
            self._timers.pop(rel_path, None)
        logger.debug("Watcher: flushing stale artefacts for %s", rel_path)
        _delete_artefacts_for_path(rel_path)


class VanerWatcher:
    """High-level watcher that manages a watchdog Observer lifecycle."""

    def __init__(
        self,
        watch_path: Path | None = None,
        debounce: float = DEBOUNCE_SECONDS,
    ) -> None:
        if not _WATCHDOG_AVAILABLE:
            raise ImportError(
                "watchdog is not installed. Run: pip install watchdog"
            )
        self._watch_path = (watch_path or REPO_ROOT).expanduser().resolve()
        self._debounce = debounce
        self._observer: Observer | None = None

    def start(self) -> None:
        """Start the background observer thread."""
        if self._observer is not None and self._observer.is_alive():
            logger.warning("Watcher already running")
            return

        handler = _DebounceHandler(self._watch_path, self._debounce)
        observer = Observer()
        observer.schedule(handler, str(self._watch_path), recursive=True)
        observer.daemon = True
        observer.start()
        self._observer = observer
        logger.info(
            "Vaner file watcher started — watching %s (debounce %.0fs)",
            self._watch_path,
            self._debounce,
        )

    def stop(self) -> None:
        """Stop the observer thread gracefully."""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
            logger.info("Vaner file watcher stopped")

    def is_alive(self) -> bool:
        return self._observer is not None and self._observer.is_alive()


def start_watcher(
    watch_path: Path | None = None,
    debounce: float = DEBOUNCE_SECONDS,
) -> VanerWatcher:
    """Convenience function: create, start and return a VanerWatcher.

    The caller is responsible for calling ``watcher.stop()`` on shutdown.
    The observer thread is daemonised so it will not block process exit.
    """
    watcher = VanerWatcher(watch_path=watch_path, debounce=debounce)
    watcher.start()
    return watcher
