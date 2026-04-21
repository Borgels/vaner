from __future__ import annotations

import pytest

from vaner.memory.policy import (
    ConflictInput,
    InvalidationContext,
    InvalidMemoryTransition,
    NegativeFeedbackContext,
    PromotionContext,
    ReuseInput,
    decide_invalidation,
    decide_on_negative_feedback,
    decide_promotion,
    decide_reuse,
    detect_conflict,
)


def test_useful_alone_does_not_promote_candidate_without_other_signals() -> None:
    decision = decide_promotion(
        PromotionContext(
            rating="useful",
            resolution_confidence=0.4,
            evidence_count=1,
            contradiction_signal=0.0,
            prior_successes=1,
            has_explicit_pin=False,
            correction_confirmed=False,
        ),
        "candidate",
    )
    assert decision.to_state == "candidate"


def test_promotion_on_high_confidence_and_multi_evidence_and_no_contradiction() -> None:
    decision = decide_promotion(
        PromotionContext(
            rating="useful",
            resolution_confidence=0.9,
            evidence_count=3,
            contradiction_signal=0.1,
            prior_successes=1,
            has_explicit_pin=False,
            correction_confirmed=False,
        ),
        "candidate",
    )
    assert decision.to_state == "trusted"


def test_wrong_always_demotes_and_increases_contradiction() -> None:
    decision = decide_on_negative_feedback(
        NegativeFeedbackContext(rating="wrong", had_contradiction=True, prior_successes=0),
        "trusted",
    )
    assert decision.to_state == "demoted"


def test_fingerprint_drift_downgrades_trusted_to_stale() -> None:
    decision = decide_invalidation(
        InvalidationContext(fingerprints_at_validation=["a", "b"], fingerprints_now=["a"], memory_confidence=0.9),
        "trusted",
    )
    assert decision is not None
    assert decision.to_state == "stale"


def test_reuse_verdict_matrix() -> None:
    assert decide_reuse(ReuseInput(True, 0.9, False, "trusted")) == "reuse_payload"
    assert decide_reuse(ReuseInput(True, 0.6, False, "candidate")) == "rerank_prior"
    assert decide_reuse(ReuseInput(False, 0.9, False, "trusted")) == "ignore_prior"


def test_conflict_signal_strength_monotonic() -> None:
    low = detect_conflict(
        ConflictInput(
            compiled_sections={"a": "auth in middleware"},
            compiled_entities={"auth", "middleware"},
            compiled_fingerprints=["a"],
            fresh_entities={"auth", "middleware"},
            fresh_fingerprints=["a"],
        )
    )
    high = detect_conflict(
        ConflictInput(
            compiled_sections={"a": "auth in middleware"},
            compiled_entities={"auth", "middleware"},
            compiled_fingerprints=["a", "b"],
            fresh_entities={"routes", "handlers"},
            fresh_fingerprints=["c"],
        )
    )
    assert high.strength >= low.strength


def test_invalid_transition_raises() -> None:
    with pytest.raises(InvalidMemoryTransition):
        decide_on_negative_feedback(NegativeFeedbackContext(rating="wrong", had_contradiction=True, prior_successes=0), "stale")
