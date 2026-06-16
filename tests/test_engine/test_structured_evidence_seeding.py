# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest

from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter


@pytest.mark.asyncio
async def test_precompute_seeds_structured_direct_scenarios_before_broad_arc(temp_repo) -> None:
    (temp_repo / "src" / "vaner" / "intent").mkdir(parents=True)
    (temp_repo / "src" / "vaner" / "intent" / "frontier.py").write_text(
        "class ExplorationFrontier:\n"
        "    def compute_score(self):\n"
        "        return 'priority graph proximity arc probability coverage gap freshness'\n",
        encoding="utf-8",
    )
    (temp_repo / ".mypy_cache" / "3.11").mkdir(parents=True)
    (temp_repo / ".mypy_cache" / "3.11" / "frontier.meta.json").write_text("frontier priority", encoding="utf-8")
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    engine.config.compute.idle_only = False
    engine.config.exploration.llm_gate = "none"
    await engine.initialize()
    for query in [
        "How does Vaner's exploration frontier decide priority?",
        "Explain exploration frontier priority scoring",
    ]:
        engine._arc_model.observe(query)
        await engine.store.insert_query_history(
            session_id="s",
            query_text=query,
            selected_paths=[],
            hit_precomputed=False,
            token_used=0,
        )

    await engine.precompute_cycle()

    explored = engine.get_explored_scenarios()
    assert explored
    assert explored[0].source == "structured_direct"
    assert "src/vaner/intent/frontier.py" in explored[0].unit_ids
    assert all(".mypy_cache" not in path for scenario in explored for path in scenario.unit_ids)


@pytest.mark.asyncio
async def test_precompute_latest_query_exact_anchor_beats_generic_signal_paths(temp_repo) -> None:
    (temp_repo / "src" / "vaner" / "learning").mkdir(parents=True)
    (temp_repo / "src" / "vaner" / "signals").mkdir(parents=True)
    (temp_repo / "src" / "vaner" / "intent").mkdir(parents=True)
    (temp_repo / "src" / "vaner" / "learning" / "reward.py").write_text(
        "class RewardInput:\n    pass\n\n"
        "def compute_reward(inputs):\n"
        "    return {'reward_total': 0.0, 'reward_components': {}}\n",
        encoding="utf-8",
    )
    (temp_repo / "src" / "vaner" / "signals" / "schema.py").write_text(
        "class SignalEvent:\n    pass\n",
        encoding="utf-8",
    )
    (temp_repo / "src" / "vaner" / "intent" / "scoring_policy.py").write_text(
        "class ScoringPolicy:\n    pass\n",
        encoding="utf-8",
    )

    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    engine.config.compute.idle_only = False
    engine.config.exploration.llm_gate = "none"
    await engine.initialize()
    query = "How does the reward computation work? What signals does it combine to produce the final reward value?"
    engine._arc_model.observe(query)
    await engine.store.insert_query_history(
        session_id="s",
        query_text=query,
        selected_paths=[],
        hit_precomputed=False,
        token_used=0,
    )

    await engine.precompute_cycle()

    explored = engine.get_explored_scenarios()
    assert explored
    assert explored[0].source == "structured_direct"
    assert "src/vaner/learning/reward.py" in explored[0].unit_ids


@pytest.mark.asyncio
async def test_precompute_latest_query_exact_anchor_keeps_store_schema_source(temp_repo) -> None:
    (temp_repo / "src" / "vaner" / "store").mkdir(parents=True)
    (temp_repo / "src" / "vaner" / "daemon").mkdir(parents=True)
    (temp_repo / "src" / "vaner" / "store" / "artefacts.py").write_text(
        "class ArtefactStore:\n"
        "    def initialize(self):\n"
        "        return 'CREATE TABLE artefacts key content metadata SELECT INSERT'\n",
        encoding="utf-8",
    )
    (temp_repo / "src" / "vaner" / "daemon" / "http.py").write_text(
        "def _artefact_store():\n"
        "    return 'route factory for context package database schema'\n",
        encoding="utf-8",
    )

    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    engine.config.compute.idle_only = False
    engine.config.exploration.llm_gate = "none"
    await engine.initialize()
    query = "How does the ArtefactStore persist and retrieve context packages? What database schema does it use?"
    engine._arc_model.observe(query)
    await engine.store.insert_query_history(
        session_id="s",
        query_text=query,
        selected_paths=[],
        hit_precomputed=False,
        token_used=0,
    )

    await engine.precompute_cycle()

    explored = engine.get_explored_scenarios()
    assert explored
    assert explored[0].source == "structured_direct"
    assert "src/vaner/store/artefacts.py" in explored[0].unit_ids
