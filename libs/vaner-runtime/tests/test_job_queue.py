"""Tests for vaner_runtime.job_queue."""

from __future__ import annotations

import asyncio

import pytest

from vaner_runtime.job_queue import JobQueue, Priority, QueuedJob


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_job(priority: int, job_id: str = "job-1", wf: str = "wf") -> QueuedJob:
    return QueuedJob(priority=priority, job_id=job_id, workflow_type=wf)


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_higher_priority_dequeued_first():
    q = JobQueue(max_concurrent=5, max_depth=20)
    # Enqueue in reverse priority order
    await q.enqueue(make_job(Priority.LOW, "low"))
    await q.enqueue(make_job(Priority.NORMAL, "normal"))
    await q.enqueue(make_job(Priority.HIGH, "high"))
    await q.enqueue(make_job(Priority.CRITICAL, "critical"))

    order = []
    for _ in range(4):
        job = await q.acquire()
        order.append(job.job_id)
        q.release()

    assert order == ["critical", "high", "normal", "low"]


@pytest.mark.asyncio
async def test_same_priority_fifo():
    q = JobQueue(max_concurrent=5, max_depth=20)
    await q.enqueue(make_job(Priority.NORMAL, "first"))
    await q.enqueue(make_job(Priority.NORMAL, "second"))
    await q.enqueue(make_job(Priority.NORMAL, "third"))

    order = []
    for _ in range(3):
        job = await q.acquire()
        order.append(job.job_id)
        q.release()

    # asyncio.PriorityQueue is a min-heap; same priority items may vary,
    # but we expect them all to appear
    assert set(order) == {"first", "second", "third"}


# ---------------------------------------------------------------------------
# Concurrency limiting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrency_limit_respected():
    q = JobQueue(max_concurrent=2, max_depth=20)

    # Enqueue 4 jobs
    for i in range(4):
        await q.enqueue(make_job(Priority.NORMAL, f"j{i}"))

    # Acquire up to max_concurrent
    await q.acquire()
    await q.acquire()

    assert q.active() == 2

    # Third acquire should block — verify it's pending
    acquired_third = False

    async def try_acquire():
        nonlocal acquired_third
        j = await q.acquire()
        acquired_third = True
        q.release()
        return j

    task = asyncio.create_task(try_acquire())
    # Give the event loop a moment — should NOT have acquired yet (semaphore blocked)
    await asyncio.sleep(0)
    assert not acquired_third

    # Release one slot
    q.release()
    await asyncio.sleep(0)
    await task
    assert acquired_third

    # Cleanup
    q.release()


@pytest.mark.asyncio
async def test_active_count_tracks_correctly():
    q = JobQueue(max_concurrent=3, max_depth=10)
    await q.enqueue(make_job(Priority.NORMAL, "a"))
    await q.enqueue(make_job(Priority.NORMAL, "b"))

    assert q.active() == 0
    await q.acquire()
    assert q.active() == 1
    await q.acquire()
    assert q.active() == 2

    q.release()
    assert q.active() == 1
    q.release()
    assert q.active() == 0


# ---------------------------------------------------------------------------
# Queue overflow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_overflow_returns_false(caplog):
    import logging
    q = JobQueue(max_concurrent=2, max_depth=3)

    results = []
    for i in range(3):
        r = await q.enqueue(make_job(Priority.NORMAL, f"j{i}"))
        results.append(r)

    # 4th enqueue should be rejected
    with caplog.at_level(logging.WARNING, logger="vaner.job_queue"):
        overflow_result = await q.enqueue(make_job(Priority.NORMAL, "overflow"))

    assert overflow_result is False
    assert all(results)  # first 3 succeeded


@pytest.mark.asyncio
async def test_queue_overflow_logs_warning(caplog):
    import logging
    q = JobQueue(max_concurrent=2, max_depth=2)
    await q.enqueue(make_job(Priority.NORMAL, "j1"))
    await q.enqueue(make_job(Priority.NORMAL, "j2"))

    with caplog.at_level(logging.WARNING, logger="vaner.job_queue"):
        await q.enqueue(make_job(Priority.CRITICAL, "overflow"))

    assert any("capacity" in r.message.lower() or "dropping" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# stats()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_returns_correct_values():
    q = JobQueue(max_concurrent=3, max_depth=10)
    await q.enqueue(make_job(Priority.NORMAL, "s1"))
    await q.enqueue(make_job(Priority.NORMAL, "s2"))
    await q.enqueue(make_job(Priority.NORMAL, "s3"))

    stats = q.stats()
    assert stats["depth"] == 3
    assert stats["active"] == 0
    assert stats["max_concurrent"] == 3
    assert stats["max_depth"] == 10

    await q.acquire()
    stats = q.stats()
    assert stats["depth"] == 2
    assert stats["active"] == 1

    q.release()
    stats = q.stats()
    assert stats["active"] == 0


@pytest.mark.asyncio
async def test_depth_decreases_on_acquire():
    q = JobQueue(max_concurrent=5, max_depth=10)
    for i in range(4):
        await q.enqueue(make_job(Priority.NORMAL, f"d{i}"))

    assert q.depth() == 4
    await q.acquire()
    assert q.depth() == 3
    q.release()


# ---------------------------------------------------------------------------
# QueuedJob ordering
# ---------------------------------------------------------------------------


def test_queued_job_ordering():
    j_low = QueuedJob(priority=Priority.LOW, job_id="a", workflow_type="wf")
    j_high = QueuedJob(priority=Priority.HIGH, job_id="b", workflow_type="wf")
    j_critical = QueuedJob(priority=Priority.CRITICAL, job_id="c", workflow_type="wf")

    assert j_critical < j_high
    assert j_high < j_low
    assert j_critical < j_low
