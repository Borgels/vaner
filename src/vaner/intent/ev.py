# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from vaner.intent.allocator import expected_value_score


def jaccard_reuse(files_a: list[str], files_b: list[str]) -> float:
    """Jaccard similarity between two file sets — proxy for prediction reuse potential."""
    set_a = set(files_a)
    set_b = set(files_b)
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def ev_score(
    hypothesis: str,
    *,
    posterior_p: float,
    payoff_seconds: float,
    reuse_potential: float,
    confidence_gain_per_second: float,
    temperature: float = 1.0,
) -> float:
    """Expected-value score for a single ponder hypothesis.

    Wraps ``expected_value_score`` from the allocator with a temperature divisor
    so callers can soften scores during high-uncertainty phases without touching
    the underlying formula.

    Args:
        hypothesis: The predicted next prompt (unused in computation, kept for
            callsite readability and future logging).
        posterior_p: Arc-model posterior probability for this category.
        payoff_seconds: Estimated lead time saved if the prediction is correct.
        reuse_potential: Jaccard overlap with other top hypotheses' file sets.
        confidence_gain_per_second: ``posterior_p / max(1, payoff_seconds)`` —
            how much confidence is gained per second of compute invested.
        temperature: Divides the raw EV score; 1.0 is neutral, >1 is
            conservative, <1 is aggressive.
    """
    _ = hypothesis  # reserved for future logging
    raw = expected_value_score(
        probability=posterior_p,
        payoff=payoff_seconds,
        reuse_potential=reuse_potential,
        confidence_gain_per_second=confidence_gain_per_second,
    )
    return raw / max(1e-9, float(temperature))
