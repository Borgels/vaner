from __future__ import annotations

from vaner.memory.policy import ReuseInput, decide_reuse


def test_decision_reuse_rejected_on_envelope_drift() -> None:
    verdict = decide_reuse(
        ReuseInput(
            evidence_fresh=True,
            envelope_similarity=0.2,
            contradiction_since_last_validation=False,
            memory_state="trusted",
        )
    )
    assert verdict == "ignore_prior"


def test_decision_reuse_rejected_on_contradiction_since_last_validation() -> None:
    verdict = decide_reuse(
        ReuseInput(
            evidence_fresh=True,
            envelope_similarity=0.95,
            contradiction_since_last_validation=True,
            memory_state="trusted",
        )
    )
    assert verdict != "reuse_payload"
