# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import math
import uuid

import pytest

from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter
from vaner.models.signal import SignalEvent


@pytest.mark.asyncio
async def test_engine_observe_persists_signal(temp_repo):
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    event = SignalEvent(
        id=str(uuid.uuid4()),
        source="test",
        kind="manual",
        timestamp=1234.5,
        payload={"path": "sample.py"},
    )
    await engine.observe(event)
    stored = await engine.store.list_signal_events(limit=10)
    assert any(item.id == event.id for item in stored)


@pytest.mark.asyncio
async def test_engine_query_records_history_and_feedback(temp_repo):
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    await engine.prepare()
    package = await engine.query("explain sample module")
    assert package.selections
    history = await engine.store.list_query_history(limit=5)
    assert history
    feedback = await engine.store.list_feedback_events(limit=5)
    assert feedback
    latest_meta = feedback[0]["metadata"]
    assert "reward_total" in latest_meta


@pytest.mark.asyncio
async def test_engine_query_full_hit_updates_learning(temp_repo):
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    await engine.prepare()

    first = await engine.query("explain sample module")
    second = await engine.query("explain sample module")

    assert first.id == second.id
    history = await engine.store.list_query_history(limit=5)
    assert history[0]["hit_precomputed"] is True
    replay = await engine.store.sample_replay_entries(limit=10)
    assert replay
    assert "reward_total" in replay[0]["payload"]


@pytest.mark.asyncio
async def test_engine_persists_policy_state_across_restarts(temp_repo):
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    await engine.prepare()
    await engine.query("explain sample module")
    await engine._persist_learning_state(force=True)

    baseline_graph_mult = engine._scoring_policy.source_multipliers.get("graph", 1.0)

    reloaded = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    await reloaded.initialize()

    assert reloaded._scoring_policy.source_multipliers.get("graph", 1.0) == baseline_graph_mult


@pytest.mark.asyncio
async def test_engine_precompute_cycle_populates_intent_state(temp_repo):
    (temp_repo / "consumer.py").write_text("import sample\n", encoding="utf-8")
    (temp_repo / "todo_notes.py").write_text("# TODO: add validation\n", encoding="utf-8")
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    await engine.prepare()

    produced = await engine.precompute_cycle()

    assert produced >= 1
    edges = await engine.store.list_relationship_edges(limit=50)
    assert any(edge[0] == "file:consumer.py" and edge[1] == "file:sample.py" for edge in edges)
    quality = await engine.store.list_quality_issues(limit=50)
    assert any(issue["key"] == "file:todo_notes.py" for issue in quality)
    hypotheses = await engine.store.list_hypotheses(limit=10)
    assert hypotheses
    cache_rows = await engine.store.list_prediction_cache(limit=10)
    assert any(isinstance(row.get("package_json"), str) and row["package_json"] for row in cache_rows)


@pytest.mark.asyncio
async def test_engine_query_graduates_validated_patterns_and_predict_fast_path(temp_repo):
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    await engine.prepare()
    package = await engine.query("explain sample module")
    selected_key = package.selections[0].artefact_key

    await engine.store.insert_hypothesis(
        question="explain sample module",
        confidence=0.95,
        evidence=["recent query"],
        relevant_keys=[selected_key],
        category="understanding",
        response_format="explanation",
        follow_ups=[],
    )

    await engine.query("explain sample module")
    await engine.query("explain sample module")
    await engine.query("explain sample module")

    patterns = await engine.store.list_validated_patterns(limit=10)
    assert patterns
    assert max(int(pattern["confirmation_count"]) for pattern in patterns) >= 3

    predictions = await engine.predict(top_k=3)
    assert predictions
    assert predictions[0].key == selected_key
    assert predictions[0].reason == "validated_pattern_match"
    assert predictions[0].score >= 2.0 + math.log(3.0)


@pytest.mark.asyncio
async def test_engine_run_reasoner_skips_regeneration_when_hypotheses_still_valid(temp_repo):
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    await engine.prepare()
    artefacts = await engine.store.list(limit=1)
    relevant_key = artefacts[0].key if artefacts else "file:sample.py"
    for index in range(5):
        await engine.store.insert_hypothesis(
            question=f"existing question {index}",
            confidence=0.8,
            evidence=["existing evidence"],
            relevant_keys=[relevant_key],
            category="implementation",
            response_format="explanation",
            follow_ups=[],
        )
    before = await engine.store.list_hypotheses(limit=50)
    before_count = len(before)

    called = False

    async def fail_if_called(_: str) -> str:
        nonlocal called
        called = True
        return "[]"

    engine.llm = fail_if_called
    await engine._run_reasoner()

    hypotheses = await engine.store.list_hypotheses(limit=20)
    assert len(hypotheses) == min(before_count, 20)
    assert called is False


@pytest.mark.asyncio
async def test_engine_predict_uses_behavioral_ranking_signals(temp_repo):
    (temp_repo / "review_engine.py").write_text("def review_changes():\n    return True\n", encoding="utf-8")
    (temp_repo / "review_notes.md").write_text("review checklist\n", encoding="utf-8")
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    await engine.prepare()

    await engine.inject_history(
        [
            "implement sample feature",
            "run code review for sample feature",
            "implement sample feature",
            "run code review for sample feature",
            "implement sample feature",
        ],
        session_id="predict-test",
    )

    predictions = await engine.predict(top_k=5)
    assert predictions
    assert "behavior:" in predictions[0].reason
    assert "review" in predictions[0].key.lower()


@pytest.mark.asyncio
async def test_engine_persists_behavioral_memory_tables(temp_repo):
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    await engine.prepare()

    await engine.query("implement sample feature")
    await engine.query("run code review for sample feature")
    await engine.query("run code review for sample feature")

    transitions = await engine.store.list_habit_transitions(limit=10)
    assert transitions
    assert any(row["previous_category"] == "implementation" and row["category"] == "review" for row in transitions)

    macros = await engine.store.list_prompt_macros(limit=10)
    assert macros
    assert any("run code review" in str(row["macro_key"]) for row in macros)

    phase = await engine.store.get_workflow_phase_summary()
    assert phase is not None
    assert phase["recent_macro"]
    assert phase["dominant_category"] in {"implementation", "review", "testing", "understanding", "planning", "debugging", "cleanup"}


@pytest.mark.asyncio
async def test_reasoner_loop_iteration_updates_unit_ids_not_file_paths_property(temp_repo):
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    await engine.prepare()

    async def fake_llm(_: str) -> str:
        return '[{"question":"investigate sample","file_paths":["sample.py"],"confidence":0.8,"rationale":"test"}]'

    engine.llm = fake_llm
    scenarios = await engine._run_reasoner_loop_iteration(
        available_paths=["sample.py"],
        coverage={"covered_paths": set()},
    )

    assert scenarios
    assert scenarios[0].unit_ids
    assert scenarios[0].file_paths == scenarios[0].unit_ids


@pytest.mark.asyncio
async def test_store_query_history_fts_search(temp_repo):
    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    await engine.prepare()
    await engine.query("explain sample module")
    await engine.query("how should I test sample module changes")

    matches = await engine.store.search_query_history("sample OR module", limit=5)
    assert matches
    assert any("sample" in str(row["query_text"]).lower() for row in matches)
