# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.intent.abstain import AbstentionPolicy


def _uniform(n: int) -> dict[str, float]:
    return {str(i): 1.0 / n for i in range(n)}


def _peaked(n: int) -> dict[str, float]:
    d = {str(i): 0.02 / max(1, n - 1) for i in range(n)}
    d["0"] = 0.98
    return d


# ---------------------------------------------------------------------------
# Uniform posterior + high contradiction → abstain
# ---------------------------------------------------------------------------


def test_uniform_posterior_and_high_contradiction_abstains():
    policy = AbstentionPolicy(entropy_threshold=0.85, contradiction_threshold=0.6)
    assert policy.should_abstain(_uniform(4), contradiction_score=0.8) is True


# ---------------------------------------------------------------------------
# Peaked posterior → no abstain regardless of contradiction
# ---------------------------------------------------------------------------


def test_peaked_posterior_no_abstain():
    policy = AbstentionPolicy(entropy_threshold=0.85, contradiction_threshold=0.6)
    assert policy.should_abstain(_peaked(4), contradiction_score=0.9) is False


# ---------------------------------------------------------------------------
# Contradiction alone (low entropy) does not trigger
# ---------------------------------------------------------------------------


def test_contradiction_alone_no_abstain():
    policy = AbstentionPolicy(entropy_threshold=0.85, contradiction_threshold=0.6)
    assert policy.should_abstain(_peaked(4), contradiction_score=1.0) is False


# ---------------------------------------------------------------------------
# Both conditions → abstain
# ---------------------------------------------------------------------------


def test_both_conditions_abstain():
    policy = AbstentionPolicy(entropy_threshold=0.85, contradiction_threshold=0.6)
    assert policy.should_abstain(_uniform(6), contradiction_score=0.75) is True


# ---------------------------------------------------------------------------
# Entropy above threshold but contradiction below → no abstain
# ---------------------------------------------------------------------------


def test_entropy_exceeded_but_contradiction_low_no_abstain():
    policy = AbstentionPolicy(entropy_threshold=0.85, contradiction_threshold=0.6)
    assert policy.should_abstain(_uniform(4), contradiction_score=0.3) is False


# ---------------------------------------------------------------------------
# contradiction_threshold > 1.0 disables contradiction gate (entropy-only)
# ---------------------------------------------------------------------------


def test_entropy_only_mode_with_high_threshold():
    policy = AbstentionPolicy(entropy_threshold=0.85, contradiction_threshold=1.0)
    # Uniform posterior exceeds entropy threshold; contradiction gate disabled.
    assert policy.should_abstain(_uniform(4), contradiction_score=0.0) is True


def test_entropy_only_mode_peaked_still_no_abstain():
    policy = AbstentionPolicy(entropy_threshold=0.85, contradiction_threshold=1.0)
    assert policy.should_abstain(_peaked(4), contradiction_score=0.0) is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_single_category_no_abstain():
    policy = AbstentionPolicy(entropy_threshold=0.0, contradiction_threshold=1.0)
    assert policy.should_abstain({"a": 1.0}) is False


def test_empty_posterior_no_abstain():
    policy = AbstentionPolicy(entropy_threshold=0.0, contradiction_threshold=1.0)
    assert policy.should_abstain({}) is False


def test_all_zero_probs_no_abstain():
    policy = AbstentionPolicy(entropy_threshold=0.0, contradiction_threshold=1.0)
    assert policy.should_abstain({"a": 0.0, "b": 0.0}) is False
