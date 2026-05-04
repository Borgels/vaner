# SPDX-License-Identifier: Apache-2.0
"""Long-lived precompute worker for Vaner's live preparation loop."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from vaner.cli.commands.config import load_config
from vaner.daemon.live_work import append_live_work_event
from vaner.focus import FocusManager
from vaner.intent.governor import PredictionGovernor

logger = logging.getLogger(__name__)

WORKER_STATUS_FILENAME = "precompute_worker.json"
PREDICTION_SNAPSHOT_FILENAME = "predictions_active.json"
WORKER_WAKE_FILENAME = "precompute_wake.json"
WORKER_CONTROL_FILENAME = "precompute_control.json"

WorkerState = Literal["starting", "idle", "queued", "running", "deferred", "completed", "failed", "cancelled", "stopping"]


def runtime_path(repo_root: Path, filename: str) -> Path:
    return repo_root / ".vaner" / "runtime" / filename


def worker_status_path(repo_root: Path) -> Path:
    return runtime_path(repo_root, WORKER_STATUS_FILENAME)


def prediction_snapshot_path(repo_root: Path) -> Path:
    return runtime_path(repo_root, PREDICTION_SNAPSHOT_FILENAME)


def worker_wake_path(repo_root: Path) -> Path:
    return runtime_path(repo_root, WORKER_WAKE_FILENAME)


def worker_control_path(repo_root: Path) -> Path:
    return runtime_path(repo_root, WORKER_CONTROL_FILENAME)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            # os.replace already moved the temp file into place.
            pass


def read_worker_status(repo_root: Path) -> dict[str, Any] | None:
    path = worker_status_path(repo_root)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_prediction_snapshot(repo_root: Path) -> dict[str, Any] | None:
    path = prediction_snapshot_path(repo_root)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_worker_wake(repo_root: Path) -> dict[str, Any] | None:
    path = worker_wake_path(repo_root)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_worker_control(repo_root: Path) -> dict[str, Any] | None:
    path = worker_control_path(repo_root)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def request_precompute_wake(repo_root: Path, *, reason: str) -> dict[str, Any]:
    payload = {
        "id": f"wake-{uuid.uuid4().hex[:12]}",
        "reason": reason,
        "created_at": time.time(),
    }
    _atomic_write_json(worker_wake_path(repo_root), payload)
    return payload


def request_worker_control(
    repo_root: Path,
    *,
    action: str,
    reason: str,
    job_id: str | None = None,
    drain_queue: bool = False,
) -> dict[str, Any]:
    payload = {
        "id": f"ctrl-{uuid.uuid4().hex[:12]}",
        "action": str(action),
        "reason": str(reason),
        "job_id": job_id,
        "drain_queue": bool(drain_queue),
        "created_at": time.time(),
    }
    _atomic_write_json(worker_control_path(repo_root), payload)
    return payload


@dataclass(slots=True)
class WorkerJob:
    id: str
    reason: str
    queued_at: float = field(default_factory=time.time)
    priority: int = 5
    sequence: int = 0

    def queue_key(self) -> tuple[int, int, float]:
        return (self.priority, self.sequence, self.queued_at)


class PrecomputeWorker:
    """Bounded, long-lived worker that owns expensive preparation cycles.

    The HTTP daemon remains a control plane. This process owns the live
    ``VanerEngine`` and writes small JSON snapshots for the cockpit, keeping
    mutable engine state out of the web server process.
    """

    def __init__(
        self,
        repo_root: Path,
        *,
        interval_seconds: float = 45.0,
        startup_delay_seconds: float = 10.0,
        queue_size: int = 2,
        budget_units: int = 15,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.interval_seconds = max(5.0, float(interval_seconds))
        self.startup_delay_seconds = max(0.0, float(startup_delay_seconds))
        self.budget_units = max(1, int(budget_units))
        self._queue: asyncio.PriorityQueue[tuple[tuple[int, int, float], WorkerJob]] = asyncio.PriorityQueue(
            maxsize=max(1, int(queue_size))
        )
        self._stopping = asyncio.Event()
        self._current: WorkerJob | None = None
        self._current_task: asyncio.Task[None] | None = None
        self._last_cycle_id: str | None = None
        self._last_control_id = ""
        self._job_sequence = 0
        self._status: dict[str, Any] = {
            "worker": {
                "state": "starting",
                "pid": os.getpid(),
                "started_at": time.time(),
                "last_heartbeat_at": time.time(),
            },
            "jobs": [],
            "queue": {"size": 0, "max_size": self._queue.maxsize, "dropped": 0, "coalesced": 0},
            "profile": {},
        }

    async def run_forever(self) -> None:
        self._cap_native_threads()
        self._set_state("starting", explanation="waiting before first worker initialization")
        if self.startup_delay_seconds:
            await asyncio.sleep(self.startup_delay_seconds)
        config = load_config(self.repo_root)
        concurrency_cap = max(1, int(os.environ.get("VANER_WORKER_EXPLORATION_CONCURRENCY", "1")))
        config.compute.exploration_concurrency = min(config.compute.exploration_concurrency, concurrency_cap)
        worker_llm = os.environ.get("VANER_WORKER_ENABLE_LLM", "").strip().lower()
        if worker_llm in {"0", "false", "no", "off"}:
            config.exploration.llm_gate = "none"
        focus_manager = FocusManager(config)
        from vaner.engine import build_default_engine

        engine = build_default_engine(self.repo_root, config)
        await engine.initialize()
        if hasattr(engine, "set_prediction_event_listener"):
            engine.set_prediction_event_listener(self._record_prediction_event)
        if hasattr(engine, "set_live_work_event_listener"):
            engine.set_live_work_event_listener(self._record_live_work_event)
        self._set_state("idle", explanation="worker initialized")
        scheduler = asyncio.create_task(self._schedule_periodic(focus_manager))
        controller = asyncio.create_task(self._watch_control())
        try:
            while not self._stopping.is_set():
                _key, job = await self._queue.get()
                self._current = job
                self._current_task = asyncio.create_task(self._run_job(job, engine=engine, focus_manager=focus_manager))
                await self._current_task
                self._current_task = None
                self._current = None
                self._queue.task_done()
        finally:
            scheduler.cancel()
            controller.cancel()
            self._set_state("stopping", explanation="worker stopping")

    async def _schedule_periodic(self, focus_manager: FocusManager) -> None:
        await self._enqueue_if_allowed("startup", priority=1, focus_manager=focus_manager)
        next_periodic_at = time.monotonic() + self.interval_seconds
        last_wake_id = ""
        while not self._stopping.is_set():
            await asyncio.sleep(1.0)
            wake = read_worker_wake(self.repo_root)
            if wake is not None:
                wake_id = str(wake.get("id") or wake.get("created_at") or "")
                if wake_id and wake_id != last_wake_id:
                    last_wake_id = wake_id
                    await self._enqueue_if_allowed(str(wake.get("reason") or "signal"), priority=0, focus_manager=focus_manager)
            if time.monotonic() >= next_periodic_at:
                await self._enqueue_if_allowed("periodic", priority=5, focus_manager=focus_manager)
                next_periodic_at = time.monotonic() + self.interval_seconds

    async def _enqueue_if_allowed(self, reason: str, *, priority: int, focus_manager: FocusManager) -> bool:
        gate = focus_manager.proactive_gate(job_class="prepared_work")
        if gate.status == "deferred":
            self._set_state("deferred", phase="idle", defer_reason=gate.defer_reason, explanation=gate.explanation)
            return False
        return await self.enqueue(reason, priority=priority)

    async def _watch_control(self) -> None:
        while not self._stopping.is_set():
            await asyncio.sleep(0.5)
            payload = read_worker_control(self.repo_root)
            if not payload:
                continue
            control_id = str(payload.get("id") or payload.get("created_at") or "")
            if not control_id or control_id == self._last_control_id:
                continue
            self._last_control_id = control_id
            self._handle_worker_control(payload)

    def _handle_worker_control(self, payload: dict[str, Any]) -> None:
        action = str(payload.get("action") or "")
        if action not in {"cancel_active", "pause", "pause_all"}:
            return
        reason = str(payload.get("reason") or "worker control requested cancellation")
        requested_job_id = str(payload.get("job_id") or "")
        current = self._current
        should_cancel_current = (
            current is not None
            and self._current_task is not None
            and not self._current_task.done()
            and (not requested_job_id or requested_job_id == current.id)
        )
        if bool(payload.get("drain_queue")):
            self._drain_queue()
        if should_cancel_current:
            self._current_task.cancel()
            self._set_state(
                "cancelled",
                job=current,
                phase="idle",
                cycle_id=self._last_cycle_id,
                explanation=reason,
                produced=0,
            )
        elif bool(payload.get("drain_queue")):
            self._set_state("idle", phase="idle", explanation=reason)

    def _drain_queue(self) -> int:
        drained = 0
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            drained += 1
            try:
                self._queue.task_done()
            except ValueError:
                logger.debug("queue task accounting was already balanced during drain", exc_info=True)
        self._status["queue"]["size"] = self._queue.qsize()
        return drained

    async def enqueue(self, reason: str, *, priority: int = 5) -> bool:
        if self._queue.full():
            if self._make_room_for(priority):
                self._status["queue"]["dropped"] = int(self._status["queue"].get("dropped") or 0) + 1
            else:
                self._status["queue"]["coalesced"] = int(self._status["queue"].get("coalesced") or 0) + 1
                self._write_status()
                return False
        self._job_sequence += 1
        job = WorkerJob(
            id=f"precompute-{uuid.uuid4().hex[:10]}",
            reason=reason,
            priority=max(0, int(priority)),
            sequence=self._job_sequence,
        )
        await self._queue.put((job.queue_key(), job))
        self._status["queue"]["size"] = self._queue.qsize()
        if self._current is None:
            self._set_state("queued", explanation=f"queued {reason} precompute")
        else:
            if job.priority < self._current.priority and self._current_task is not None and not self._current_task.done():
                self._current_task.cancel()
            self._write_status()
        return True

    def _make_room_for(self, priority: int) -> bool:
        pending = list(getattr(self._queue, "_queue", []))
        if not pending:
            return True
        worst_index = max(range(len(pending)), key=lambda idx: pending[idx][1].queue_key())
        worst_job = pending[worst_index][1]
        if worst_job.priority <= priority:
            return False
        del pending[worst_index]
        self._queue._queue.clear()  # type: ignore[attr-defined]
        self._queue._queue.extend(pending)  # type: ignore[attr-defined]
        import heapq

        heapq.heapify(self._queue._queue)  # type: ignore[attr-defined]
        return True

    async def _run_job(self, job: WorkerJob, *, engine: Any, focus_manager: FocusManager) -> None:
        gate = focus_manager.proactive_gate(job_class="prepared_work")
        if gate.status == "deferred":
            self._set_state("deferred", job=job, defer_reason=gate.defer_reason, explanation=gate.explanation)
            return
        cycle_id = uuid.uuid4().hex
        self._last_cycle_id = cycle_id
        started = time.monotonic()
        profile: dict[str, float] = {"queue_wait_ms": max(0.0, (time.time() - job.queued_at) * 1000.0)}
        self._set_state("running", job=job, phase="source_refresh", cycle_id=cycle_id)
        try:
            source_start = time.monotonic()
            try:
                from vaner.intent.source_refresh import refresh_intent_artefacts_from_sources

                await refresh_intent_artefacts_from_sources(engine.config, engine.store)
            except Exception:
                logger.exception("precompute worker source refresh failed")
            profile["source_refresh_ms"] = (time.monotonic() - source_start) * 1000.0

            self._set_state("running", job=job, phase="prediction_precompute", cycle_id=cycle_id)
            precompute_start = time.monotonic()
            governor = PredictionGovernor(mode=PredictionGovernor.Mode.BUDGET, budget_units=self.budget_units)
            produced = await engine.precompute_cycle(
                governor=governor,
                registry_ready_callback=lambda current_engine: self._write_predictions(current_engine, cycle_id=cycle_id),
            )
            profile["precompute_ms"] = (time.monotonic() - precompute_start) * 1000.0
            profile["total_ms"] = (time.monotonic() - started) * 1000.0
            profile["produced"] = float(produced or 0)
            self._write_predictions(engine, cycle_id=cycle_id)
            self._status["profile"] = profile
            self._set_state("completed", job=job, phase="idle", cycle_id=cycle_id, produced=int(produced or 0))
        except asyncio.CancelledError:
            profile["total_ms"] = (time.monotonic() - started) * 1000.0
            profile["preempted"] = 1.0
            self._status["profile"] = profile
            try:
                self._write_predictions(engine, cycle_id=cycle_id)
            except Exception:
                logger.debug("preempted worker snapshot write failed", exc_info=True)
            self._set_state(
                "cancelled",
                job=job,
                phase="idle",
                cycle_id=cycle_id,
                explanation="preempted by fresher higher-priority work",
                produced=0,
            )
        except Exception as exc:
            profile["total_ms"] = (time.monotonic() - started) * 1000.0
            self._status["profile"] = profile
            logger.exception("precompute worker job failed")
            self._set_state("failed", job=job, phase="idle", cycle_id=cycle_id, explanation=str(exc))

    def _write_predictions(self, engine: Any, *, cycle_id: str) -> None:
        from vaner.intent.prediction_serialization import serialize_prediction_nested

        predictions = []
        by_state: dict[str, list[dict[str, Any]]] = {}
        registry = getattr(engine, "prediction_registry", None)
        if registry is not None:
            for prompt in registry.all():
                item = serialize_prediction_nested(prompt)
                state = str(item.get("readiness") or item.get("run", {}).get("readiness") or "unknown")
                by_state.setdefault(state, []).append(item)
                if state == "ready":
                    predictions.append(item)
        if not predictions and not any(by_state.values()):
            existing = read_prediction_snapshot(self.repo_root)
            if _snapshot_has_prediction_rows(existing):
                logger.info("precompute worker preserving previous prediction snapshot; current cycle produced no rows")
                return
        _atomic_write_json(
            prediction_snapshot_path(self.repo_root),
            {
                "predictions": predictions,
                "by_state": by_state,
                "cycle_id": cycle_id,
                "generated_at": time.time(),
                "source": "precompute_worker",
            },
        )

    def _set_state(
        self,
        state: WorkerState,
        *,
        job: WorkerJob | None = None,
        phase: str | None = None,
        cycle_id: str | None = None,
        defer_reason: str | None = None,
        explanation: str | None = None,
        produced: int | None = None,
    ) -> None:
        now = time.time()
        self._status["worker"].update(
            {
                "state": state,
                "phase": phase or self._status["worker"].get("phase") or "idle",
                "pid": os.getpid(),
                "last_heartbeat_at": now,
                "cycle_id": cycle_id or self._last_cycle_id,
                "defer_reason": defer_reason,
                "explanation": explanation,
                "produced": produced,
            }
        )
        jobs: list[dict[str, Any]] = []
        active = job or self._current
        if active is not None:
            jobs.append(
                {
                    "id": active.id,
                    "job_class": "prepared_work",
                    "status": state,
                    "priority": "high" if active.priority <= 1 else "normal",
                    "priority_value": active.priority,
                    "reason": active.reason,
                    "queued_at": active.queued_at,
                    "started_at": now if state == "running" else None,
                    "defer_reason": defer_reason,
                    "explanation": explanation,
                    "cycle_id": cycle_id or self._last_cycle_id,
                }
            )
        self._status["jobs"] = jobs
        self._status["queue"]["size"] = self._queue.qsize()
        self._record_worker_event(
            state=state,
            job=active,
            phase=str(self._status["worker"].get("phase") or phase or "idle"),
            cycle_id=cycle_id or self._last_cycle_id,
            defer_reason=defer_reason,
            explanation=explanation,
            produced=produced,
        )
        self._write_status()

    def _record_worker_event(
        self,
        *,
        state: WorkerState,
        job: WorkerJob | None,
        phase: str,
        cycle_id: str | None,
        defer_reason: str | None,
        explanation: str | None,
        produced: int | None,
    ) -> None:
        try:
            append_live_work_event(
                self.repo_root,
                entity_type="worker",
                entity_id=job.id if job is not None else "precompute-worker",
                job_id=job.id if job is not None else None,
                cycle_id=cycle_id,
                stage=phase,
                status=state,
                summary=explanation or f"Precompute worker {state}",
                metadata={
                    "reason": job.reason if job is not None else "",
                    "priority": job.priority if job is not None else None,
                    "defer_reason": defer_reason,
                    "produced": produced,
                },
            )
        except Exception:
            logger.debug("failed to write worker live event", exc_info=True)

    def _record_prediction_event(self, event: Any) -> None:
        payload = getattr(event, "payload", {}) if event is not None else {}
        kind = str(getattr(event, "kind", "") or "")
        prediction_id = str(getattr(event, "prediction_id", "") or "")
        if not prediction_id:
            return
        status = "updated"
        stage = "prediction"
        summary = kind.replace(".", " ")
        artifact_kind: str | None = None
        if kind == "prediction.enrolled":
            stage = "queued"
            status = "queued"
            summary = "Prediction enrolled for background preparation."
        elif kind == "prediction.readiness_changed":
            stage = str(payload.get("to_state") or "readiness") if isinstance(payload, dict) else "readiness"
            status = stage
            from_state = str(payload.get("from_state") or "") if isinstance(payload, dict) else ""
            reason = str(payload.get("reason") or "") if isinstance(payload, dict) else ""
            summary = f"Readiness changed {from_state} -> {stage}".strip()
            if reason:
                summary = f"{summary}: {reason}"
        elif kind == "prediction.progress":
            stage = "progress"
            status = "running"
            if isinstance(payload, dict):
                summary = (
                    f"Progress: {int(payload.get('tokens_used') or 0)}/"
                    f"{int(payload.get('token_budget') or 0)} tokens, "
                    f"{int(payload.get('scenarios_complete') or 0)} scenarios complete."
                )
        elif kind == "prediction.artifact_added":
            stage = "artifact"
            status = "updated"
            artifact_kind = str(payload.get("kind") or "artifact") if isinstance(payload, dict) else "artifact"
            summary = f"Added {artifact_kind} artifact."
        elif kind == "prediction.staled":
            stage = "stale"
            status = "stale"
            reason = str(payload.get("reason") or "") if isinstance(payload, dict) else ""
            summary = f"Prediction staled{': ' + reason if reason else ''}."
        try:
            append_live_work_event(
                self.repo_root,
                entity_type="prediction",
                entity_id=prediction_id,
                stage=stage,
                status=status,
                summary=summary,
                cycle_id=self._last_cycle_id,
                job_id=self._current.id if self._current is not None else None,
                artifact_kind=artifact_kind,
                metadata={"kind": kind, "payload": payload if isinstance(payload, dict) else {}},
                ts=float(getattr(event, "ts", 0.0) or time.time()),
            )
        except Exception:
            logger.debug("failed to write prediction live event", exc_info=True)

    def _record_live_work_event(self, payload: dict[str, Any]) -> None:
        try:
            append_live_work_event(
                self.repo_root,
                entity_type=str(payload.get("entity_type") or "worker"),  # type: ignore[arg-type]
                entity_id=str(payload.get("entity_id") or "precompute-worker"),
                stage=str(payload.get("stage") or "worker"),
                status=str(payload.get("status") or "updated"),
                summary=str(payload.get("summary") or "Background work updated."),
                cycle_id=str(payload.get("cycle_id")) if payload.get("cycle_id") is not None else self._last_cycle_id,
                job_id=str(payload.get("job_id")) if payload.get("job_id") is not None else (self._current.id if self._current else None),
                scenario_id=str(payload.get("scenario_id")) if payload.get("scenario_id") is not None else None,
                targets=[str(item) for item in list(payload.get("targets") or [])],
                model=str(payload.get("model")) if payload.get("model") is not None else None,
                latency_ms=float(payload.get("latency_ms")) if payload.get("latency_ms") is not None else None,
                token_usage=dict(payload.get("token_usage") or {}),
                artifact_kind=str(payload.get("artifact_kind")) if payload.get("artifact_kind") is not None else None,
                safe_preview=str(payload.get("safe_preview")) if payload.get("safe_preview") is not None else None,
                metadata=dict(payload.get("metadata") or {}),
            )
        except Exception:
            logger.debug("failed to write engine live event", exc_info=True)

    def _write_status(self) -> None:
        self._status["worker"]["last_heartbeat_at"] = time.time()
        _atomic_write_json(worker_status_path(self.repo_root), self._status)

    @staticmethod
    def _cap_native_threads() -> None:
        # Conservative defaults; operators can override before launching.
        for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            os.environ.setdefault(key, "1")
        try:
            os.nice(8)
        except OSError:
            # Restricted environments may deny priority changes; continue with default priority.
            logger.debug("failed to lower native thread priority", exc_info=True)


def _snapshot_has_prediction_rows(snapshot: dict[str, Any] | None) -> bool:
    if not isinstance(snapshot, dict):
        return False
    predictions = snapshot.get("predictions")
    if isinstance(predictions, list) and predictions:
        return True
    by_state = snapshot.get("by_state")
    if isinstance(by_state, dict):
        return any(isinstance(rows, list) and bool(rows) for rows in by_state.values())
    return False


def run_precompute_worker(repo_root: Path, *, interval_seconds: float = 45.0, startup_delay_seconds: float = 10.0) -> None:
    logging.basicConfig(level=os.environ.get("VANER_LOG_LEVEL", "INFO"))
    asyncio.run(
        PrecomputeWorker(
            repo_root,
            interval_seconds=interval_seconds,
            startup_delay_seconds=startup_delay_seconds,
        ).run_forever()
    )
