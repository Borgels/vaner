"""Priority job queue with concurrency budget for the preparation engine.

Priorities:
  CRITICAL = 0   (broker-requested immediate refresh)
  HIGH     = 25  (recently active files)
  NORMAL   = 50  (background preparation)
  LOW      = 100 (speculative, low-value)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import IntEnum

_log = logging.getLogger("vaner.job_queue")


class Priority(IntEnum):
    CRITICAL = 0
    HIGH = 25
    NORMAL = 50
    LOW = 100


@dataclass(order=True)
class QueuedJob:
    """A job waiting in the priority queue."""

    priority: int
    job_id: str = field(compare=False)
    workflow_type: str = field(compare=False)
    payload: dict = field(compare=False, default_factory=dict)


class JobQueue:
    """Priority job queue with a concurrency budget.

    Internally uses asyncio.PriorityQueue for ordering and asyncio.Semaphore
    to cap concurrent worker slots.
    """

    def __init__(self, max_concurrent: int = 2, max_depth: int = 20) -> None:
        self._max_concurrent = max_concurrent
        self._max_depth = max_depth
        self._queue: asyncio.PriorityQueue[QueuedJob] = asyncio.PriorityQueue()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active = 0

    async def enqueue(self, job: QueuedJob) -> bool:
        """Add job to queue.

        Returns False (and logs a warning) if the queue is at capacity.
        Returns True on success.
        """
        if self._queue.qsize() >= self._max_depth:
            _log.warning(
                "JobQueue at capacity (%d/%d) — dropping job %s (type=%s, priority=%d)",
                self._queue.qsize(),
                self._max_depth,
                job.job_id,
                job.workflow_type,
                job.priority,
            )
            return False
        await self._queue.put(job)
        return True

    async def acquire(self) -> QueuedJob:
        """Block until a job is available AND a concurrency slot is free.

        Acquires the semaphore (concurrency budget) before returning the job.
        Callers MUST call release() when the job finishes.
        """
        job = await self._queue.get()
        await self._semaphore.acquire()
        self._active += 1
        return job

    def release(self) -> None:
        """Release a concurrency slot. Call when a job completes or fails."""
        self._active = max(0, self._active - 1)
        self._semaphore.release()

    def depth(self) -> int:
        """Current number of jobs waiting in the queue."""
        return self._queue.qsize()

    def active(self) -> int:
        """Number of jobs currently being processed."""
        return self._active

    def stats(self) -> dict:
        """Return a snapshot of queue statistics."""
        return {
            "depth": self.depth(),
            "active": self._active,
            "max_concurrent": self._max_concurrent,
            "max_depth": self._max_depth,
        }
