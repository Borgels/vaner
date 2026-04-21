# SPDX-License-Identifier: Apache-2.0
"""Verify that :meth:`VanerDaemon.run_once` emits pipeline events in order."""

from __future__ import annotations

import asyncio

import pytest

from vaner.daemon.runner import VanerDaemon
from vaner.events import get_bus, reset_bus
from vaner.models.config import VanerConfig


@pytest.fixture(autouse=True)
def _fresh_bus() -> None:
    reset_bus()


async def _drain_events(queue: asyncio.Queue, deadline: float) -> list:
    events: list = []
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        try:
            event = await asyncio.wait_for(queue.get(), timeout=remaining)
        except TimeoutError:
            break
        if event is None:
            break
        events.append(event)
    return events


@pytest.mark.asyncio
async def test_run_once_publishes_cycle_target_and_artefact_events(temp_repo):
    config = VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
    )
    bus = get_bus()
    queue = bus.subscribe()

    daemon = VanerDaemon(config)
    await daemon.initialize()
    written = await daemon.run_once()
    assert written >= 1

    # Give the bus a beat to deliver queued events.
    await asyncio.sleep(0.05)
    deadline = asyncio.get_event_loop().time() + 0.5
    events = await _drain_events(queue, deadline)

    kinds = [(event.stage, event.kind) for event in events]
    assert ("system", "cycle.start") in kinds
    assert ("signals", "signal.ingest") in kinds
    assert ("targets", "target.planned") in kinds
    assert any(stage == "artefacts" and kind == "artefact.upsert" for stage, kind in kinds)
    assert ("system", "cycle.end") in kinds

    # cycle.start must come before cycle.end
    start_index = kinds.index(("system", "cycle.start"))
    end_index = kinds.index(("system", "cycle.end"))
    assert start_index < end_index

    # Every event inside the cycle must carry the same cycle_id.
    cycle_events = [event for event in events if event.cycle_id is not None]
    assert cycle_events
    cycle_ids = {event.cycle_id for event in cycle_events}
    assert len(cycle_ids) == 1

    # cycle.end payload should include duration and written counts.
    cycle_end = next(event for event in events if event.kind == "cycle.end")
    assert cycle_end.payload["written"] == written
    assert "duration_ms" in cycle_end.payload


@pytest.mark.asyncio
async def test_run_once_target_planned_payload_has_paths(temp_repo):
    config = VanerConfig(
        repo_root=temp_repo,
        store_path=temp_repo / ".vaner" / "store.db",
        telemetry_path=temp_repo / ".vaner" / "telemetry.db",
    )
    bus = get_bus()
    queue = bus.subscribe()

    daemon = VanerDaemon(config)
    await daemon.initialize()
    await daemon.run_once()

    await asyncio.sleep(0.05)
    deadline = asyncio.get_event_loop().time() + 0.5
    events = await _drain_events(queue, deadline)

    planned = [event for event in events if event.kind == "target.planned"]
    assert planned
    payload = planned[0].payload
    assert "count" in payload
    assert "paths" in payload and isinstance(payload["paths"], list)
