# SPDX-License-Identifier: Apache-2.0
"""WS7 — goal-seeded predictions.

A workspace goal should surface as at least one prediction with
``source="goal"`` on the next precompute cycle, and the goal's id
should be the prediction's anchor so the cycle-to-cycle invalidation
can find it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter


async def _stub_llm(_prompt: str) -> str:
    return '{"ranked_files": ["src/parser.py"], "semantic_intent": "probe parser", "confidence": 0.6, "follow_on": []}'


def _seed_repo(repo: Path) -> None:
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "parser.py").write_text("def parse(x):\n    return x\n")


@pytest.mark.asyncio
async def test_active_goal_produces_goal_sourced_prediction(temp_repo: Path):
    _seed_repo(temp_repo)
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo), llm=_stub_llm)
    engine.config.compute.idle_only = False
    engine.config.exploration.llm_gate = "all"
    await engine.initialize()

    # Record a workspace goal via the store.
    await engine.store.upsert_workspace_goal(
        id="goal-jwt-migration",
        title="JWT migration",
        description="Replace session tokens with JWT",
        source="user_declared",
        confidence=0.9,
        status="active",
        evidence_json=json.dumps([]),
        related_files_json=json.dumps(["src/auth.py"]),
    )

    await engine.precompute_cycle()
    predictions = engine.get_active_predictions()
    goal_preds = [p for p in predictions if p.spec.source == "goal"]
    assert goal_preds, "expected at least one goal-sourced prediction"
    assert any(p.spec.anchor == "goal-jwt-migration" for p in goal_preds)


@pytest.mark.asyncio
async def test_archived_goal_is_not_seeded(temp_repo: Path):
    """Goals with status != 'active' must not seed predictions."""
    _seed_repo(temp_repo)
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo), llm=_stub_llm)
    engine.config.compute.idle_only = False
    engine.config.exploration.llm_gate = "all"
    await engine.initialize()

    await engine.store.upsert_workspace_goal(
        id="g-archived",
        title="Old goal",
        description="",
        source="user_declared",
        confidence=0.9,
        status="achieved",
        evidence_json="[]",
        related_files_json="[]",
    )

    await engine.precompute_cycle()
    predictions = engine.get_active_predictions()
    assert not any(p.spec.source == "goal" for p in predictions)
