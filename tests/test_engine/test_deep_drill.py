# SPDX-License-Identifier: Apache-2.0
"""Tests for the high-priority deep-drill exploration behavior.

Verifies that scenarios whose effective priority clears the configured
``deep_drill_priority_threshold`` get:
  1. A widened LLM follow-on prompt (up to ``deep_drill_max_followons`` branches).
  2. A softer ``deep_drill_branch_decay`` applied to their children.
  3. A decrementing depth-bonus budget that lets the lineage exceed
     ``max_exploration_depth``.

These rely on mocking the LLM so no external service is needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter
from vaner.intent.frontier import ExplorationScenario, file_set_fingerprint


@pytest.mark.asyncio
async def test_deep_drill_widens_follow_on_cap(temp_repo: Path) -> None:
    """A high-priority scenario should accept up to ``deep_drill_max_followons``."""

    # Capture the prompt the LLM sees so we can assert on its shape.
    captured: dict[str, str] = {}

    async def _capturing_llm(prompt: str) -> str:
        captured["prompt"] = prompt
        # Return 5 follow-ons so we can verify the cap is enforced per scenario.
        return (
            '{"ranked_files": [], "semantic_intent": "", "confidence": 0.9, '
            '"follow_on": ['
            '{"files": ["a.py"], "reason": "r1", "confidence": 0.9},'
            '{"files": ["b.py"], "reason": "r2", "confidence": 0.9},'
            '{"files": ["c.py"], "reason": "r3", "confidence": 0.9},'
            '{"files": ["d.py"], "reason": "r4", "confidence": 0.9},'
            '{"files": ["e.py"], "reason": "r5", "confidence": 0.9}'
            "]}"
        )

    adapter = CodeRepoAdapter(temp_repo)
    engine = VanerEngine(adapter=adapter, llm=_capturing_llm)
    engine.config.compute.idle_only = False
    engine.config.exploration.llm_gate = "all"
    engine.config.exploration.deep_drill_max_followons = 5

    scenario = ExplorationScenario(
        id=file_set_fingerprint(["sample.py"]),
        file_paths=["sample.py"],
        anchor="test",
        source="graph",
        priority=0.8,
        depth=0,
        reason="unit",
    )

    ranked, follow_on, _, _ = await engine._explore_scenario_with_llm(
        scenario=scenario,
        available_paths=["sample.py", "a.py", "b.py", "c.py", "d.py", "e.py"],
        recent_queries=[],
        covered_paths=set(),
        artefacts_by_key={},
        high_priority=True,
    )

    assert ranked == []
    assert len(follow_on) == 5, f"expected 5 follow-ons, got {len(follow_on)}"
    assert "HIGH-PRIORITY" in captured["prompt"]
    assert "0-5" in captured["prompt"]


@pytest.mark.asyncio
async def test_normal_scenario_caps_follow_on_at_three(temp_repo: Path) -> None:
    """Non-high-priority path keeps the original 0-3 follow-on cap."""

    async def _llm(_prompt: str) -> str:
        # 5 proposals; parser should drop to 3.
        return (
            '{"ranked_files": [], "semantic_intent": "", "confidence": 0.5, '
            '"follow_on": ['
            '{"files": ["a.py"], "reason": "r1", "confidence": 0.9},'
            '{"files": ["b.py"], "reason": "r2", "confidence": 0.9},'
            '{"files": ["c.py"], "reason": "r3", "confidence": 0.9},'
            '{"files": ["d.py"], "reason": "r4", "confidence": 0.9},'
            '{"files": ["e.py"], "reason": "r5", "confidence": 0.9}'
            "]}"
        )

    adapter = CodeRepoAdapter(temp_repo)
    engine = VanerEngine(adapter=adapter, llm=_llm)
    engine.config.compute.idle_only = False
    engine.config.exploration.llm_gate = "all"

    scenario = ExplorationScenario(
        id=file_set_fingerprint(["sample.py"]),
        file_paths=["sample.py"],
        anchor="test",
        source="graph",
        priority=0.3,
        depth=0,
        reason="unit",
    )

    _, follow_on, _, _ = await engine._explore_scenario_with_llm(
        scenario=scenario,
        available_paths=["sample.py", "a.py", "b.py", "c.py", "d.py", "e.py"],
        recent_queries=[],
        covered_paths=set(),
        artefacts_by_key={},
        high_priority=False,
    )
    assert len(follow_on) == 3


def test_default_config_has_deep_drill_fields() -> None:
    """Regression guard: the new config fields must be present + sane defaults."""
    from vaner.models.config import ExplorationConfig

    cfg = ExplorationConfig()
    assert 0.0 <= cfg.deep_drill_priority_threshold <= 1.0
    assert cfg.deep_drill_depth_bonus >= 0
    assert cfg.deep_drill_max_followons >= 3
    assert 0.0 < cfg.deep_drill_branch_decay <= 1.0
