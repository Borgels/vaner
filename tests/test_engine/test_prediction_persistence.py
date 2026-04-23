# SPDX-License-Identifier: Apache-2.0
"""WS6 — persistence tests.

The core WS6 promise: a prediction's accumulated state survives across
precompute cycles. If cycle 1 produces a ``ready`` prediction with an
evidence score of 2.0 and a non-empty ``prepared_briefing``, cycle 2 must
still carry those values when no invalidation signal fired.

This is what motivated the whole WS6 work: the rebuild-per-cycle baseline
threw away compute every 90 seconds for no reason.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vaner.engine import VanerEngine
from vaner.intent.adapter import CodeRepoAdapter
from vaner.intent.prediction_registry import PredictionRegistry


def _seed_repo(repo: Path) -> None:
    for sub in ("src", "tests", "docs", "debug", "review"):
        (repo / sub).mkdir(exist_ok=True)
    (repo / "src" / "parser.py").write_text("def parse(x):\n    return x\n")
    (repo / "src" / "handler.py").write_text("def handle(x):\n    return x\n")
    (repo / "src" / "engine.py").write_text("def run():\n    pass\n")
    (repo / "tests" / "test_parser.py").write_text("def test_parse():\n    pass\n")
    (repo / "tests" / "conftest.py").write_text("")
    (repo / "docs" / "README.md").write_text("# API\n")
    (repo / "debug" / "trace.py").write_text("def trace():\n    pass\n")
    (repo / "review" / "audit.py").write_text("def audit():\n    pass\n")


async def _stub_llm(_prompt: str) -> str:
    return '{"ranked_files": ["src/parser.py"], "semantic_intent": "probe parser", "confidence": 0.7, "follow_on": []}'


def _make_engine(repo_root: Path) -> VanerEngine:
    engine = VanerEngine(adapter=CodeRepoAdapter(repo_root), llm=_stub_llm)
    engine.config.compute.idle_only = False
    engine.config.exploration.llm_gate = "all"
    engine.config.exploration.predicted_response_enabled = True
    engine.config.exploration.predicted_response_max_per_cycle = 3
    engine.config.exploration.predicted_response_min_macro_use_count = 1
    return engine


# ---------------------------------------------------------------------------
# Registry-level merge contract (no engine needed)
# ---------------------------------------------------------------------------


def test_merge_preserves_existing_prediction_state():
    """If a prediction already has scenarios + evidence + briefing, re-merging
    its spec must NOT reset those fields."""
    from vaner.intent.prediction import PredictionSpec, prediction_id

    reg = PredictionRegistry(cycle_token_pool=10_000)
    spec = PredictionSpec(
        id=prediction_id("arc", "anchor", "Work on parser"),
        label="Work on parser",
        description="",
        source="arc",
        anchor="anchor",
        confidence=0.7,
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=0.0,
    )
    # Cycle 1: enrol + accumulate state
    reg.merge([spec], cycle_n=1)
    reg.attach_scenario(spec.id, "scen-1")
    reg.record_call(spec.id, tokens_used=120)
    reg.record_evidence(spec.id, delta_score=2.5)
    reg.attach_artifact(spec.id, briefing="## Context\nparser files…")
    prompt_before = reg.get(spec.id)
    assert prompt_before is not None
    assert prompt_before.artifacts.evidence_score == pytest.approx(2.5)
    assert prompt_before.artifacts.prepared_briefing is not None

    # Cycle 2: re-merge the same spec (same id, possibly different confidence)
    spec_refreshed = PredictionSpec(
        id=spec.id,
        label=spec.label,
        description=spec.description,
        source="arc",
        anchor="anchor",
        confidence=0.85,  # drifted up
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=spec.created_at,
    )
    reg.merge([spec_refreshed], cycle_n=2)
    prompt_after = reg.get(spec.id)
    assert prompt_after is not None
    # State preserved
    assert prompt_after.artifacts.evidence_score == pytest.approx(2.5)
    assert prompt_after.artifacts.prepared_briefing is not None
    assert prompt_after.run.tokens_used == 120
    assert prompt_after.run.model_calls == 1
    # Metadata refreshed
    assert prompt_after.spec.confidence == pytest.approx(0.85)
    assert prompt_after.run.last_seen_cycle == 2


def test_merge_does_not_delete_predictions_absent_from_cycle():
    """A prediction enrolled in cycle 1 but not re-proposed in cycle 2
    must persist — absence is not an invalidation signal."""
    from vaner.intent.prediction import PredictionSpec, prediction_id

    reg = PredictionRegistry(cycle_token_pool=10_000)
    a = PredictionSpec(
        id=prediction_id("arc", "a", "A"),
        label="A",
        description="",
        source="arc",
        anchor="a",
        confidence=0.7,
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=0.0,
    )
    b = PredictionSpec(
        id=prediction_id("arc", "b", "B"),
        label="B",
        description="",
        source="arc",
        anchor="b",
        confidence=0.6,
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=0.0,
    )
    reg.merge([a, b], cycle_n=1)
    assert len(reg.active()) == 2
    # Cycle 2 only re-proposes A
    reg.merge([a], cycle_n=2)
    # B is still there, untouched
    active = {p.id: p for p in reg.active()}
    assert a.id in active
    assert b.id in active
    assert active[a.id].run.last_seen_cycle == 2
    assert active[b.id].run.last_seen_cycle == 1


def test_merge_enrolls_new_predictions_via_weight_formula():
    """Specs new in cycle N get enrolled fresh."""
    from vaner.intent.prediction import PredictionSpec, prediction_id

    reg = PredictionRegistry(cycle_token_pool=10_000)
    a = PredictionSpec(
        id=prediction_id("arc", "a", "A"),
        label="A",
        description="",
        source="arc",
        anchor="a",
        confidence=0.9,
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=0.0,
    )
    reg.merge([a], cycle_n=1)

    # Add a new spec in cycle 2.
    b = PredictionSpec(
        id=prediction_id("arc", "b", "B"),
        label="B",
        description="",
        source="arc",
        anchor="b",
        confidence=0.5,
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=0.0,
    )
    reg.merge([b], cycle_n=2)
    assert len(reg.all()) == 2
    assert reg.get(b.id) is not None
    assert reg.get(b.id).run.last_seen_cycle == 2  # type: ignore[union-attr]
    assert reg.get(b.id).run.weight >= PredictionRegistry.MIN_FLOOR_WEIGHT  # type: ignore[union-attr]


def test_adopted_prediction_is_excluded_from_active():
    """WS6: adoption sets spent=True; the prediction drops out of
    ``active()`` until its evidence is invalidated and the cycle
    re-enrolls it fresh."""
    from vaner.intent.prediction import PredictionSpec, prediction_id

    reg = PredictionRegistry(cycle_token_pool=10_000)
    spec = PredictionSpec(
        id=prediction_id("arc", "x", "X"),
        label="X",
        description="",
        source="arc",
        anchor="x",
        confidence=0.7,
        hypothesis_type="likely_next",
        specificity="concrete",
        created_at=0.0,
    )
    reg.merge([spec], cycle_n=1)
    assert len(reg.active()) == 1
    reg.record_adoption(spec.id)
    assert len(reg.active()) == 0, "spent predictions must not surface in active()"
    # But the prediction is still in the registry.
    assert reg.get(spec.id) is not None
    assert reg.get(spec.id).run.spent is True  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# End-to-end through VanerEngine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ready_prediction_survives_across_two_precompute_cycles(temp_repo: Path):
    """Two consecutive precompute_cycle calls on the same engine.

    If cycle 1 drives any prediction to ``ready``, cycle 2 must still show
    that prediction (same id) when no invalidation signal fired. This is
    the WS6 ship-gate invariant — the whole reason we stopped rebuilding
    per cycle.
    """
    _seed_repo(temp_repo)
    engine = _make_engine(temp_repo)
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
    cycle1 = {p.id: p for p in engine.get_active_predictions()}
    assert cycle1, "cycle 1 produced no predictions"

    await engine.precompute_cycle()
    cycle2 = {p.id: p for p in engine.get_active_predictions()}

    # At least one id from cycle 1 should persist to cycle 2. Without WS6,
    # the registry would have been rebuilt and the ids would be identical by
    # hash (since specs are derived from the same arc state) — but the
    # run.last_seen_cycle would be 2 while tokens_used / evidence_score /
    # briefings would all be reset to zero. With WS6, accumulated state
    # survives.
    shared_ids = set(cycle1) & set(cycle2)
    assert shared_ids, "no predictions survived cycle 2"

    # For each surviving prediction, accumulated state should be at least
    # as large as it was in cycle 1 (evidence only grows, tokens only grow).
    for pid in shared_ids:
        before = cycle1[pid]
        after = cycle2[pid]
        assert after.run.tokens_used >= before.run.tokens_used
        assert after.artifacts.evidence_score >= before.artifacts.evidence_score
        assert after.run.last_seen_cycle == 2
