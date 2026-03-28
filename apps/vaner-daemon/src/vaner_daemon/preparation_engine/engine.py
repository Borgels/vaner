from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path

from vaner_daemon.state_engine import ContextSnapshot, StateEngine

from .graph import build_preparation_graph
from .triggers import Debouncer, PrepTrigger

logger = logging.getLogger("vaner.prep_engine")

_CANCELLED_CTX_TTL = 86400.0  # 24 hours


class PreparationEngine:
    def __init__(
        self,
        repo_root: Path,
        state_engine: StateEngine,
        loop: asyncio.AbstractEventLoop,
        model_name: str = "qwen2.5-coder:32b",
    ):
        self._repo_root = repo_root
        self._state_engine = state_engine
        self._loop = loop
        self._model_name = model_name
        self._debouncer = Debouncer(min_interval_seconds=5.0)
        self._db_path = repo_root / ".vaner" / "preparation.db"
        self._graph = build_preparation_graph(self._db_path)
        self._last_prep_at: float | None = None
        self._jobs_queued: int = 0
        self._active_tasks: dict[str, asyncio.Task] = {}
        from vaner_runtime.telemetry import TelemetryStore
        self._telemetry = TelemetryStore(repo_root / ".vaner" / "telemetry.db")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._ensure_cancelled_table()
        self._cleanup_old_cancellations()
        self._state_engine.on_context_changed(self._on_context_changed)
        self._state_engine.on_context_invalidated(self._on_context_invalidated)
        logger.info("PreparationEngine started (model=%s)", self._model_name)

    def stop(self) -> None:
        for task in list(self._active_tasks.values()):
            task.cancel()
        logger.info("PreparationEngine stopped")

    def get_stats(self) -> dict:
        return {
            "jobs_queued": self._jobs_queued,
            "jobs_running": len(self._active_tasks),
            "last_prep_at": self._last_prep_at,
        }

    # ------------------------------------------------------------------
    # Crash recovery
    # ------------------------------------------------------------------

    def recover_in_progress_runs(self) -> None:
        """Resume any LangGraph workflows that were in-progress before a crash."""
        if not self._db_path.exists():
            return
        try:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # LangGraph SqliteSaver stores checkpoints in 'checkpoints' table
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if "checkpoints" not in tables:
                conn.close()
                return

            # Get latest checkpoint per thread_id
            rows = conn.execute(
                "SELECT thread_id, next FROM checkpoints "
                "WHERE (thread_id, checkpoint_id) IN "
                "(SELECT thread_id, MAX(checkpoint_id) FROM checkpoints GROUP BY thread_id)"
            ).fetchall()
            conn.close()
        except Exception as exc:
            logger.error("Failed to query checkpoints for recovery: %s", exc)
            return

        recovered = 0
        for row in rows:
            thread_id = row["thread_id"]
            next_val = row["next"]
            # Skip completed (next is empty/null) and cancelled contexts
            if not next_val:
                continue
            if self._is_context_cancelled(thread_id):
                logger.info("Skipping recovery of cancelled context_key=%s", thread_id)
                continue
            logger.info("Recovering in-progress run for context_key=%s", thread_id)
            state = {
                "trigger": None,
                "jobs": [],
                "completed": [],
                "errors": [],
                "context_key": thread_id,
                "repo_root": str(self._repo_root),
                "model_name": self._model_name,
            }
            asyncio.run_coroutine_threadsafe(
                self._resume_run(thread_id, state), self._loop
            )
            recovered += 1

        if recovered:
            logger.info("Crash recovery: resumed %d in-progress run(s)", recovered)

    async def _resume_run(self, thread_id: str, state: dict) -> None:
        try:
            await self._graph.ainvoke(
                state,
                config={"configurable": {"thread_id": thread_id}},
            )
            self._last_prep_at = time.time()
            logger.info("Recovery complete for context_key=%s", thread_id)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("Recovery failed for context_key=%s: %s", thread_id, exc)

    # ------------------------------------------------------------------
    # Cancellation persistence
    # ------------------------------------------------------------------

    def _ensure_cancelled_table(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cancelled_contexts "
            "(context_key TEXT PRIMARY KEY, cancelled_at REAL NOT NULL)"
        )
        conn.commit()
        conn.close()

    def _mark_context_cancelled(self, context_key: str) -> None:
        try:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.execute(
                "INSERT OR REPLACE INTO cancelled_contexts (context_key, cancelled_at) VALUES (?, ?)",
                (context_key, time.time()),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.error("Failed to persist cancellation for %s: %s", context_key, exc)

    def _is_context_cancelled(self, context_key: str) -> bool:
        if not self._db_path.exists():
            return False
        try:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            row = conn.execute(
                "SELECT 1 FROM cancelled_contexts WHERE context_key = ?", (context_key,)
            ).fetchone()
            conn.close()
            return row is not None
        except Exception:
            return False

    def _cleanup_old_cancellations(self) -> None:
        if not self._db_path.exists():
            return
        try:
            cutoff = time.time() - _CANCELLED_CTX_TTL
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.execute("DELETE FROM cancelled_contexts WHERE cancelled_at < ?", (cutoff,))
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("Failed to clean up old cancellations: %s", exc)

    # ------------------------------------------------------------------
    # Callbacks (called from StateEngine — may be non-async thread context)
    # ------------------------------------------------------------------

    def _on_context_changed(self, snapshot: ContextSnapshot) -> None:
        trigger = PrepTrigger(
            context_key=snapshot.context_key,
            active_files=snapshot.active_files,
            branch=snapshot.branch,
            reason="file_changed",
        )
        if not self._debouncer.should_trigger(trigger):
            return
        self._jobs_queued += 1
        asyncio.run_coroutine_threadsafe(self._run_preparation(trigger), self._loop)

    def _on_context_invalidated(self, old_branch: str, new_branch: str) -> None:
        for key, task in list(self._active_tasks.items()):
            task.cancel()
            self._mark_context_cancelled(key)
            logger.info("Cancelled prep task %s — branch switch %s→%s", key, old_branch, new_branch)
        self._active_tasks.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run_preparation(self, trigger: PrepTrigger) -> None:
        task_key = trigger.context_key
        current = asyncio.current_task()
        if current:
            self._active_tasks[task_key] = current
        t0 = time.time()
        try:
            state = {
                "trigger": trigger,
                "jobs": [],
                "completed": [],
                "errors": [],
                "context_key": trigger.context_key,
                "repo_root": str(self._repo_root),
                "model_name": self._model_name,
            }
            await self._graph.ainvoke(
                state,
                config={"configurable": {"thread_id": trigger.context_key}},
            )
            elapsed_ms = (time.time() - t0) * 1000
            self._last_prep_at = time.time()
            self._telemetry.record_prep_run(trigger.context_key, elapsed_ms, 0)
            logger.info("Preparation complete for context_key=%s", trigger.context_key)
        except asyncio.CancelledError:
            logger.info("Preparation cancelled for context_key=%s", trigger.context_key)
        except Exception as exc:
            elapsed_ms = (time.time() - t0) * 1000
            self._telemetry.record_prep_run(trigger.context_key, elapsed_ms, 0, error=str(exc))
            logger.error("Preparation failed for context_key=%s: %s", trigger.context_key, exc)
        finally:
            self._active_tasks.pop(task_key, None)
