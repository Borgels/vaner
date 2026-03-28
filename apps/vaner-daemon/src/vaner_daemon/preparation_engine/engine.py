from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from vaner_daemon.state_engine import ContextSnapshot, StateEngine

from .graph import build_preparation_graph
from .triggers import Debouncer, PrepTrigger

logger = logging.getLogger("vaner.prep_engine")


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
        self._graph = build_preparation_graph(repo_root / ".vaner" / "preparation.db")
        self._last_prep_at: float | None = None
        self._jobs_queued: int = 0
        self._active_tasks: dict[str, asyncio.Task] = {}

    def start(self) -> None:
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
            logger.info(
                "Cancelled prep task %s — branch switch %s→%s", key, old_branch, new_branch
            )
        self._active_tasks.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run_preparation(self, trigger: PrepTrigger) -> None:
        task_key = trigger.context_key
        current = asyncio.current_task()
        if current:
            self._active_tasks[task_key] = current
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
            self._last_prep_at = time.time()
            logger.info("Preparation complete for context_key=%s", trigger.context_key)
        except asyncio.CancelledError:
            logger.info("Preparation cancelled for context_key=%s", trigger.context_key)
        except Exception as exc:
            logger.error("Preparation failed for context_key=%s: %s", trigger.context_key, exc)
        finally:
            self._active_tasks.pop(task_key, None)
