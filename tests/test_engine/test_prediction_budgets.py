# SPDX-License-Identifier: Apache-2.0
"""Phase 4 Phase A.2 — engine-level prediction enrolment tests.

Validates that precompute_cycle builds a PredictionRegistry with predictions
enrolled from multiple sources, and that get_active_predictions() exposes
them to the outside world.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter
from vaner.intent.prediction_registry import PredictionRegistry


def _make_engine(repo_root: Path, *, llm) -> VanerEngine:
    adapter = CodeRepoAdapter(repo_root)
    engine = VanerEngine(adapter=adapter, llm=llm)
    engine.config.compute.idle_only = False
    engine.config.exploration.llm_gate = "none"
    return engine


@pytest.mark.asyncio
async def test_get_active_predictions_empty_before_first_cycle(temp_repo: Path):
    async def _llm(_prompt: str) -> str:
        return ""

    engine = _make_engine(temp_repo, llm=_llm)
    assert engine.get_active_predictions() == []
    assert engine.prediction_registry is None


@pytest.mark.asyncio
async def test_precompute_cycle_builds_registry_with_multiple_sources(temp_repo: Path):
    async def _llm(_prompt: str) -> str:
        return '{"ranked_paths":[],"follow_on_categories":[],"semantic_intent":"","confidence":0.0}'

    engine = _make_engine(temp_repo, llm=_llm)
    await engine.initialize()

    # Seed history + patterns so multiple sources can enrol.
    for q in [
        "implement a parser",
        "add tests for parser",
        "fix exception in parser",
    ]:
        engine._arc_model.observe(q)
        await engine.store.insert_query_history(
            session_id="s",
            query_text=q,
            selected_paths=[],
            hit_precomputed=False,
            token_used=0,
        )
    # A repeated macro to feed the "pattern" source.
    for _ in range(3):
        await engine.store.bump_prompt_macro(
            macro_key="review latest implementation",
            example_query="do a code review of the latest implementation",
            category="review",
            confidence=0.9,
        )

    await engine.precompute_cycle()

    registry = engine.prediction_registry
    assert isinstance(registry, PredictionRegistry)
    active = engine.get_active_predictions()
    assert active, "expected at least one active prediction after a cycle"

    sources = {p.spec.source for p in active}
    # Ship-gate bar: at least 3 distinct source values across a run.
    # A single cycle exercises arc + pattern + history; ensure ≥ 2 here
    # (history is always present when recent_query_text is non-empty).
    assert {"arc", "history"}.issubset(sources) or {"pattern", "history"}.issubset(sources) or {"arc", "pattern"}.issubset(sources)


@pytest.mark.asyncio
async def test_active_predictions_start_in_queued_state(temp_repo: Path):
    async def _llm(_prompt: str) -> str:
        return '{"ranked_paths":[],"follow_on_categories":[],"semantic_intent":"","confidence":0.0}'

    engine = _make_engine(temp_repo, llm=_llm)
    await engine.initialize()
    engine._arc_model.observe("implement a parser")
    await engine.store.insert_query_history(
        session_id="s",
        query_text="implement a parser",
        selected_paths=[],
        hit_precomputed=False,
        token_used=0,
    )
    await engine.precompute_cycle()

    active = engine.get_active_predictions()
    assert active
    # Without a scenario attached, newly enrolled predictions stay queued.
    # (Phase A.2 wires enrolment only; scenario attachment comes later.)
    assert all(p.run.readiness == "queued" for p in active)


@pytest.mark.asyncio
async def test_prediction_weights_respect_floor_and_sum_roughly_to_one(temp_repo: Path):
    async def _llm(_prompt: str) -> str:
        return '{"ranked_paths":[],"follow_on_categories":[],"semantic_intent":"","confidence":0.0}'

    engine = _make_engine(temp_repo, llm=_llm)
    await engine.initialize()
    for q in ["add tests for parser", "fix exception in parser", "implement the handler"]:
        engine._arc_model.observe(q)
        await engine.store.insert_query_history(
            session_id="s",
            query_text=q,
            selected_paths=[],
            hit_precomputed=False,
            token_used=0,
        )

    await engine.precompute_cycle()
    active = engine.get_active_predictions()
    assert active

    floor = PredictionRegistry.MIN_FLOOR_WEIGHT
    total = sum(p.run.weight for p in active)
    # Floor is a hard guarantee; total may exceed 1.0 when floor clamps a share.
    assert total >= 1.0 - 1e-6
    for prompt in active:
        assert prompt.run.weight >= floor
        assert prompt.run.token_budget >= 0
