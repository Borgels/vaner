"""State engine: maintains the current developer context snapshot.

Consumes events from the EventCollector queue and updates an
in-memory + SQLite-persisted state representing what the developer
is currently working on.

Hot-path read: get_snapshot() must complete in <1ms (in-memory read).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
import subprocess
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

from vaner_daemon.config import DaemonConfig
from vaner_daemon.event_collector import EventKind, VanerEvent

logger = logging.getLogger("vaner.state_engine")

_STATE_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS active_files (
    path TEXT PRIMARY KEY,
    last_touched REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS daemon_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class ContextSnapshot:
    branch: str
    active_files: list[str]   # most recently touched, most recent first
    recent_diff: str          # output of git diff HEAD --stat, may be empty
    context_key: str          # hash of branch + active_files[:5] — for cache scoping
    timestamp: float
    repo_path: str


class StateEngine:
    """Maintains the current developer context snapshot."""

    def __init__(self, config: DaemonConfig, event_queue: asyncio.Queue) -> None:
        self._config = config
        self._event_queue = event_queue
        self._branch: str = ""
        self._active_files: OrderedDict[str, float] = OrderedDict()  # path → last_touched
        self._diff_cache: str = ""
        self._diff_cache_at: float = 0.0
        self._running: bool = False
        self._db: sqlite3.Connection | None = None

        # Callbacks
        self._on_context_changed: list[Callable] = []
        self._on_context_invalidated: list[Callable] = []

    def on_context_changed(self, callback: Callable) -> None:
        """Register callback(snapshot: ContextSnapshot) for context changes."""
        self._on_context_changed.append(callback)

    def on_context_invalidated(self, callback: Callable) -> None:
        """Register callback(old_branch: str, new_branch: str) for branch switches."""
        self._on_context_invalidated.append(callback)

    async def start(self) -> None:
        """Initialize state from SQLite persistence and start event loop."""
        self._db = self._init_db()
        self._branch = await self._get_current_branch()
        self._load_active_files_from_db()
        self._running = True
        asyncio.create_task(self._consume_events())
        logger.info("StateEngine started, branch=%s", self._branch)

    async def stop(self) -> None:
        self._running = False
        if self._db:
            try:
                self._db.close()
            except Exception:
                pass

    def get_snapshot(self) -> ContextSnapshot:
        """Synchronous hot-path read. Returns current snapshot in <1ms."""
        files = list(self._active_files.keys())[:self._config.max_active_files]
        context_key = self._compute_context_key(self._branch, files)
        diff = self._diff_cache if (time.time() - self._diff_cache_at) < self._config.diff_cache_ttl_seconds else ""
        return ContextSnapshot(
            branch=self._branch,
            active_files=files,
            recent_diff=diff,
            context_key=context_key,
            timestamp=time.time(),
            repo_path=str(self._config.repo_path),
        )

    # ------------------------------------------------------------------
    # Internal: DB
    # ------------------------------------------------------------------

    def _init_db(self) -> sqlite3.Connection:
        db_dir = self._config.repo_path / ".vaner"
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / "state.db"
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_STATE_DB_SCHEMA)
        conn.commit()
        return conn

    def _load_active_files_from_db(self) -> None:
        """Load last N active files from DB ordered by last_touched DESC."""
        if self._db is None:
            return
        try:
            rows = self._db.execute(
                "SELECT path, last_touched FROM active_files ORDER BY last_touched DESC LIMIT ?",
                (self._config.max_active_files,),
            ).fetchall()
            # Reverse so we insert oldest first, newest last (OrderedDict preserves insertion order)
            for path, last_touched in reversed(rows):
                self._active_files[path] = last_touched
        except Exception as exc:
            logger.warning("Failed to load active files from DB: %s", exc)

    def _save_active_files_to_db(self) -> None:
        """Upsert all current active files to SQLite."""
        if self._db is None:
            return
        try:
            self._db.execute("DELETE FROM active_files")
            self._db.executemany(
                "INSERT OR REPLACE INTO active_files (path, last_touched) VALUES (?, ?)",
                list(self._active_files.items()),
            )
            self._db.commit()
        except Exception as exc:
            logger.warning("Failed to save active files to DB: %s", exc)

    # ------------------------------------------------------------------
    # Internal: git helpers
    # ------------------------------------------------------------------

    async def _get_current_branch(self) -> str:
        """Run git rev-parse --abbrev-ref HEAD and return the branch name."""
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    ["git", "-C", str(self._config.repo_path), "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                ),
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as exc:
            logger.warning("Failed to get current branch: %s", exc)
        return "unknown"

    def _get_diff_sync(self) -> str:
        """Run git diff HEAD --stat synchronously, return output string."""
        try:
            result = subprocess.run(
                ["git", "-C", str(self._config.repo_path), "diff", "HEAD", "--stat"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as exc:
            logger.warning("Failed to get diff: %s", exc)
        return ""

    async def _refresh_diff_cache(self) -> None:
        """Refresh the diff cache in a thread executor."""
        try:
            diff = await asyncio.get_event_loop().run_in_executor(None, self._get_diff_sync)
            self._diff_cache = diff
            self._diff_cache_at = time.time()
        except Exception as exc:
            logger.warning("Failed to refresh diff cache: %s", exc)

    # ------------------------------------------------------------------
    # Internal: context key
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_context_key(branch: str, files: list[str]) -> str:
        raw = branch + "|".join(files[:5])
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    # ------------------------------------------------------------------
    # Internal: event loop
    # ------------------------------------------------------------------

    async def _consume_events(self) -> None:
        """Async loop reading events from queue and dispatching them."""
        while self._running:
            try:
                event: VanerEvent = await asyncio.wait_for(
                    self._event_queue.get(), timeout=1.0
                )
                try:
                    await self._handle_event(event)
                except Exception as exc:
                    logger.exception("Error handling event %s: %s", event, exc)
                finally:
                    self._event_queue.task_done()
            except asyncio.TimeoutError:
                continue
            except Exception as exc:
                logger.exception("Unexpected error in event consumer: %s", exc)

    async def _handle_event(self, event: VanerEvent) -> None:
        """Update state based on event kind, call callbacks, persist."""
        if event.kind in (EventKind.FILE_CHANGED, EventKind.FILE_CREATED):
            now = time.time()
            # Move to front (most recently touched) — remove and reinsert
            if event.path in self._active_files:
                del self._active_files[event.path]
            self._active_files[event.path] = now

            # Trim to max
            while len(self._active_files) > self._config.max_active_files:
                self._active_files.popitem(last=False)  # remove oldest

            self._save_active_files_to_db()
            snapshot = self.get_snapshot()
            await self._fire_context_changed(snapshot)

        elif event.kind == EventKind.FILE_DELETED:
            if event.path in self._active_files:
                del self._active_files[event.path]
                self._save_active_files_to_db()

        elif event.kind == EventKind.GIT_COMMIT:
            await self._refresh_diff_cache()
            snapshot = self.get_snapshot()
            await self._fire_context_changed(snapshot)

        elif event.kind in (EventKind.GIT_BRANCH_SWITCH, EventKind.GIT_CHECKOUT):
            old_branch = self._branch
            new_branch = event.branch if event.branch else await self._get_current_branch()

            if new_branch != old_branch:
                self._branch = new_branch
                self._active_files.clear()
                self._diff_cache = ""
                self._diff_cache_at = 0.0
                self._save_active_files_to_db()

                await self._fire_context_invalidated(old_branch, new_branch)
                snapshot = self.get_snapshot()
                await self._fire_context_changed(snapshot)

    async def _fire_context_changed(self, snapshot: ContextSnapshot) -> None:
        for cb in self._on_context_changed:
            try:
                result = cb(snapshot)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.warning("on_context_changed callback error: %s", exc)

    async def _fire_context_invalidated(self, old_branch: str, new_branch: str) -> None:
        for cb in self._on_context_invalidated:
            try:
                result = cb(old_branch, new_branch)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.warning("on_context_invalidated callback error: %s", exc)
