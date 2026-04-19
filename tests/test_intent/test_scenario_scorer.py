from __future__ import annotations

from vaner.intent.scenario_scorer import scenario_score
from vaner.models.scenario import EvidenceRef, Scenario


def _scenario(**overrides: object) -> Scenario:
    base = Scenario(
        id="scn_score",
        kind="change",
        confidence=0.8,
        entities=["src/main.py"],
        evidence=[],
        prepared_context="context",
        coverage_gaps=[],
        freshness="fresh",
        cost_to_expand="medium",
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_scenario_score_prefers_fresh_useful_feedback() -> None:
    useful = _scenario(last_outcome="useful", evidence=[EvidenceRef(key=f"k{idx}") for idx in range(4)])
    stale = _scenario(freshness="stale", last_outcome="irrelevant")
    assert scenario_score(useful) > scenario_score(stale)


def test_scenario_score_is_clamped_between_zero_and_one() -> None:
    high = _scenario(
        confidence=0.95,
        freshness="fresh",
        cost_to_expand="low",
        last_outcome="useful",
        entities=["a", "b", "c", "d", "e", "f"],
        evidence=[EvidenceRef(key=f"k{idx}") for idx in range(12)],
    )
    low = _scenario(confidence=0.0, freshness="stale", cost_to_expand="high", last_outcome="irrelevant")
    assert 0.0 <= scenario_score(high) <= 1.0
    assert 0.0 <= scenario_score(low) <= 1.0
