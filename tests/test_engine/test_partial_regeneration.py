# SPDX-License-Identifier: Apache-2.0
"""Tests for partial-regeneration Stage A skip in ``_precompute_predicted_responses``.

When a cached predicted_prompt exists for a macro AND cycle volatility is below
0.2, the engine should reuse the cached rewrite instead of spending another LLM
call on Stage A.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from vaner.intent.cache import CacheMatchResult


@pytest.mark.asyncio
async def test_partial_regen_skips_stage_a_on_low_volatility_cache_hit(monkeypatch, tmp_path):
    """With volatility < 0.2 and a cached predicted_prompt, Stage A is not called."""
    from vaner.cli.commands.config import load_config
    from vaner.engine import VanerEngine
    from vaner.intent.adapter import CodeRepoAdapter

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# test\n")
    config = load_config(repo)

    call_log: list[str] = []

    async def fake_llm(prompt: str) -> str:
        call_log.append(prompt)
        return "Stage B draft output"

    engine = VanerEngine(
        adapter=CodeRepoAdapter(repo),
        config=config,
        llm=fake_llm,
        embed=None,
    )
    await engine.initialize()

    # Seed a macro so the predicted-response loop has something to qualify.
    # Use replace_prompt_macros to override any seed macros from the defaults bundle.
    await engine.store.replace_prompt_macros(
        [
            {
                "macro_key": "test-macro",
                "example_query": "how does foo work",
                "category": "understanding",
                "use_count": 5,
                "confidence": 0.80,
            }
        ]
    )

    # Drive the cycle state: low volatility + high evidence quality + budget.
    engine._cycle_policy_state["volatility_score"] = 0.05
    engine._cycle_policy_state["draft_posterior_threshold"] = 0.50
    engine._cycle_policy_state["draft_evidence_threshold"] = 0.40
    engine._cycle_policy_state["draft_volatility_ceiling"] = 0.50
    engine._cycle_policy_state["draft_budget_min_ms"] = 0.0

    # Mock the cache to return a valid prior rewrite.
    cached_match = CacheMatchResult(
        tier="full_hit",
        similarity=0.95,
        package=None,
        enrichment={"predicted_prompt": "PREVIOUSLY REWRITTEN PROMPT"},
    )
    engine._cache.match = AsyncMock(return_value=cached_match)  # type: ignore[method-assign]
    engine._cache.store_entry = AsyncMock(return_value=None)  # type: ignore[method-assign]

    # Mock _build_package_for_paths to keep the test fast.
    engine._build_package_for_paths = AsyncMock(return_value=(MagicMock(), []))  # type: ignore[method-assign]

    generated = await engine._precompute_predicted_responses(
        max_per_cycle=1,
        min_use_count=1,
        deadline=None,
        available_paths=["README.md"],
        recent_queries=["how does foo work"],
    )
    assert generated == 1
    # With partial regen, only Stage B should run (1 LLM call).
    assert len(call_log) == 1
    # Confirm Stage B got the cached rewrite, not a re-written prompt.
    assert "PREVIOUSLY REWRITTEN PROMPT" in call_log[0]


@pytest.mark.asyncio
async def test_partial_regen_runs_stage_a_when_volatility_high(monkeypatch, tmp_path):
    """Volatility >= 0.2 forces full Stage A + Stage B (2 LLM calls)."""
    from vaner.cli.commands.config import load_config
    from vaner.engine import VanerEngine
    from vaner.intent.adapter import CodeRepoAdapter

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# test\n")
    config = load_config(repo)

    call_log: list[str] = []

    async def fake_llm(prompt: str) -> str:
        call_log.append(prompt)
        return "LLM output"

    engine = VanerEngine(
        adapter=CodeRepoAdapter(repo),
        config=config,
        llm=fake_llm,
        embed=None,
    )
    await engine.initialize()

    await engine.store.replace_prompt_macros(
        [
            {
                "macro_key": "volatile-macro",
                "example_query": "how does bar work",
                "category": "understanding",
                "use_count": 5,
                "confidence": 0.80,
            }
        ]
    )

    # Volatility too high for partial regen
    engine._cycle_policy_state["volatility_score"] = 0.35
    engine._cycle_policy_state["draft_posterior_threshold"] = 0.50
    engine._cycle_policy_state["draft_evidence_threshold"] = 0.40
    engine._cycle_policy_state["draft_volatility_ceiling"] = 0.50
    engine._cycle_policy_state["draft_budget_min_ms"] = 0.0

    engine._cache.store_entry = AsyncMock(return_value=None)  # type: ignore[method-assign]
    engine._build_package_for_paths = AsyncMock(return_value=(MagicMock(), []))  # type: ignore[method-assign]

    generated = await engine._precompute_predicted_responses(
        max_per_cycle=1,
        min_use_count=1,
        deadline=None,
        available_paths=["README.md"],
        recent_queries=["how does bar work"],
    )
    assert generated == 1
    # Full path: Stage A rewrite + Stage B draft = 2 LLM calls.
    assert len(call_log) == 2


@pytest.mark.asyncio
async def test_partial_regen_runs_stage_a_when_cache_miss(monkeypatch, tmp_path):
    """Low volatility but no cached rewrite → still must run Stage A."""
    from vaner.cli.commands.config import load_config
    from vaner.engine import VanerEngine
    from vaner.intent.adapter import CodeRepoAdapter

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# test\n")
    config = load_config(repo)

    call_log: list[str] = []

    async def fake_llm(prompt: str) -> str:
        call_log.append(prompt)
        return "LLM output"

    engine = VanerEngine(
        adapter=CodeRepoAdapter(repo),
        config=config,
        llm=fake_llm,
        embed=None,
    )
    await engine.initialize()

    await engine.store.replace_prompt_macros(
        [
            {
                "macro_key": "fresh-macro",
                "example_query": "how does baz work",
                "category": "understanding",
                "use_count": 5,
                "confidence": 0.80,
            }
        ]
    )

    engine._cycle_policy_state["volatility_score"] = 0.05  # low — would allow partial
    engine._cycle_policy_state["draft_posterior_threshold"] = 0.50
    engine._cycle_policy_state["draft_evidence_threshold"] = 0.40
    engine._cycle_policy_state["draft_volatility_ceiling"] = 0.50
    engine._cycle_policy_state["draft_budget_min_ms"] = 0.0

    # Cache miss — no prior rewrite
    cache_miss = CacheMatchResult(tier="cold_miss", similarity=0.0, package=None, enrichment={})
    engine._cache.match = AsyncMock(return_value=cache_miss)  # type: ignore[method-assign]
    engine._cache.store_entry = AsyncMock(return_value=None)  # type: ignore[method-assign]
    engine._build_package_for_paths = AsyncMock(return_value=(MagicMock(), []))  # type: ignore[method-assign]

    generated = await engine._precompute_predicted_responses(
        max_per_cycle=1,
        min_use_count=1,
        deadline=None,
        available_paths=["README.md"],
        recent_queries=["how does baz work"],
    )
    assert generated == 1
    # Even with low volatility, no cached rewrite → full Stage A + Stage B.
    assert len(call_log) == 2
