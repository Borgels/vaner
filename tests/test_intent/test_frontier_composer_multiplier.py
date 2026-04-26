# SPDX-License-Identifier: Apache-2.0
"""Tests for the 0.8.7 WS5 frontier + work-style composer multipliers.

Validates:

- ``ExplorationFrontier._SOURCE_MULTIPLIERS_INIT`` registers
  ``"composer_intent": 1.0`` so the existing per-source learning loop
  picks it up.
- ``IntentPriorAdjustments.composer_signal_weight_multiplier`` is
  populated for every WorkStyle, with the load-bearing
  ``mixed=1.0`` identity invariant preserved.
- ``adjustments_for`` averages the new field correctly.
- The 0.8.6 WS4 ``mixed-is-identity`` invariant for all *other*
  multipliers still holds — the new field doesn't perturb existing
  behavior on default configs.
"""

from __future__ import annotations

from typing import get_args

import pytest

from vaner.intent.frontier import ExplorationFrontier
from vaner.intent.work_style_priors import (
    WORK_STYLE_PRIORS,
    adjustments_for,
    default_adjustments,
)
from vaner.setup.enums import WorkStyle


class TestFrontierSourceMultiplier:
    def test_composer_intent_registered_at_baseline(self) -> None:
        assert ExplorationFrontier._SOURCE_MULTIPLIERS_INIT["composer_intent"] == 1.0

    def test_existing_source_multipliers_unchanged(self) -> None:
        # Snapshot test on the values the existing 0.8.6 frontier tests
        # rely on — a refactor that perturbs these would surface here.
        assert ExplorationFrontier._SOURCE_MULTIPLIERS_INIT["graph"] == 1.0
        assert ExplorationFrontier._SOURCE_MULTIPLIERS_INIT["arc"] == 1.0
        assert ExplorationFrontier._SOURCE_MULTIPLIERS_INIT["pattern"] == 1.2
        assert ExplorationFrontier._SOURCE_MULTIPLIERS_INIT["llm_branch"] == 0.9
        assert ExplorationFrontier._SOURCE_MULTIPLIERS_INIT["skill"] == 1.1


class TestComposerSignalWeightMultiplierTable:
    def test_every_work_style_has_composer_multiplier(self) -> None:
        for style in get_args(WorkStyle):
            spec = WORK_STYLE_PRIORS[style]
            # Pin the type — every entry must explicitly populate the field.
            assert isinstance(spec.composer_signal_weight_multiplier, float)
            # Conservative range, same convention as the other multipliers.
            assert 0.7 <= spec.composer_signal_weight_multiplier <= 1.5

    def test_writing_has_strongest_boost(self) -> None:
        """Writing's drafts are the most intent-revealing — boost is highest."""
        assert WORK_STYLE_PRIORS["writing"].composer_signal_weight_multiplier == 1.4

    def test_research_boost(self) -> None:
        assert WORK_STYLE_PRIORS["research"].composer_signal_weight_multiplier == 1.3

    def test_planning_boost(self) -> None:
        assert WORK_STYLE_PRIORS["planning"].composer_signal_weight_multiplier == 1.2

    def test_support_and_learning_boost(self) -> None:
        assert WORK_STYLE_PRIORS["support"].composer_signal_weight_multiplier == 1.1
        assert WORK_STYLE_PRIORS["learning"].composer_signal_weight_multiplier == 1.1

    def test_coding_general_mixed_unsure_are_identity(self) -> None:
        for style in ("coding", "general", "mixed", "unsure"):
            assert WORK_STYLE_PRIORS[style].composer_signal_weight_multiplier == 1.0


class TestMixedIsIdentityInvariant:
    """The load-bearing 0.8.6 WS4 invariant: mixed work-style is identity.

    A user on default ``setup.work_styles == ["mixed"]`` MUST see
    byte-identical engine behavior to pre-0.8.7. The composer multiplier
    cannot change that invariant.
    """

    def test_mixed_composer_multiplier_is_one(self) -> None:
        assert WORK_STYLE_PRIORS["mixed"].composer_signal_weight_multiplier == 1.0

    def test_default_adjustments_composer_is_one(self) -> None:
        assert default_adjustments().composer_signal_weight_multiplier == 1.0

    def test_unsure_composer_multiplier_is_one(self) -> None:
        # ``unsure`` is the second identity element — same invariant.
        assert WORK_STYLE_PRIORS["unsure"].composer_signal_weight_multiplier == 1.0

    def test_mixed_only_input_yields_identity_for_all_existing_fields(self) -> None:
        """Pre-existing fields under ``mixed`` are unchanged by WS5."""
        adj = adjustments_for(("mixed",))
        assert adj.artefact_alignment_weight_multiplier == 1.0
        assert adj.long_horizon_bonus_multiplier == 1.0
        assert adj.possible_branch_bonus_multiplier == 1.0
        assert adj.drafting_evidence_floor == 0.0
        assert adj.drafting_volatility_ceiling_multiplier == 1.0
        assert adj.preferred_artefact_templates == ()
        # New field also identity under mixed.
        assert adj.composer_signal_weight_multiplier == 1.0


class TestAveraging:
    def test_composer_multiplier_averages_arithmetically(self) -> None:
        adj = adjustments_for(("writing", "coding"))
        expected = (
            WORK_STYLE_PRIORS["writing"].composer_signal_weight_multiplier + WORK_STYLE_PRIORS["coding"].composer_signal_weight_multiplier
        ) / 2
        assert adj.composer_signal_weight_multiplier == pytest.approx(expected)

    def test_averaging_order_independent(self) -> None:
        a = adjustments_for(("research", "writing"))
        b = adjustments_for(("writing", "research"))
        assert a.composer_signal_weight_multiplier == b.composer_signal_weight_multiplier
