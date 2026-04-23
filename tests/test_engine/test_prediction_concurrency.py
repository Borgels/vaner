# SPDX-License-Identifier: Apache-2.0
"""Phase 4 / WS1.h — registry invariants under concurrent access.

The exploration loop runs `_process_scenario` concurrently via a semaphore
(`_compute_effective_concurrency`), so the PredictionRegistry is mutated
from multiple asyncio tasks. This test exercises the registry's lock
explicitly and verifies that invariants hold under parallel writes.
"""

from __future__ import annotations

import asyncio
import random

import pytest

from vaner.intent.prediction import PredictionSpec, prediction_id
from vaner.intent.prediction_registry import PredictionRegistry


def _spec(label: str, *, confidence: float = 0.7) -> PredictionSpec:
    return PredictionSpec(
        id=prediction_id("arc", label, label),
        label=label,
        description=f"{label} description",
        source="arc",
        anchor=label,
        confidence=confidence,
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=0.0,
    )


async def _worker(
    reg: PredictionRegistry,
    pid: str,
    scenario_ids: list[str],
) -> None:
    """Simulate an exploration worker: attach, record, complete, all under
    the registry's lock."""
    for sid in scenario_ids:
        async with reg.lock:
            reg.attach_scenario(pid, sid)
            reg.record_call(pid, tokens_used=random.randint(10, 100))
            reg.record_evidence(pid, delta_score=random.random())
            reg.complete_scenario(pid, sid)
        # Yield so other workers can interleave.
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_concurrent_mutations_preserve_scenarios_complete_monotonicity():
    """Under N parallel workers each writing M scenarios, final
    scenarios_complete must equal N × M — nothing is lost."""
    reg = PredictionRegistry(cycle_token_pool=10_000)
    spec = _spec("A")
    reg.enroll(spec, initial_weight=1.0)

    n_workers = 4
    m_scenarios = 25
    tasks = [_worker(reg, spec.id, [f"scen-{i}-{j}" for j in range(m_scenarios)]) for i in range(n_workers)]
    await asyncio.gather(*tasks)

    prompt = reg.get(spec.id)
    assert prompt is not None
    assert prompt.run.scenarios_complete == n_workers * m_scenarios
    # scenarios_spawned should match (attach_scenario is idempotent on dup IDs,
    # and every scenario id here is unique).
    assert prompt.run.scenarios_spawned == n_workers * m_scenarios


@pytest.mark.asyncio
async def test_concurrent_mutations_preserve_token_sum():
    """Every recorded call should contribute to tokens_used — no drops."""
    reg = PredictionRegistry(cycle_token_pool=10_000)
    spec = _spec("B")
    reg.enroll(spec, initial_weight=1.0)

    # Pre-compute deterministic token deltas so we can assert a known sum.
    per_worker_tokens = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    expected_total = sum(per_worker_tokens) * 4  # 4 workers

    async def _worker(sid_prefix: str) -> None:
        for tokens in per_worker_tokens:
            async with reg.lock:
                reg.record_call(spec.id, tokens_used=tokens)
            await asyncio.sleep(0)

    await asyncio.gather(*[_worker(f"w{i}") for i in range(4)])
    prompt = reg.get(spec.id)
    assert prompt is not None
    assert prompt.run.tokens_used == expected_total
    assert prompt.run.model_calls == len(per_worker_tokens) * 4


@pytest.mark.asyncio
async def test_concurrent_rebalance_keeps_floor_invariant():
    """Rebalance called interleaved with evidence writes must never produce
    a weight below MIN_FLOOR_WEIGHT or a token_budget below MIN_TOKEN_BUDGET."""
    reg = PredictionRegistry(cycle_token_pool=10_000)
    specs = [_spec(f"P{i}", confidence=0.2 + 0.1 * i) for i in range(4)]
    reg.enroll_batch(specs)

    async def _recorder(spec: PredictionSpec) -> None:
        for _ in range(20):
            async with reg.lock:
                reg.record_call(spec.id, tokens_used=random.randint(10, 50))
                reg.record_evidence(spec.id, delta_score=random.random())
            await asyncio.sleep(0)

    async def _rebalancer() -> None:
        for _ in range(10):
            async with reg.lock:
                reg.rebalance()
            await asyncio.sleep(0)

    await asyncio.gather(
        *[_recorder(spec) for spec in specs],
        _rebalancer(),
    )

    for prompt in reg.active():
        assert prompt.run.weight >= PredictionRegistry.MIN_FLOOR_WEIGHT
        assert prompt.run.token_budget >= PredictionRegistry.MIN_TOKEN_BUDGET


@pytest.mark.asyncio
async def test_concurrent_transitions_never_skip_states():
    """Under parallel attempts to advance readiness, the state machine must
    still reject illegal jumps — a concurrent `attach_scenario` + `transition`
    pair never leaves a prediction in an impossible state."""
    from vaner.intent.prediction_registry import InvalidTransitionError

    reg = PredictionRegistry(cycle_token_pool=1_000)
    spec = _spec("C")
    reg.enroll(spec, initial_weight=1.0)

    illegal_attempts = 0

    async def _jumper() -> None:
        nonlocal illegal_attempts
        for _ in range(10):
            try:
                async with reg.lock:
                    # The only legal transition from queued is grounding
                    # (attach_scenario) or stale. "ready" is illegal here.
                    reg.transition(spec.id, "ready")
            except InvalidTransitionError:
                illegal_attempts += 1
            await asyncio.sleep(0)

    async def _attacher() -> None:
        async with reg.lock:
            reg.attach_scenario(spec.id, "scen-1")

    await asyncio.gather(_jumper(), _attacher())

    prompt = reg.get(spec.id)
    assert prompt is not None
    # After attacher runs, prediction is in grounding (or queued if jumper
    # interleaved first). Must NEVER be ready without passing through the
    # full chain.
    assert prompt.run.readiness in {"queued", "grounding"}
    assert illegal_attempts > 0
