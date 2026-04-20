from __future__ import annotations

from vaner.memory.policy import PromotionContext, decide_promotion


def test_feedback_useful_does_not_auto_promote_without_gate() -> None:
    decision = decide_promotion(
        PromotionContext(
            rating="useful",
            resolution_confidence=0.3,
            evidence_count=1,
            contradiction_signal=0.0,
            prior_successes=1,
            has_explicit_pin=False,
            correction_confirmed=False,
        ),
        "candidate",
    )
    assert decision.to_state == "candidate"


def test_feedback_useful_promotes_when_gate_passes() -> None:
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
