# SPDX-License-Identifier: Apache-2.0
"""WS2 — Deep-Run preset table + applicator (0.8.3).

The preset table from spec §7.2 lives here as immutable data. Engine
code calls :func:`preset_for` and :func:`horizon_bias_for` to obtain
the override bundle for an active session, then weaves the values into
its own scoring / drafting / scheduling decisions.

Design notes:

- Presets are *additive overrides*, not replacements. Engine code
  reads the base config and applies the preset deltas on top. Outside
  a Deep-Run session, no overrides apply.
- The preset bundle is pure data — no side effects, no lazy loading,
  no async. The engine consults it every cycle without cost.
- Adding a new preset means adding one entry to :data:`PRESETS`. The
  shape of :class:`DeepRunPresetSpec` is fixed by spec §16.2 (config
  surface) — adding a field requires a config-schema migration.
- Horizon biases are orthogonal to presets and compose multiplicatively
  with the preset's own long-horizon / possible-branch multipliers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from vaner.intent.deep_run import (
    DeepRunFocus,
    DeepRunHorizonBias,
    DeepRunPreset,
    DeepRunSession,
)


@dataclass(frozen=True, slots=True)
class DeepRunPresetSpec:
    """The bundle of runtime overrides for one preset.

    Field meanings mirror the spec §7.2 table. Multipliers are applied
    multiplicatively to the corresponding base values; biases are
    additive on top of the base ratios. Thresholds replace the base
    drafter values for the duration of the session.
    """

    exploit_ratio_bias: float
    invest_ratio_bias: float
    no_regret_ratio_bias: float
    artefact_alignment_weight_multiplier: float
    long_horizon_bonus_multiplier: float
    possible_branch_bonus_multiplier: float
    draft_evidence_threshold: float
    draft_volatility_ceiling: float
    max_revisits_per_prediction: int
    improvement_threshold_to_persist: float
    maturation_budget_share_per_cycle: float
    remote_budget_per_hour_cap: int
    adaptive_cycle_utilisation: float
    idle_curve_multiplier: float


@dataclass(frozen=True, slots=True)
class HorizonBiasSpec:
    """The bundle of horizon-bias multipliers for one bias mode.

    Applied as additive scoring on top of the preset's existing
    weights; see spec §7.4. Suppression values < 1.0 reduce the
    score for the named hypothesis class; multipliers > 1.0 boost.
    """

    likely_next_multiplier: float
    long_horizon_multiplier: float
    finish_partials_multiplier: float
    new_queued_suppression: float


# ---------------------------------------------------------------------------
# The preset table (spec §7.2). Immutable; values are the single source
# of truth for "what does Conservative / Balanced / Aggressive do."
# ---------------------------------------------------------------------------


PRESETS: Final[dict[DeepRunPreset, DeepRunPresetSpec]] = {
    "conservative": DeepRunPresetSpec(
        # Lean into exploiting / finishing existing high-confidence work;
        # raise drafting bars so only well-evidenced predictions promote.
        exploit_ratio_bias=0.10,
        invest_ratio_bias=-0.05,
        no_regret_ratio_bias=0.05,
        artefact_alignment_weight_multiplier=1.5,
        long_horizon_bonus_multiplier=1.0,
        possible_branch_bonus_multiplier=1.0,
        draft_evidence_threshold=0.55,
        draft_volatility_ceiling=0.30,
        max_revisits_per_prediction=2,
        improvement_threshold_to_persist=0.10,
        maturation_budget_share_per_cycle=0.30,
        remote_budget_per_hour_cap=30,
        adaptive_cycle_utilisation=0.80,
        idle_curve_multiplier=1.5,
    ),
    "balanced": DeepRunPresetSpec(
        # Default overnight setting. Moderate exploration, moderate
        # drafting depth, broader artefact alignment, moderate maturation.
        exploit_ratio_bias=0.0,
        invest_ratio_bias=0.05,
        no_regret_ratio_bias=0.05,
        artefact_alignment_weight_multiplier=1.5,
        long_horizon_bonus_multiplier=1.5,
        possible_branch_bonus_multiplier=1.3,
        draft_evidence_threshold=0.50,
        draft_volatility_ceiling=0.40,
        max_revisits_per_prediction=4,
        improvement_threshold_to_persist=0.05,
        maturation_budget_share_per_cycle=0.50,
        remote_budget_per_hour_cap=60,
        adaptive_cycle_utilisation=0.95,
        idle_curve_multiplier=0.7,
    ),
    "aggressive": DeepRunPresetSpec(
        # Wider exploration frontier, stronger long-tail / possible-branch
        # weighting, larger drafting + maturation budgets. Relax drafting
        # bar so more predictions reach READY and are then matured upward.
        exploit_ratio_bias=-0.10,
        invest_ratio_bias=0.15,
        no_regret_ratio_bias=0.0,
        artefact_alignment_weight_multiplier=2.0,
        long_horizon_bonus_multiplier=2.0,
        possible_branch_bonus_multiplier=1.8,
        draft_evidence_threshold=0.45,
        draft_volatility_ceiling=0.50,
        max_revisits_per_prediction=8,
        improvement_threshold_to_persist=0.02,
        maturation_budget_share_per_cycle=0.65,
        remote_budget_per_hour_cap=120,
        adaptive_cycle_utilisation=1.0,
        idle_curve_multiplier=0.5,
    ),
}


HORIZON_BIASES: Final[dict[DeepRunHorizonBias, HorizonBiasSpec]] = {
    "likely_next": HorizonBiasSpec(
        likely_next_multiplier=1.4,
        long_horizon_multiplier=1.0,
        finish_partials_multiplier=1.0,
        new_queued_suppression=1.0,
    ),
    "long_horizon": HorizonBiasSpec(
        likely_next_multiplier=0.8,
        long_horizon_multiplier=1.6,
        finish_partials_multiplier=1.0,
        new_queued_suppression=1.0,
    ),
    "finish_partials": HorizonBiasSpec(
        likely_next_multiplier=1.0,
        long_horizon_multiplier=1.0,
        # Boost partials that are already in flight (grounding /
        # evidence_gathering / drafting); softer boost for READY
        # predictions that are still maturable. The "finishing" prefix
        # is the operative principle: complete a partial before
        # starting a new one.
        finish_partials_multiplier=1.5,
        new_queued_suppression=0.7,
    ),
    "balanced": HorizonBiasSpec(
        likely_next_multiplier=1.0,
        long_horizon_multiplier=1.0,
        finish_partials_multiplier=1.0,
        new_queued_suppression=1.0,
    ),
}


# ---------------------------------------------------------------------------
# Lookups + utility helpers
# ---------------------------------------------------------------------------


def preset_for(session: DeepRunSession) -> DeepRunPresetSpec:
    """Return the preset bundle for an active session.

    Raises :class:`KeyError` if the session's preset is unknown — that
    would be a schema-migration bug, not a runtime concern. Callers
    should not catch.
    """

    return PRESETS[session.preset]


def horizon_bias_for(session: DeepRunSession) -> HorizonBiasSpec:
    """Return the horizon-bias multipliers for an active session."""

    return HORIZON_BIASES[session.horizon_bias]


def applies_focus_to_anchor(
    session: DeepRunSession,
    *,
    goal_status: str | None,
    anchor_path: str | None,
) -> bool:
    """Frontier admission gate for a candidate prediction anchor.

    Returns ``True`` if the candidate clears the active session's focus
    setting, ``False`` if it should be excluded.

    - ``active_goals``: only goals whose status is in
      ``{"active", "dormant"}`` contribute admissions; other goals'
      anchors are excluded.
    - ``current_workspace``: only anchors under the session's
      ``workspace_root`` are admitted (path-prefix match).
      ``anchor_path == None`` admits (it is not a workspace-bound
      anchor).
    - ``all_recent``: no admission gate; always returns ``True``.
    """

    focus: DeepRunFocus = session.focus
    if focus == "all_recent":
        return True
    if focus == "active_goals":
        if goal_status is None:
            return True
        return goal_status in ("active", "dormant")
    if focus == "current_workspace":
        if anchor_path is None:
            return True
        return anchor_path.startswith(session.workspace_root)
    return True


__all__ = [
    "HORIZON_BIASES",
    "PRESETS",
    "DeepRunPresetSpec",
    "HorizonBiasSpec",
    "applies_focus_to_anchor",
    "horizon_bias_for",
    "preset_for",
]
