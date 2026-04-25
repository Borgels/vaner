# SPDX-License-Identifier: Apache-2.0
"""WS4 — Work-style intent prior tests (0.8.6).

Pure-data invariants on :data:`WORK_STYLE_PRIORS` plus the
:func:`adjustments_for` averaging helper, the
:func:`compose_with_deep_run_preset` math, and the engine-level
no-op-by-default invariant.
"""

from __future__ import annotations

from typing import get_args

import pytest

from vaner.intent.work_style_priors import (
    WORK_STYLE_PRIORS,
    IntentPriorAdjustments,
    adjustments_for,
    compose_with_deep_run_preset,
    default_adjustments,
)
from vaner.setup.enums import WorkStyle

# ---------------------------------------------------------------------------
# Table invariants
# ---------------------------------------------------------------------------


def test_table_keys_match_workstyle_literal() -> None:
    """Every value in the WorkStyle Literal MUST have an entry in
    :data:`WORK_STYLE_PRIORS`. Adding a new style without populating
    the table is the kind of silent gap WS4 was built to prevent.
    """

    literal_values = set(get_args(WorkStyle))
    table_keys = set(WORK_STYLE_PRIORS.keys())
    assert table_keys == literal_values, (
        f"WORK_STYLE_PRIORS drift: missing={literal_values - table_keys}, extra={table_keys - literal_values}"
    )


@pytest.mark.parametrize("style", list(get_args(WorkStyle)))
def test_multipliers_inside_conservative_range(style: WorkStyle) -> None:
    """Spec §7 fixes the multiplier range at ``[0.7, 1.5]`` and the
    drafting floor at ``[0.0, 0.6]``. Drift past these edges means the
    table has gone from explainable nudges to a tuning monster — fail
    fast so the reviewer notices.
    """

    spec = WORK_STYLE_PRIORS[style]
    assert 0.7 <= spec.artefact_alignment_weight_multiplier <= 1.5
    assert 0.7 <= spec.long_horizon_bonus_multiplier <= 1.5
    assert 0.7 <= spec.possible_branch_bonus_multiplier <= 1.5
    assert 0.7 <= spec.drafting_volatility_ceiling_multiplier <= 1.5
    assert 0.0 <= spec.drafting_evidence_floor <= 0.6


def test_mixed_is_neutral() -> None:
    """``adjustments_for(("mixed",))`` MUST return the identity element.

    Engines on default ``setup.work_styles == ["mixed"]`` configs MUST
    see byte-identical behaviour to pre-WS4. This is the load-bearing
    invariant for the whole work-style layer.
    """

    adj = adjustments_for(("mixed",))
    assert adj.artefact_alignment_weight_multiplier == 1.0
    assert adj.long_horizon_bonus_multiplier == 1.0
    assert adj.possible_branch_bonus_multiplier == 1.0
    assert adj.drafting_evidence_floor == 0.0
    assert adj.drafting_volatility_ceiling_multiplier == 1.0
    assert adj.preferred_artefact_templates == ()


def test_default_adjustments_equals_mixed() -> None:
    """``default_adjustments()`` must be the mixed identity element."""

    assert default_adjustments() == adjustments_for(("mixed",))


def test_empty_input_falls_back_to_default() -> None:
    """Empty work_styles list → default identity (defensive)."""

    assert adjustments_for(()) == default_adjustments()
    assert adjustments_for([]) == default_adjustments()


# ---------------------------------------------------------------------------
# Averaging
# ---------------------------------------------------------------------------


def test_averaging_two_styles() -> None:
    """``adjustments_for(("research","planning"))`` MUST return the
    arithmetic mean of the per-style multipliers — not the geometric
    mean, not "first wins". Checked numerically on one field so the
    test fails on accidental policy drift.
    """

    adj = adjustments_for(("research", "planning"))
    research = WORK_STYLE_PRIORS["research"]
    planning = WORK_STYLE_PRIORS["planning"]
    expected = (research.artefact_alignment_weight_multiplier + planning.artefact_alignment_weight_multiplier) / 2
    assert adj.artefact_alignment_weight_multiplier == pytest.approx(expected)


def test_averaging_is_order_independent() -> None:
    """Averaging must commute — the user's selection order doesn't
    affect the resulting prior."""

    a = adjustments_for(("research", "planning"))
    b = adjustments_for(("planning", "research"))
    assert a == b


def test_template_union() -> None:
    """``adjustments_for(("planning","support"))`` MUST union the
    per-style template tuples, deduplicated and sorted (deterministic
    output regardless of input order).
    """

    adj = adjustments_for(("planning", "support"))
    expected = tuple(
        sorted(
            set(WORK_STYLE_PRIORS["planning"].preferred_artefact_templates) | set(WORK_STYLE_PRIORS["support"].preferred_artefact_templates)
        )
    )
    assert adj.preferred_artefact_templates == expected
    # Sorted contract: list is monotonically non-decreasing.
    assert list(adj.preferred_artefact_templates) == sorted(adj.preferred_artefact_templates)


def test_unknown_style_raises_keyerror() -> None:
    """Unknown style names indicate a schema-migration bug, not a
    runtime concern. The averaging helper must surface it loudly."""

    with pytest.raises(KeyError):
        adjustments_for(("not_a_real_style",))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Composition with deep-run preset
# ---------------------------------------------------------------------------


def test_compose_with_preset_is_multiplicative() -> None:
    """Pure math: base 1.5 × work-style 1.2 == 1.8. The composition
    must be multiplicative so it associates with the existing
    HorizonBiasSpec convention."""

    work_style = IntentPriorAdjustments(
        artefact_alignment_weight_multiplier=1.2,
        long_horizon_bonus_multiplier=1.0,
        possible_branch_bonus_multiplier=1.0,
        drafting_evidence_floor=0.0,
        drafting_volatility_ceiling_multiplier=1.0,
        preferred_artefact_templates=(),
    )
    aa, lh, pb = compose_with_deep_run_preset(
        work_style,
        base_artefact_alignment=1.5,
        base_long_horizon=2.0,
        base_possible_branch=0.5,
    )
    assert aa == pytest.approx(1.5 * 1.2)
    assert lh == pytest.approx(2.0 * 1.0)
    assert pb == pytest.approx(0.5 * 1.0)


def test_compose_no_op_for_mixed() -> None:
    """Composing the mixed identity with any base must leave the base
    unchanged. The frontier scoring path can rely on this without a
    null check at the call site."""

    base = (1.7, 0.8, 1.3)
    aa, lh, pb = compose_with_deep_run_preset(
        default_adjustments(),
        base_artefact_alignment=base[0],
        base_long_horizon=base[1],
        base_possible_branch=base[2],
    )
    assert (aa, lh, pb) == base


# ---------------------------------------------------------------------------
# Engine-level no-op-by-default invariant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_no_op_when_default(temp_repo) -> None:  # type: ignore[no-untyped-def]
    """With ``setup.work_styles == ["mixed"]`` (the default), the engine's
    cached adjustments MUST be the identity element and the briefing
    assembler MUST receive an empty preferred-template tuple. This is
    the load-bearing invariant: any byte-of-behaviour change to default
    configs would surface here as a failing assertion.
    """

    from vaner.engine import VanerEngine
    from vaner.intent.adapter import CodeRepoAdapter

    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    # Invariant 1: cached adjustments are the identity element.
    adj = engine._cycle_work_style_adjustments
    assert adj.artefact_alignment_weight_multiplier == 1.0
    assert adj.long_horizon_bonus_multiplier == 1.0
    assert adj.possible_branch_bonus_multiplier == 1.0
    assert adj.drafting_evidence_floor == 0.0
    assert adj.drafting_volatility_ceiling_multiplier == 1.0
    assert adj.preferred_artefact_templates == ()

    # Invariant 2: briefing assembler sees no preferred templates.
    assert engine._briefing_assembler.preferred_artefact_templates == ()

    # Invariant 3: refresh is idempotent under the default config —
    # repeated calls keep the identity element.
    engine._refresh_work_style_adjustments()
    assert engine._cycle_work_style_adjustments == default_adjustments()


@pytest.mark.asyncio
async def test_engine_picks_up_non_default_work_style(temp_repo) -> None:  # type: ignore[no-untyped-def]
    """When the config picks a non-mixed work style, the engine refresh
    MUST flow it through to both the cached adjustments and the
    briefing assembler's preferred-template tuple.
    """

    from vaner.engine import VanerEngine
    from vaner.intent.adapter import CodeRepoAdapter

    engine = VanerEngine(adapter=CodeRepoAdapter(temp_repo))
    engine.config.setup.work_styles = ["planning"]
    engine._refresh_work_style_adjustments()

    expected = WORK_STYLE_PRIORS["planning"]
    assert engine._cycle_work_style_adjustments == expected
    assert engine._briefing_assembler.preferred_artefact_templates == expected.preferred_artefact_templates
