# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio

import pytest

from vaner.daemon.live_work import read_live_work_events
from vaner.daemon.precompute_worker import (
    PrecomputeWorker,
    WorkerJob,
    prediction_snapshot_path,
    read_worker_control,
    read_worker_wake,
    request_precompute_wake,
    request_worker_control,
)
from vaner.intent.prediction_registry import PredictionEvent


@pytest.mark.asyncio
async def test_high_priority_enqueue_replaces_normal_pending_job(tmp_path):
    worker = PrecomputeWorker(tmp_path, queue_size=2)

    assert await worker.enqueue("periodic-a", priority=5) is True
    assert await worker.enqueue("periodic-b", priority=5) is True
    assert await worker.enqueue("intent_signal", priority=0) is True

    pending_reasons = {item[1].reason for item in worker._queue._queue}
    assert "intent_signal" in pending_reasons
    assert len(pending_reasons) == 2
    assert worker._status["queue"]["dropped"] == 1


def test_request_precompute_wake_writes_runtime_signal(tmp_path):
    wake = request_precompute_wake(tmp_path, reason="intent_signal")

    stored = read_worker_wake(tmp_path)
    assert stored is not None
    assert stored["id"] == wake["id"]
    assert stored["reason"] == "intent_signal"


def test_request_worker_control_writes_runtime_signal(tmp_path):
    control = request_worker_control(
        tmp_path,
        action="pause_all",
        reason="user paused Vaner",
        drain_queue=True,
    )

    stored = read_worker_control(tmp_path)
    assert stored is not None
    assert stored["id"] == control["id"]
    assert stored["action"] == "pause_all"
    assert stored["reason"] == "user paused Vaner"
    assert stored["drain_queue"] is True


@pytest.mark.asyncio
async def test_high_priority_enqueue_preempts_lower_priority_current_job(tmp_path):
    worker = PrecomputeWorker(tmp_path, queue_size=2)
    current_task = asyncio.create_task(asyncio.sleep(60))
    worker._current = WorkerJob(id="running", reason="periodic", priority=5)
    worker._current_task = current_task

    try:
        assert await worker.enqueue("intent_signal", priority=0) is True
        await asyncio.sleep(0)
        assert current_task.cancelled()
    finally:
        current_task.cancel()


@pytest.mark.asyncio
async def test_worker_control_cancels_active_job_and_drains_queue(tmp_path):
    worker = PrecomputeWorker(tmp_path, queue_size=2)
    current_task = asyncio.create_task(asyncio.sleep(60))
    worker._current = WorkerJob(id="running", reason="periodic", priority=5)
    worker._current_task = current_task
    assert await worker.enqueue("pending", priority=5) is True

    try:
        worker._handle_worker_control(
            {
                "action": "pause_all",
                "reason": "all workspaces paused by user",
                "drain_queue": True,
            }
        )
        await asyncio.sleep(0)
        assert current_task.cancelled()
        assert worker._queue.qsize() == 0
        assert worker._status["worker"]["state"] == "cancelled"
        assert worker._status["worker"]["explanation"] == "all workspaces paused by user"
    finally:
        current_task.cancel()


@pytest.mark.asyncio
async def test_scheduler_gate_skips_enqueue_when_focus_paused(tmp_path):
    worker = PrecomputeWorker(tmp_path, queue_size=2)

    class Gate:
        status = "deferred"
        defer_reason = "global_paused"
        explanation = "Vaner is paused."

    class Focus:
        def proactive_gate(self, *, job_class: str):
            assert job_class == "prepared_work"
            return Gate()

    enqueued = await worker._enqueue_if_allowed("periodic", priority=5, focus_manager=Focus())  # type: ignore[arg-type]

    assert enqueued is False
    assert worker._queue.qsize() == 0
    assert worker._status["worker"]["state"] == "deferred"
    assert worker._status["worker"]["defer_reason"] == "global_paused"


def test_empty_worker_cycle_preserves_previous_prediction_snapshot(tmp_path):
    path = prediction_snapshot_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"predictions":[{"id":"ready-1"}],"by_state":{"ready":[{"id":"ready-1"}]},"cycle_id":"old"}\n',
        encoding="utf-8",
    )
    worker = PrecomputeWorker(tmp_path, queue_size=2)

    class EmptyEngine:
        prediction_registry = None

    worker._write_predictions(EmptyEngine(), cycle_id="new")

    stored = path.read_text(encoding="utf-8")
    assert '"ready-1"' in stored
    assert '"new"' not in stored


def test_worker_state_writes_live_worker_event(tmp_path):
    worker = PrecomputeWorker(tmp_path, queue_size=2)
    job = WorkerJob(id="job-one", reason="intent_signal", priority=0)

    worker._set_state(
        "running",
        job=job,
        phase="prediction_precompute",
        cycle_id="cycle-one",
        explanation="Exploring likely next work.",
    )

    rows = read_live_work_events(tmp_path, entity_type="worker", entity_id="job-one")
    assert len(rows) == 1
    assert rows[0]["stage"] == "prediction_precompute"
    assert rows[0]["status"] == "running"
    assert rows[0]["summary"] == "Exploring likely next work."
    assert rows[0]["metadata"]["reason"] == "intent_signal"


def test_prediction_event_writes_live_prediction_event(tmp_path):
    worker = PrecomputeWorker(tmp_path, queue_size=2)
    worker._last_cycle_id = "cycle-one"

    worker._record_prediction_event(
        PredictionEvent(
            kind="prediction.readiness_changed",
            prediction_id="pred-one",
            payload={"from_state": "queued", "to_state": "drafting", "reason": "evidence ready"},
            ts=123.0,
        )
    )

    rows = read_live_work_events(tmp_path, entity_type="prediction", entity_id="pred-one")
    assert len(rows) == 1
    assert rows[0]["stage"] == "drafting"
    assert rows[0]["status"] == "drafting"
    assert rows[0]["cycle_id"] == "cycle-one"
    assert rows[0]["summary"] == "Readiness changed queued -> drafting: evidence ready"
