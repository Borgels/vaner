# SPDX-License-Identifier: Apache-2.0
"""Tests for the adaptive cycle budget that sizes ponder time against
the user's inter-prompt cadence.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter


def _make_engine(repo_root: Path) -> VanerEngine:
    adapter = CodeRepoAdapter(repo_root)

    async def _llm(_prompt: str) -> str:
        # Return a minimally-valid LLM response — shape matches what the
        # exploration loop expects.
        return '{"ranked_files": [], "semantic_intent": "", "confidence": 0.1, "follow_on": []}'

    engine = VanerEngine(adapter=adapter, llm=_llm)
    engine.config.compute.idle_only = False
    engine.config.exploration.llm_gate = "none"  # keep the cycle deterministic
    return engine


@pytest.mark.asyncio
async def test_active_cadence_shortens_cycle_deadline(temp_repo: Path):
    """A short inter-prompt EMA should shrink the cycle below max_cycle_seconds."""
    engine = _make_engine(temp_repo)
    engine.config.compute.adaptive_cycle_budget = True
    engine.config.compute.max_cycle_seconds = 600  # 10 minutes hard cap
    engine.config.compute.adaptive_cycle_min_seconds = 1.0
    engine.config.compute.adaptive_cycle_utilisation = 0.8

    await engine.prepare()
    # Simulate an active session: five prompts, ~15 seconds apart.
    now = time.time()
    for offset in (60, 45, 30, 15, 0):
        engine._timing_model.record_prompt(timestamp=now - offset)

    budget = engine._timing_model.budget_seconds_for_cycle(
        hard_cap_seconds=float(engine.config.compute.max_cycle_seconds),
        soft_min_seconds=float(engine.config.compute.adaptive_cycle_min_seconds),
        utilisation_fraction=float(engine.config.compute.adaptive_cycle_utilisation),
    )
    # EMA ≈ 15s * 0.8 utilisation = ~12s. Budget must be shrunk below the cap.
    assert budget < float(engine.config.compute.max_cycle_seconds)
    assert budget >= float(engine.config.compute.adaptive_cycle_min_seconds)


@pytest.mark.asyncio
async def test_idle_user_restores_full_budget(temp_repo: Path):
    """A long time since the last prompt → budget saturates at the hard cap."""
    engine = _make_engine(temp_repo)
    engine.config.compute.adaptive_cycle_budget = True
    engine.config.compute.max_cycle_seconds = 120
    await engine.prepare()

    # Simulate a user who was active yesterday and just came back — no recent
    # prompt recorded at all, so the model has no EMA.
    engine._timing_model.reset()
    budget = engine._timing_model.budget_seconds_for_cycle(
        hard_cap_seconds=120.0,
        soft_min_seconds=5.0,
        utilisation_fraction=0.8,
    )
    assert budget == 120.0


@pytest.mark.asyncio
async def test_adaptive_cycle_budget_can_be_disabled(temp_repo: Path):
    """With adaptive_cycle_budget=False, only max_cycle_seconds applies."""
    engine = _make_engine(temp_repo)
    engine.config.compute.adaptive_cycle_budget = False
    engine.config.compute.max_cycle_seconds = 300
    # Record a fast cadence — model *would* shrink the budget if consulted.
    engine._timing_model.rebuild_from_history([time.time() - 10.0, time.time()])

    # The flag gates consultation inside precompute_cycle; to check disable
    # behaviour we just confirm the cycle respects the static cap.
    await engine.prepare()
    started = time.monotonic()
    await engine.precompute_cycle()
    elapsed = time.monotonic() - started
    assert elapsed < 300


@pytest.mark.asyncio
async def test_cache_match_records_access_count(temp_repo: Path):
    """TieredPredictionCache.match() should bump access_count on a hit."""
    engine = _make_engine(temp_repo)
    await engine.initialize()
    await engine.store.upsert_prediction_cache(
        cache_key="hit-me",
        prompt_hint="how does authentication work",
        package_json=None,
        enrichment={
            "anchor_units": ["sample.py"],
            "source_units": ["sample.py"],
            "semantic_intent": "authentication flow",
        },
        ttl_seconds=3600,
    )
    # Match with strong path overlap so the entry is selected and the tier is
    # above cold_miss — that's the threshold for access-count bumps.
    result = await engine._cache.match("authentication", relevant_paths={"sample.py"})
    assert result.cache_key == "hit-me"
    assert result.tier != "cold_miss"

    rows = await engine.store.list_prediction_cache()
    row = next(r for r in rows if r["cache_key"] == "hit-me")
    assert row["access_count"] >= 1
    assert row["last_accessed_at"] > 0.0
