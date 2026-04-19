from __future__ import annotations

from vaner.learning.reward import RewardInput, compute_reward


def test_compute_reward_full_hit_is_positive() -> None:
    outcome = compute_reward(
        RewardInput(
            cache_tier="full_hit",
            similarity=0.92,
            quality_lift=0.35,
        )
    )
    assert outcome.reward_total > 0.5
    assert "tier_weighted" in outcome.reward_components
    assert "similarity_weighted" in outcome.reward_components
    assert "quality_lift_weighted" in outcome.reward_components


def test_compute_reward_cold_miss_negative() -> None:
    outcome = compute_reward(
        RewardInput(
            cache_tier="cold_miss",
            similarity=0.05,
            quality_lift=-0.2,
        )
    )
    assert outcome.reward_total < 0.0


def test_compute_reward_supports_optional_host_and_judge() -> None:
    baseline = compute_reward(
        RewardInput(
            cache_tier="partial_hit",
            similarity=0.4,
            quality_lift=0.1,
        )
    )
    richer = compute_reward(
        RewardInput(
            cache_tier="partial_hit",
            similarity=0.4,
            quality_lift=0.1,
            host_outcome=0.8,
            judge_score=0.9,
        )
    )
    assert richer.reward_total > baseline.reward_total
    assert "host_outcome_weighted" in richer.reward_components
    assert "judge_score_weighted" in richer.reward_components
