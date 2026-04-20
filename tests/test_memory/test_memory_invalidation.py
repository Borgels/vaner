from __future__ import annotations

from vaner.memory.policy import InvalidationContext, decide_invalidation


def test_evidence_drift_downgrades_trusted_to_stale_on_next_resolve() -> None:
    decision = decide_invalidation(
        InvalidationContext(
            fingerprints_at_validation=["h1", "h2"],
            fingerprints_now=["h1"],
            memory_confidence=0.8,
        ),
        "trusted",
    )
    assert decision is not None
    assert decision.to_state == "stale"
