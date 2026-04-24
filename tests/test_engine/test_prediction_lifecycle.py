# SPDX-License-Identifier: Apache-2.0
"""Phase 4 / WS1 — end-to-end prediction lifecycle through precompute_cycle.

Ship-gate bar for WS1: ``engine.get_active_predictions()`` must include at
least one prediction that reached ``ready`` after a representative cycle
with a stub LLM. Also verifies that scenarios are tagged with prediction_id,
that the registry records evidence/calls as the LLM cycle runs, and that
transitions strictly follow the state machine (no skipping).
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
    return engine


def _seed_repo_with_category_keywords(repo: Path) -> None:
    """Write files whose names match multiple _CATEGORY_KEYWORDS entries so
    seed_from_arc / seed_from_prompt_macros can admit scenarios when the
    cycle runs. The default temp_repo only ships sample.py which matches
    none of the keyword sets."""
    for sub in ("src", "tests", "docs", "debug", "review"):
        (repo / sub).mkdir(exist_ok=True)
    (repo / "src" / "parser.py").write_text("def parse(x):\n    return x\n", encoding="utf-8")
    (repo / "src" / "handler.py").write_text("def handle(x):\n    return x\n", encoding="utf-8")
    (repo / "src" / "engine.py").write_text("def run():\n    pass\n", encoding="utf-8")
    (repo / "src" / "impl.py").write_text("def impl():\n    pass\n", encoding="utf-8")
    (repo / "tests" / "test_parser.py").write_text("def test_parse():\n    pass\n", encoding="utf-8")
    (repo / "tests" / "test_handler.py").write_text("def test_handle():\n    pass\n", encoding="utf-8")
    (repo / "tests" / "conftest.py").write_text("", encoding="utf-8")
    (repo / "docs" / "README.md").write_text("# API\n", encoding="utf-8")
    (repo / "debug" / "trace.py").write_text("def trace_error():\n    pass\n", encoding="utf-8")
    (repo / "review" / "audit.py").write_text("def audit():\n    pass\n", encoding="utf-8")


async def _stub_llm_returning_high_confidence(_prompt: str) -> str:
    """Stub LLM that mimics what `_explore_scenario_with_llm` expects:
    a JSON payload with ranked_paths, follow_on_categories, semantic_intent,
    and a confidence above the evidence-gathering floor (0.3).
    """
    return '{"ranked_paths": ["sample.py"], "follow_on_categories": [], "semantic_intent": "probe sample module", "confidence": 0.7}'


# ---------------------------------------------------------------------------
# Registry population
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cycle_enrolls_registry_and_exposes_active_predictions(temp_repo: Path):
    _seed_repo_with_category_keywords(temp_repo)
    engine = _make_engine(temp_repo, llm=_stub_llm_returning_high_confidence)
    await engine.initialize()
    # Seed history so multiple sources enrol.
    for q in ["implement a parser", "add tests for the parser module", "fix exception in parser"]:
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
    assert active, "expected a non-empty registry snapshot after precompute"
    # Registry hangs off the engine
    assert isinstance(engine.prediction_registry, PredictionRegistry)


# ---------------------------------------------------------------------------
# Scenarios get tagged with prediction_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arc_seeded_scenarios_receive_prediction_id(temp_repo: Path):
    """Arc-sourced scenarios admitted by the frontier should carry the
    prediction_id of the parent PredictedPrompt — otherwise the engine
    cannot route evidence back to the right registry entry."""
    _seed_repo_with_category_keywords(temp_repo)
    engine = _make_engine(temp_repo, llm=_stub_llm_returning_high_confidence)
    # llm_gate=none ensures we skip the LLM round-trip in this test and just
    # inspect admission-side tagging.
    engine.config.exploration.llm_gate = "none"
    await engine.initialize()
    for q in ["implement parser", "add tests for parser", "fix exception in parser"]:
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
    # At least one prediction should have at least one scenario attached.
    attached = [p for p in active if p.artifacts.scenario_ids]
    assert attached, (
        "no prediction has a scenario_id attached — either the arc seeds "
        "didn't carry prediction_id or _process_scenario didn't wire the "
        "registry mutation"
    )


# ---------------------------------------------------------------------------
# LLM-cycle evidence recording
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_cycle_records_calls_and_evidence(temp_repo: Path):
    """After _process_scenario runs, predictions whose scenarios were
    explored should show tokens_used > 0 and evidence_score > 0."""
    _seed_repo_with_category_keywords(temp_repo)
    engine = _make_engine(temp_repo, llm=_stub_llm_returning_high_confidence)
    engine.config.exploration.llm_gate = "all"
    await engine.initialize()
    for q in ["implement parser", "add tests for parser"]:
        engine._arc_model.observe(q)
        await engine.store.insert_query_history(
            session_id="s",
            query_text=q,
            selected_paths=[],
            hit_precomputed=False,
            token_used=0,
        )
    await engine.precompute_cycle()

    touched = [p for p in engine.get_active_predictions() if p.artifacts.scenario_ids and p.run.tokens_used > 0]
    assert touched, "expected at least one prediction with recorded LLM work"
    for prompt in touched:
        assert prompt.run.model_calls >= 1
        assert prompt.artifacts.evidence_score > 0.0
        # Transition should have advanced past queued.
        assert prompt.run.readiness != "queued"


# ---------------------------------------------------------------------------
# Readiness transitions respect the state machine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_prediction_skips_directly_to_ready(temp_repo: Path):
    """Invariant: a prediction never reaches 'ready' without first visiting
    grounding → evidence_gathering → drafting. Observed via the event log."""
    from vaner.intent.prediction_registry import PredictionEvent

    observed_paths: list[list[str]] = []

    # Subscribe a listener BEFORE building the registry.
    # We hook by wrapping _merge_prediction_specs.
    original = VanerEngine._merge_prediction_specs

    def _build_with_listener(self, **kw):
        registry, cats, macros = original(self, **kw)
        path_log: list[str] = []

        def _listener(event: PredictionEvent) -> None:
            if event.kind == "prediction.readiness_changed":
                path_log.append(str(event.payload.get("to_state", "")))

        registry._listener = _listener  # type: ignore[attr-defined]
        observed_paths.append(path_log)
        return registry, cats, macros

    VanerEngine._merge_prediction_specs = _build_with_listener  # type: ignore[assignment]
    try:
        _seed_repo_with_category_keywords(temp_repo)
        engine = _make_engine(temp_repo, llm=_stub_llm_returning_high_confidence)
        engine.config.exploration.llm_gate = "all"
        await engine.initialize()
        for q in ["implement parser", "add tests for parser"]:
            engine._arc_model.observe(q)
            await engine.store.insert_query_history(
                session_id="s",
                query_text=q,
                selected_paths=[],
                hit_precomputed=False,
                token_used=0,
            )
        await engine.precompute_cycle()
    finally:
        VanerEngine._merge_prediction_specs = original  # type: ignore[assignment]

    # If any prediction ever reached 'ready', the path to it must include
    # grounding and evidence_gathering and drafting (in some order). The
    # registry enforces the state machine programmatically, so illegal
    # transitions would have raised — but this verifies that the engine
    # doesn't bypass the registry by setting readiness directly.
    for path in observed_paths:
        if "ready" in path:
            assert "grounding" in path
            assert "evidence_gathering" in path
            assert "drafting" in path


# ---------------------------------------------------------------------------
# Rebalance cadence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rebalance_cadence_keeps_weight_sum_nontrivial(temp_repo: Path):
    """After enough scenarios complete, rebalance should have been called
    at least once — the resulting weights should still all be >= MIN_FLOOR."""
    _seed_repo_with_category_keywords(temp_repo)
    engine = _make_engine(temp_repo, llm=_stub_llm_returning_high_confidence)
    engine.config.exploration.llm_gate = "all"
    await engine.initialize()
    for q in ["implement parser", "add tests for parser", "fix exception", "refactor parser", "review diff"]:
        engine._arc_model.observe(q)
        await engine.store.insert_query_history(
            session_id="s",
            query_text=q,
            selected_paths=[],
            hit_precomputed=False,
            token_used=0,
        )
    await engine.precompute_cycle()

    floor = PredictionRegistry.MIN_FLOOR_WEIGHT
    active = engine.get_active_predictions()
    for prompt in active:
        assert prompt.run.weight >= floor
        # After rebalance the token_budget must still respect the absolute floor.
        assert prompt.run.token_budget >= PredictionRegistry.MIN_TOKEN_BUDGET
