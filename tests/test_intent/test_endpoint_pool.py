# SPDX-License-Identifier: Apache-2.0

"""Tests for the multi-endpoint exploration pool (PR #135 P5)."""

from __future__ import annotations

import asyncio

import pytest

from vaner.clients.endpoint_pool import (
    FAILURE_COOLDOWN_SECONDS,
    FAILURE_THRESHOLD,
    EndpointHealth,
    ExplorationEndpointPool,
    _PoolEntry,
)


class _StubClient:
    """An async callable that records calls and can be set to fail."""

    def __init__(self, url: str, fail: bool = False) -> None:
        self.url = url
        self.fail = fail
        self.call_count = 0

    async def __call__(self, prompt: str) -> str:
        self.call_count += 1
        if self.fail:
            raise RuntimeError(f"simulated failure at {self.url}")
        return f"ok:{self.url}"


def _make_entry(url: str, weight: float = 1.0, fail: bool = False) -> tuple[_PoolEntry, _StubClient]:
    client = _StubClient(url, fail=fail)
    entry = _PoolEntry(url=url, model="stub", weight=weight, client=client)
    return entry, client


@pytest.mark.asyncio
async def test_pool_requires_at_least_one_endpoint():
    with pytest.raises(ValueError):
        ExplorationEndpointPool([])


@pytest.mark.asyncio
async def test_pool_distributes_calls_round_robin():
    e1, c1 = _make_entry("http://a")
    e2, c2 = _make_entry("http://b")
    pool = ExplorationEndpointPool([e1, e2])
    for _ in range(10):
        await pool("prompt")
    # Equal weight → 5 calls each (tolerance 1 for cursor boundary).
    assert abs(c1.call_count - c2.call_count) <= 1
    assert c1.call_count + c2.call_count == 10


@pytest.mark.asyncio
async def test_pool_weighted_distribution_favours_higher_weight():
    e1, c1 = _make_entry("http://a", weight=1.0)
    e2, c2 = _make_entry("http://b", weight=3.0)
    pool = ExplorationEndpointPool([e1, e2])
    for _ in range(40):
        await pool("prompt")
    # Expect c2 to receive ~3x as many calls as c1 (tolerance wide).
    total = c1.call_count + c2.call_count
    assert total == 40
    assert c2.call_count > c1.call_count * 1.5  # at least 1.5x, generous floor
    assert c1.call_count > 0  # c1 still gets some traffic


@pytest.mark.asyncio
async def test_pool_marks_failing_endpoint_unhealthy_after_threshold():
    e1, c1 = _make_entry("http://bad", fail=True)
    e2, c2 = _make_entry("http://good")
    pool = ExplorationEndpointPool([e1, e2])

    # Drive enough calls that the bad endpoint exceeds FAILURE_THRESHOLD.
    # Alternating round-robin means bad gets picked ceil(2*THRESHOLD/2) times.
    failures_seen = 0
    for _ in range(2 * FAILURE_THRESHOLD + 2):
        try:
            await pool("prompt")
        except RuntimeError:
            failures_seen += 1

    # Bad endpoint raised; good endpoint succeeded.
    assert failures_seen >= FAILURE_THRESHOLD
    assert c2.call_count > 0

    # Bad endpoint should now be in cooldown; further calls should route
    # exclusively to the good one until cooldown lifts.
    c1_before = c1.call_count
    c2_before = c2.call_count
    for _ in range(6):
        await pool("prompt")
    assert c1.call_count == c1_before  # no new calls to bad endpoint
    assert c2.call_count == c2_before + 6


@pytest.mark.asyncio
async def test_pool_recovers_when_endpoint_starts_working_again(monkeypatch):
    e1, c1 = _make_entry("http://a", fail=True)
    e2, c2 = _make_entry("http://b")
    pool = ExplorationEndpointPool([e1, e2])

    # Drive enough failures to put A in cooldown.
    for _ in range(2 * FAILURE_THRESHOLD):
        try:
            await pool("prompt")
        except RuntimeError:
            pass

    # Now fast-forward past the cooldown window by patching time.monotonic.
    import vaner.clients.endpoint_pool as pool_mod

    real_monotonic = pool_mod.time.monotonic

    offset = FAILURE_COOLDOWN_SECONDS + 1
    monkeypatch.setattr(pool_mod.time, "monotonic", lambda: real_monotonic() + offset)

    # Flip A to healthy.
    c1.fail = False

    # On the next call after cooldown, A should be half-open and get picked.
    c1_before = c1.call_count
    for _ in range(4):
        await pool("prompt")
    assert c1.call_count > c1_before


@pytest.mark.asyncio
async def test_pool_fallback_when_every_endpoint_in_cooldown():
    e1, c1 = _make_entry("http://a", fail=True)
    e2, c2 = _make_entry("http://b", fail=True)
    pool = ExplorationEndpointPool([e1, e2])

    # Drive both into cooldown.
    for _ in range(3 * FAILURE_THRESHOLD):
        try:
            await pool("prompt")
        except RuntimeError:
            pass

    # Pool should still attempt a call (picking the soonest-to-recover one)
    # rather than refusing — call raises the underlying exception but does
    # not silently drop.
    with pytest.raises(RuntimeError):
        await pool("prompt")


@pytest.mark.asyncio
async def test_pool_concurrent_callers_do_not_corrupt_state():
    e1, c1 = _make_entry("http://a")
    e2, c2 = _make_entry("http://b")
    pool = ExplorationEndpointPool([e1, e2])

    async def one_call() -> str:
        return await pool("prompt")

    # 20 concurrent pool calls.
    results = await asyncio.gather(*(one_call() for _ in range(20)))
    assert all(r.startswith("ok:") for r in results)
    assert c1.call_count + c2.call_count == 20
    # Within a few of equal distribution under asyncio scheduling.
    assert abs(c1.call_count - c2.call_count) <= 4


@pytest.mark.asyncio
async def test_pool_snapshot_exposes_health_counters():
    e1, c1 = _make_entry("http://a")
    e2, c2 = _make_entry("http://b", fail=True)
    pool = ExplorationEndpointPool([e1, e2])

    await pool("prompt")  # succeeds against a
    try:
        await pool("prompt")  # fails against b
    except RuntimeError:
        pass

    snap = pool.snapshot()
    assert len(snap) == 2
    by_url = {entry["url"]: entry for entry in snap}
    assert by_url["http://a"]["health"]["total_calls"] == 1
    assert by_url["http://a"]["health"]["total_failures"] == 0
    assert by_url["http://b"]["health"]["total_calls"] == 1
    assert by_url["http://b"]["health"]["total_failures"] == 1


def test_endpoint_health_dataclass_defaults():
    h = EndpointHealth()
    assert h.consecutive_failures == 0
    assert h.total_calls == 0
    assert h.cooldown_until == 0.0
    d = h.as_dict()
    assert d["consecutive_failures"] == 0
    assert d["total_calls"] == 0
