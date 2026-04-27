# SPDX-License-Identifier: Apache-2.0
"""Tests for vaner.setup.recommended.resolver.

Synthetic-name registry: NO real model names, on purpose. The resolver
is pure logic over the registry — what we test is the budget-fitting
+ ordering invariants, not which real-world model it happens to pick.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from vaner.setup.memory_budget import MemoryBudget
from vaner.setup.recommended.resolver import alternatives_for, pick_for
from vaner.setup.recommended.schema import RecommendedModel, Registry


def _model(**overrides: Any) -> RecommendedModel:
    base: dict[str, Any] = {
        "id": "fake:7b",
        "family": "fake",
        "params_b": 7.0,
        "min_effective_gb_q4": 5.0,
        "intent_lean": ("mixed",),
        "ollama_id": "fake:7b",
        "huggingface_id": None,
        "context_length": 8192,
        "popularity_rank": 100,
        "rank_source": "ollama-library",
    }
    base.update(overrides)
    return RecommendedModel.model_validate(base)


def _registry(*models: RecommendedModel) -> Registry:
    return Registry(
        schema_version=1,
        generated_at=datetime.now(tz=UTC),
        generator="test",
        sources=(),
        models=models,
    )


def _budget(gb: float) -> MemoryBudget:
    return MemoryBudget(effective_gb_q4=gb, accelerator="nvidia")


# ---------------------------------------------------------------------------
# Empty / fallback cases
# ---------------------------------------------------------------------------


def test_empty_registry_returns_none() -> None:
    assert pick_for(_registry(), _budget(64.0)) is None


def test_zero_budget_returns_none() -> None:
    reg = _registry(_model())
    assert pick_for(reg, _budget(0.0)) is None


def test_no_fitting_model_returns_none() -> None:
    reg = _registry(_model(min_effective_gb_q4=200.0))
    assert pick_for(reg, _budget(8.0)) is None


# ---------------------------------------------------------------------------
# Budget-fit invariants
# ---------------------------------------------------------------------------


def test_picks_only_models_that_fit() -> None:
    too_big = _model(id="big:70b", params_b=70.0, min_effective_gb_q4=43.0)
    fits = _model(id="small:7b", params_b=7.0, min_effective_gb_q4=5.0)
    reg = _registry(too_big, fits)
    pick = pick_for(reg, _budget(8.0))
    assert pick is not None
    assert pick.id == "small:7b"


def test_prefers_larger_when_both_fit() -> None:
    """Within a budget, the larger model wins (capabilities scale with params)."""
    seven = _model(id="a:7b", params_b=7.0, min_effective_gb_q4=5.0)
    thirteen = _model(id="b:13b", params_b=13.0, min_effective_gb_q4=8.5)
    reg = _registry(seven, thirteen)
    pick = pick_for(reg, _budget(20.0))
    assert pick is not None
    assert pick.id == "b:13b"


# ---------------------------------------------------------------------------
# Intent matching
# ---------------------------------------------------------------------------


def test_intent_match_beats_size_when_close() -> None:
    """A coding model wins over a same-band general model for coding work."""
    coding = _model(
        id="x:7b-coder",
        params_b=7.0,
        min_effective_gb_q4=5.0,
        intent_lean=("coding",),
    )
    general = _model(
        id="x:8b-general",
        params_b=8.0,
        min_effective_gb_q4=5.5,
        intent_lean=("mixed",),
    )
    reg = _registry(coding, general)
    pick = pick_for(reg, _budget(20.0), work_styles=("coding",))
    assert pick is not None
    assert pick.id == "x:7b-coder"


def test_general_model_falls_back_when_no_intent_match() -> None:
    """If no model has the user's intent, the largest fitting wins."""
    nothing = _model(
        id="x:8b-other",
        params_b=8.0,
        min_effective_gb_q4=5.5,
        intent_lean=("support",),
    )
    reg = _registry(nothing)
    pick = pick_for(reg, _budget(20.0), work_styles=("coding",))
    assert pick is not None
    assert pick.id == "x:8b-other"


def test_general_unsure_collapses_to_mixed() -> None:
    """The wizard's 'general' / 'unsure' work-style values match 'mixed'."""
    mixed = _model(id="m:7b", params_b=7.0, min_effective_gb_q4=5.0, intent_lean=("mixed",))
    coding = _model(id="c:7b", params_b=7.0, min_effective_gb_q4=5.0, intent_lean=("coding",))
    reg = _registry(coding, mixed)
    pick = pick_for(reg, _budget(20.0), work_styles=("unsure",))
    # Mixed should match unsure; coding should not.
    assert pick is not None
    assert pick.id == "m:7b"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_picks_are_deterministic_under_ties() -> None:
    """Same registry + budget always returns the same pick."""
    a = _model(id="a:7b", params_b=7.0, min_effective_gb_q4=5.0, popularity_rank=100)
    b = _model(id="b:7b", params_b=7.0, min_effective_gb_q4=5.0, popularity_rank=100)
    reg = _registry(a, b)
    p1 = pick_for(reg, _budget(20.0))
    p2 = pick_for(reg, _budget(20.0))
    p3 = pick_for(reg, _budget(20.0))
    assert p1 is not None
    assert p1.id == p2.id == p3.id


# ---------------------------------------------------------------------------
# Alternatives
# ---------------------------------------------------------------------------


def test_alternatives_returns_top_n_in_order() -> None:
    a = _model(id="a:13b", params_b=13.0, min_effective_gb_q4=8.0)
    b = _model(id="b:7b", params_b=7.0, min_effective_gb_q4=5.0)
    c = _model(id="c:3b", params_b=3.0, min_effective_gb_q4=2.5)
    reg = _registry(a, b, c)
    alts = alternatives_for(reg, _budget(20.0), limit=3)
    ids = [m.id for m in alts]
    assert ids == ["a:13b", "b:7b", "c:3b"]


def test_alternatives_filters_to_fitting() -> None:
    too_big = _model(id="big:200b", params_b=200.0, min_effective_gb_q4=120.0)
    fits = _model(id="small:7b", params_b=7.0, min_effective_gb_q4=5.0)
    reg = _registry(too_big, fits)
    alts = alternatives_for(reg, _budget(8.0), limit=3)
    ids = [m.id for m in alts]
    assert ids == ["small:7b"]


def test_alternatives_returns_empty_for_empty_registry() -> None:
    assert alternatives_for(_registry(), _budget(64.0)) == ()


def test_alternatives_returns_empty_for_zero_limit() -> None:
    reg = _registry(_model())
    assert alternatives_for(reg, _budget(64.0), limit=0) == ()
