# SPDX-License-Identifier: Apache-2.0
"""WS1 — VanerPolicyBundle dataclass tests (0.8.6)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from vaner.setup.policy import VanerPolicyBundle


def _valid_bundle() -> VanerPolicyBundle:
    return VanerPolicyBundle(
        id="test_bundle",
        label="Test Bundle",
        description="for unit tests",
        local_cloud_posture="local_preferred",
        runtime_profile="medium",
        spend_profile="zero",
        latency_profile="balanced",
        privacy_profile="standard",
        prediction_horizon_bias={
            "likely_next": 1.0,
            "long_horizon": 1.0,
            "finish_partials": 1.0,
            "balanced": 1.0,
        },
        drafting_aggressiveness=1.0,
        exploration_ratio=0.0,
        persistence_strength=1.0,
        goal_weighting=1.0,
        context_injection_default="policy_hybrid",
        deep_run_profile="balanced",
    )


def test_bundle_is_frozen() -> None:
    bundle = _valid_bundle()
    with pytest.raises(FrozenInstanceError):
        bundle.id = "different_id"  # type: ignore[misc]


def test_horizon_bias_keys_must_be_exact() -> None:
    with pytest.raises(ValueError, match="prediction_horizon_bias"):
        VanerPolicyBundle(
            id="x",
            label="x",
            description="x",
            local_cloud_posture="local_preferred",
            runtime_profile="medium",
            spend_profile="zero",
            latency_profile="balanced",
            privacy_profile="standard",
            # Missing "balanced" key.
            prediction_horizon_bias={
                "likely_next": 1.0,
                "long_horizon": 1.0,
                "finish_partials": 1.0,
            },
            drafting_aggressiveness=1.0,
            exploration_ratio=0.0,
            persistence_strength=1.0,
            goal_weighting=1.0,
            context_injection_default="policy_hybrid",
            deep_run_profile="balanced",
        )


def test_horizon_bias_extra_keys_rejected() -> None:
    with pytest.raises(ValueError, match="prediction_horizon_bias"):
        VanerPolicyBundle(
            id="x",
            label="x",
            description="x",
            local_cloud_posture="local_preferred",
            runtime_profile="medium",
            spend_profile="zero",
            latency_profile="balanced",
            privacy_profile="standard",
            prediction_horizon_bias={
                "likely_next": 1.0,
                "long_horizon": 1.0,
                "finish_partials": 1.0,
                "balanced": 1.0,
                "unexpected": 1.0,
            },
            drafting_aggressiveness=1.0,
            exploration_ratio=0.0,
            persistence_strength=1.0,
            goal_weighting=1.0,
            context_injection_default="policy_hybrid",
            deep_run_profile="balanced",
        )


def test_horizon_bias_is_immutable_view() -> None:
    bundle = _valid_bundle()
    # The mapping is wrapped as a MappingProxyType and so cannot be
    # mutated after construction.
    assert isinstance(bundle.prediction_horizon_bias, MappingProxyType)
    with pytest.raises(TypeError):
        bundle.prediction_horizon_bias["likely_next"] = 99.0  # type: ignore[index]


def test_horizon_bias_keys_are_exactly_the_four_expected() -> None:
    bundle = _valid_bundle()
    assert set(bundle.prediction_horizon_bias.keys()) == {
        "likely_next",
        "long_horizon",
        "finish_partials",
        "balanced",
    }
