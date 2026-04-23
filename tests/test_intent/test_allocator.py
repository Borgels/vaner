# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from vaner.intent.allocator import BudgetAllocation, NoRegretSlice, PortfolioAllocator, expected_value_score

# ---------------------------------------------------------------------------
# BudgetAllocation
# ---------------------------------------------------------------------------


def test_budget_allocation_total():
    ba = BudgetAllocation(exploit_ms=50.0, hedge_ms=20.0, invest_ms=10.0, no_regret_ms=20.0)
    assert ba.total_ms == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# expected_value_score
# ---------------------------------------------------------------------------


def test_ev_score_positive():
    score = expected_value_score(probability=0.8, payoff=10.0, reuse_potential=0.5, confidence_gain_per_second=0.1)
    assert score == pytest.approx(0.4)


def test_ev_score_zero_on_negative_inputs():
    assert expected_value_score(probability=-1.0, payoff=10.0, reuse_potential=0.5, confidence_gain_per_second=1.0) == 0.0
    assert expected_value_score(probability=0.5, payoff=-5.0, reuse_potential=0.5, confidence_gain_per_second=1.0) == 0.0


# ---------------------------------------------------------------------------
# PortfolioAllocator — allocate()
# ---------------------------------------------------------------------------


def test_allocator_proportions():
    alloc = PortfolioAllocator(exploit_ratio=0.5, hedge_ratio=0.2, invest_ratio=0.1, no_regret_ratio=0.2)
    result = alloc.allocate(total_ms=1000.0)
    assert result.total_ms == pytest.approx(1000.0)
    assert result.exploit_ms == pytest.approx(500.0)
    assert result.hedge_ms == pytest.approx(200.0)
    assert result.invest_ms == pytest.approx(100.0)
    assert result.no_regret_ms == pytest.approx(200.0)


def test_allocator_zero_total():
    alloc = PortfolioAllocator()
    result = alloc.allocate(total_ms=0.0)
    assert result.total_ms == pytest.approx(0.0)


def test_allocator_degenerate_all_zero_ratios():
    alloc = PortfolioAllocator(exploit_ratio=0.0, hedge_ratio=0.0, invest_ratio=0.0, no_regret_ratio=0.0)
    result = alloc.allocate(total_ms=500.0)
    # Falls back to full exploit
    assert result.exploit_ms == pytest.approx(500.0)
    assert result.total_ms == pytest.approx(500.0)


def test_allocator_unequal_ratios_normalised():
    alloc = PortfolioAllocator(exploit_ratio=1.0, hedge_ratio=1.0, invest_ratio=0.0, no_regret_ratio=0.0)
    result = alloc.allocate(total_ms=200.0)
    assert result.exploit_ms == pytest.approx(100.0)
    assert result.hedge_ms == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# PortfolioAllocator — update_from_returns()
# ---------------------------------------------------------------------------


def test_update_from_returns_nudges_up_on_useful():
    alloc = PortfolioAllocator(exploit_ratio=0.5)
    original = alloc.exploit_ratio
    alloc.update_from_returns("exploit", useful=True)
    assert alloc.exploit_ratio > original


def test_update_from_returns_nudges_down_on_miss():
    alloc = PortfolioAllocator(hedge_ratio=0.3)
    original = alloc.hedge_ratio
    alloc.update_from_returns("hedge", useful=False)
    assert alloc.hedge_ratio < original


def test_update_from_returns_clamped_at_min():
    alloc = PortfolioAllocator(invest_ratio=0.05)
    for _ in range(500):
        alloc.update_from_returns("invest", useful=False)
    assert alloc.invest_ratio >= 0.05


def test_update_from_returns_clamped_at_max():
    alloc = PortfolioAllocator(no_regret_ratio=0.70)
    for _ in range(500):
        alloc.update_from_returns("no_regret", useful=True)
    assert alloc.no_regret_ratio <= 0.70


def test_update_from_returns_unknown_bucket_no_op():
    alloc = PortfolioAllocator(exploit_ratio=0.5)
    alloc.update_from_returns("unknown_bucket", useful=True)
    assert alloc.exploit_ratio == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# NoRegretSlice.collect()
# ---------------------------------------------------------------------------


class _FakeWorkingSet:
    def __init__(self, keys):
        self.artefact_keys = keys


class _MockStore:
    def __init__(self, *, keys=None, edges=None, quality_issues=None, signal_events=None):
        self._keys = keys or []
        self._edges = edges or []
        self._quality_issues = quality_issues or []
        self._signal_events = signal_events or []

    async def get_latest_working_set(self):
        if not self._keys:
            return None
        return _FakeWorkingSet(self._keys)

    async def list_relationship_edges(self, limit=500):
        return self._edges

    async def list_quality_issues(self, limit=50):
        return self._quality_issues

    async def list_signal_events(self, limit=20):
        return self._signal_events


@pytest.mark.asyncio
async def test_no_regret_slice_empty_store():
    store = _MockStore()
    result = await NoRegretSlice().collect(store)
    assert result == []


@pytest.mark.asyncio
async def test_no_regret_slice_working_set():
    store = _MockStore(keys=["repo:src/a.py", "repo:src/b.py"])
    result = await NoRegretSlice().collect(store)
    assert "src/a.py" in result
    assert "src/b.py" in result


@pytest.mark.asyncio
async def test_no_regret_slice_neighbor_added():
    store = _MockStore(
        keys=["repo:src/a.py"],
        edges=[("repo:src/a.py", "repo:src/c.py", {})],
    )
    result = await NoRegretSlice().collect(store)
    assert "src/c.py" in result


@pytest.mark.asyncio
async def test_no_regret_slice_todo_quality_issue():
    store = _MockStore(
        quality_issues=[{"type": "todo", "key": "repo:src/fixme.py"}],
    )
    result = await NoRegretSlice().collect(store)
    assert "src/fixme.py" in result


@pytest.mark.asyncio
async def test_no_regret_slice_respects_limit():
    keys = [f"repo:src/f{i}.py" for i in range(100)]
    store = _MockStore(keys=keys)
    result = await NoRegretSlice().collect(store, limit=5)
    assert len(result) <= 5


@pytest.mark.asyncio
async def test_no_regret_slice_deduplicates():
    store = _MockStore(
        keys=["repo:src/a.py"],
        edges=[("repo:src/a.py", "repo:src/a.py", {})],
    )
    result = await NoRegretSlice().collect(store)
    assert result.count("src/a.py") == 1
