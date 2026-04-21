# SPDX-License-Identifier: Apache-2.0
"""Tests for the unified pipeline event bus."""

from __future__ import annotations

import asyncio

import pytest

from vaner.events import (
    STAGES,
    EventBus,
    VanerEvent,
    current_cycle_id,
    cycle_scope,
    get_bus,
    publish,
    reset_bus,
)


@pytest.fixture(autouse=True)
def _fresh_bus() -> None:
    reset_bus()


def test_vaner_event_to_dict_includes_structured_and_legacy_fields() -> None:
    event = VanerEvent(
        stage="scenarios",
        kind="expand",
        ts=1700000000.0,
        payload={"msg": "scn_abc expanded"},
        scn="scn_abc",
    )
    data = event.to_dict()
    assert data["stage"] == "scenarios"
    assert data["kind"] == "expand"
    assert data["payload"] == {"msg": "scn_abc expanded"}
    assert data["scn"] == "scn_abc"
    assert data["tag"] == "expand"
    assert data["msg"] == "scn_abc expanded"
    assert data["color"] == "var(--accent)"
    assert "t" in data and isinstance(data["t"], str)
    assert "ts" in data and isinstance(data["ts"], float)


def test_vaner_event_legacy_tag_uses_first_kind_segment() -> None:
    event = VanerEvent(stage="model", kind="llm.request", ts=0.0)
    assert event.legacy_tag() == "llm"


def test_stages_tuple_contains_expected_lanes() -> None:
    assert {"signals", "targets", "model", "artefacts", "scenarios", "decisions", "system"}.issubset(set(STAGES))


@pytest.mark.asyncio
async def test_event_bus_fan_out_to_all_subscribers() -> None:
    bus = EventBus()
    first = bus.subscribe()
    second = bus.subscribe()
    assert bus.subscriber_count == 2

    bus.publish(VanerEvent(stage="system", kind="ping"))

    a = await asyncio.wait_for(first.get(), timeout=0.5)
    b = await asyncio.wait_for(second.get(), timeout=0.5)
    assert a.kind == "ping"
    assert b.kind == "ping"


@pytest.mark.asyncio
async def test_event_bus_drops_slow_subscriber_instead_of_blocking() -> None:
    bus = EventBus(queue_size=2)
    slow = bus.subscribe()
    # fill the queue past capacity; publisher must not block and slow sub is dropped
    for _ in range(10):
        bus.publish(VanerEvent(stage="system", kind="noise"))
    assert bus.subscriber_count == 0
    # queue should still have its two events available to drain
    assert slow.qsize() == 2


@pytest.mark.asyncio
async def test_event_bus_close_sends_sentinel_to_subscribers() -> None:
    bus = EventBus()
    queue = bus.subscribe()
    await bus.close()
    sentinel = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert sentinel is None
    assert bus.subscriber_count == 0


@pytest.mark.asyncio
async def test_publish_helper_emits_on_singleton_bus() -> None:
    bus = get_bus()
    queue = bus.subscribe()
    event = publish("model", "llm.request", {"model": "qwen"}, path="src/app.py")
    received = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert received.id == event.id
    assert received.stage == "model"
    assert received.kind == "llm.request"
    assert received.path == "src/app.py"
    assert received.payload == {"model": "qwen"}


@pytest.mark.asyncio
async def test_cycle_scope_propagates_to_published_events() -> None:
    bus = get_bus()
    queue = bus.subscribe()
    with cycle_scope("cyc_xyz"):
        assert current_cycle_id() == "cyc_xyz"
        publish("system", "cycle.heartbeat")
    assert current_cycle_id() is None
    received = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert received.cycle_id == "cyc_xyz"


@pytest.mark.asyncio
async def test_explicit_cycle_id_overrides_scope() -> None:
    bus = get_bus()
    queue = bus.subscribe()
    with cycle_scope("outer"):
        publish("system", "cycle.start", cycle_id="inner")
    received = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert received.cycle_id == "inner"


def test_publish_without_subscribers_is_noop() -> None:
    # Must not raise when no one is listening.
    publish("system", "cycle.start", {"msg": "offline"})


@pytest.mark.asyncio
async def test_reset_bus_returns_fresh_instance_without_subscribers() -> None:
    first_bus = get_bus()
    queue = first_bus.subscribe()
    reset_bus()
    second_bus = get_bus()
    assert second_bus is not first_bus
    # Old subscriber is orphaned; new publishes don't reach it.
    publish("system", "cycle.start")
    assert queue.empty()
