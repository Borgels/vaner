# SPDX-License-Identifier: Apache-2.0
"""WS2 — Preset table + applicator tests (0.8.3).

Pure-data invariants on the three presets and the four horizon biases,
plus the ``applies_focus_to_anchor`` admission gate.
"""

from __future__ import annotations

import time

import pytest

from vaner.intent.deep_run import DeepRunSession
from vaner.intent.deep_run_policy import (
    HORIZON_BIASES,
    PRESETS,
    applies_focus_to_anchor,
    horizon_bias_for,
    preset_for,
)


def _session(**overrides: object) -> DeepRunSession:
    defaults: dict[str, object] = {
        "ends_at": time.time() + 3600,
        "preset": "balanced",
        "focus": "active_goals",
        "horizon_bias": "balanced",
        "locality": "local_preferred",
        "cost_cap_usd": 0.0,
        "workspace_root": "/tmp/repo",
    }
    defaults.update(overrides)
    return DeepRunSession.new(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Preset table invariants
# ---------------------------------------------------------------------------


def test_three_presets_defined() -> None:
    assert set(PRESETS.keys()) == {"conservative", "balanced", "aggressive"}


@pytest.mark.parametrize("preset", ["conservative", "balanced", "aggressive"])
def test_preset_for_returns_matching_spec(preset: str) -> None:
    session = _session(preset=preset)
    spec = preset_for(session)
    assert spec is PRESETS[preset]


def test_preset_progression_aggressive_explores_more_than_conservative() -> None:
    """Sanity check: the 'aggressive' preset exists to spend more on
    exploration than 'conservative'. If invariants here are ever
    violated, the preset table has drifted from spec §7.2."""
    cons = PRESETS["conservative"]
    agg = PRESETS["aggressive"]
    assert agg.invest_ratio_bias > cons.invest_ratio_bias
    assert agg.exploit_ratio_bias < cons.exploit_ratio_bias
    assert agg.maturation_budget_share_per_cycle > cons.maturation_budget_share_per_cycle
    assert agg.max_revisits_per_prediction > cons.max_revisits_per_prediction
    assert agg.draft_evidence_threshold < cons.draft_evidence_threshold
    assert agg.idle_curve_multiplier < cons.idle_curve_multiplier


def test_balanced_is_between_conservative_and_aggressive() -> None:
    """Balanced should sit (loosely) between the other two on the
    headline knobs. This is an editorial promise of the preset table."""
    cons = PRESETS["conservative"]
    bal = PRESETS["balanced"]
    agg = PRESETS["aggressive"]
    assert cons.maturation_budget_share_per_cycle <= bal.maturation_budget_share_per_cycle <= agg.maturation_budget_share_per_cycle
    assert cons.max_revisits_per_prediction <= bal.max_revisits_per_prediction <= agg.max_revisits_per_prediction


# ---------------------------------------------------------------------------
# Horizon-bias table
# ---------------------------------------------------------------------------


def test_four_horizon_biases_defined() -> None:
    assert set(HORIZON_BIASES.keys()) == {
        "likely_next",
        "long_horizon",
        "finish_partials",
        "balanced",
    }


def test_finish_partials_suppresses_new_queued() -> None:
    bias = HORIZON_BIASES["finish_partials"]
    assert bias.new_queued_suppression < 1.0
    assert bias.finish_partials_multiplier > 1.0


def test_long_horizon_suppresses_likely_next() -> None:
    bias = HORIZON_BIASES["long_horizon"]
    assert bias.likely_next_multiplier < 1.0
    assert bias.long_horizon_multiplier > 1.0


def test_balanced_horizon_is_no_op() -> None:
    """Balanced bias should not move any score; values are all 1.0."""
    bias = HORIZON_BIASES["balanced"]
    assert bias.likely_next_multiplier == 1.0
    assert bias.long_horizon_multiplier == 1.0
    assert bias.finish_partials_multiplier == 1.0
    assert bias.new_queued_suppression == 1.0


def test_horizon_bias_for_session() -> None:
    session = _session(horizon_bias="long_horizon")
    assert horizon_bias_for(session) is HORIZON_BIASES["long_horizon"]


# ---------------------------------------------------------------------------
# Focus admission gate
# ---------------------------------------------------------------------------


def test_focus_all_recent_admits_everything() -> None:
    session = _session(focus="all_recent")
    assert applies_focus_to_anchor(session, goal_status="abandoned", anchor_path="/elsewhere/x.md")


def test_focus_active_goals_excludes_terminated_goals() -> None:
    session = _session(focus="active_goals")
    assert applies_focus_to_anchor(session, goal_status="active", anchor_path=None)
    assert applies_focus_to_anchor(session, goal_status="dormant", anchor_path=None)
    assert not applies_focus_to_anchor(session, goal_status="abandoned", anchor_path=None)
    assert not applies_focus_to_anchor(session, goal_status="achieved", anchor_path=None)


def test_focus_active_goals_admits_when_no_goal_attached() -> None:
    """Anchors without a goal attachment (raw scenario admissions) are
    not gated by goal-status focus — the gate is a *positive* filter
    on goal-attached anchors only."""
    session = _session(focus="active_goals")
    assert applies_focus_to_anchor(session, goal_status=None, anchor_path="/repo/x.md")


def test_focus_current_workspace_path_prefix_match() -> None:
    session = _session(focus="current_workspace", workspace_root="/repo")
    assert applies_focus_to_anchor(session, goal_status=None, anchor_path="/repo/sub/x.py")
    assert not applies_focus_to_anchor(session, goal_status=None, anchor_path="/elsewhere/x.py")


def test_focus_current_workspace_admits_pathless_anchors() -> None:
    session = _session(focus="current_workspace", workspace_root="/repo")
    assert applies_focus_to_anchor(session, goal_status=None, anchor_path=None)
