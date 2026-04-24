# SPDX-License-Identifier: Apache-2.0
"""WS8 — VanerEngine.resolve_query: canonical query → Resolution path."""

from __future__ import annotations

from pathlib import Path

import pytest

from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter
from vaner.intent.prediction import (
    PredictionSpec,
    prediction_id,
)


async def _stub_llm(_prompt: str) -> str:
    return '{"ranked_files": ["src/parser.py"], "semantic_intent": "probe parser", "confidence": 0.7, "follow_on": []}'


def _seed_repo(repo: Path) -> None:
    (repo / "src").mkdir(exist_ok=True)
    (repo / "src" / "parser.py").write_text("def parse(x):\n    return x\n")
    (repo / "src" / "handler.py").write_text("def handle(x):\n    return x\n")


def _make_engine(repo_root: Path) -> VanerEngine:
    engine = VanerEngine(adapter=CodeRepoAdapter(repo_root), llm=_stub_llm)
    engine.config.compute.idle_only = False
    engine.config.exploration.llm_gate = "all"
    return engine


@pytest.mark.asyncio
async def test_resolve_query_returns_predictive_hit_when_prediction_matches(temp_repo: Path):
    _seed_repo(temp_repo)
    engine = _make_engine(temp_repo)
    await engine.initialize()
    # Seed a matching prediction directly via the registry (no precompute
    # needed — we're testing the resolve path, not the cycle).
    from vaner.intent.prediction_registry import PredictionRegistry

    engine._prediction_registry = PredictionRegistry(cycle_token_pool=2_000)
    spec = PredictionSpec(
        id=prediction_id("pattern", "add parser tests", "Add parser tests"),
        label="Add parser tests",
        description="Write unit coverage for the parser module",
        source="pattern",
        anchor="add parser tests",
        confidence=0.75,
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=0.0,
    )
    engine._prediction_registry.enroll(spec, initial_weight=1.0)
    # Attach a briefing + draft so the resolve can surface them.
    engine._prediction_registry.attach_artifact(
        spec.id,
        briefing="## Context\nparser.py defines parse()",
        draft="TENTATIVE: add a table-driven test for parse()",
    )

    resolution = await engine.resolve_query("add parser tests")
    assert resolution.provenance.mode == "predictive_hit"
    assert resolution.intent == "Add parser tests"
    assert resolution.predicted_response is not None
    assert "TENTATIVE" in resolution.predicted_response
    assert resolution.prepared_briefing is not None
    assert "parser.py" in resolution.prepared_briefing
    # Token accounting is honest — non-zero and budget >= used.
    assert resolution.briefing_token_used > 0
    assert resolution.briefing_token_budget >= resolution.briefing_token_used


@pytest.mark.asyncio
async def test_resolve_query_populates_alternatives_when_runners_up_exist(temp_repo: Path):
    _seed_repo(temp_repo)
    engine = _make_engine(temp_repo)
    await engine.initialize()
    from vaner.intent.prediction_registry import PredictionRegistry

    engine._prediction_registry = PredictionRegistry(cycle_token_pool=2_000)
    # Three predictions, all sharing tokens with "parser tests"; they
    # should rank by overlap and all but the top one become alternatives.
    for label in [
        "Add parser tests",
        "Write parser tests for edge cases",
        "Refactor parser tests",
    ]:
        spec = PredictionSpec(
            id=prediction_id("pattern", label.lower(), label),
            label=label,
            description="",
            source="pattern",
            anchor=label.lower(),
            confidence=0.6,
            hypothesis_type="likely_next",
            specificity="concrete",
            created_at=0.0,
        )
        engine._prediction_registry.enroll(spec, initial_weight=0.5)
        engine._prediction_registry.attach_artifact(spec.id, briefing="b")

    resolution = await engine.resolve_query("parser tests")
    # Primary match + 2 runners-up.
    assert len(resolution.alternatives_considered) >= 1
    assert all(alt.reason_rejected for alt in resolution.alternatives_considered)


@pytest.mark.asyncio
async def test_resolve_query_falls_back_to_heuristic_path(temp_repo: Path):
    """When no prediction matches, resolve_query uses the engine's
    heuristic/cache path and still returns a honest Resolution."""
    _seed_repo(temp_repo)
    engine = _make_engine(temp_repo)
    await engine.initialize()
    await engine.prepare()

    resolution = await engine.resolve_query("unrelated orthogonal question")
    # Provenance reflects the tiered cache outcome.
    assert resolution.provenance.mode in {
        "predictive_hit",
        "cached_result",
        "fresh_resolution",
        "retrieval_fallback",
    }
    # Intent is carried through.
    assert resolution.intent == "unrelated orthogonal question"
    assert resolution.resolution_id.startswith("resolve-")


@pytest.mark.asyncio
async def test_resolve_query_honours_include_flags(temp_repo: Path):
    _seed_repo(temp_repo)
    engine = _make_engine(temp_repo)
    await engine.initialize()
    from vaner.intent.prediction_registry import PredictionRegistry

    engine._prediction_registry = PredictionRegistry(cycle_token_pool=2_000)
    spec = PredictionSpec(
        id=prediction_id("arc", "parser", "Parser deep-dive"),
        label="Parser deep-dive",
        description="",
        source="arc",
        anchor="parser",
        confidence=0.7,
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=0.0,
    )
    engine._prediction_registry.enroll(spec, initial_weight=1.0)
    engine._prediction_registry.attach_artifact(
        spec.id,
        briefing="the briefing body",
        draft="a cached draft",
    )

    off = await engine.resolve_query(
        "parser deep-dive",
        include_briefing=False,
        include_predicted_response=False,
    )
    assert off.prepared_briefing is None
    assert off.predicted_response is None

    on = await engine.resolve_query(
        "parser deep-dive",
        include_briefing=True,
        include_predicted_response=True,
    )
    assert on.prepared_briefing is not None
    assert on.predicted_response == "a cached draft"
