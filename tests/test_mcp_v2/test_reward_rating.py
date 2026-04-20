from __future__ import annotations

from vaner.learning.reward import RewardInput, compute_reward


def test_useful_rating_increases_reward_vs_no_rating() -> None:
    base = compute_reward(RewardInput(cache_tier="miss", similarity=0.4)).reward_total
    rated = compute_reward(RewardInput(cache_tier="miss", similarity=0.4, rating="useful")).reward_total
    assert rated > base


def test_wrong_rating_produces_negative_reward() -> None:
    reward = compute_reward(RewardInput(cache_tier="miss", similarity=0.1, rating="wrong")).reward_total
    assert reward < 0


def test_contradiction_signal_reduces_reward() -> None:
    low = compute_reward(RewardInput(cache_tier="partial_hit", similarity=0.8, contradiction_signal=0.0)).reward_total
    high = compute_reward(RewardInput(cache_tier="partial_hit", similarity=0.8, contradiction_signal=0.8)).reward_total
    assert high < low


def test_rating_absent_keeps_current_behavior() -> None:
    reward = compute_reward(RewardInput(cache_tier="full_hit", similarity=0.9)).reward_total
    assert -1.0 <= reward <= 1.0
